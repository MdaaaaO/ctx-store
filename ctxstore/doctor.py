"""`ctx doctor`: what this machine and store give the tool. Writes nothing."""
import platform

from . import __version__, fs
from .contract import API


def report(config, roots):
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
            {
                "path": root,
                "schema_version": fs.schema_version(root),
                "filesystem": fs.filesystem(root),
                "read_only": fs.read_only(root),
                "lock_mode": config.lock_mode or fs.lock_mode(root),
            }
            for root in roots
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
    for store in data["stores"]:
        lines += [
            f"store: {store['path']} (from {data['store_source']})",
            f"  schema version: {store['schema_version']}",
            f"  filesystem: {store['filesystem']}",
            f"  read-only: {'yes' if store['read_only'] else 'no'}",
            f"  lock mode: {store['lock_mode']}",
        ]
    return "\n".join(lines)
