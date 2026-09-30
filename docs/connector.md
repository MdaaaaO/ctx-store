# The connector: ctx from claude.ai

`ctx-serve` puts the MCP server of `ctx mcp` behind HTTP, with authentication, so a store on your
machine can be used from claude.ai on the web, and from any MCP client that speaks Streamable HTTP.

It is not part of the core. The core (`ctxstore/`) has no network and no server; `ctxserve/` is a
separate package in the same repo, standard library only.

## What it is

| Item | Rule |
|---|---|
| Transport | Streamable HTTP: every JSON-RPC message is one `POST /mcp`, answered with one JSON object; a notification is answered `202`. No stream, no session: `GET` and `DELETE` on `/mcp` answer `405` |
| Protocol | MCP `2026-07-28` (stateless, no `initialize`) and the handshake revisions `2025-11-25` back to `2024-11-05`, on the same endpoint; versions, headers and refusals in [interface.md](interface.md#front-ends) |
| Tools | the same as `ctx mcp`: one per built verb |
| Authentication | required. A server with none configured does not start |
| Bind | `127.0.0.1`. The way out is a tunnel or a reverse proxy that terminates TLS |
| Store | the one `CTX_STORE` names. Writes are validated, locked and audited as everywhere; the actor is `connector` |

## Two ways in

| Way | For | Set |
|---|---|---|
| OAuth 2.1 | claude.ai, Claude Desktop, Claude mobile: they accept OAuth or no authentication from a connector you add yourself, and no authentication is not offered here | `CTX_SERVE_SECRET`, `CTX_SERVE_URL` |
| Bearer token | Claude Code, scripts, `curl` | `CTX_SERVE_TOKEN` |

Both can be on at once.

The OAuth side is an authorization server for one owner: dynamic client registration, the
authorization code grant with PKCE (S256), refresh tokens that rotate. Consent is a page this server
shows, where you type the owner passphrase (`CTX_SERVE_SECRET`). Five wrong passphrases in ten
minutes lock the page for the rest of the ten minutes.

Registered redirect addresses are limited to Claude's callback
(`https://claude.ai/api/mcp/auth_callback`, and the same on `claude.com`) and loopback callbacks
(`http://localhost:<port>/callback`, `http://127.0.0.1:<port>/callback`).

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `CTX_STORE` | none, required | the store to serve |
| `CTX_SERVE_SECRET` | unset | owner passphrase, at least 16 characters; turns OAuth on |
| `CTX_SERVE_URL` | none, required with OAuth | the `https://` address the server is reached at, without `/mcp` |
| `CTX_SERVE_TOKEN` | unset | a bearer token, at least 16 characters |
| `CTX_SERVE_PORT` | `8377` | port on this machine |
| `CTX_SERVE_BIND` | `127.0.0.1` | address to bind; change it only behind a proxy you trust |
| `CTX_SERVE_ORIGINS` | unset | more allowed `Origin` values, comma-separated; `https://claude.ai` and `https://claude.com` always are |
| `CTX_SERVE_STATE` | `$XDG_STATE_HOME/ctx-serve/state.json`; without `XDG_STATE_HOME`, `~/.local/state/ctx-serve/state.json` | registered clients and the digests of codes and tokens; mode 600, never inside the store |
| `CTX_ACTOR` | `connector` | the name writes are recorded under |

The state file holds sha256 digests. The passphrase, the bearer token and the tokens handed out are
not in it.

## Run it behind a tunnel

1. Pick a tunnel that gives you an `https://` address for a local port.

   | Tunnel | Address | For |
   |---|---|---|
   | `cloudflared tunnel --url http://127.0.0.1:8377` (a quick tunnel, no account) | new on every start | a first test: the connector has to be added again after each restart |
   | a named Cloudflare Tunnel, or `tailscale funnel 8377` | stays the same | use |

   Start the tunnel first: the server needs its address.
2. Start the server with that address:

   ```sh
   export CTX_STORE=/path/to/store
   export CTX_SERVE_URL=https://ctx.example.com
   export CTX_SERVE_SECRET="$(cat ~/.config/ctx/serve-passphrase)"   # never on the command line
   ./ctx-serve
   ```

3. In claude.ai: Settings → Connectors → Add custom connector, and enter
   `https://ctx.example.com/mcp`. Claude opens the consent page; type the passphrase.
4. Claude Code, with a bearer token instead:

   ```sh
   claude mcp add --transport http ctx https://ctx.example.com/mcp \
     --header "Authorization: Bearer $CTX_SERVE_TOKEN"
   ```

## What to know before you expose a store

- The log (stderr) holds the method, the path and the status of each request, and only a method
  and a path the server knows. Nothing else a client sent is written, so no token reaches it.

- Whoever holds the passphrase, or a token, can read and write the whole store. There are no
  scopes and no read-only mode yet.
- Tool results are text from your docs. A doc that holds instructions is read by the model as text
  it was given; keep docs you did not write out of a store you serve.
- The secret guard refuses writes that look like secrets. It does not look at what is already in
  the store: `ctx find` for the usual prefixes before you serve it.
- The tunnel provider sees the traffic in clear text at its edge. That is true of every tunnel that
  terminates TLS for you.
- To cut access: stop the server, delete the state file (every token is gone), change the
  passphrase.

## Not verified

The flows are tested against this server by a client written for the tests (`tests/test_serve.py`):
discovery, registration, consent, PKCE, refresh rotation, and the refusals. They have not been run
from claude.ai itself.
