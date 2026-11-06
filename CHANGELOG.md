# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The environment variables and the container
image's port and volume are the public interface: renaming or removing any of them is a
breaking change.

## [Unreleased]

## [0.1.1] - 2026-11-06

### Fixed

- OAuth sign-in through Pocket ID 2.19+ (and other providers that validate RFC 8707
  resource indicators) failed with "The 'resource' or 'scope' parameter is invalid".
  The MCP client's `resource` is no longer forwarded to the identity provider; this server
  issues its own tokens and uses the provider only for login. (Same fix as
  mealie-agent-mcp 0.1.1.)

### Added

- `KARAKEEP_MCP_OIDC_FORWARD_RESOURCE` (default `false`) to restore forwarding for
  providers that need the resource to set the token audience.

### Changed

- FastMCP 4.1.6; container built with uv 0.13.7.

## [0.1.0] - 2026-11-05

### Added

- 15 curated tools for Karakeep bookmarks, notes, tags, lists and highlights, with
  compact responses and page content as chunked markdown.
- Hybrid search by default, with automatic keyword fallback for date sorting and for
  instances without embeddings.
- `KARAKEEP_MCP_READ_ONLY` to register only the 6 read tools.
- stdio and streamable HTTP transports, `/healthz` health endpoint.
- Inbound auth modes `none` (loopback only unless overridden), `bearer` and `oidc`
  (OAuth proxy in front of any OIDC provider), adapted from mealie-agent-mcp.
- Per-person Karakeep accounts in `oidc` mode: `KARAKEEP_MCP_USER_KEYS` /
  `KARAKEEP_MCP_USER_KEYS_FILE` map the verified OIDC subject to that person's API key.
- Non-root multi-arch container image `ghcr.io/loidolt/karakeep-agent-mcp`.
- Unit tests with a mocked Karakeep API; integration tests against Karakeep 0.34.1.

[Unreleased]: https://github.com/loidolt/karakeep-agent-mcp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/loidolt/karakeep-agent-mcp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/loidolt/karakeep-agent-mcp/releases/tag/v0.1.0
