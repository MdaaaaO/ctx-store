"""MCP server over stdio: a thin adapter, one tool per built verb.

JSON-RPC 2.0, one message per line. Nothing is kept between calls; every
tool call runs the same code as the command line.
"""
import json

from . import __version__, spec
from .contract import CtxError

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
KINDS = {"text": "string", "int": "integer", "flag": "boolean"}
HIDDEN = ("from", "out")  # files of the server's machine are not the client's


def describe(text, positional, options):
    """A verb's help as a tool's description. The help is written for the
    command line: what differs over MCP is said after it, so a model does
    not look for a parameter that the help names and the schema lacks."""
    notes = []
    if positional:
        notes.append("Parameters named in angle brackets above are, in order: " + ", ".join(positional) + ".")
    hidden = [f"--{name}" for name in HIDDEN if name in options]
    if hidden:
        notes.append("Not offered over MCP: " + ", ".join(hidden) + " (files of the server's machine).")
    return text + ("\n\nOver MCP. " + " ".join(notes) if notes else "")


def tools():
    from . import cli
    sections = spec.verbs()
    listed = []
    for verb in cli.VERBS:
        if verb not in cli.BUILT:
            continue
        positional, options = cli.BUILT[verb]
        properties = {name: {"type": "string"} for name in positional}
        for name, kind in options.items():
            if name not in HIDDEN:
                properties[name] = {"type": KINDS[kind]}
        listed.append({
            "name": f"ctx_{verb}",
            "description": describe(sections[verb], positional, options),
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": list(cli.REQUIRED.get(verb, ())),
                "additionalProperties": False,
            },
        })
    return listed


def call(params, environ, store):
    from . import cli
    name = params.get("name") if isinstance(params, dict) else None
    verb = name[4:] if isinstance(name, str) and name.startswith("ctx_") else None
    if verb not in cli.BUILT:
        raise LookupError(f"unknown tool: {name}")
    given = params.get("arguments") or {}
    if not isinstance(given, dict):
        raise LookupError("arguments must be an object")
    try:
        if any(key in HIDDEN for key in given):
            raise CtxError("USAGE", next(key for key in given if key in HIDDEN))
        checked = cli.required(verb, cli.typed(verb, given, files=False))
        _, text = cli.dispatch(verb, checked, environ, store)
    except CtxError as failure:
        return {"content": [{"type": "text", "text": failure.line()}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "isError": False}


def respond(message, environ, store):
    """The reply to one message, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    ident, method = message.get("id"), message.get("method")
    if "id" not in message:
        return None
    if method == "initialize":
        asked = (message.get("params") or {}).get("protocolVersion")
        return _result(ident, {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ctx", "version": __version__},
        })
    if method == "ping":
        return _result(ident, {})
    if method == "tools/list":
        return _result(ident, {"tools": tools()})
    if method == "tools/call":
        try:
            return _result(ident, call(message.get("params"), environ, store))
        except LookupError as failure:
            return _error(ident, -32602, str(failure))
    return _error(ident, -32601, f"method not found: {method}")


def _result(ident, result):
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident, code, text):
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": text}}


def serve(source, out, environ, store=None):
    for line in source:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = _error(None, -32700, "parse error")
        else:
            reply = respond(message, environ, store)
        if reply is not None:
            out.write(json.dumps(reply, ensure_ascii=False, sort_keys=True) + "\n")
            out.flush()
