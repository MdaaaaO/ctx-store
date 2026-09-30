"""The HTTP connector: MCP over Streamable HTTP, behind authentication."""
import base64
import hashlib
import http.client
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.parse

from ctxserve import auth as oauth
from ctxserve.server import PROTOCOLS, SUPPORTED, build
from tests.harness import FIXTURE, VERSION

SECRET = "correct horse battery staple"
TOKEN = "static-token-for-claude-code-0123"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
VERIFIER = "v" * 64
EPIC = "epics/sample-rollout"


def challenge(verifier=VERIFIER):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


class ServeCase(unittest.TestCase):
    secret, token = SECRET, TOKEN

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.store = os.path.join(self.work.name, "store")
        shutil.copytree(FIXTURE, self.store)
        self.clock = Clock()
        environ = {"CTX_STORE": self.store, "CTX_SERVE_PORT": "0", "CTX_SERVE_URL": "https://ctx.example.test",
                   "CTX_SERVE_STATE": os.path.join(self.work.name, "state", "state.json")}
        if self.secret:
            environ["CTX_SERVE_SECRET"] = self.secret
        if self.token:
            environ["CTX_SERVE_TOKEN"] = self.token
        self.environ = environ
        self.server = build(environ, clock=self.clock)
        self.port = self.server.server_address[1]
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, headers=None, form=None):
        """(status, headers, body) of one request."""
        headers = dict(headers or {})
        if form is not None:
            body, headers["Content-Type"] = urllib.parse.urlencode(form), "application/x-www-form-urlencoded"
        elif isinstance(body, (dict, list)):
            body, headers["Content-Type"] = json.dumps(body), "application/json"
        link = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            link.request(method, path, body=body, headers=headers)
            reply = link.getresponse()
            return reply.status, dict(reply.getheaders()), reply.read().decode("utf-8")
        finally:
            link.close()

    def rpc(self, message, token=TOKEN, **headers):
        if token:
            headers["Authorization"] = f"Bearer {token}"
        status, got, body = self.call("POST", "/mcp", message, headers)
        return status, got, json.loads(body) if body else None

    def tool(self, name, token=TOKEN, **arguments):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        status, _, reply = self.rpc(message, token)
        self.assertEqual(status, 200)
        return reply["result"]

    # the OAuth flow, step by step

    def register(self, uris=(CALLBACK,)):
        status, _, body = self.call("POST", "/register", {"redirect_uris": list(uris), "client_name": "Claude"})
        self.assertEqual(status, 201, body)
        return json.loads(body)["client_id"]

    def authorize(self, client, passphrase=SECRET, **changes):
        query = {"client_id": client, "redirect_uri": CALLBACK, "response_type": "code", "state": "s-1",
                 "code_challenge": challenge(), "code_challenge_method": "S256", **changes}
        status, _, page = self.call("GET", "/authorize?" + urllib.parse.urlencode(query))
        if status != 200:
            return status, page
        form = page.split('name="form" value="')[1].split('"')[0]
        status, headers, body = self.call("POST", "/authorize", form={"form": form, "passphrase": passphrase})
        return status, headers.get("Location") or body

    def code(self, client):
        status, location = self.authorize(client)
        self.assertEqual(status, 303, location)
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
        self.assertTrue(location.startswith(CALLBACK + "?"))
        self.assertEqual(query["state"], "s-1")
        return query["code"]

    def tokens(self, client, **changes):
        form = {"grant_type": "authorization_code", "code": self.code(client), "client_id": client,
                "redirect_uri": CALLBACK, "code_verifier": VERIFIER, **changes}
        status, _, body = self.call("POST", "/token", form=form)
        return status, json.loads(body)


