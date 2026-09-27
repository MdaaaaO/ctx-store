"""The one module that touches the filesystem.

P0 only reads: nothing here writes under a store.
"""
import json
import os

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


def resolve_stores(configured, start, walk=True):
    if configured:
        roots = []
        for path in configured:
            if not is_store(path):
                raise CtxError("NO_STORE", path)
            roots.append(os.path.realpath(path))
        return roots
    root = find_store(start) if walk else None
    if root is None:
        raise CtxError("NO_STORE")
    return [root]


def schema_version(root):
    try:
        with open(os.path.join(root, MARKER), encoding="utf-8") as handle:
            version = json.load(handle)["schema_version"]
    except (ValueError, KeyError, TypeError):
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
