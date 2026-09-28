"""The HTTP side: one MCP endpoint, the OAuth endpoints, nothing else.

Every JSON-RPC message is one POST to /mcp, answered with one JSON object
(a request) or 202 (a notification). The server keeps no session and opens
no stream, so GET and DELETE on /mcp answer 405.
"""
import html
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ctxstore import __version__, mcp

from .auth import Auth, Refused, State

LIMIT = 1024 * 1024  # bytes of a request body
PROTOCOLS = mcp.PROTOCOLS
ORIGINS = ("https://claude.ai", "https://claude.com")
METHODS = ("GET", "POST", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT")
ROUTES = ("/mcp", "/authorize", "/token", "/register", "/.well-known/oauth-authorization-server")
PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ctx: allow access</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}}
input,button{{font:inherit;padding:.5rem;width:100%;box-sizing:border-box;margin:.25rem 0}}
code{{word-break:break-all}}.bad{{color:#b00020}}</style></head><body>
<h1>Allow access to this store?</h1>
<p><strong>{name}</strong> asks to read and write the ctx store served at <code>{url}</code>.</p>
<p>After you allow it, you are sent to <code>{host}</code>.</p>
{note}
<form method="post" action="/authorize">
<input type="hidden" name="form" value="{form}">
<label>Owner passphrase <input type="password" name="passphrase" autocomplete="off" autofocus required></label>
<button type="submit">Allow</button>
</form>
<p>To refuse, close this page.</p>
</body></html>
"""
STOP = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ctx: not allowed</title></head>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem">
<h1>Not allowed</h1><p>{text}</p></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ctx-serve/" + __version__
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30  # seconds a client may take to send its request

    # --- plumbing ---

    # The log holds the method, the path and the status, and only when the server knows them:
    # another method is `?`, another path `/…`. Nothing else a client sends is written. A
    # request line can carry a token, and a malformed one can carry anything.

    def parse_request(self):
        self.why = ""  # a reason belongs to one request; a connection carries many
        return super().parse_request()

    def log_request(self, code="-", size="-"):
        if self.server.log:
            status = getattr(code, "value", code)
            method = self.command if self.command in METHODS else "?"
            try:
                route = self.route
            except (AttributeError, ValueError):
                route = ""
            known = route in ROUTES or route.startswith("/.well-known/oauth-protected-resource")
            why = f" {self.why}" if getattr(self, "why", "") else ""
            self.server.log.write(f"{self.address_string()} {method} {route if known else '/…'} {status}{why}\n")

    def log_error(self, pattern, *args):
        pass  # the refusal is logged with its status by log_request, in the one shape

    def log_message(self, pattern, *args):
        pass

    def send(self, status, body=b"", kind="application/json", headers=()):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        if body:
            self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":  # the answer to HEAD is its headers
            self.wfile.write(body)

    def refuse(self, failure):
        self.send(failure.status, {"error": failure.code, "error_description": failure.text})

    def body(self):
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            size = -1
        if self.headers.get("Transfer-Encoding"):
            size = -1  # a body without a length is not read
        if size < 0 or size > LIMIT:
            self.close_connection = True
            self.send(413 if size > LIMIT else 411, {"error": "invalid_request"})
            return None
        return self.rfile.read(size)

    def origin_ok(self):
        """A browser names its origin; one that is not allowed is a page of
        another site talking to this server (DNS rebinding included)."""
        origin = self.headers.get("Origin")
        return origin is None or origin in self.server.origins

    @property
    def route(self):
        return urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"

    # --- GET ---

    def do_GET(self):
        auth, route = self.server.auth, self.route
        self.unread()
        if not self.origin_ok():
            return self.send(403, {"error": "origin not allowed"})
        if route.startswith("/.well-known/oauth-protected-resource") and auth.secret:
            return self.send(200, auth.resource_metadata())
        if route == "/.well-known/oauth-authorization-server" and auth.secret:
            return self.send(200, auth.server_metadata())
        if route == "/authorize" and auth.secret:
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            try:
                request = auth.request(query)
            except Refused as failure:
                return self.send(failure.status, STOP.format(text=html.escape(failure.text or failure.code)), "text/html; charset=utf-8")
            return self.page(request)
        if route == "/mcp":
            return self.send(405, {"error": "this server opens no stream"}, headers=[("Allow", "POST")])
        self.send(404, {"error": "not found"})

    def page(self, request, note=""):
        host = urllib.parse.urlsplit(request["redirect_uri"]).netloc
        text = PAGE.format(name=html.escape(request["name"] or "An MCP client"), url=html.escape(self.server.auth.url),
                           host=html.escape(host), form=html.escape(self.server.auth.form(request)),
                           note=f'<p class="bad">{html.escape(note)}</p>' if note else "")
        # The form posts to this server and the answer sends the browser on to the client's
        # callback. A browser holds that redirect to `form-action` too, so the callback's origin
        # is named beside 'self'; it is one of the callbacks the server accepts at registration.
        target = urllib.parse.urlsplit(request["redirect_uri"])
        policy = f"default-src 'none'; style-src 'unsafe-inline'; form-action 'self' {target.scheme}://{target.netloc}"
        self.send(200, text, "text/html; charset=utf-8", headers=[("Content-Security-Policy", policy)])

    def unread(self):
        """A request whose body is not read cannot share its connection: what is
        left of it would be taken for the next request."""
        if self.headers.get("Content-Length", "0").strip() not in ("", "0") or self.headers.get("Transfer-Encoding"):
            self.close_connection = True

    def do_DELETE(self):
        self.unread()
        self.send(405 if self.route == "/mcp" else 404, {"error": "not allowed"}, headers=[("Allow", "POST")])

    def refuse_method(self):
        """A method this server has no use for. Its body is not read, so the
        connection ends with the answer."""
        self.close_connection = True
        self.send(405, {"error": "not allowed"}, headers=[("Allow", "POST"), ("Connection", "close")])

    do_PUT = do_PATCH = do_HEAD = do_OPTIONS = do_TRACE = do_CONNECT = refuse_method

    # --- POST ---

    def do_POST(self):
        auth, route = self.server.auth, self.route
        # The body is read before anything is decided, so a refusal leaves the connection
        # clean for the request that follows on it. It is bounded by LIMIT.
        raw = self.body()
        if raw is None:
            return None
        if not self.origin_ok() and not (route == "/authorize" and self.headers.get("Origin") in (auth.url, "null")):
            # The consent form is this server's own page. A browser names the page's origin when
            # it posts the form, or `null` when the page asked for no referrer. What ties the post
            # to a page this server showed is the form's one-time token, not the header.
            return self.send(403, {"error": "origin not allowed"})
        if route == "/mcp":
            return self.rpc(raw)
        if not auth.secret or route not in ("/register", "/authorize", "/token"):
            return self.send(404, {"error": "not found"})
        try:
            if route == "/register":
                try:
                    given = json.loads(raw)
                except ValueError:
                    raise Refused(400, "invalid_client_metadata") from None
                return self.send(201, auth.register(given))
            form = dict(urllib.parse.parse_qsl(raw.decode("utf-8", errors="replace")))
            if route == "/token":
                return self.send(200, auth.exchange(form))
            return self.send(303, headers=[("Location", auth.consent(form.get("form"), form.get("passphrase")))])
        except Refused as failure:
            if route == "/authorize":
                return self.send(failure.status, STOP.format(text=html.escape(failure.text or failure.code)),
                                 "text/html; charset=utf-8")
            return self.refuse(failure)

    def rpc(self, raw):
        auth = self.server.auth
        if not auth.allows(self.headers.get("Authorization")):
            return self.send(401, {"error": "unauthorized"}, headers=[("WWW-Authenticate", auth.challenge())])
        version = self.headers.get("MCP-Protocol-Version")
        if version is not None and version not in PROTOCOLS:
            self.why = "protocol-version"  # the server's word for the refusal, never the client's text
            return self.send(400, {"error": "unsupported MCP-Protocol-Version", "supported": list(PROTOCOLS)})
        try:
            message = json.loads(raw)
        except ValueError:
            self.why = "not-json"
            return self.send(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if isinstance(message, list):
            self.why = "batch"
            return self.send(400, {"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32600, "message": "one message per request"}})
        reply = mcp.respond(message, self.server.environ, None)
        if reply is None:
            return self.send(202)
        self.send(200, reply)


def build(environ, log=None, clock=None):
    """The server of this environment; raises ValueError for one that would
    be open or unreachable."""
    url = environ.get("CTX_SERVE_URL", "").rstrip("/")
    secret, token = environ.get("CTX_SERVE_SECRET") or None, environ.get("CTX_SERVE_TOKEN") or None
    if not environ.get("CTX_STORE"):
        raise ValueError("CTX_STORE is not set: name the store to serve")
    if not secret and not token:
        raise ValueError("no authentication: set CTX_SERVE_SECRET (OAuth, for claude.ai) or CTX_SERVE_TOKEN (a bearer token)")
    for name, value in (("CTX_SERVE_SECRET", secret), ("CTX_SERVE_TOKEN", token)):
        if value and len(value) < 16:
            raise ValueError(f"{name} is shorter than 16 characters")
    if secret and not url.startswith("https://") and not url.startswith("http://127.0.0.1") \
            and not url.startswith("http://localhost"):
        raise ValueError("CTX_SERVE_URL must be the https URL the server is reached at")
    bind = environ.get("CTX_SERVE_BIND", "127.0.0.1")
    try:
        port = int(environ.get("CTX_SERVE_PORT", "8377"))
    except ValueError:
        raise ValueError("CTX_SERVE_PORT is not a number") from None
    home = environ.get("XDG_STATE_HOME") or os.path.join(environ.get("HOME", ""), ".local", "state")
    path = environ.get("CTX_SERVE_STATE") or os.path.join(home, "ctx-serve", "state.json")
    arguments = {"clock": clock} if clock else {}
    state = State(path if secret else None, **arguments)
    server = ThreadingHTTPServer((bind, port), Handler)
    server.daemon_threads = True
    server.auth = Auth(url or f"http://{bind}:{server.server_address[1]}", secret, token, state, **arguments)
    server.environ = {**environ, "CTX_ACTOR": environ.get("CTX_ACTOR", "connector"), "CTX_NO_WALK": "1"}
    server.origins = set(ORIGINS) | {o for o in environ.get("CTX_SERVE_ORIGINS", "").split(",") if o}
    server.log = log
    return server


def main(argv=None, environ=None):
    environ = os.environ if environ is None else environ
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        sys.stderr.write("usage: ctx-serve   (configured by CTX_SERVE_* and CTX_STORE; see docs/connector.md)\n")
        return 1
    try:
        server = build(environ, log=sys.stderr)
    except (ValueError, OSError) as failure:
        sys.stderr.write(f"ctx-serve: {failure}\n")
        return 1
    host, port = server.server_address[:2]
    modes = [name for name, on in (("OAuth", server.auth.secret), ("bearer token", server.auth.token)) if on]
    sys.stderr.write(f"ctx-serve {__version__}: http://{host}:{port}/mcp, {' and '.join(modes)}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
