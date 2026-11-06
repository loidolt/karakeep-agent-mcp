"""End-to-end tests against a real Karakeep.

They create bookmarks, a tag and a list, and delete them again. Use a throwaway
instance or a dedicated account:
    docker compose -f dev/compose.yaml up -d --wait karakeep
    eval "$(uv run python dev/bootstrap_karakeep.py)" && export KARAKEEP_URL KARAKEEP_API_KEY
    uv run pytest -m integration
Set KARAKEEP_IT_URL to a web page to also check crawled page content.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastmcp import Client

from karakeep_agent_mcp.config import Settings
from karakeep_agent_mcp.server import create_server

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (os.environ.get("KARAKEEP_URL") and os.environ.get("KARAKEEP_API_KEY")),
        reason="KARAKEEP_URL / KARAKEEP_API_KEY not set",
    ),
]

RUN = uuid.uuid4().hex[:8]
TAG = f"mcp-it-{RUN}"
LIST = f"MCP IT {RUN}"


@pytest.fixture
async def mcp_client() -> AsyncIterator[Client[Any]]:
    async with Client(create_server(Settings.load())) as c:
        yield c
    await cleanup()


async def cleanup() -> None:
    """Remove what the tools cannot: the test's tags and lists (and any stray bookmarks)."""
    settings = Settings.load()
    assert settings.karakeep_api_key
    headers = {"Authorization": f"Bearer {settings.karakeep_api_key.get_secret_value()}"}
    async with httpx.AsyncClient(
        base_url=f"{settings.karakeep_url}/api/v1", headers=headers
    ) as http:
        found = await http.get("/bookmarks/search", params={"q": f"#{TAG}", "limit": 50})
        for b in found.json().get("bookmarks", []):
            await http.delete(f"/bookmarks/{b['id']}")
        for t in (await http.get("/tags", params={"nameContains": RUN})).json()["tags"]:
            await http.delete(f"/tags/{t['id']}")
        for lst in (await http.get("/lists")).json()["lists"]:
            if RUN in lst["name"]:
                await http.delete(f"/lists/{lst['id']}")


async def call(client: Client[Any], tool: str, /, **args: Any) -> Any:
    result = await client.call_tool(tool, args, raise_on_error=False)
    text = result.content[0].text  # type: ignore[union-attr]
    assert not result.is_error, f"{tool}: {text}"
    return json.loads(text)


async def eventually(check: Callable[[], Awaitable[bool]], seconds: float = 60) -> None:
    """Karakeep indexes, tags and crawls in background workers."""
    deadline = asyncio.get_running_loop().time() + seconds
    while not await check():
        if asyncio.get_running_loop().time() > deadline:
            pytest.fail("timed out waiting for Karakeep's background work")
        await asyncio.sleep(2)


async def test_note_lifecycle(mcp_client: Client[Any]) -> None:
    text = f"Integration note {RUN}: sourdough starter at 78 percent hydration."
    note = await call(mcp_client, "save_note", text=text, title=f"IT {RUN}", tags=[TAG])
    bid = note["id"]
    assert note["type"] == "text"
    assert {"name": TAG, "by": "human"} in note["tags"]

    async def indexed() -> bool:
        found = await call(mcp_client, "search_bookmarks", query=f"{RUN} sourdough")
        return any(b["id"] == bid for b in found["bookmarks"])

    await eventually(indexed)
    by_tag = await call(mcp_client, "search_bookmarks", query=f"#{TAG}", sort="newest")
    assert [b["id"] for b in by_tag["bookmarks"]] == [bid]
    assert by_tag["search_mode"] == "fts"
    listed = await call(mcp_client, "list_bookmarks", tag=TAG)
    assert [b["id"] for b in listed["bookmarks"]] == [bid]

    got = await call(mcp_client, "get_bookmark", bookmark_id=bid, max_chars=500)
    assert RUN in got["content"]["text"]

    lst = await call(mcp_client, "create_list", name=LIST, icon="🧪")
    added = await call(mcp_client, "add_to_list", bookmark_id=bid, list=LIST)
    assert added["added_to"]["id"] == lst["id"]
    in_list = await call(mcp_client, "list_bookmarks", list=lst["id"])
    assert [b["id"] for b in in_list["bookmarks"]] == [bid]
    assert {"id": lst["id"], "name": LIST} in (
        await call(mcp_client, "get_bookmark", bookmark_id=bid, include_content=False)
    )["lists"]
    await call(mcp_client, "remove_from_list", bookmark_id=bid, list=LIST)
    assert (await call(mcp_client, "list_bookmarks", list=LIST))["bookmarks"] == []
    assert any(x["id"] == lst["id"] for x in (await call(mcp_client, "list_lists"))["lists"])

    updated = await call(
        mcp_client,
        "update_bookmark",
        bookmark_id=bid,
        title=f"Renamed {RUN}",
        note="my note",
        favourited=True,
        archived=True,
    )
    assert (updated["title"], updated["note"], updated["favourited"], updated["archived"]) == (
        f"Renamed {RUN}",
        "my note",
        True,
        True,
    )

    extra = f"{TAG}-extra"
    tagged = await call(mcp_client, "tag_bookmark", bookmark_id=bid, tags=[extra])
    assert extra in tagged["tags"]
    tags = await call(mcp_client, "list_tags", name_contains=RUN)
    assert {t["name"] for t in tags["tags"]} >= {TAG, extra}
    untagged = await call(mcp_client, "untag_bookmark", bookmark_id=bid, tags=[extra, "nope"])
    assert untagged["removed"] == [extra] and untagged["not_on_bookmark"] == ["nope"]

    assert (await call(mcp_client, "list_highlights", bookmark_id=bid))["highlights"] == []
    assert "highlights" in await call(mcp_client, "list_highlights", limit=1)

    deleted = await call(mcp_client, "delete_bookmark", bookmark_id=bid)
    assert deleted == {"deleted": bid, "title": f"Renamed {RUN}"}
    gone = await mcp_client.call_tool("get_bookmark", {"bookmark_id": bid}, raise_on_error=False)
    assert gone.is_error


async def test_save_url_is_deduplicated(mcp_client: Client[Any]) -> None:
    url = os.environ.get("KARAKEEP_IT_URL") or f"https://example.com/?mcp-it={RUN}"
    first = await call(mcp_client, "save_url", url=url, tags=[TAG], note="it")
    again = await call(mcp_client, "save_url", url=url)
    assert again["id"] == first["id"] and again["already_saved"] is True
    assert "already_saved" not in first

    if os.environ.get("KARAKEEP_IT_URL"):

        async def crawled() -> bool:
            got = await call(mcp_client, "get_bookmark", bookmark_id=first["id"], max_chars=500)
            return isinstance(got.get("content"), dict)

        await eventually(crawled, seconds=120)
    await call(mcp_client, "delete_bookmark", bookmark_id=first["id"])