class Unauthenticated(ServeCase):
    def test_every_call_without_a_token_is_rejected(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        before = sorted(os.walk(self.store))
        for headers in ({}, {"Authorization": "Bearer wrong-token-wrong-token"}, {"Authorization": "Bearer "},
                        {"Authorization": "Basic " + base64.b64encode(b"owner:" + SECRET.encode()).decode()},
                        {"Authorization": TOKEN}, {"X-Api-Key": TOKEN}):
            for body in (message, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "ctx_log", "arguments": {"doc": EPIC, "text": "x"}}}):
                status, got, text = self.call("POST", "/mcp", body, headers)
                self.assertEqual(status, 401, headers)
                self.assertEqual(got["WWW-Authenticate"], 'Bearer resource_metadata='
                                 '"https://ctx.example.test/.well-known/oauth-protected-resource"')
                self.assertNotIn("tools", text)
        self.assertEqual(self.call("POST", "/mcp?token=" + TOKEN, message)[0], 401)
        self.assertEqual(sorted(os.walk(self.store)), before)

    def test_nothing_but_the_endpoints(self):
        for method, path in (("GET", "/"), ("GET", "/mcp/../ctx-store.json"), ("GET", "/ctx-store.json"),
                             ("POST", "/nothing"), ("GET", "/.well-known/other"), ("DELETE", "/token")):
            self.assertEqual(self.call(method, path)[0], 404, path)
        self.assertEqual(self.call("GET", "/mcp")[0], 405)
        self.assertEqual(self.call("DELETE", "/mcp")[0], 405)

    def test_a_page_of_another_site_is_refused(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertEqual(self.rpc(message, Origin="https://evil.example")[0], 403)
        self.assertEqual(self.rpc(message, Origin="https://claude.ai")[0], 200)
        self.assertEqual(self.call("GET", "/.well-known/oauth-authorization-server",
                                   headers={"Origin": "http://127.0.0.1:8377"})[0], 403)


class Connection(ServeCase):
    """A tunnel sends many requests over one connection."""

    def talk(self, *requests):
        """Statuses of requests sent one after the other on one connection."""
        link = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        statuses = []
        try:
            for method, path, body, headers in requests:
                link.request(method, path, body=body, headers=headers)
                reply = link.getresponse()
                reply.read()
                statuses.append(reply.status)
        finally:
            link.close()
        return statuses

    def test_a_refused_request_does_not_spoil_the_next(self):
        ping = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        good = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        self.assertEqual(self.talk(
            ("POST", "/mcp", ping, {"Content-Type": "application/json"}),
            ("POST", "/mcp", ping, {**good, "Origin": "https://evil.example"}),
            ("POST", "/mcp", ping, {**good, "MCP-Protocol-Version": "1999-01-01"}),
            ("POST", "/nothing", ping, good),
            ("POST", "/mcp", "not json", good),
            ("POST", "/token", "grant_type=nothing", {"Content-Type": "application/x-www-form-urlencoded"}),
            ("POST", "/mcp", ping, good),
        ), [401, 403, 400, 404, 400, 400, 200])

    def test_a_body_that_is_not_read_ends_the_connection(self):
        for method in ("GET", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT", "BREW"):
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as link:
                link.sendall((f"{method} /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\nhello"
                              f"POST /mcp HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n").encode())
                data = b""
                while True:
                    part = link.recv(4096)
                    if not part:
                        break
                    data += part
                self.assertEqual(data.count(b"HTTP/1.1 "), 1, method)
                self.assertTrue(data.startswith(b"HTTP/1.1 501" if method == "BREW" else b"HTTP/1.1 405"), method)
                head, _, body = data.partition(b"\r\n\r\n")
                if method == "HEAD":
                    self.assertEqual(body, b"")  # the answer to HEAD is its headers
                elif method != "BREW":  # a method nobody knows is answered by the library's own page
                    self.assertIn(b"error", body, method)

    def test_the_log_holds_no_query_and_no_request_text(self):
        import io
        self.server.log = io.StringIO()
        self.call("POST", "/mcp?token=" + TOKEN, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.call("GET", "/authorize?client_id=secret-looking-value&state=" + SECRET.replace(" ", "+"))
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.call("GET", "/" + TOKEN + "/mcp")
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as link:
            link.sendall(f"{TOKEN} /mcp HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            link.recv(4096)
        text = self.server.log.getvalue()
        self.assertEqual(text.splitlines(), ["127.0.0.1 POST /mcp 401", "127.0.0.1 GET /authorize 400",
                                             "127.0.0.1 POST /mcp 200", "127.0.0.1 GET /… 404",
                                             "127.0.0.1 ? /mcp 501"])
        for value in (TOKEN, "secret-looking-value", "correct", "token=", "client_id", "state"):
            self.assertNotIn(value, text)


class Bearer(ServeCase):
    def test_brief_find_and_log(self):
        status, headers, reply = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize",
                                           "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        self.assertEqual((status, headers["Content-Type"]), (200, "application/json"))
        self.assertEqual(reply["result"]["serverInfo"]["name"], "ctx")
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})[::2], (202, None))
        self.assertTrue(self.tool("ctx_brief", registry=True)["content"][0]["text"].startswith("sessions: 2 not ended"))
        self.assertTrue(self.tool("ctx_find", query="region")["content"][0]["text"].startswith("2 hits"))
        self.assertEqual(self.tool("ctx_log", doc=EPIC, text="over http", date="2026-01-09"),
                         {"isError": False, "content": [{"type": "text", "text": "logged: epics/sample-rollout"}]})
        with open(os.path.join(self.store, EPIC + ".md")) as handle:
            self.assertTrue(handle.read().endswith("- 2026-01-09 — over http\n"))
        with open(os.path.join(self.store, ".audit", "connector.jsonl")) as handle:
            self.assertEqual(json.loads(handle.read())["actor"], "connector")

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/mcp", "not json", {"Authorization": f"Bearer {TOKEN}"})[0], 400)
        self.assertEqual(self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])[0], 400)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, **{"MCP-Protocol-Version": "1999-01-01"})[0], 400)
        for version in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"):
            self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, **{"MCP-Protocol-Version": version})[0], 200)
        status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize",
                                     "params": {"protocolVersion": "2025-11-25", "capabilities": {}}},
                                    **{"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual((status, reply["result"]["protocolVersion"]), (200, "2025-11-25"))
        import io
        self.server.log = io.StringIO()
        # `initialize` agrees the version: one this server does not know, in the header or the
        # body, gets the newest one it speaks instead of a refusal (#48)
        for header, body in (("2099-01-01", "2099-01-01"), ("2099-01-01", "2025-06-18"), (None, "2099-01-01"),
                             (None, None), ("2025-11-25", "2025-11-25")):
            params = {"capabilities": {}, **({"protocolVersion": body} if body else {})}
            status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": params},
                                        **({"MCP-Protocol-Version": header} if header else {}))
            self.assertEqual((status, reply["result"]["protocolVersion"]),
                             (200, body if body in PROTOCOLS else PROTOCOLS[0]), (header, body))
        status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": ["x"]})
        self.assertEqual((status, reply["result"]["protocolVersion"]), (200, PROTOCOLS[0]))
        self.assertEqual(self.server.log.getvalue().splitlines(),
                         ["127.0.0.1 POST /mcp 200 protocol-negotiated"] * 4 + ["127.0.0.1 POST /mcp 200"]
                         + ["127.0.0.1 POST /mcp 200 protocol-negotiated"])
        self.assertNotIn("2099", self.server.log.getvalue())
        self.server.log = io.StringIO()
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, **{"MCP-Protocol-Version": "1999-01-01"})
        self.call("POST", "/mcp", "not json", {"Authorization": f"Bearer {TOKEN}"})
        self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        self.assertEqual(self.server.log.getvalue().splitlines(), [
            "127.0.0.1 POST /mcp 400 protocol-version", "127.0.0.1 POST /mcp 400 not-json",
            "127.0.0.1 POST /mcp 400 batch", "127.0.0.1 POST /mcp 200"])

    def test_a_reason_belongs_to_one_request(self):
        import io
        self.server.log = io.StringIO()
        good = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
        link = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            for method, body in (("POST", "not json"), ("DELETE", None), ("POST", "not json"), ("PUT", None)):
                link.request(method, "/mcp", body=body, headers=good)
                link.getresponse().read()
                if method == "PUT":
                    break
        finally:
            link.close()
        self.assertEqual(self.server.log.getvalue().splitlines(), [
            "127.0.0.1 POST /mcp 400 not-json", "127.0.0.1 DELETE /mcp 405",
            "127.0.0.1 POST /mcp 400 not-json", "127.0.0.1 PUT /mcp 405"])
        # a body over the limit is refused on its headers; the server never reads it
        for length, status in (("1048577", b"413"), ("-1", b"411"), ("many", b"411")):
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as link:
                link.sendall((f"POST /mcp HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {TOKEN}\r\n"
                              f"Content-Type: application/json\r\nContent-Length: {length}\r\n\r\n").encode())
                self.assertEqual(link.recv(64).split(b" ")[1], status, length)
        self.assertTrue(self.tool("ctx_log", doc=EPIC, text="ghp_" + "a" * 36)["isError"])


