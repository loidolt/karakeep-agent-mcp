"""Choose the Karakeep API key for the current request.

Two modes:

- One shared key (``KARAKEEP_API_KEY``): every caller acts as that Karakeep user.
- Per-user keys (``KARAKEEP_MCP_USER_KEYS``, oidc mode only): the key is looked up
  by the verified OIDC subject (``sub``) of the signed-in user. Unmapped users
  get an error, never another user's key.

Only ``sub`` is used. Email and username are often editable by the user at the
provider (Pocket ID allows it by default), so they must not select an account.
"""

from __future__ import annotations

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token


class NoKarakeepAccount(ToolError):
    pass


def current_subject() -> str | None:
    token = get_access_token()
    if token is None:
        return None
    subject = getattr(token, "subject", None) or (token.claims or {}).get("sub")
    return str(subject) if subject else None


class KeyResolver:
    def __init__(self, *, shared_key: str | None, user_keys: dict[str, str]) -> None:
        if (shared_key is None) == (not user_keys):
            raise ValueError("configure exactly one of a shared key or per-user keys")
        self._shared = shared_key
        self._by_subject = dict(user_keys)

    @property
    def per_user(self) -> bool:
        return self._shared is None

    def __call__(self) -> str:
        if self._shared is not None:
            return self._shared
        subject = current_subject()
        if subject is None:
            raise NoKarakeepAccount("This server needs a signed-in user to choose a Karakeep key.")
        key = self._by_subject.get(subject)
        if key is None:
            raise NoKarakeepAccount(
                f"No Karakeep account is linked to your sign-in (subject {subject!r}). "
                "Ask the server operator to add it to KARAKEEP_MCP_USER_KEYS."
            )
        return key
