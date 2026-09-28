"""`ctx doctor`: what this machine and store give the tool. Writes nothing."""
import platform

from . import __version__, fs
from .contract import API


def report(config, backends):
    return {
        "version": __version__,
        "api": API,
        "python": platform.python_version(),
        "lock_timeout": config.lock_timeout,
        "git": config.git,
        "store_source": config.source,
        "scratch": config.scratch,
        "cache_dir": {
            "path": config.cache_dir,
            "exists": bool(config.cache_dir) and fs.exists(config.cache_dir),
        },
        "stores": [
            {"backend": backend.name, "locator": backend.locator, **backend.describe()}
            for backend in backends
        ],
    }


def text(data):
    lines = [
        f"ctx: {data['version']} (api {data['api']})",
        f"python: {data['python']}",
        f"lock timeout: {data['lock_timeout']:g} s",
        f"git: {'on' if data['git'] else 'off'}",
        f"scratch: {data['scratch'] or '-'}",
        "cache dir: {} ({})".format(
            data["cache_dir"]["path"] or "-",
            "exists" if data["cache_dir"]["exists"] else "absent",
        ),
    ]
    labels = {"schema_version": "schema version", "read_only": "read-only", "lock_mode": "lock mode"}
    for store in data["stores"]:
        lines.append(f"store: {store['locator']} ({store['backend']}, from {data['store_source']})")
        for key, value in store.items():
            if key not in ("backend", "locator", "path"):
                shown = ("yes" if value else "no") if isinstance(value, bool) else value
                lines.append(f"  {labels.get(key, key)}: {shown}")
    return "\n".join(lines)
