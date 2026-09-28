"""The one module that touches the filesystem: for the Markdown backend, and
for the files of a run that are not store data (payloads, scratch, the spec)."""
import json
import os
import signal
import subprocess
import threading
import time

from .contract import CtxError

try:
    import fcntl
except ImportError:  # no flock on this platform
    fcntl = None

MARKER = "ctx-store.json"
NESTED = ".context"


def package_text(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def cwd():
    return os.getcwd()


def exists(path):
    return os.path.exists(path)


def is_store(path):
    return os.path.isfile(os.path.join(path, MARKER))


def find_store(start):
    """Walk up from start to the nearest store root: a directory holding the
    marker, or holding a `.context/` directory that does."""
    current = os.path.realpath(start)
    while True:
        if is_store(current):
            return current
        nested = os.path.join(current, NESTED)
        if is_store(nested):
            return nested
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def canonical(path):
    return os.path.realpath(path)


def schema_version(root):
    try:
        with open(os.path.join(root, MARKER), encoding="utf-8") as handle:
            version = json.load(handle)["schema_version"]
    except (OSError, ValueError, KeyError, TypeError):
        raise CtxError("SCHEMA_VIOLATION", MARKER) from None
    if type(version) is not int or version < 1:
        raise CtxError("SCHEMA_VIOLATION", MARKER)
    return version


def read_only(root):
    if not os.access(root, os.W_OK):
        return True
    try:
        return bool(os.statvfs(root).f_flag & os.ST_RDONLY)
    except (AttributeError, OSError):
        return False


def filesystem(root, mounts="/proc/self/mounts"):
    """Filesystem type of the mount holding root; 'unknown' where the mount
    table is not readable."""
    try:
        with open(mounts, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return "unknown"
    best, kind = "", "unknown"
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        point = fields[1].replace("\\040", " ")
        inside = root == point or root.startswith(point.rstrip("/") + "/")
        if inside and len(point) >= len(best):
            best, kind = point, fields[2]
    return kind


def flock_works(root):
    """Prove flock without writing: two read-only descriptors on the marker,
    the second must be refused while the first holds the lock."""
    if fcntl is None:
        return False
    marker = os.path.join(root, MARKER)
    try:
        first = os.open(marker, os.O_RDONLY)
    except OSError:
        return False
    try:
        second = os.open(marker, os.O_RDONLY)
        try:
            fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
        except OSError:
            return False
        finally:
            os.close(second)
    finally:
        os.close(first)


def lock_mode(root):
    if flock_works(root):
        return "flock"
    return "none" if read_only(root) else "mkdir"


# --- docs -------------------------------------------------------------------

def marker(root):
    """The marker file's content."""
    schema_version(root)
    with open(os.path.join(root, MARKER), encoding="utf-8") as handle:
        return json.load(handle)


def write_scratch(folder, name, data):
    """A payload file in the scratch directory (`--out auto`); returns its path."""
    path = os.path.join(folder, name)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
    except OSError:
        raise CtxError("USAGE", "CTX_SCRATCH") from None
    return path


def types(root):
    """Doc type schemas: `.ctx/types/<type>.json`, by type name."""
    folder = os.path.join(root, ".ctx", "types")
    found = {}
    if os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                try:
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        found[name[:-5]] = json.load(handle)
                except ValueError:
                    raise CtxError("SCHEMA_VIOLATION", f".ctx/types/{name}") from None
    return found


def doc_path(root, key):
    """(absolute path, key) of a doc; the path is canonical and inside the store."""
    if not key or "\0" in key or os.path.isabs(key):
        raise CtxError("PATH_ESCAPE", key)
    relative = key if key.endswith(".md") else key + ".md"
    path = os.path.realpath(os.path.join(root, relative))
    if os.path.commonpath([root, path]) != root or path == root:
        raise CtxError("PATH_ESCAPE", key)
    inside = os.path.relpath(path, root)
    if any(part.startswith(".") for part in inside.split(os.sep)):
        raise CtxError("PATH_ESCAPE", key)
    return path, inside[:-3].replace(os.sep, "/")


def list_docs(root):
    """Keys of every `*.md` below the root, sorted; dot directories are not docs."""
    keys = []
    for folder, folders, files in os.walk(root):
        folders[:] = sorted(f for f in folders if not f.startswith("."))
        for name in files:
            if name.endswith(".md") and not name.startswith("."):
                inside = os.path.relpath(os.path.join(folder, name), root)
                keys.append(inside[:-3].replace(os.sep, "/"))
    return sorted(keys)


def read_bytes(path, key):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise CtxError("NO_SUCH_DOC", key) from None


def read_payload(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        raise CtxError("USAGE", path) from None


# --- writes -----------------------------------------------------------------

def _terminate(signum, frame):
    raise SystemExit(128 + signum)


class Lock:
    """One lock per store. flock where the probe proved it, an atomic mkdir
    lock otherwise. Waits up to `timeout` seconds, then LOCK_TIMEOUT."""

    def __init__(self, root, timeout, mode=None):
        self.folder = os.path.join(root, ".lock")
        self.timeout = timeout
        self.mode = mode or lock_mode(root)
        self.root = root
        self.handle = None
        self.held = None
        self.previous = None

    def __enter__(self):
        if read_only(self.root) or self.mode == "none":
            raise CtxError("STORE_READONLY", self.root)
        try:
            os.makedirs(self.folder, exist_ok=True)
        except OSError:
            raise CtxError("STORE_READONLY", self.root) from None
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try():
                break
            if time.monotonic() >= deadline:
                raise CtxError("LOCK_TIMEOUT", "store")
            time.sleep(0.02)
        if threading.current_thread() is threading.main_thread():
            self.previous = signal.signal(signal.SIGTERM, _terminate)
        return self

    def _try(self):
        if self.mode == "flock":
            handle = os.open(os.path.join(self.folder, "store.lock"), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(handle)
                return False
            self.handle = handle
            return True
        held = os.path.join(self.folder, "store.d")
        try:
            os.mkdir(held)
        except FileExistsError:
            self._break_stale(held)
            return False
        with open(os.path.join(held, "owner"), "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        self.held = held
        return True

    @staticmethod
    def _break_stale(held):
        """Remove a mkdir lock whose owner process is gone."""
        owner = os.path.join(held, "owner")
        try:
            with open(owner, encoding="utf-8") as handle:
                pid = int(handle.read().strip())
            os.kill(pid, 0)
        except (ProcessLookupError, ValueError):
            try:
                os.unlink(owner)
                os.rmdir(held)
            except OSError:
                pass
        except (OSError, PermissionError):
            pass  # no owner file yet, or a live process of another user

    def __exit__(self, *exc):
        if self.previous is not None:
            signal.signal(signal.SIGTERM, self.previous)
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            os.close(self.handle)
        if self.held is not None:
            try:
                os.unlink(os.path.join(self.held, "owner"))
                os.rmdir(self.held)
            except OSError:
                pass
        return False


def write_atomic(path, data):
    """Temp file in the doc's directory, fsync, rename, then re-read. The temp
    file is removed on any failure, a signal included."""
    folder = os.path.dirname(path)
    temp = os.path.join(folder, f".ctx-tmp-{os.getpid()}-{os.path.basename(path)}")
    try:
        try:
            os.makedirs(folder, exist_ok=True)
            handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except (PermissionError, OSError) as failure:
            if isinstance(failure, FileExistsError):
                raise
            raise CtxError("STORE_READONLY", folder) from None
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if os.path.exists(path):
            os.chmod(temp, os.stat(path).st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    with open(path, "rb") as handle:
        return handle.read()


def folder(root, key):
    """The key of a directory below the root ('' for the root itself), or
    None when `key` does not name one."""
    if key in ("", ".", "/"):
        return ""
    if "\0" in key or os.path.isabs(key):
        return None
    path = os.path.realpath(os.path.join(root, key))
    if not os.path.isdir(path) or os.path.commonpath([root, path]) != root:
        return None
    inside = os.path.relpath(path, root)
    if any(part.startswith(".") for part in inside.split(os.sep)):
        return None
    return inside.replace(os.sep, "/")


def remove(path):
    try:
        os.unlink(path)
    except PermissionError:
        raise CtxError("STORE_READONLY", path) from None


def is_dir(path):
    return os.path.isdir(path)


def template(root, name):
    """The scaffold of a doc type: `.ctx/templates/<type>.md`, or None."""
    path = os.path.join(root, ".ctx", "templates", name + ".md")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def audit_append(root, actor, row):
    """Add one row, numbered from the store's counter. Call under the lock."""
    folder = os.path.join(root, ".audit")
    os.makedirs(folder, exist_ok=True)
    counter = os.path.join(folder, "seq")
    try:
        with open(counter, encoding="utf-8") as handle:
            last = int(handle.read().strip() or 0)
    except (FileNotFoundError, ValueError):
        last = max([r.get("seq", 0) for r in audit_rows(root) if type(r.get("seq")) is int], default=0)
    row = {**row, "seq": last + 1}
    line = (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    handle = os.open(os.path.join(folder, f"{actor}.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(handle, line)
        os.fsync(handle)
    finally:
        os.close(handle)
    write_atomic(counter, f"{row['seq']}\n".encode("utf-8"))
    return row


def audit_archive(root, actor):
    source = os.path.join(root, ".audit", f"{actor}.jsonl")
    if not os.path.isfile(source):
        return False
    folder = os.path.join(root, ".audit", "archive")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"{actor}.jsonl")
    with open(source, "rb") as handle:
        rows = handle.read()
    sink = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(sink, rows)
        os.fsync(sink)
    finally:
        os.close(sink)
    os.unlink(source)
    return True


def _git(root, *args, env=None, quiet=False):
    """One git command in the store. A command that cannot run, runs out of
    time or fails is GIT_FAILED; `quiet` returns None instead."""
    try:
        done = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.SubprocessError):
        done = None
    if done is None or done.returncode != 0:
        if quiet:
            return None
        raise CtxError("GIT_FAILED", args[0])
    return done.stdout.strip()


def git_commit(root, actor, message, debounce, now):
    """Commit the store's changes, unless ctx committed less than `debounce`
    seconds ago. None when the store is in no work tree or nothing changed."""
    if _git(root, "rev-parse", "--is-inside-work-tree", quiet=True) != "true":
        return None
    if not _git(root, "status", "--porcelain", "--", "."):
        return None
    last = _git(root, "log", "-1", "--format=%ct", "--grep", "^ctx: ", "--", ".", quiet=True) or ""
    if last.isdigit() and now - int(last) < debounce:
        return None
    environ = {**os.environ, "GIT_AUTHOR_NAME": actor, "GIT_AUTHOR_EMAIL": f"{actor}@ctx.invalid",
               "GIT_COMMITTER_NAME": actor, "GIT_COMMITTER_EMAIL": f"{actor}@ctx.invalid",
               "GIT_AUTHOR_DATE": f"{now} +0000", "GIT_COMMITTER_DATE": f"{now} +0000"}
    _git(root, "add", "-A", "--", ".")
    _git(root, "commit", "-q", "--no-verify", "-m", f"ctx: {message}", "--", ".", env=environ)
    return "committed: " + _git(root, "rev-parse", "--short", "HEAD")


def audit_rows(root):
    """Every audit row, in file order per actor, the archived ones included; a
    broken line is skipped."""
    rows = []
    for folder in (os.path.join(root, ".audit", "archive"), os.path.join(root, ".audit")):
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".jsonl"):
                    rows += _rows(os.path.join(folder, name))
    return rows


def _rows(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows
