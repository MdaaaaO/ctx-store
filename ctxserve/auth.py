"""Who may call: a static bearer token, or OAuth 2.1 for one owner.

The OAuth side is an authorization server small enough to read: dynamic
client registration, the authorization code grant with PKCE (S256), refresh
tokens that rotate. There is one resource owner. Consent is the owner's
passphrase, typed into the page this server shows.

Tokens, codes and the passphrase are never stored: the state file holds
sha256 digests, and lives outside the store.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

CODE_SECONDS = 300
ACCESS_SECONDS = 3600
REFRESH_SECONDS = 30 * 86400
FORM_SECONDS = 600
ATTEMPTS, WINDOW = 5, 600  # wrong passphrases allowed per ten minutes
CLIENTS = 200              # registered clients kept; the oldest go first
CALLBACKS = (
    re.compile(r"^https://claude\.ai/api/mcp/auth_callback$"),
    re.compile(r"^https://claude\.com/api/mcp/auth_callback$"),
    re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?/callback$"),
)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same(left, right):
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def allowed_callback(uri):
    return isinstance(uri, str) and any(rule.match(uri) for rule in CALLBACKS)


def challenge_of(verifier):
    raw = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class Refused(Exception):
    """An OAuth error: (HTTP status, error code, description)."""

    def __init__(self, status, code, text=""):
        super().__init__(code)
        self.status, self.code, self.text = status, code, text


class State:
    """The server's memory between requests, kept in one JSON file."""

    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        self.data = {"clients": {}, "codes": {}, "access": {}, "refresh": {}, "forms": {}, "failures": []}
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                self.data.update(json.load(handle))

    def save(self):
        now = self.clock()
        for name in ("codes", "access", "refresh", "forms"):
            self.data[name] = {k: v for k, v in self.data[name].items() if v["until"] > now}
        self.data["failures"] = [at for at in self.data["failures"] if at > now - WINDOW]
        clients = sorted(self.data["clients"].items(), key=lambda item: item[1]["at"])
        self.data["clients"] = dict(clients[-CLIENTS:])
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        temp = f"{self.path}.{os.getpid()}.tmp"
        handle = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(self.data, out)
        os.replace(temp, self.path)


class Auth:
    def __init__(self, url, secret=None, token=None, state=None, clock=time.time):
        self.url = url.rstrip("/")
        self.secret, self.token = secret, token
        self.clock = clock
        self.state = state or State(None, clock)
        self.lock = threading.RLock()  # requests run in threads; the state is one

    # --- the resource: who may call /mcp ---

    def allows(self, header):
        """Whether an Authorization header carries a token this server gave
        out, or the static one."""
        with self.lock:
            kind, _, value = (header or "").partition(" ")
            if kind.lower() != "bearer" or not value.strip():
                return False
            value = value.strip()
            if self.token and same(value, self.token):
                return True
            entry = self.state.data["access"].get(digest(value))
            return bool(entry) and entry["until"] > self.clock()

    def challenge(self):
        return f'Bearer resource_metadata="{self.url}/.well-known/oauth-protected-resource"'

    # --- discovery ---

    def resource_metadata(self):
        return {"resource": f"{self.url}/mcp", "authorization_servers": [self.url],
                "bearer_methods_supported": ["header"]}

    def server_metadata(self):
        return {
            "issuer": self.url,
            "authorization_endpoint": f"{self.url}/authorize",
            "token_endpoint": f"{self.url}/token",
            "registration_endpoint": f"{self.url}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }

    # --- registration ---

    def register(self, body):
        with self.lock:
            uris = body.get("redirect_uris") if isinstance(body, dict) else None
            if not isinstance(uris, list) or not uris or not all(allowed_callback(uri) for uri in uris):
                raise Refused(400, "invalid_redirect_uri", "redirect_uris must be Claude's callback or a loopback callback")
            name = body.get("client_name")
            client = secrets.token_urlsafe(24)
            self.state.data["clients"][client] = {
                "redirect_uris": uris, "name": name[:80] if isinstance(name, str) else "", "at": self.clock()}
            self.state.save()
            return {"client_id": client, "redirect_uris": uris, "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                    "client_name": self.state.data["clients"][client]["name"]}

    # --- authorization ---

    def request(self, query):
        """The checked parameters of an authorization request. A request whose
        client or redirect cannot be trusted is refused here and never
        redirected."""
        with self.lock:
            client = self.state.data["clients"].get(query.get("client_id", ""))
            if not client:
                raise Refused(400, "invalid_client", "unknown client_id")
            redirect = query.get("redirect_uri", "")
            if redirect not in client["redirect_uris"] or not allowed_callback(redirect):
                raise Refused(400, "invalid_request", "redirect_uri is not the client's")
            if query.get("response_type") != "code":
                raise Refused(400, "unsupported_response_type")
            challenge = query.get("code_challenge", "")
            if query.get("code_challenge_method") != "S256" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge):
                raise Refused(400, "invalid_request", "PKCE with S256 is required")
            return {"client_id": query["client_id"], "redirect_uri": redirect, "code_challenge": challenge,
                    "state": query.get("state", ""), "name": client["name"]}

    def form(self, request):
        """A one-time token that ties the consent page to the request it shows."""
        with self.lock:
            token = secrets.token_urlsafe(24)
            self.state.data["forms"][digest(token)] = {"request": request, "until": self.clock() + FORM_SECONDS}
            self.state.save()
            return token

    def consent(self, form, passphrase):
        """The redirect that carries the code, once the owner's passphrase is
        right."""
        with self.lock:
            if not self.secret:
                raise Refused(503, "server_error", "no owner passphrase is set")
            now = self.clock()
            if len([at for at in self.state.data["failures"] if at > now - WINDOW]) >= ATTEMPTS:
                raise Refused(429, "access_denied", "too many wrong passphrases; try again later")
            entry = self.state.data["forms"].pop(digest(form or ""), None)
            if not entry or entry["until"] <= now:
                self.state.save()
                raise Refused(400, "invalid_request", "the page has expired; start again from Claude")
            request = entry["request"]
            if not same(passphrase or "", self.secret):
                self.state.data["failures"].append(now)
                self.state.save()
                raise Refused(403, "access_denied", "wrong passphrase")
            code = secrets.token_urlsafe(32)
            self.state.data["codes"][digest(code)] = {**request, "until": now + CODE_SECONDS}
            self.state.save()
            answer = {"code": code}
            if request["state"]:
                answer["state"] = request["state"]
            joiner = "&" if "?" in request["redirect_uri"] else "?"
            return request["redirect_uri"] + joiner + urllib.parse.urlencode(answer)

    # --- tokens ---

    def _issue(self, client):
        now = self.clock()
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.state.data["access"][digest(access)] = {"client_id": client, "until": now + ACCESS_SECONDS}
        self.state.data["refresh"][digest(refresh)] = {"client_id": client, "until": now + REFRESH_SECONDS}
        self.state.save()
        return {"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_SECONDS,
                "refresh_token": refresh}

    def exchange(self, form):
        with self.lock:
            grant = form.get("grant_type")
            now = self.clock()
            if grant == "authorization_code":
                entry = self.state.data["codes"].pop(digest(form.get("code", "")), None)
                self.state.save()  # a code is spent by its first use, right or wrong
                verifier = form.get("code_verifier", "")
                if (not entry or entry["until"] <= now
                        or form.get("client_id") != entry["client_id"]
                        or form.get("redirect_uri") != entry["redirect_uri"]
                        or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
                        or not same(challenge_of(verifier), entry["code_challenge"])):
                    raise Refused(400, "invalid_grant")
                return self._issue(entry["client_id"])
            if grant == "refresh_token":
                entry = self.state.data["refresh"].pop(digest(form.get("refresh_token", "")), None)
                self.state.save()  # rotation: the old refresh token is gone either way
                if not entry or entry["until"] <= now or form.get("client_id") != entry["client_id"]:
                    raise Refused(400, "invalid_grant")
                return self._issue(entry["client_id"])
            raise Refused(400, "unsupported_grant_type")
