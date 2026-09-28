"""The contract: exit codes, fixed error strings and the --json envelope.

Everything here is mirrored in interface.md; tests keep the two in step.
"""
import json

API = 1

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_FOUND = 2
EXIT_VALIDATION = 3
EXIT_LOCK_TIMEOUT = 4
EXIT_READONLY = 5

# code -> (exit code, fixed message)
ERRORS = {
    "USAGE": (EXIT_USAGE, "bad command line"),
    "NOT_BUILT": (EXIT_USAGE, "verb is specified but not built in this version"),
    "NO_STORE": (EXIT_NOT_FOUND, "no store found"),
    "NO_SUCH_DOC": (EXIT_NOT_FOUND, "no such doc"),
    "NO_SUCH_SECTION": (EXIT_NOT_FOUND, "no such section"),
    "NO_MATCH": (EXIT_NOT_FOUND, "text not found in the doc"),
    "DOC_EXISTS": (EXIT_VALIDATION, "doc exists"),
    "AMBIGUOUS_SELECTOR": (EXIT_VALIDATION, "selector matches more than one target"),
    "SCHEMA_VIOLATION": (EXIT_VALIDATION, "schema violation"),
    "SECRET_DETECTED": (EXIT_VALIDATION, "payload looks like a secret"),
    "PATH_ESCAPE": (EXIT_VALIDATION, "path leaves the store"),
    "NOT_OWNER": (EXIT_VALIDATION, "doc is owned by another actor"),
    "GENERATED": (EXIT_VALIDATION, "doc is generated"),
    "UNAUDITED_WRITE": (EXIT_VALIDATION, "doc changed with no audit row"),
    "LOCK_TIMEOUT": (EXIT_LOCK_TIMEOUT, "lock timeout"),
    "STORE_READONLY": (EXIT_READONLY, "store is read-only or unwritable"),
    "STORE_NOT_NAMED": (EXIT_READONLY, "a write needs CTX_STORE or --store"),
}


class CtxError(Exception):
    """A contract failure: one code, an optional detail slot."""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def exit_code(self):
        return ERRORS[self.code][0]

    @property
    def message(self):
        return ERRORS[self.code][1]

    def line(self):
        head = f"{self.code} {self.detail}" if self.detail else self.code
        return f"{head}: {self.message}"

    def envelope(self):
        return {
            "api": API,
            "ok": False,
            "error": {
                "code": self.code,
                "detail": self.detail,
                "message": self.message,
                "exit": self.exit_code,
            },
        }


class Findings(CtxError):
    """Several failures of one run (validate): (code, detail, doc) each. The
    exit code and the envelope's error are the first one's."""

    def __init__(self, found):
        super().__init__(found[0][0], found[0][1])
        self.found = found

    def line(self):
        return "\n".join(
            CtxError(code, detail if detail == doc else f"{doc} {detail}").line()
            for code, detail, doc in self.found
        )

    def envelope(self):
        envelope = super().envelope()
        envelope["error"]["findings"] = [
            {"code": code, "detail": detail, "doc": doc, "message": ERRORS[code][1]}
            for code, detail, doc in self.found
        ]
        return envelope


def ok_envelope(verb, data):
    return {"api": API, "ok": True, "verb": verb, "data": data}


def dump(envelope):
    return json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False)
