"""`init`: a Markdown store made, or brought up to date, from the settings,
schemas and templates a consumer hands over. It runs before any backend can
open the store, and reads files of the run, so it is on `fs` itself."""
import json
import os.path
import re

from . import fs
from .backend import SCHEMA_VERSION, SCHEME, SETTINGS, check_settings, open_store
from .contract import CtxError
from .store import _check_schema, _seq, digest
from .verbs import _clock

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
KEYS = ("schema_version", *SETTINGS, "resolve", "maintain")


def _marker(data):
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _settings(path):
    """The marker a `--settings` file asks for, checked as a store checks its
    marker when it is opened; a key the store does not know is refused too."""
    try:
        data = json.loads(fs.read_payload(path))
    except ValueError:
        raise CtxError("SCHEMA_VIOLATION", path) from None
    if isinstance(data, dict):
        for key in data:
            if key not in KEYS:
                raise CtxError("SCHEMA_VIOLATION", key)
        version = data.setdefault("schema_version", SCHEMA_VERSION)
        if type(version) is not int or version != SCHEMA_VERSION:
            raise CtxError("SCHEMA_VIOLATION", "schema_version")
    check_settings(data, path)
    return data


def _schema(name, data):
    _check_schema(name, json.loads(data))


def _template(name, data):
    data.decode("utf-8")


def _copies(folder, option, suffix, check):
    """(store file, bytes) of every `<type><suffix>` in a folder, each one
    checked; anything else in the folder is USAGE."""
    found = []
    for name, source, is_file in fs.entries(folder, option):
        if not (is_file and name.endswith(suffix) and NAME.match(name[: -len(suffix)])):
            raise CtxError("USAGE", source)
        data = fs.read_payload(source)
        try:
            check(name[: -len(suffix)], data)
        except (CtxError, ValueError):
            raise CtxError("SCHEMA_VIOLATION", source) from None
        found.append((f".ctx/{option[2:]}/{name}", data))
    return found


def _same(before, data):
    """Whether an existing marker holds these settings, however it is laid out."""
    try:
        return json.loads(before) == json.loads(data)
    except ValueError:
        return False


def _put(root, name, data):
    if fs.write_atomic(os.path.join(root, *name.split("/")), data) != data:
        raise CtxError("STORE_READONLY", name)


def _row(store, config, now, name, before, data):
    store.audit_append(config.actor, {
        "ts": now,
        "actor": config.actor,
        "verb": "init",
        "doc": name,
        "before": None if before is None else digest(before),
        "after": digest(data),
    })


def _last_init(root, config):
    """Store file -> the hash the latest `init` row left it with."""
    if not fs.is_store(root):
        return {}
    last = {}
    for row in sorted(open_store(root, config).audit_rows(), key=_seq):
        if row.get("verb") == "init" and isinstance(row.get("doc"), str):
            last[row["doc"]] = row.get("after")
    return last


def init(config, params):
    """Every input is checked before anything is written, so a refused run
    leaves no half-made store."""
    if params.get("upgrade") and params.get("replace"):
        raise CtxError("USAGE", "--upgrade")
    if not config.stores:
        raise CtxError("STORE_NOT_NAMED")
    if len(config.stores) > 1:
        raise CtxError("USAGE", "--store" if config.source == "flag" else "CTX_STORE")
    locator = config.stores[0]
    match = SCHEME.match(locator)
    if match and match.group(1) != "markdown":
        raise CtxError("USAGE", locator)
    root = fs.canonical(match.group(2) if match else locator)
    if fs.exists(root) and not fs.is_dir(root):
        raise CtxError("USAGE", locator)
    now, _ = _clock(params.get("now"))
    files = []
    if "settings" in params:
        files.append((fs.MARKER, _marker(_settings(params["settings"]))))
    elif not fs.is_store(root):
        files.append((fs.MARKER, _marker({"schema_version": SCHEMA_VERSION})))
    if "types" in params:
        files += _copies(params["types"], "--types", ".json", _schema)
    if "templates" in params:
        files += _copies(params["templates"], "--templates", ".md", _template)
    # --upgrade replaces a file only while it holds what init last wrote there:
    # one edited since is kept. A file that already matches, with no init row
    # yet (a store made by hand), gets one as the baseline for the next upgrade.
    upgrade = params.get("upgrade")
    last = _last_init(root, config) if upgrade else {}
    plan, unchanged, kept, baseline = [], [], [], []
    for name, data in files:
        before = fs.read_file(os.path.join(root, *name.split("/")))
        if before == data or (name == fs.MARKER and before is not None and _same(before, data)):
            unchanged.append(name)
            if upgrade and name not in last:
                baseline.append((name, before))
        elif before is not None and upgrade and last.get(name) != digest(before):
            kept.append(name)
        elif before is not None and not (params.get("replace") or upgrade):
            raise CtxError("SCHEMA_VIOLATION", name)
        else:
            plan.append((name, before, data))
    if plan or baseline:
        # The lock lives in the store and its mode is probed on the marker, so
        # a new store's marker is written first, unlocked: no other run opens
        # a directory without one. The rest, and every audit row, is written
        # under the lock.
        fresh = bool(plan) and plan[0][0] == fs.MARKER and plan[0][1] is None
        if fresh:
            _put(root, fs.MARKER, plan[0][2])
        store = open_store(root, config)
        with store.lock():
            for name, before, data in plan:
                if not (fresh and name == fs.MARKER):
                    _put(root, name, data)
                _row(store, config, now, name, before, data)
            for name, data in baseline:
                _row(store, config, now, name, data, data)
    written = [name for name, _, _ in plan]
    lines = [f"ok: store {root}, {len(written)} written, {len(unchanged)} unchanged"]
    if upgrade:
        lines[0] += f", {len(kept)} kept"
    lines += [f"{'written' if name in written else 'kept' if name in kept else 'unchanged'}: {name}"
              for name, _ in files]
    data = {"store": root, "unchanged": unchanged, "written": written}
    if upgrade:
        data["kept"] = kept
    return data, "\n".join(lines)
