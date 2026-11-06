"""Thin async wrapper around the Karakeep REST API (``/api/v1``)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
from fastmcp.exceptions import ToolError

from . import __version__

JSON = Any


class KarakeepError(ToolError):
    """A Karakeep API call failed. The message is safe to show to the model."""

    def __init__(self, status: int | None, message: str, code: str | None = None) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


def _error_detail(resp: httpx.Response) -> tuple[str, str | None]:
    try:
        body = resp.json()
    except ValueError:
        return (resp.text.strip()[:200] or resp.reason_phrase or "error"), None
    if isinstance(body, dict):
        code = body.get("code") if isinstance(body.get("code"), str) else None
        message = body.get("message") or body.get("error") or body
        return str(message)[:500], code
    return str(body)[:500], None


class KarakeepClient:
    """One connection pool; the API key is chosen per request by ``key_for_request``.

    The key resolver lets one server act as different Karakeep users depending
    on who is signed in (see users.py), without a client per user.
    """

    def __init__(
        self,
        base_url: str,
        key_for_request: Callable[[], str],
        *,
        timeout: float = 30.0,
        verify: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key_for_request = key_for_request
        self._http = httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v1",
            headers={
                "Accept": "application/json",
                "User-Agent": f"karakeep-agent-mcp/{__version__}",
            },
            timeout=timeout,
            verify=verify,
            transport=transport,
            # Never follow redirects: keys must only ever go to KARAKEEP_URL.
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: JSON = None,
    ) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._key_for_request()}"}
        if params:
            params = {k: _param(v) for k, v in params.items() if v is not None}
        try:
            resp = await self._http.request(method, path, params=params, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise KarakeepError(
                None, f"Karakeep did not respond in time ({method} {path})"
            ) from exc
        except httpx.HTTPError as exc:
            raise KarakeepError(None, f"Could not reach Karakeep: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            message, code = _error_detail(resp)
            if resp.status_code == 401:
                hint = " (check the Karakeep API key)"
            elif resp.status_code == 404:
                hint = " (not found)"
            else:
                hint = ""
            raise KarakeepError(
                resp.status_code,
                f"Karakeep returned HTTP {resp.status_code} for {method} {path}: {message}{hint}",
                code,
            )
        if resp.status_code >= 300:
            raise KarakeepError(
                resp.status_code,
                f"Karakeep redirected {method} {path}; check KARAKEEP_URL (redirects are not "
                "followed so the API key stays on the configured host)",
            )
        return resp

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: JSON = None,
    ) -> JSON:
        resp = await self._send(method, path, params=params, json=json)
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    async def get(self, path: str, **kw: Any) -> JSON:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> JSON:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw: Any) -> JSON:
        return await self.request("PUT", path, **kw)

    async def patch(self, path: str, **kw: Any) -> JSON:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw: Any) -> JSON:
        return await self.request("DELETE", path, **kw)

    async def create_bookmark(self, body: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """POST /bookmarks. Returns (bookmark, created); Karakeep answers 200 for a duplicate."""
        resp = await self._send("POST", "/bookmarks", json=body)
        data: dict[str, Any] = resp.json()
        return data, resp.status_code == 201


def _param(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value
