"""Secret guard: every payload is scanned before it is written. A finding
names the rule, never the value."""
import re

from .contract import CtxError

RULES = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}")),
    ("api-key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
)


def scan(payload):
    for name, rule in RULES:
        if rule.search(payload):
            raise CtxError("SECRET_DETECTED", name)
