"""MCP server over stdio: a thin adapter, one tool per built verb.

JSON-RPC 2.0, one message per line. Nothing is kept between calls; every
tool call runs the same code as the command line.

Two eras of the protocol are served side by side. A request that names its
version in `_meta` is answered as that revision says (2026-07-28, stateless);
any other request is answered as the handshake revisions say, `initialize`
included. The HTTP connector hands in its request headers, and the checks the
Streamable HTTP binding asks for are made here, so both transports agree.
"""
import base64
import binascii
import json

from . import __version__, spec
from .config import ACTOR as ACTOR_NAME
from .contract import CtxError

MODERN = ("2026-07-28",)  # stateless: every request names its version in `_meta`
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")  # agreed once, in `initialize`
SUPPORTED = MODERN + PROTOCOLS  # newest first: what `server/discover` and a version refusal list
META = "io.modelcontextprotocol/"
NAMED = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}  # methods that carry Mcp-Name
SERVER = {"name": "ctx", "version": __version__}
KINDS = {"text": "string", "int": "integer", "flag": "boolean"}
HIDDEN = ("from", "out")  # files of the server's machine are not the client's
READ_ONLY = ("doctor", "brief", "get", "find", "resolve", "view")  # no `actor`: they write nothing
ACTOR = ("The optional `actor` writes as that actor instead of the server's; the store's settings "
         "must allow the name (`mcp.actors`).")


def describe(text, positional, options, actor=False):
    """A verb's help as a tool's description. The help is written for the
    command line: what differs over MCP is said after it, so a model does
    not look for a parameter that the help names and the schema lacks."""
    notes = []
    if positional:
        notes.append("Parameters named in angle brackets above are, in order: " + ", ".join(positional) + ".")
    hidden = [f"--{name}" for name in HIDDEN if name in options]
    if hidden:
        notes.append("Not offered over MCP: " + ", ".join(hidden) + " (files of the server's machine).")
    if actor:
        notes.append(ACTOR)
    return text + ("\n\nOver MCP. " + " ".join(notes) if notes else "")


def tools(remote=False):
    """The tool list. `remote` (the HTTP connector, one identity) offers no
    `actor`."""
    from . import cli
    sections = spec.verbs()
    listed = []
    for verb in cli.VERBS:
        if verb not in cli.BUILT or verb in cli.LOCAL:
            continue
        positional, options = cli.BUILT[verb]
        properties = {name: {"type": "string"} for name in positional}
        for name, kind in options.items():
            if name not in HIDDEN:
                properties[name] = {"type": KINDS[kind]}
        offered = not remote and verb not in READ_ONLY
        if offered:
            properties["actor"] = {"type": "string"}
        listed.append({
            "name": f"ctx_{verb}",
            "description": describe(sections[verb], positional, options, offered),
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": list(cli.REQUIRED.get(verb, ())),
                "additionalProperties": False,
            },
        })
    return listed


def call(params, environ, store, remote=False):
    from . import cli
    name = params.get("name") if isinstance(params, dict) else None
    verb = name[4:] if isinstance(name, str) and name.startswith("ctx_") else None
    if verb not in cli.BUILT or verb in cli.LOCAL:
        raise LookupError(f"unknown tool: {name}")
    given = params.get("arguments") or {}
    if not isinstance(given, dict):
        raise LookupError("arguments must be an object")
    try:
        if any(key in HIDDEN for key in given):
            raise CtxError("USAGE", next(key for key in given if key in HIDDEN))
        actor = given.get("actor")
        if "actor" in given and (remote or verb in READ_ONLY or not isinstance(actor, str)
                                 or not ACTOR_NAME.match(actor)):
            raise CtxError("USAGE", "actor")
        given = {key: value for key, value in given.items() if key != "actor"}
        checked = cli.required(verb, cli.typed(verb, given, files=False))
        _, text = cli.dispatch(verb, checked, environ, store, actor=actor)
    except CtxError as failure:
        return {"content": [{"type": "text", "text": failure.line()}], "isError": True}
    return {"content": [{"type": "text", "text": text}], "isError": False}


class Refused(Exception):
    """A request the protocol refuses before it is served: its JSON-RPC error
    and the server's own word for the log (never the client's text). Over HTTP
    every refusal is a 400, as the Streamable HTTP binding asks."""

    def __init__(self, code, text, why, data=None):
        super().__init__(text)
        self.code, self.text, self.why, self.data = code, text, why, data


def respond(message, environ, store):
    """The reply to one message, or None for a notification."""
    return answer(message, environ, store)[0]


