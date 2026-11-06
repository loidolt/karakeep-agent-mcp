"""OIDC login identity that survives refresh responses without a new ID token.

FastMCP 4.1.5 verifies ID-token expiry on every resource request. OIDC permits
refresh responses to omit the ID token, so its original expiry must not end an
otherwise valid refreshed session. We retain minimal proof of a verified login
in FastMCP's signed access/refresh tokens. Only that signed proof permits reuse
of the same ID token; a new login or replacement ID token is verified normally.
"""

from __future__ import annotations

import hashlib
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastmcp.server.auth import AccessToken, OIDCProxy, TokenVerifier
from fastmcp.server.auth.oauth_proxy.models import UpstreamTokenSet
from joserfc.errors import JoseError
from mcp.server.auth.provider import RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken


@dataclass
class _Session:
    identity: dict[str, Any] | None
    upstream_expires_at: float | None = None


_session: ContextVar[_Session | None] = ContextVar("karakeep_oidc_session", default=None)


class _IdentityVerifier(TokenVerifier):
    def __init__(self, verifier: TokenVerifier) -> None:
        super().__init__(required_scopes=verifier.required_scopes)
        self.verifier = verifier

    async def verify_token(self, token: str) -> AccessToken | None:
        session = _session.get()
        if (
            session
            and session.upstream_expires_at is not None
            and session.upstream_expires_at <= time.time()
        ):
            return None
        identity = session.identity if session else None
        digest = hashlib.sha256(token.encode()).hexdigest()
        if identity and identity.get("id_token_sha256") == digest:
            # This exact ID token was verified before the proxy signed the
            # session proof. Its lifetime is now bounded by the upstream access
            # token, enforced above and by FastMCP's alternate-verification path.
            return AccessToken(
                token=token,
                client_id=identity["client_id"],
                subject=identity["subject"],
                scopes=[],
                claims=identity["claims"],
            )
        validated = await self.verifier.verify_token(token)
        if not validated or not validated.subject or validated.expires_at is None:
            return None
        if identity and validated.subject != identity["subject"]:
            return None  # a refresh must not switch the authenticated user
        return validated


class SessionOIDCProxy(OIDCProxy):
    """Small compatibility layer for the pinned FastMCP OAuth implementation."""

    def __init__(self, *, verify_id_token: bool = False, **kwargs: Any) -> None:
        self._identity_sessions = verify_id_token
        super().__init__(verify_id_token=verify_id_token, **kwargs)

    def get_token_verifier(self, **kwargs: Any) -> TokenVerifier:
        verifier = super().get_token_verifier(**kwargs)
        return _IdentityVerifier(verifier) if self._identity_sessions else verifier

    async def _extract_upstream_claims(self, idp_tokens: dict[str, Any]) -> dict[str, Any] | None:
        if not self._identity_sessions:
            return await super()._extract_upstream_claims(idp_tokens)
        token = idp_tokens.get("id_token")
        if not isinstance(token, str):
            raise TokenError("invalid_grant", "Missing upstream ID token")
        validated = await self._token_validator.verify_token(token)
        if validated is None:
            raise TokenError("invalid_grant", "Invalid upstream ID token")
        claims = validated.claims or {}
        return {
            "karakeep_identity": {
                "id_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "client_id": validated.client_id,
                "subject": validated.subject,
                "claims": {k: claims[k] for k in ("iss", "aud", "sub") if k in claims},
            }
        }

    @staticmethod
    def _identity(payload: dict[str, Any]) -> dict[str, Any] | None:
        upstream = payload.get("upstream_claims") or {}
        identity = upstream.get("karakeep_identity")
        return identity if isinstance(identity, dict) else None

    def _get_verification_token(self, upstream_token_set: UpstreamTokenSet) -> str | None:
        session = _session.get()
        if session is not None:
            session.upstream_expires_at = upstream_token_set.expires_at
        return super()._get_verification_token(upstream_token_set)

    async def load_access_token(self, token: str) -> AccessToken | None:
        if not self._identity_sessions:
            result = await super().load_access_token(token)
        else:
            try:
                payload = self.jwt_issuer.verify_token(token)
            except (JoseError, ValueError, KeyError):
                return None
            context = _session.set(_Session(self._identity(payload)))
            try:
                result = await super().load_access_token(token)
            finally:
                _session.reset(context)
        # FastMCP annotates this result as the SDK's base AccessToken type.
        return AccessToken.model_validate(result.model_dump()) if result is not None else None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not self._identity_sessions:
            return await super().exchange_refresh_token(client, refresh_token, scopes)
        try:
            payload = self.jwt_issuer.verify_token(
                refresh_token.token,
                expected_token_use="refresh",  # noqa: S106 - token kind, not a secret
            )
        except (JoseError, ValueError, KeyError) as exc:
            raise TokenError("invalid_grant", "Invalid refresh token") from exc
        context = _session.set(_Session(self._identity(payload)))
        try:
            return await super().exchange_refresh_token(client, refresh_token, scopes)
        finally:
            _session.reset(context)
