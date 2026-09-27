"""Configuration comes from the environment only; nothing is read from $HOME
except the XDG cache directory."""
import os

from .contract import CtxError

DEFAULT_LOCK_TIMEOUT = 10.0


class Config:
    def __init__(self, environ, store_flag=None):
        self.stores = _store_list(store_flag or environ.get("CTX_STORE", ""))
        self.lock_timeout = _seconds(environ.get("CTX_LOCK_TIMEOUT", ""))
        self.cache_dir = _cache_dir(environ)
        self.scratch = environ.get("CTX_SCRATCH") or None
        self.git = environ.get("CTX_GIT", "") == "1"


def _store_list(value):
    return [part for part in value.split(os.pathsep) if part]


def _seconds(value):
    if not value:
        return DEFAULT_LOCK_TIMEOUT
    try:
        seconds = float(value)
    except ValueError:
        seconds = -1.0
    if seconds < 0 or seconds != seconds or seconds == float("inf"):
        raise CtxError("USAGE", "CTX_LOCK_TIMEOUT")
    return seconds


def _cache_dir(environ):
    if environ.get("CTX_CACHE_DIR"):
        return environ["CTX_CACHE_DIR"]
    if environ.get("XDG_CACHE_HOME"):
        return os.path.join(environ["XDG_CACHE_HOME"], "ctx")
    if environ.get("HOME"):
        return os.path.join(environ["HOME"], ".cache", "ctx")
    return None