class Stateless(ServeCase):
    """MCP 2026-07-28: no `initialize`; each request names its version in `_meta`
    and mirrors it, its method and its tool's name in headers (#64)."""

    MODERN = "2026-07-28"

    def modern(self, method, params=None, ident=1, version=MODERN, meta=None, token=TOKEN, **headers):
        """(status, reply) of one 2026-07-28 request with the headers the binding asks for;
        a header given as None is left out."""
        meta = {"io.modelcontextprotocol/protocolVersion": version,
                "io.modelcontextprotocol/clientInfo": {"name": "probe", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {}} if meta is None else meta
        params = {**(params or {}), "_meta": meta}
        sent = {"MCP-Protocol-Version": version, "Mcp-Method": method}
        if method == "tools/call":
            sent["Mcp-Name"] = params.get("name")
        sent.update({name.replace("_", "-"): value for name, value in headers.items()})
        sent = {name: value for name, value in sent.items() if value is not None}
        status, _, reply = self.rpc({"jsonrpc": "2.0", "id": ident, "method": method, "params": params}, token, **sent)
        return status, reply

    def logged(self):
        import io
        self.server.log = io.StringIO()
        return self.server.log

    def test_initialize_is_always_the_handshake(self):
        """A client that falls back to `initialize` may still send the new
        version, in `_meta` or the header: the method selects the handshake and
        the version is agreed, never refused (#48)."""
        log = self.logged()
        params = {"protocolVersion": self.MODERN, "capabilities": {}}
        for status, reply in (self.modern("initialize", params),
                              self.modern("initialize", params, meta={}, Mcp_Method=None),
                              self.modern("initialize", params, meta={}, Mcp_Method="tools/list")):
            self.assertEqual(status, 200)
            self.assertEqual(reply["result"]["protocolVersion"], PROTOCOLS[0])
            self.assertNotIn("resultType", reply["result"])
        self.assertEqual(log.getvalue().count("POST /mcp 200 protocol-negotiated"), 3)
        self.assertNotIn(" 400 ", log.getvalue())

    def test_a_modern_client_is_served_on_its_first_request(self):
        log = self.logged()
        status, reply = self.modern("server/discover")
        self.assertEqual((status, reply), (200, {"jsonrpc": "2.0", "id": 1, "result": {
            "resultType": "complete", "supportedVersions": list(SUPPORTED), "capabilities": {"tools": {}},
            "ttlMs": 0, "cacheScope": "private",
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "ctx", "version": VERSION}}}}))
        self.assertEqual(SUPPORTED[0], self.MODERN)
        self.assertEqual(list(SUPPORTED[1:]), list(PROTOCOLS))
        status, reply = self.modern("tools/list")
        self.assertEqual(status, 200)
        result = reply["result"]
        self.assertEqual((result["resultType"], result["ttlMs"], result["cacheScope"]), ("complete", 0, "private"))
        self.assertIn("ctx_log", [tool["name"] for tool in result["tools"]])
        status, reply = self.modern("tools/call", {"name": "ctx_log", "arguments": {
            "doc": EPIC, "text": "stateless", "date": "2026-01-09"}})
        self.assertEqual((status, reply["result"]), (200, {
            "resultType": "complete", "isError": False,
            "content": [{"type": "text", "text": "logged: epics/sample-rollout"}],
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "ctx", "version": VERSION}}}))
        with open(os.path.join(self.store, EPIC + ".md")) as handle:
            self.assertTrue(handle.read().endswith("- 2026-01-09 — stateless\n"))
        # a tool's failure is still a result; a tool that does not exist is still -32602
        status, reply = self.modern("tools/call", {"name": "ctx_log", "arguments": {"doc": "epics/none", "text": "x"}})
        self.assertEqual((status, reply["result"]["isError"]), (200, True))
        status, reply = self.modern("tools/call", {"name": "rm", "arguments": {}})
        self.assertEqual((status, reply["error"]["code"]), (200, -32602))
        # a notification is accepted as before
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}},
                                  **{"MCP-Protocol-Version": self.MODERN})[::2], (202, None))
        self.assertEqual(log.getvalue().splitlines(), ["127.0.0.1 POST /mcp 200"] * 5 + ["127.0.0.1 POST /mcp 202"])

    def test_a_method_of_the_handshake_revisions_only_is_not_found(self):
        log = self.logged()
        for method in ("ping", "resources/list"):
            status, reply = self.modern(method)
            self.assertEqual((status, reply["error"]["code"], reply["id"]), (404, -32601, 1), method)
        self.assertEqual(log.getvalue().splitlines(), ["127.0.0.1 POST /mcp 404 method-unknown"] * 2)

    def test_a_legacy_client_is_served_as_before(self):
        status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize",
                                     "params": {"protocolVersion": "2025-11-25", "capabilities": {}}},
                                    **{"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual(reply["result"], {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                                           "serverInfo": {"name": "ctx", "version": VERSION}})
        listing = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, **{"MCP-Protocol-Version": "2025-11-25"})
        self.assertEqual(listing[0], 200)
        self.assertEqual(sorted(listing[2]["result"]), ["tools"])  # no resultType, no cache hints
        self.assertEqual(self.tool("ctx_find", query="region")["content"][0]["text"][:6], "2 hits")
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 3, "method": "server/discover"})[2]["error"]["code"], -32601)
        # `initialize` names no modern version: one it is offered gets the newest handshake revision
        status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 7, "method": "initialize",
                                     "params": {"protocolVersion": self.MODERN, "capabilities": {}}},
                                    **{"MCP-Protocol-Version": self.MODERN})
        self.assertEqual((status, reply["result"]["protocolVersion"]), (200, PROTOCOLS[0]))
        # a handshake revision named in `_meta` is served as that revision
        status, reply = self.modern("tools/list", version="2025-11-25")
        self.assertEqual((status, sorted(reply["result"])), (200, ["tools"]))

    def test_the_dual_era_probe(self):
        """A client that speaks both eras sends a modern request first. This server answers it;
        a version it does not know is refused with a modern error, so the client retries with one
        from the list instead of falling back to `initialize`."""
        log = self.logged()
        status, reply = self.modern("tools/list", version="2027-01-01")
        self.assertEqual(status, 400)
        self.assertEqual(reply, {"jsonrpc": "2.0", "id": 1, "error": {
            "code": -32022, "message": "Unsupported protocol version",
            "data": {"supported": list(SUPPORTED), "requested": "2027-01-01"}}})
        retry = reply["error"]["data"]["supported"][0]
        status, reply = self.modern("tools/list", version=retry, ident=2)
        self.assertEqual((status, reply["result"]["resultType"]), (200, "complete"))
        # claude.ai's opening today: a 2026-07-28 request first; it is answered, so no fallback
        status, reply = self.modern("tools/call", {"name": "ctx_brief", "arguments": {"registry": True}}, ident=3)
        self.assertTrue(reply["result"]["content"][0]["text"].startswith("sessions: 2 not ended"))
        self.assertEqual(log.getvalue().splitlines(),
                         ["127.0.0.1 POST /mcp 400 protocol-version", "127.0.0.1 POST /mcp 200", "127.0.0.1 POST /mcp 200"])

    def test_an_unsupported_version(self):
        log = self.logged()
        # named in `_meta` and the header alike
        for version in ("2099-01-01", "1.0", ""):
            status, reply = self.modern("server/discover", version=version)
            self.assertEqual((status, reply["error"]["code"], reply["error"]["data"]),
                             (400, -32022, {"supported": list(SUPPORTED), "requested": version}), version)
        # in the header of a request of the handshake revisions
        status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}, **{"MCP-Protocol-Version": "2099-01-01"})
        self.assertEqual((status, reply["id"], reply["error"]["code"], reply["error"]["data"]["requested"]),
                         (400, 5, -32022, "2099-01-01"))
        # a notification has no id to answer, and still is not accepted
        status, _, reply = self.rpc({"jsonrpc": "2.0", "method": "notifications/cancelled"}, **{"MCP-Protocol-Version": "2099-01-01"})
        self.assertEqual((status, reply["id"], reply["error"]["code"]), (400, None, -32022))
        self.assertEqual(log.getvalue().splitlines(), ["127.0.0.1 POST /mcp 400 protocol-version"] * 5)
        self.assertNotIn("2099", log.getvalue())

    def test_headers_must_match_the_body(self):
        log = self.logged()
        call = {"name": "ctx_find", "arguments": {"query": "region"}}
        refused = [
            ("tools/list", None, {"MCP_Protocol_Version": None}),              # the version header missing
            ("tools/list", None, {"MCP_Protocol_Version": "2025-11-25"}),      # another version than `_meta`'s
            ("tools/list", None, {"Mcp_Method": None}),                        # the method header missing
            ("tools/list", None, {"Mcp_Method": "tools/call"}),                # another method than the body's
            ("tools/list", None, {"Mcp_Method": "Tools/List"}),                # values are case-sensitive
            ("tools/call", call, {"Mcp_Name": None}),                          # the name header missing
            ("tools/call", call, {"Mcp_Name": "ctx_log"}),                     # another tool than the body's
            ("tools/call", call, {"Mcp_Name": "=?base64?Y3R4X2xvZw==?="}),     # the same, encoded
            ("tools/call", call, {"Mcp_Name": "=?base64?not base64?="}),       # an encoding that does not decode
            ("tools/call", call, {"Mcp_Name": "ctx_find\x7f"}),                # a character a header may not carry
        ]
        for method, params, headers in refused:
            status, reply = self.modern(method, params, **headers)
            self.assertEqual((status, reply["id"], reply["error"]["code"]), (400, 1, -32020), headers)
        # a name in the Base64 sentinel form is decoded before it is compared
        status, reply = self.modern("tools/call", call, Mcp_Name="=?base64?" + base64.b64encode(b"ctx_find").decode() + "?=")
        self.assertEqual((status, reply["result"]["isError"]), (200, False))
        # header names are not case-sensitive
        status, reply = self.modern("tools/list", MCP_Protocol_Version=None, Mcp_Method=None,
                                    **{"mcp-protocol-version": self.MODERN, "MCP-METHOD": "tools/list"})
        self.assertEqual(status, 200)
        # `_meta` lacks what every request must carry
        meta = {"io.modelcontextprotocol/protocolVersion": self.MODERN}
        for params, headers in (({"_meta": meta}, {}), ({}, {"MCP-Protocol-Version": self.MODERN}),
                                ({"_meta": {**meta, "io.modelcontextprotocol/clientCapabilities": []}}, {}),
                                ({"_meta": {"io.modelcontextprotocol/protocolVersion": 20260728}}, {})):
            status, _, reply = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": params},
                                        **{"MCP-Protocol-Version": self.MODERN, "Mcp-Method": "tools/list", **headers})
            self.assertEqual((status, reply["error"]["code"]), (400, -32602), params)
        self.assertEqual(log.getvalue().splitlines(),
                         ["127.0.0.1 POST /mcp 400 header-mismatch"] * len(refused) + ["127.0.0.1 POST /mcp 200"] * 2
                         + ["127.0.0.1 POST /mcp 400 meta-missing"] * 3 + ["127.0.0.1 POST /mcp 400 meta-invalid"])
        for text in ("ctx_log", "ctx_find", "base64", "Tools/List", "probe"):
            self.assertNotIn(text, log.getvalue())

    def test_authentication_comes_first(self):
        log = self.logged()
        for token in (None, "wrong-token-wrong-token"):
            for version in (self.MODERN, "2099-01-01"):
                status, reply = self.modern("tools/list", version=version, token=token)
                self.assertEqual((status, reply), (401, {"error": "unauthorized"}))
            status, reply = self.modern("tools/list", token=token, Mcp_Method="tools/call")
            self.assertEqual(status, 401)
        self.assertEqual(log.getvalue().splitlines(), ["127.0.0.1 POST /mcp 401"] * 6)


