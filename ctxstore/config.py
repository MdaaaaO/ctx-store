"""Configuration comes from the environment only; nothing is read from $HOME
except the XDG cache directory."""
import os
import re

from .backend import split_locators
from .contract import CtxError

DEFAULT_LOCK_TIMEOUT = 10.0
ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Config:
    def __init__(self, environ, store_flag=None):
        self.stores = _store_list(store_flag or environ.get("CTX_STORE", ""))
        self.lock_timeout = _seconds(environ.get("CTX_LOCK_TIMEOUT", ""))
        self.cache_dir = _cache_dir(environ)
        self.scratch = environ.get("CTX_SCRATCH") or None
        self.git = environ.get("CTX_GIT", "") == "1"
        self.actor = _actor(environ)
        self.lock_mode = environ.get("CTX_LOCK_MODE") or None
        if self.lock_mode not in (None, "flock", "mkdir"):
            raise CtxError("USAGE", "CTX_LOCK_MODE")
        self.walk = environ.get("CTX_NO_WALK", "") != "1"
        self.source = "flag" if store_flag else "env" if self.stores else "walk"


def _actor(environ):
    name = environ.get("CTX_ACTOR") or environ.get("USER") or environ.get("LOGNAME") or "unknown"
    if not ACTOR.match(name):
        raise CtxError("USAGE", "CTX_ACTOR")
    return name


def _store_list(value):
    return split_locators(value)


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