def answer(message, environ, store, headers=None):
    """(reply, HTTP status, reason) for one message. `headers` are the HTTP
    request's (None over stdio); the reply is None for a notification. The
    reason is a fixed word for the server's log, empty when there is none."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request"), 200, ""
    ident, method = message.get("id"), message.get("method")
    header = headers.get("MCP-Protocol-Version") if headers is not None else None
    if method == "initialize":
        # The handshake agrees the version: one this server does not know, in the header or the
        # body, gets the newest handshake revision it speaks instead of a refusal (#48).
        params = message.get("params")
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        why = "protocol-negotiated" if header is not None and header not in PROTOCOLS or asked not in PROTOCOLS else ""
        if "id" not in message:
            return None, 202, why
        return _result(ident, {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": dict(SERVER),
        }), 200, why
    if "id" not in message:
        if header is not None and header not in SUPPORTED:
            return _unsupported(None, header), 400, "protocol-version"
        return None, 202, ""
    try:
        modern = _era(message, headers)
    except Refused as refusal:
        return _error(ident, refusal.code, refusal.text, refusal.data), 400, refusal.why
    remote = headers is not None  # the HTTP connector: one identity, so no `actor`
    if modern:
        return _stateless(ident, method, message.get("params"), environ, store, remote)
    return _handshake(ident, method, message.get("params"), environ, store, remote), 200, ""


def _era(message, headers):
    """True for a request served statelessly, False for one served as the
    handshake revisions serve it; raises Refused for a request that is neither."""
    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    header = headers.get("MCP-Protocol-Version") if headers is not None else None
    if META + "protocolVersion" not in meta:  # a request of the handshake revisions
        if header in MODERN:
            raise Refused(-32602, f"_meta lacks {META}protocolVersion", "meta-missing")
        if header is not None and header not in SUPPORTED:
            raise _refusal(header)
        return False
    stated = meta[META + "protocolVersion"]
    if not isinstance(stated, str):
        raise Refused(-32602, f"_meta {META}protocolVersion is not a string", "meta-invalid")
    if headers is not None and header != stated:
        raise Refused(-32020, "header mismatch: MCP-Protocol-Version", "header-mismatch")
    if stated not in SUPPORTED:
        raise _refusal(stated)
    if stated not in MODERN:
        return False  # a handshake revision named in `_meta` is served as that revision
    if not isinstance(meta.get(META + "clientCapabilities"), dict):
        raise Refused(-32602, f"_meta lacks {META}clientCapabilities", "meta-missing")
    if headers is not None:
        method = message.get("method")
        if headers.get("Mcp-Method") != method:
            raise Refused(-32020, "header mismatch: Mcp-Method", "header-mismatch")
        if method in NAMED and _decoded(headers.get("Mcp-Name")) != params.get(NAMED[method]):
            raise Refused(-32020, "header mismatch: Mcp-Name", "header-mismatch")
    return True


def _decoded(value):
    """A header's value as the client meant it: the Base64 sentinel form
    (`=?base64?…?=`) decoded. None for a value missing, malformed or holding
    characters a header may not carry, which then matches no body value."""
    if value is None or any(not (" " <= char <= "~" or char == "\t") for char in value):
        return None
    if len(value) >= 11 and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
    return value


def _refusal(version):
    return Refused(-32022, "Unsupported protocol version", "protocol-version",
                   {"supported": list(SUPPORTED), "requested": version})


def _unsupported(ident, version):
    refusal = _refusal(version)
    return _error(ident, refusal.code, refusal.text, refusal.data)


def _stateless(ident, method, params, environ, store, remote=False):
    """(reply, HTTP status, reason) of a 2026-07-28 request. Every result says
    it is complete and names this server; the tool list is not cached."""
    if method == "server/discover":
        result = {"supportedVersions": list(SUPPORTED), "capabilities": {"tools": {}},
                  "ttlMs": 0, "cacheScope": "private"}
    elif method == "tools/list":
        result = {"tools": tools(remote), "ttlMs": 0, "cacheScope": "private"}
    elif method == "tools/call":
        try:
            result = call(params, environ, store, remote)
        except LookupError as failure:
            return _error(ident, -32602, str(failure)), 200, ""
    else:
        return _error(ident, -32601, f"method not found: {method}"), 404, "method-unknown"
    return _result(ident, {**result, "resultType": "complete", "_meta": {META + "serverInfo": dict(SERVER)}}), 200, ""


def _handshake(ident, method, params, environ, store, remote=False):
    """The reply to a request of the handshake revisions (2025-11-25 and before)."""
    if method == "ping":
        return _result(ident, {})
    if method == "tools/list":
        return _result(ident, {"tools": tools(remote)})
    if method == "tools/call":
        try:
            return _result(ident, call(params, environ, store, remote))
        except LookupError as failure:
            return _error(ident, -32602, str(failure))
    return _error(ident, -32601, f"method not found: {method}")


def _result(ident, result):
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident, code, text, data=None):
    error = {"code": code, "message": text}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


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
