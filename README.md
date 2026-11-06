# karakeep-agent-mcp

A small, curated, remote [Model Context Protocol](https://modelcontextprotocol.io) server
for [Karakeep](https://karakeep.app) (formerly Hoarder). It lets Claude (claude.ai,
Claude Desktop and mobile, Claude Code), LibreChat and other MCP clients search and read
your saved pages and notes, save new ones, and organize them with tags and lists, over
a URL with sign-in through your own OIDC provider.

- **15 focused tools** with descriptions written for language models, not a 1:1 dump of
  Karakeep's API.
- **Compact responses.** Bookmark JSON is trimmed to the fields an agent needs (about 3×
  smaller); saved pages come back as markdown, in chunks, instead of raw HTML.
- **Hybrid search by default**, combining Karakeep's semantic and keyword search, with
  Karakeep's query language (`#tag`, `list:`, `is:fav`, `after:` …) for filters.
- **Safe by default.** No admin, user, API key, backup, feed, webhook or asset tools. A
  read-only mode. Refuses to serve unauthenticated HTTP beyond localhost.
- **Remote-ready.** stdio for local clients, streamable HTTP (`/mcp`) for remote ones,
  with static bearer tokens or OAuth through any OIDC provider (Pocket ID, Authentik,
  Authelia, Keycloak, …), one shared Karakeep account or one per signed-in person.
- Built on [FastMCP](https://gofastmcp.com) 4, with pinned dependencies and a multi-arch
  container image.

Tested against Karakeep 0.34.1.

Why not Karakeep's own [`@karakeep/mcp`](https://docs.karakeep.app/integrations/mcp)?
It is a good local server, but it speaks stdio only with a single API key, so there is
no URL to give claude.ai or a phone, and no sign-in. This server is for that case.

## Tools

| Tool | Kind | What it does |
| --- | --- | --- |
| `search_bookmarks` | read | Hybrid, semantic or keyword search with Karakeep's qualifiers |
| `list_bookmarks` | read | Browse by save date: all, one list, one tag, favourites, archived |
| `get_bookmark` | read | One bookmark: title, URL, author, summary, note, tags, lists, page text |
| `list_lists` | read | Lists with icons and nesting; smart lists show their query |
| `list_tags` | read | Tags with bookmark counts (and how many were AI-attached) |
| `list_highlights` | read | Highlights and their notes, overall or for one bookmark |
| `save_url` | write | Save a web page (Karakeep fetches it); optional title, note, tags, list |
| `save_note` | write | Save a text note, optionally with tags and a list |
| `update_bookmark` | write | Change title, note, summary, favourite or archived |
| `tag_bookmark` | write | Attach tags (created when missing) |
| `untag_bookmark` | write | Detach tags, AI- or user-attached |
| `add_to_list` | write | Add a bookmark to a manual list |
| `remove_from_list` | write | Remove a bookmark from a list |
| `create_list` | write | New list, nested list, or smart list from a search query |
| `delete_bookmark` | write, destructive | Permanently delete a bookmark |

With `KARAKEEP_MCP_READ_ONLY=true` only the read tools are registered.

Lists can be given by id or by exact name (case-insensitive); an ambiguous name returns
the matching ids. Tags are given by name.

`search_bookmarks` defaults to `hybrid`. Sorting by date (`newest`, `oldest`) needs
keyword search, so the tool switches to `fts` and says so in `search_mode`. On a Karakeep
without an embedding model, hybrid search falls back to `fts` the same way and
`semantic` returns Karakeep's error.

`get_bookmark` returns up to `max_chars` (default 8000) of the page as markdown. When
there is more, `content.next_cursor` is set; pass it back as `content_cursor` to get only
the next chunk. While Karakeep is still fetching a page, `pending` shows which background
jobs (crawl, AI tagging, embedding, summary) haven't finished.

`save_url` does not duplicate: for a URL that is already saved it returns the existing
bookmark with `already_saved: true`, adds the requested tags and list, and leaves its
title and note alone. Deleting a bookmark leaves its tags in place (Karakeep behaviour).

## Quick start

### 1. Create a Karakeep API key

In Karakeep, open Settings → API Keys and create a key for the agent. The key acts as
that Karakeep user, with access to all of their bookmarks, so use the account whose
bookmarks the agent should see. Karakeep has no read-only keys; use
`KARAKEEP_MCP_READ_ONLY` for that.

### 2a. Run as a container (remote clients)

```bash
docker run -d --name karakeep-mcp -p 8000:8000 \
  -e KARAKEEP_URL=https://keep.example.com \
  -e KARAKEEP_API_KEY=... \
  -e KARAKEEP_MCP_AUTH_MODE=bearer \
  -e KARAKEEP_MCP_BEARER_TOKENS="$(openssl rand -hex 32)" \
  -v karakeep-mcp-data:/data \
  ghcr.io/loidolt/karakeep-agent-mcp:latest
```

The MCP endpoint is `http://<host>:8000/mcp`; `GET /healthz` is an unauthenticated health
check. Put TLS in front of it (reverse proxy, Cloudflare Tunnel, …) before exposing it.
For sign-in with OAuth instead of a token, see [`oidc`](#oidc) below.

### 2b. Run locally over stdio

```bash
KARAKEEP_URL=https://keep.example.com KARAKEEP_API_KEY=... \
  uvx --from git+https://github.com/loidolt/karakeep-agent-mcp karakeep-agent-mcp
```

## Configuration

All configuration is through environment variables. CLI flags (`--transport`, `--host`,
`--port`, `--read-only`) override them.

### Karakeep

| Variable | Default | Description |
| --- | --- | --- |
| `KARAKEEP_URL` | *required* | Base URL of Karakeep, e.g. `https://keep.example.com` or `http://karakeep:3000` (a trailing `/api/v1` is accepted) |
| `KARAKEEP_API_KEY` | *required\** | API key of the Karakeep user the agent acts as. \*Not set when using `KARAKEEP_MCP_USER_KEYS` |
| `KARAKEEP_PUBLIC_URL` | `KARAKEEP_URL` | Base for bookmark links in tool output, when `KARAKEEP_URL` is an internal address |
| `KARAKEEP_TIMEOUT` | `30` | Seconds per Karakeep request |
| `KARAKEEP_VERIFY_TLS` | `true` | Verify Karakeep's TLS certificate |

### Server

| Variable | Default (image default) | Description |
| --- | --- | --- |
| `KARAKEEP_MCP_READ_ONLY` | `false` | Register read tools only |
| `KARAKEEP_MCP_TRANSPORT` | `stdio` (`http`) | `stdio` or `http` (streamable HTTP) |
| `KARAKEEP_MCP_HOST` | `127.0.0.1` (`0.0.0.0`) | Bind address for `http` |
| `KARAKEEP_MCP_PORT` | `8000` | Port for `http` |
| `KARAKEEP_MCP_PATH` | `/mcp` | Path of the MCP endpoint |
| `KARAKEEP_MCP_STATELESS_HTTP` | `false` | Disable MCP session affinity; OIDC still needs shared state or replica affinity (see below) |
| `KARAKEEP_MCP_LOG_LEVEL` | `INFO` | Python log level |

### Inbound authentication (HTTP transport)

| Variable | Default (image default) | Description |
| --- | --- | --- |
| `KARAKEEP_MCP_AUTH_MODE` | `none` | `none`, `bearer` or `oidc` |
| `KARAKEEP_MCP_ALLOW_UNAUTHENTICATED_REMOTE` | `false` | Allow `none` on a non-loopback address (only when something else authenticates requests) |
| `KARAKEEP_MCP_BEARER_TOKENS` | | `bearer`: comma-separated accepted tokens, each ≥ 16 characters |
| `KARAKEEP_MCP_BASE_URL` | | `oidc`: public URL of this server without the path, e.g. `https://keep-mcp.example.com` |
| `KARAKEEP_MCP_OIDC_ISSUER` | | `oidc`: issuer URL; discovery is read from `<issuer>/.well-known/openid-configuration` |
| `KARAKEEP_MCP_OIDC_CONFIG_URL` | | `oidc`: full discovery URL, instead of `KARAKEEP_MCP_OIDC_ISSUER` |
| `KARAKEEP_MCP_OIDC_CLIENT_ID` | | `oidc`: client id registered at the provider |
| `KARAKEEP_MCP_OIDC_CLIENT_SECRET` | | `oidc`: client secret |
| `KARAKEEP_MCP_OIDC_SCOPES` | `openid,profile,email` | `oidc`: scopes requested from the provider |
| `KARAKEEP_MCP_OIDC_AUDIENCE` | | `oidc`: `audience` parameter, for providers that need one |
| `KARAKEEP_MCP_OIDC_VERIFY_ID_TOKEN` | `true` | `oidc`: verify ID tokens at login and on replacement; retain verified identity across refreshes that omit an ID token. `false` verifies access-token JWTs instead |
| `KARAKEEP_MCP_OIDC_ALGORITHM` | `RS256` | `oidc`: expected JWT signing algorithm. Also supports RS384/512, ES256/384/512, PS256/384/512 and EdDSA; match the upstream client's configuration |
| `KARAKEEP_MCP_OIDC_ALLOWED_CLIENT_REDIRECT_URIS` | any | `oidc`: comma-separated patterns of MCP-client callback URLs allowed to register, e.g. `https://claude.ai/api/mcp/auth_callback,http://localhost:*` |
| `KARAKEEP_MCP_JWT_SIGNING_KEY` | derived from client secret | `oidc`: key for the tokens this server issues to MCP clients |
| `KARAKEEP_MCP_STATE_DIR` | FastMCP data dir (`/data`) | `oidc`: where client registrations and encrypted upstream tokens are stored. Persist it |
| `KARAKEEP_MCP_USER_KEYS` | | `oidc`: per-person Karakeep keys, comma-separated `<subject>=<api key>` (see [below](#one-karakeep-account-per-person)) |
| `KARAKEEP_MCP_USER_KEYS_FILE` | | `oidc`: the same, one `<subject>=<api key>` per line (`#` comments allowed), e.g. a mounted secret |

## Authentication modes

### `none`

For stdio, or HTTP on `127.0.0.1` for a client on the same machine. The server refuses to
start with `none` on any other address unless `KARAKEEP_MCP_ALLOW_UNAUTHENTICATED_REMOTE=true`.

### `bearer`

Clients send `Authorization: Bearer <token>`. Simple and works with Claude Code, LibreChat
and most MCP clients. Configure several tokens (comma-separated) to give each client its
own and rotate them independently. All of them act as the `KARAKEEP_API_KEY` user.

### `oidc`

In this mode the server is an OAuth authorization server for MCP clients (with dynamic
client registration and a consent screen) and delegates the actual login to your OIDC
provider through one pre-registered client. MCP clients never talk to the provider
directly, so provider quirks around newer MCP OAuth parameters (such as Pocket ID
rejecting the RFC 8707 `resource` parameter) don't matter.

1. In your OIDC provider, create a **confidential** client (authorization code flow) with
   the redirect URI:

   ```
   <KARAKEEP_MCP_BASE_URL>/auth/callback
   ```

   e.g. `https://keep-mcp.example.com/auth/callback`. This is the only redirect URI the
   provider needs. MCP clients' own callbacks (for claude.ai,
   `https://claude.ai/api/mcp/auth_callback`) are registered with this server, not with
   the provider.
2. **Restrict who may use the client** at the provider (Pocket ID: "allowed user groups";
   Authentik: application bindings; Authelia: an `authorization_policy`). With a shared
   `KARAKEEP_API_KEY`, anyone who can complete the login gets that Karakeep user's
   bookmarks.
3. Set `KARAKEEP_MCP_AUTH_MODE=oidc`, `KARAKEEP_MCP_BASE_URL`, `KARAKEEP_MCP_OIDC_ISSUER`,
   `KARAKEEP_MCP_OIDC_CLIENT_ID`, `KARAKEEP_MCP_OIDC_CLIENT_SECRET`, and mount a volume at
   `KARAKEEP_MCP_STATE_DIR` (`/data` in the image) so registrations survive restarts.

| Provider | Issuer (`KARAKEEP_MCP_OIDC_ISSUER`) |
| --- | --- |
| Pocket ID | `https://id.example.com` |
| Authentik | `https://auth.example.com/application/o/<app-slug>/` |
| Authelia | `https://auth.example.com` |
| Keycloak | `https://kc.example.com/realms/<realm>` |

#### Pocket ID

1. Administration → OIDC Clients → Add OIDC Client. Name it (e.g. "Karakeep MCP"), set
   the callback URL to `https://keep-mcp.example.com/auth/callback`, leave **Public
   client** off, and save. Copy the client ID and the client secret (shown once).
2. Under **Allowed User Groups**, pick the group(s) whose members may connect.
3. Configure the server:

   ```bash
   KARAKEEP_MCP_AUTH_MODE=oidc
   KARAKEEP_MCP_BASE_URL=https://keep-mcp.example.com
   KARAKEEP_MCP_OIDC_ISSUER=https://id.example.com
   KARAKEEP_MCP_OIDC_CLIENT_ID=<client id>
   KARAKEEP_MCP_OIDC_CLIENT_SECRET=<client secret>
   KARAKEEP_MCP_OIDC_ALLOWED_CLIENT_REDIRECT_URIS=https://claude.ai/api/mcp/auth_callback,http://localhost:*,http://127.0.0.1:*
   ```

Pocket ID signs ID tokens with RS256 by default, which is the server's default too.

#### One Karakeep account per person

By default every signed-in person acts as the one `KARAKEEP_API_KEY` user. To give each
person their own Karakeep account instead, leave `KARAKEEP_API_KEY` unset and map each
person's OIDC **subject** (`sub` claim) to an API key they created in *their own*
Karakeep account:

```bash
KARAKEEP_MCP_USER_KEYS=3f9c1a2e-...=ak2_...,8b7d4c10-...=ak2_...
# or, one per line in a file (e.g. a Docker/Ansible secret):
KARAKEEP_MCP_USER_KEYS_FILE=/run/secrets/karakeep-user-keys
```

In Pocket ID the subject is the user's ID: open the user under Administration → Users
and copy the ID from the page's URL. People who sign in without a mapping get a tool error that
names their subject (so you can add it) and never reach Karakeep. Only `sub` is used:
email addresses and usernames can often be changed by users themselves at the
provider (Pocket ID can allow this), so they must not select an account. Per-user keys need
`oidc` mode; `bearer` and stdio always use `KARAKEEP_API_KEY`. Restart the server after
changing the mapping.

#### Security notes for `oidc`

The server also publishes the standard discovery documents
(`/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`),
so MCP clients find everything from the `/mcp` URL alone.

ID-token mode validates signature, issuer, audience, expiry and subject for a new login.
It carries minimal verified identity proof in FastMCP's signed tokens. A successful
upstream refresh may omit a replacement ID token; any replacement must be valid and
identify the same user. Access remains bounded by token expiry, stored token mappings,
and successful upstream refresh. If the provider omits the access-token lifetime, the
session lifetime defaults to one hour. This uses local token verification, not live
revocation introspection. Some providers require adding `offline_access` to
`KARAKEEP_MCP_OIDC_SCOPES` before they issue refresh tokens.

For multiple replicas in OIDC mode, use the same public base URL, client credentials and
signing key, and share the actual storage backing `KARAKEEP_MCP_STATE_DIR`. Otherwise,
keep the OAuth flow and authenticated requests on the same replica.
`KARAKEEP_MCP_STATELESS_HTTP` only removes MCP session affinity; it does not replicate
OAuth registrations, transactions or tokens.

## Client setup

### claude.ai, Claude Desktop, Claude mobile

Use a public HTTPS URL. In claude.ai: Customize → Connectors → Add custom connector,
URL `https://keep-mcp.example.com/mcp`.

- **OAuth:** use `oidc` mode and automatic client registration. Claude opens your
  provider's login when you connect.
- **Bearer:** use `bearer` mode, choose **No sign in**, and add the request header
  `Authorization: Bearer <token>` with one of `KARAKEEP_MCP_BEARER_TOKENS`.

Connectors added on claude.ai are available in Claude Desktop and the mobile apps.
See [Claude's connector setup](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
for the current UI and organization permissions.

### Claude Code

```bash
# bearer mode
claude mcp add --transport http karakeep https://keep-mcp.example.com/mcp \
  --header "Authorization: Bearer $KARAKEEP_MCP_TOKEN"

# oidc mode (then run /mcp in Claude Code to log in)
claude mcp add --transport http karakeep https://keep-mcp.example.com/mcp

# local stdio, no server to run
claude mcp add karakeep -e KARAKEEP_URL=https://keep.example.com -e KARAKEEP_API_KEY=... \
  -- uvx --from git+https://github.com/loidolt/karakeep-agent-mcp karakeep-agent-mcp
```

### LibreChat

In `librechat.yaml`:

```yaml
mcpServers:
  karakeep:
    type: streamable-http
    url: http://karakeep-mcp:8000/mcp   # or your public https URL
    headers:
      Authorization: "Bearer ${KARAKEEP_MCP_TOKEN}"
    timeout: 60000
```

and set `KARAKEEP_MCP_TOKEN` in LibreChat's `.env` to one of `KARAKEEP_MCP_BEARER_TOKENS`.

### Other clients (stdio)

```json
{
  "mcpServers": {
    "karakeep": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/loidolt/karakeep-agent-mcp", "karakeep-agent-mcp"],
      "env": { "KARAKEEP_URL": "https://keep.example.com", "KARAKEEP_API_KEY": "..." }
    }
  }
}
```

## Security model

- **The agent acts as a Karakeep user.** A Karakeep API key has the full rights of the
  account that created it (including admin rights for an admin account). This server
  only exposes the tools above, so connected clients can't reach Karakeep's admin, user,
  API key, backup or feed endpoints, but they can read, change and delete that user's
  bookmarks. With per-user keys each person only reaches their own account.
- **Read-only mode** (`KARAKEEP_MCP_READ_ONLY=true`) removes every write tool from the
  server, not just from descriptions. Use it for clients that only need to look things up.
- **Saved content is untrusted.** Pages saved from the web can contain text written to
  manipulate language models ("ignore previous instructions and …"). The server
  therefore exposes no tools that send data to arbitrary destinations. The only tool
  that reaches an outside URL is `save_url`, which makes *Karakeep* fetch the page you
  name. Tool descriptions and server instructions tell models to treat bookmark content
  as data. Still, keep a human in the loop for write actions (especially
  `delete_bookmark`), and be careful about connecting this server in the same session as
  tools that can exfiltrate data (email, web requests, file access).
- **Inbound auth.** Unauthenticated HTTP is refused beyond localhost. Bearer tokens are
  compared in constant time and never forwarded to Karakeep. In `oidc` mode, MCP clients
  only ever receive tokens issued by this server; upstream provider tokens are stored
  encrypted in `KARAKEEP_MCP_STATE_DIR`.
- **Outbound.** Karakeep keys are only sent to `KARAKEEP_URL`; redirects are not
  followed. Model-supplied ids are encoded as single path segments. Keys are never
  included in logs, tool output or configuration errors.
- **Container.** Runs as an unprivileged user (uid 10001), writes only to `/data`.

## Development

```bash
uv sync
uv run pytest                 # unit tests (Karakeep API mocked)
uv run ruff check . && uv run ruff format --check . && uv run mypy

# integration tests against a throwaway Karakeep + Meilisearch
docker compose -f dev/compose.yaml up -d --wait karakeep
eval "$(uv run python dev/bootstrap_karakeep.py)" && export KARAKEEP_URL KARAKEEP_API_KEY
uv run pytest -m integration

# the container in bearer mode, exercised over streamable HTTP
export KARAKEEP_MCP_BEARER_TOKENS=$(openssl rand -hex 32)
docker compose -f dev/compose.yaml --profile mcp up -d --build --wait
uv run python dev/smoke_http.py http://127.0.0.1:8000/mcp "$KARAKEEP_MCP_BEARER_TOKENS"
```

The dev Karakeep has no crawler browser and no AI provider, so nothing is auto-tagged and
semantic search is unavailable (hybrid falls back to keyword search). The integration
tests and the smoke script create bookmarks, a tag and a list and remove them again; they
also run against a real instance with a dedicated API key, but prefer a throwaway one.
Set `MCP_HOST_PORT` if port 8000 is taken.

Releases: push a tag `vX.Y.Z`; CI builds `linux/amd64` and `linux/arm64` images and
publishes `ghcr.io/loidolt/karakeep-agent-mcp:X.Y.Z`, `X.Y`, `X` (from 1.0) and `latest`.

## License

[MIT](LICENSE)
