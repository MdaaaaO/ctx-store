"""The time a run records."""
import time


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def today():
    return time.strftime("%Y-%m-%d", time.localtime())
