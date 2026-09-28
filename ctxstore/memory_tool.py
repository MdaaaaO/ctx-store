"""Handler for Anthropic's memory tool: its six commands, 1:1 on the store.

The tool speaks of files below `/memories`; here that directory is the store.
A failure is the contract's one line, as the tool result.
"""
import json

from .contract import CtxError

ROOT = "/memories"
# command -> (verb, tool field -> verb parameter)
COMMANDS = {
    "view": ("view", {"path": "doc", "view_range": "range"}),
    "create": ("create", {"path": "doc", "file_text": "text"}),
    "str_replace": ("str_replace", {"path": "doc", "old_str": "old", "new_str": "new"}),
    "insert": ("insert", {"path": "doc", "insert_line": "line", "insert_text": "text"}),
    "delete": ("delete", {"path": "doc"}),
    "rename": ("rename", {"old_path": "doc", "new_path": "to"}),
}


def _doc(path):
    if not isinstance(path, str) or not (path == ROOT or path.startswith(ROOT + "/")):
        raise CtxError("PATH_ESCAPE", str(path))
    return path[len(ROOT):].lstrip("/")


def translate(call):
    """(verb, parameters) of one tool call."""
    if not isinstance(call, dict) or call.get("command") not in COMMANDS:
        raise CtxError("USAGE", "command")
    verb, fields = COMMANDS[call["command"]]
    params = {}
    for field, value in call.items():
        if field == "command":
            continue
        if field not in fields:
            raise CtxError("USAGE", field)
        name = fields[field]
        if name in ("doc", "to"):
            value = _doc(value)
        elif name == "range":
            if not (isinstance(value, list) and len(value) == 2 and all(type(v) is int for v in value)):
                raise CtxError("USAGE", field)
            value = f"{value[0]}:{value[1]}"
        params[name] = value
    return verb, params


def handle(call, environ, store=None, now=None):
    """The tool result of one call: (text, whether it is an error)."""
    from . import cli
    try:
        verb, params = translate(call)
        params = cli.required(verb, cli.typed(verb, params, files=False))
        _, text = cli.dispatch(verb, params, environ, store, now)
    except CtxError as failure:
        return failure.line(), True
    return text, False


def run(payload, environ, store=None, now=None):
    """`ctx memory`: one tool call as JSON on stdin."""
    try:
        call = json.loads(payload)
    except ValueError:
        raise CtxError("USAGE", "stdin") from None
    from . import cli
    verb, params = translate(call)
    params = cli.required(verb, cli.typed(verb, params, files=False))
    return cli.dispatch(verb, params, environ, store, now)