class OAuth(ServeCase):
    def test_discovery(self):
        for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
            status, _, body = self.call("GET", path)
            self.assertEqual((status, json.loads(body)), (200, {
                "resource": "https://ctx.example.test/mcp", "authorization_servers": ["https://ctx.example.test"],
                "bearer_methods_supported": ["header"]}))
        meta = json.loads(self.call("GET", "/.well-known/oauth-authorization-server")[2])
        self.assertEqual(meta["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(meta["registration_endpoint"], "https://ctx.example.test/register")
        self.assertEqual(meta["token_endpoint_auth_methods_supported"], ["none"])

    def test_the_whole_flow(self):
        client = self.register()
        status, tokens = self.tokens(client)
        self.assertEqual(status, 200)
        self.assertEqual((tokens["token_type"], tokens["expires_in"]), ("Bearer", 3600))
        self.assertTrue(self.tool("ctx_brief", token=tokens["access_token"], registry=True)["content"])
        self.assertEqual(self.tool("ctx_log", token=tokens["access_token"], doc=EPIC, text="from claude.ai")["isError"], False)

    def test_tokens_expire_and_refresh_rotates(self):
        client = self.register()
        _, first = self.tokens(client)
        self.clock.now += 3601
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, first["access_token"])[0], 401)
        form = {"grant_type": "refresh_token", "refresh_token": first["refresh_token"], "client_id": client}
        status, _, body = self.call("POST", "/token", form=form)
        second = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, second["access_token"])[0], 200)
        self.assertNotEqual(second["refresh_token"], first["refresh_token"])
        status, _, body = self.call("POST", "/token", form=form)
        self.assertEqual((status, json.loads(body)["error"]), (400, "invalid_grant"))
        self.clock.now += 31 * 86400
        form["refresh_token"] = second["refresh_token"]
        self.assertEqual(self.call("POST", "/token", form=form)[0], 400)

    def test_a_code_works_once_and_only_with_its_verifier(self):
        client = self.register()
        for changes in ({"code_verifier": "w" * 64}, {"code_verifier": "short"}, {"client_id": "another"},
                        {"redirect_uri": "https://claude.com/api/mcp/auth_callback"}, {"code": "guess"}):
            status, body = self.tokens(client, **changes)
            self.assertEqual((status, body["error"]), (400, "invalid_grant"), changes)
        code = self.code(client)
        form = {"grant_type": "authorization_code", "code": code, "client_id": client, "redirect_uri": CALLBACK,
                "code_verifier": VERIFIER}
        self.assertEqual(self.call("POST", "/token", form=form)[0], 200)
        self.assertEqual(self.call("POST", "/token", form=form)[0], 400)
        expired = self.code(client)
        self.clock.now += 301
        self.assertEqual(self.call("POST", "/token", form={**form, "code": expired})[0], 400)
        self.assertEqual(self.call("POST", "/token", form={"grant_type": "client_credentials"})[0], 400)

    def test_only_known_callbacks(self):
        for uri in ("https://evil.example/cb", "https://claude.ai.evil.example/api/mcp/auth_callback",
                    "http://claude.ai/api/mcp/auth_callback", "https://claude.ai/api/mcp/auth_callback/../x",
                    "http://localhost.evil.example/callback", "http://127.0.0.1:99999999/callback", "javascript:alert(1)"):
            status, _, body = self.call("POST", "/register", {"redirect_uris": [uri]})
            self.assertEqual((status, json.loads(body)["error"]), (400, "invalid_redirect_uri"), uri)
        self.assertEqual(self.call("POST", "/register", {"redirect_uris": []})[0], 400)
        self.assertEqual(self.call("POST", "/register", "not json")[0], 400)
        for uri in ("http://localhost:3118/callback", "http://127.0.0.1:41000/callback", "http://localhost/callback"):
            self.register([uri])
        client = self.register()
        for changes in ({"redirect_uri": "http://localhost:3118/callback"}, {"client_id": "unknown"},
                        {"code_challenge_method": "plain"}, {"code_challenge": "short"}, {"response_type": "token"}):
            status, page = self.authorize(client, **changes)
            self.assertEqual(status, 400, changes)
            self.assertNotIn("Location", page)

    def test_the_passphrase(self):
        client = self.register()
        status, page = self.authorize(client, passphrase="wrong")
        self.assertEqual(status, 403)
        self.assertNotIn(SECRET, page)
        for _ in range(4):
            self.assertEqual(self.authorize(client, passphrase="wrong")[0], 403)
        status, page = self.authorize(client)
        self.assertEqual(status, 429)
        self.clock.now += 601
        self.assertEqual(self.authorize(client)[0], 303)

    def test_the_form_is_posted_as_a_browser_posts_it(self):
        client = self.register()
        query = {"client_id": client, "redirect_uri": CALLBACK, "response_type": "code", "state": "s",
                 "code_challenge": challenge(), "code_challenge_method": "S256"}

        def post(origin):
            page = self.call("GET", "/authorize?" + urllib.parse.urlencode(query))[2]
            form = page.split('name="form" value="')[1].split('"')[0]
            headers = {} if origin is None else {"Origin": origin}
            return self.call("POST", "/authorize", form={"form": form, "passphrase": SECRET}, headers=headers)[0]
        self.assertEqual(post("https://ctx.example.test"), 303)  # the page's own origin
        self.assertEqual(post("null"), 303)                       # a page that sends no referrer
        self.assertEqual(post(None), 303)
        self.assertEqual(post("https://evil.example"), 403)
        self.assertEqual(post("https://claude.ai"), 303)
        # `null` opens nothing else
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        self.assertEqual(self.rpc(ping, Origin="null")[0], 403)
        self.assertEqual(self.call("POST", "/token", form={"grant_type": "x"}, headers={"Origin": "null"})[0], 403)
        # and the form's token is what ties a post to a page: without one, nothing is granted
        self.assertEqual(self.call("POST", "/authorize", form={"form": "made-up", "passphrase": SECRET},
                                   headers={"Origin": "null"})[0], 400)

    def test_the_consent_page(self):
        client = self.register()
        query = {"client_id": client, "redirect_uri": CALLBACK, "response_type": "code", "state": "s",
                 "code_challenge": challenge(), "code_challenge_method": "S256"}
        status, headers, page = self.call("GET", "/authorize?" + urllib.parse.urlencode(query))
        self.assertEqual(status, 200)
        self.assertIn("claude.ai", page)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        # the browser follows the redirect after the post only to an origin the policy names
        self.assertEqual(headers["Content-Security-Policy"],
                         "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://claude.ai")
        local = self.register(["http://localhost:3118/callback"])
        other = self.call("GET", "/authorize?" + urllib.parse.urlencode(
            {**query, "client_id": local, "redirect_uri": "http://localhost:3118/callback"}))
        self.assertTrue(other[1]["Content-Security-Policy"].endswith("form-action 'self' http://localhost:3118"))
        form = page.split('name="form" value="')[1].split('"')[0]
        self.assertEqual(self.call("POST", "/authorize", form={"form": form, "passphrase": SECRET})[0], 303)
        self.assertEqual(self.call("POST", "/authorize", form={"form": form, "passphrase": SECRET})[0], 400)
        self.assertEqual(self.call("POST", "/authorize", form={"form": "made-up", "passphrase": SECRET})[0], 400)
        named = self.call("POST", "/register", {"redirect_uris": [CALLBACK], "client_name": "<script>x</script>"})
        query["client_id"] = json.loads(named[2])["client_id"]
        page = self.call("GET", "/authorize?" + urllib.parse.urlencode(query))[2]
        self.assertNotIn("<script>", page)

    def test_the_state_file_holds_no_secret(self):
        client = self.register()
        _, tokens = self.tokens(client)
        path = self.environ["CTX_SERVE_STATE"]
        with open(path) as handle:
            text = handle.read()
        for value in (SECRET, TOKEN, tokens["access_token"], tokens["refresh_token"]):
            self.assertNotIn(value, text)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertFalse(os.path.commonpath([path, self.store]) == self.store)
        again = build({**self.environ, "CTX_SERVE_PORT": "0"}, clock=self.clock)
        self.addCleanup(again.server_close)
        self.assertTrue(again.auth.allows("Bearer " + tokens["access_token"]))


