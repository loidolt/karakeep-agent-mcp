"""OAuth flows through the real proxy with a local, signed-token OIDC provider."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastmcp import FastMCP
from joserfc import jwk, jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from karakeep_agent_mcp.config import Settings
from karakeep_agent_mcp.server import create_server

from .conftest import API, make_settings
from .test_http import serve_in_thread

BASE = "https://mcp.example.com"
CALLBACK = "https://client.example.com/callback"
SCOPES = "openid profile email"
VERIFIER = "test-pkce-verifier-" + "x" * 48
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
)


@asynccontextmanager
async def running(app: Starlette) -> AsyncIterator[None]:
    # Yield fixtures can resume in a different task; AnyIO's lifespan scope
    # must enter and exit in the same task.
    ready, stop = asyncio.Event(), asyncio.Event()

    async def lifetime() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    task = asyncio.create_task(lifetime())
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        yield
    finally:
        stop.set()
        await task


@dataclass
class Provider:
    algorithm: str = "RS256"
    issuer: str = ""
    subject: str = "user1"
    audience: str = "karakeep-mcp"
    scopes: str = SCOPES
    id_lifetime: int = 3600
    access_lifetime: int = 3600
    include_refresh_id: bool = True
    include_initial_id: bool = True
    refresh_revoked: bool = False
    jwt_access: bool = False
    include_access_lifetime: bool = True
    include_refresh_token: bool = True
    key: Any = field(init=False)
    last_access_token: str = ""
    requests: int = 0

    def __post_init__(self) -> None:
        self.key = (
            jwk.ECKey.generate_key("P-256")
            if self.algorithm == "ES256"
            else jwk.RSAKey.generate_key(2048)
        )

    def id_token(self) -> str:
        return jwt.encode(
            {"alg": self.algorithm},
            {
                "iss": self.issuer,
                "aud": self.audience,
                "sub": self.subject,
                "iat": int(time.time()),
                "exp": int(time.time()) + self.id_lifetime,
            },
            self.key,
            algorithms=[self.algorithm],
        )

    async def discovery(self, request: Request) -> JSONResponse:
        self.issuer = str(request.base_url).rstrip("/")
        return JSONResponse(
            {
                "issuer": self.issuer,
                "authorization_endpoint": self.issuer + "/authorize",
                "token_endpoint": self.issuer + "/token",
                "jwks_uri": self.issuer + "/jwks",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": [self.algorithm],
            }
        )

    async def jwks(self, _: Request) -> JSONResponse:
        return JSONResponse({"keys": [self.key.as_dict(private=False)]})

    async def token(self, request: Request) -> JSONResponse:
        form = await request.form()
        assert (
            request.headers["authorization"]
            == "Basic " + base64.b64encode(b"karakeep-mcp:test-client-secret").decode()
        )
        if form["grant_type"] == "refresh_token" and self.refresh_revoked:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        self.requests += 1
        self.last_access_token = f"opaque-access-{self.requests}"
        if self.jwt_access:
            self.last_access_token = jwt.encode(
                {"alg": self.algorithm},
                {
                    "iss": self.issuer,
                    "aud": self.audience,
                    "sub": self.subject,
                    "exp": int(time.time()) + self.access_lifetime,
                    "scope": self.scopes,
                },
                self.key,
                algorithms=[self.algorithm],
            )
        result = {
            "access_token": self.last_access_token,
            "token_type": "Bearer",
            "refresh_token": "opaque-refresh",
            "expires_in": self.access_lifetime,
            "scope": self.scopes,
        }
        include_id = (
            self.include_refresh_id
            if form["grant_type"] == "refresh_token"
            else self.include_initial_id
        )
        if include_id:
            result["id_token"] = self.id_token()
        if not self.include_access_lifetime:
            result.pop("expires_in")
        if not self.include_refresh_token:
            result.pop("refresh_token")
        return JSONResponse(result)

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/.well-known/openid-configuration", self.discovery),
                Route("/jwks", self.jwks),
                Route("/token", self.token, methods=["POST"]),
            ]
        )


@dataclass
class Flow:
    http: httpx.AsyncClient
    mcp: FastMCP
    settings: Settings
    provider: Provider
    client_id: str
    karakeep: respx.Router

    async def login(self, *, check_binding: bool = False) -> httpx.Response:
        authorize = await self.http.get(
            "/authorize",
            params={
                "client_id": self.client_id,
                "redirect_uri": CALLBACK,
                "response_type": "code",
                "scope": SCOPES,
                "state": "client-state",
                "code_challenge": CHALLENGE,
                "code_challenge_method": "S256",
            },
        )
        assert authorize.status_code == 302
        consent = await self.http.get(authorize.headers["location"])
        fields = dict(re.findall(r'name="([^"]+)" value="([^"]*)"', consent.text))
        assert "csrf_token" in fields
        if check_binding:
            async with httpx.AsyncClient(transport=self.http._transport, base_url=BASE) as other:
                denied = await other.post("/consent", data=fields | {"action": "approve"})
                assert denied.status_code == 403
        approved = await self.http.post("/consent", data=fields | {"action": "approve"})
        assert approved.status_code == 302
        upstream = parse_qs(urlparse(approved.headers["location"]).query)
        assert upstream["redirect_uri"] == [BASE + "/auth/callback"]
        callback_params = {"state": upstream["state"][0], "code": "test-idp-code"}
        if check_binding:
            async with httpx.AsyncClient(transport=self.http._transport, base_url=BASE) as other:
                denied = await other.get("/auth/callback", params=callback_params)
                assert denied.status_code == 403
        callback = await self.http.get("/auth/callback", params=callback_params)
        assert callback.status_code == 302
        query = parse_qs(urlparse(callback.headers["location"]).query)
        assert query["state"] == ["client-state"]
        params = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "code": query["code"][0],
            "redirect_uri": CALLBACK,
        }
        if check_binding:
            denied = await self.http.post("/token", data=params | {"code_verifier": "z" * 64})
            assert denied.status_code in (400, 401)
        return await self.http.post("/token", data=params | {"code_verifier": VERIFIER})

    async def refresh(self, tokens: dict[str, Any]) -> httpx.Response:
        return await self.http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.client_id,
                "refresh_token": tokens["refresh_token"],
            },
        )

    async def access(self, token: str) -> int:
        response = await self.http.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        return response.status_code

    async def call_tool(self, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Initialize an MCP session and call one tool over streamable HTTP."""
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }

        async def rpc(payload: dict[str, Any]) -> httpx.Response:
            response = await self.http.post("/mcp", headers=headers, json=payload)
            assert response.status_code in (200, 202), response.text
            return response

        init = await rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            }
        )
        if session := init.headers.get("mcp-session-id"):
            headers["mcp-session-id"] = session
        await rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        response = await rpc(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        if response.headers["content-type"].startswith("text/event-stream"):
            data = [
                line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")
            ]
            message = json.loads(data[-1])
        else:
            message = response.json()
        result: dict[str, Any] = message["result"]
        return result


@pytest.fixture
async def flow(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[Flow]:
    options = getattr(request, "param", {})
    provider = Provider(
        algorithm=options.get("provider_algorithm", "RS256"),
        jwt_access=not options.get("verify_id_token", True),
    )
    with serve_in_thread(provider.app()) as issuer:
        settings = make_settings(
            transport="http",
            auth_mode="oidc",
            base_url=BASE,
            oidc_issuer=issuer,
            oidc_client_id="karakeep-mcp",
            oidc_client_secret="test-client-secret",
            state_dir=str(tmp_path),
            oidc_algorithm=options.get("algorithm", "RS256"),
            oidc_verify_id_token=options.get("verify_id_token", True),
            oidc_audience="karakeep-mcp" if provider.jwt_access else None,
            oidc_allowed_client_redirect_uris=[CALLBACK],
            **(
                {"karakeep_api_key": None, "user_keys_raw": options["user_keys"]}
                if "user_keys" in options
                else {}
            ),
        )
        karakeep = respx.Router(base_url=API, assert_all_mocked=True)
        mcp = create_server(
            settings, karakeep_transport=httpx.MockTransport(karakeep.async_handler)
        )
        app = mcp.http_app(path="/mcp")
        async with (
            running(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app),
                base_url=BASE,
            ) as http,
        ):
            registered = await http.post(
                "/register",
                json={
                    "client_name": "Test",
                    "redirect_uris": [CALLBACK],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": SCOPES,
                },
            )
            assert registered.status_code == 201
            yield Flow(http, mcp, settings, provider, registered.json()["client_id"], karakeep)


@pytest.mark.parametrize(
    "flow", [{}, {"provider_algorithm": "ES256", "algorithm": "ES256"}], indirect=True
)
async def test_oidc_login_and_security_boundaries(flow: Flow) -> None:
    response = await flow.login(check_binding=True)
    assert response.status_code == 200
    tokens = response.json()
    assert await flow.access(tokens["access_token"]) == 200
    assert await flow.access(flow.provider.last_access_token) == 401
    assert await flow.access(flow.provider.id_token()) == 401
    assert await flow.access(tokens["refresh_token"]) == 401
    assert await flow.access(tokens["access_token"] + "tampered") == 401
    bad_redirect = await flow.http.get(
        "/authorize",
        params={
            "client_id": flow.client_id,
            "redirect_uri": "https://evil.example/callback",
            "response_type": "code",
            "code_challenge": CHALLENGE,
            "code_challenge_method": "S256",
        },
    )
    assert bad_redirect.status_code >= 400


@pytest.mark.parametrize("refresh_id", [False, True])
async def test_refresh_after_id_token_expiry(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, refresh_id: bool
) -> None:
    flow.provider.id_lifetime = flow.provider.access_lifetime = 30
    response = await flow.login()
    assert response.status_code == 200
    tokens = response.json()
    assert await flow.access(tokens["access_token"]) == 200
    later = time.time() + 31
    monkeypatch.setattr(time, "time", lambda: later)
    assert await flow.access(tokens["access_token"]) == 401
    flow.provider.access_lifetime = 3600
    flow.provider.include_refresh_id = refresh_id
    refreshed = await flow.refresh(tokens)
    assert refreshed.status_code == 200
    assert await flow.access(refreshed.json()["access_token"]) == 200
    # Refresh-token rotation still prevents reuse.
    assert (await flow.refresh(tokens)).status_code >= 400


async def test_short_id_token_does_not_shorten_access_session(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow.provider.id_lifetime = 30
    tokens = (await flow.login()).json()
    later = time.time() + 31
    monkeypatch.setattr(time, "time", lambda: later)
    assert await flow.access(tokens["access_token"]) == 200


@pytest.mark.parametrize("problem", ["expired", "audience", "signature", "issuer", "missing"])
async def test_invalid_initial_id_token_cannot_start_session(flow: Flow, problem: str) -> None:
    if problem == "expired":
        flow.provider.id_lifetime = -10
    elif problem == "audience":
        flow.provider.audience = "another-client"
    elif problem == "issuer":
        flow.provider.issuer = "https://another-issuer.example.com"
    elif problem == "missing":
        flow.provider.include_initial_id = False
    else:
        # JWKS is cached from the original key; the token endpoint then uses a different key.
        await flow.mcp.auth._token_validator.verifier._get_verification_key(
            flow.provider.id_token()
        )
        flow.provider.key = jwk.RSAKey.generate_key(2048)
    assert (await flow.login()).status_code >= 400


@pytest.mark.parametrize("problem", ["subject", "expired", "audience"])
async def test_refresh_cannot_change_subject_or_use_invalid_replacement(
    flow: Flow, problem: str
) -> None:
    tokens = (await flow.login()).json()
    if problem == "subject":
        flow.provider.subject = "different-user"
    elif problem == "expired":
        flow.provider.id_lifetime = -10
    else:
        flow.provider.audience = "another-client"
    assert (await flow.refresh(tokens)).status_code >= 400


async def test_revoked_upstream_refresh_fails(flow: Flow) -> None:
    tokens = (await flow.login()).json()
    flow.provider.refresh_revoked = True
    assert (await flow.refresh(tokens)).status_code >= 400


async def test_scope_reduction_is_enforced_after_refresh(flow: Flow) -> None:
    tokens = (await flow.login()).json()
    flow.provider.scopes = "openid"
    refreshed = await flow.refresh(tokens)
    assert refreshed.status_code == 200
    assert await flow.access(refreshed.json()["access_token"]) == 403


async def test_oauth_state_survives_restart_and_is_encrypted(flow: Flow, tmp_path: Path) -> None:
    tokens = (await flow.login()).json()
    restarted = create_server(flow.settings)
    app = restarted.http_app(path="/mcp")
    async with app.router.lifespan_context(app):
        assert await restarted.auth.load_access_token(tokens["access_token"]) is not None
    plaintext_present = await asyncio.to_thread(
        lambda: any(
            flow.provider.last_access_token.encode() in p.read_bytes()
            for p in tmp_path.rglob("*")
            if p.is_file()
        )
    )
    assert not plaintext_present


@pytest.mark.parametrize("flow", [{"verify_id_token": False}], indirect=True)
async def test_access_token_jwt_mode_still_supports_refresh(flow: Flow) -> None:
    tokens = (await flow.login()).json()
    assert await flow.access(tokens["access_token"]) == 200
    flow.provider.include_refresh_id = False
    refreshed = await flow.refresh(tokens)
    assert refreshed.status_code == 200
    assert await flow.access(refreshed.json()["access_token"]) == 200


async def test_identity_proof_is_isolated_between_concurrent_requests(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow.provider.id_lifetime = 30
    first = (await flow.login()).json()
    flow.provider.subject = "user2"
    second = (await flow.login()).json()
    later = time.time() + 31
    monkeypatch.setattr(time, "time", lambda: later)
    validated = await asyncio.gather(
        flow.mcp.auth.load_access_token(first["access_token"]),
        flow.mcp.auth.load_access_token(second["access_token"]),
    )
    assert [token.subject for token in validated] == ["user1", "user2"]


async def test_missing_lifetime_does_not_create_a_year_long_oidc_session(flow: Flow) -> None:
    flow.provider.include_access_lifetime = flow.provider.include_refresh_token = False
    response = await flow.login()
    assert response.status_code == 200
    assert response.json()["expires_in"] == 3600


@pytest.mark.parametrize(
    "flow", [{"user_keys": "user1=key-for-user1,user2=key-for-user2"}], indirect=True
)
async def test_per_user_keys_follow_the_signed_in_subject(flow: Flow) -> None:
    route = flow.karakeep.get("/lists").respond(json={"lists": []})
    for subject in ("user1", "user2"):
        flow.provider.subject = subject
        tokens = (await flow.login()).json()
        result = await flow.call_tool(tokens["access_token"], "list_lists", {})
        assert not result.get("isError"), result
        assert route.calls[-1].request.headers["authorization"] == f"Bearer key-for-{subject}"

    flow.provider.subject = "stranger"
    tokens = (await flow.login()).json()
    calls = route.call_count
    result = await flow.call_tool(tokens["access_token"], "list_lists", {})
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "stranger" in text and "KARAKEEP_MCP_USER_KEYS" in text
    assert "key-for" not in text
    assert route.call_count == calls, "an unmapped user must not reach Karakeep at all"
