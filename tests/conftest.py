from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client, FastMCP

from karakeep_agent_mcp.config import Settings
from karakeep_agent_mcp.server import create_server

KARAKEEP = "http://karakeep.test"
API = f"{KARAKEEP}/api/v1"
API_KEY = "karakeep-key"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"karakeep_url": KARAKEEP, "karakeep_api_key": API_KEY}
    values.update(overrides)
    return Settings.load(**values)


@pytest.fixture(autouse=True)
def _clean_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real KARAKEEP_* environment out of unit tests."""
    import os

    if request.node.get_closest_marker("integration"):
        return
    for key in list(os.environ):
        if key.startswith("KARAKEEP_"):
            monkeypatch.delenv(key)


@pytest.fixture
def karakeep() -> respx.Router:
    return respx.Router(base_url=API, assert_all_mocked=True)


@pytest.fixture
def server_factory(karakeep: respx.Router) -> Callable[..., FastMCP]:
    def factory(**overrides: Any) -> FastMCP:
        return create_server(
            make_settings(**overrides),
            karakeep_transport=httpx.MockTransport(karakeep.async_handler),
        )

    return factory


@pytest.fixture
async def client(server_factory: Callable[..., FastMCP]) -> AsyncIterator[Client[Any]]:
    async with Client(server_factory()) as c:
        yield c