class BearerOnly(ServeCase):
    secret = None

    def test_no_oauth_endpoints(self):
        for method, path in (("GET", "/.well-known/oauth-authorization-server"), ("POST", "/register"),
                             ("GET", "/authorize"), ("POST", "/token")):
            self.assertEqual(self.call(method, path, body="{}" if method == "POST" else None)[0], 404, path)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})[0], 200)
        self.assertEqual(self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=None)[0], 401)


class Start(unittest.TestCase):
    def test_a_server_that_would_be_open_does_not_start(self):
        base = {"CTX_STORE": FIXTURE, "CTX_SERVE_PORT": "0", "CTX_SERVE_URL": "https://ctx.example.test"}
        for environ, word in (
                (base, "no authentication"),
                ({**base, "CTX_SERVE_TOKEN": "short"}, "shorter than 16"),
                ({**base, "CTX_SERVE_SECRET": "short"}, "shorter than 16"),
                ({**base, "CTX_SERVE_SECRET": SECRET, "CTX_SERVE_URL": "http://ctx.example.test"}, "https"),
                ({**base, "CTX_SERVE_SECRET": SECRET, "CTX_SERVE_URL": ""}, "https"),
                ({"CTX_SERVE_TOKEN": TOKEN, "CTX_SERVE_PORT": "0"}, "CTX_STORE"),
                ({**base, "CTX_SERVE_TOKEN": TOKEN, "CTX_SERVE_PORT": "http"}, "not a number")):
            with self.assertRaises(ValueError) as raised:
                build(environ)
            self.assertIn(word, str(raised.exception))

    def test_it_binds_to_this_machine_only(self):
        server = build({"CTX_STORE": FIXTURE, "CTX_SERVE_PORT": "0", "CTX_SERVE_TOKEN": TOKEN})
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_callbacks(self):
        self.assertTrue(oauth.allowed_callback(CALLBACK))
        self.assertFalse(oauth.allowed_callback(CALLBACK + "\nhttps://evil.example"))
        self.assertFalse(oauth.allowed_callback(None))


if __name__ == "__main__":
    unittest.main()
