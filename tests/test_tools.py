from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client, FastMCP

from .conftest import KARAKEEP

READ_TOOLS = {
    "search_bookmarks",
    "list_bookmarks",
    "get_bookmark",
    "list_lists",
    "list_tags",
    "list_highlights",
}
WRITE_TOOLS = {
    "save_url",
    "save_note",
    "update_bookmark",
    "tag_bookmark",
    "untag_bookmark",
    "add_to_list",
    "remove_from_list",
    "create_list",
    "delete_bookmark",
}


def bookmark(bid: str = "b1", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": bid,
        "createdAt": "2026-11-05T17:58:12.345Z",
        "modifiedAt": "2026-11-05T18:00:00.000Z",
        "title": None,
        "archived": False,
        "favourited": False,
        "taggingStatus": "success",
        "summarizationStatus": None,
        "embeddingStatus": "success",
        "note": None,
        "summary": None,
        "source": "api",
        "userId": "u1",
        "tags": [
            {"id": "t1", "name": "python", "attachedBy": "human"},
            {"id": "t2", "name": "Web Dev", "attachedBy": "ai"},
        ],
        "content": {
            "type": "link",
            "url": "https://example.com/post",
            "title": "A Post",
            "description": "About things",
            "imageUrl": "https://example.com/i.png",
            "imageAssetId": "a1",
            "screenshotAssetId": "a2",
            "htmlContent": None,
            "crawlStatus": "success",
            "crawledAt": "2026-11-05T17:58:30.000Z",
            "author": "Ann",
            "publisher": None,
            "readerViewScore": 87,
        },
        "assets": [{"id": "a1", "assetType": "bannerImage"}],
    }
    data.update(overrides)
    return data


LISTS = {
    "lists": [
        {"id": "l1", "name": "Reading", "icon": "📚", "parentId": None, "type": "manual"},
        {
            "id": "l2",
            "name": "Favs",
            "icon": "⭐",
            "parentId": None,
            "type": "smart",
            "query": "is:fav",
        },
    ]
}


async def call(client: Client[Any], tool: str, /, **args: Any) -> Any:
    result = await client.call_tool(tool, args, raise_on_error=False)
    text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
    if result.is_error:
        return {"error": text}
    return json.loads(text)


def body(route: respx.Route, index: int = -1) -> Any:
    return json.loads(route.calls[index].request.content)


def params(route: respx.Route, index: int = -1) -> dict[str, str]:
    return dict(route.calls[index].request.url.params)


async def test_tool_sets(server_factory: Callable[..., FastMCP]) -> None:
    async with Client(server_factory()) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert set(tools) == READ_TOOLS | WRITE_TOOLS
    for name in READ_TOOLS:
        assert tools[name].annotations and tools[name].annotations.read_only_hint is True
    for name in WRITE_TOOLS:
        assert tools[name].annotations and tools[name].annotations.read_only_hint is False
        assert tools[name].description
    assert tools["delete_bookmark"].annotations.destructive_hint is True  # type: ignore[union-attr]
    destructive = {n for n, t in tools.items() if t.annotations and t.annotations.destructive_hint}
    assert destructive == {"delete_bookmark"}
    assert tools["save_url"].annotations.open_world_hint is True  # type: ignore[union-attr]

    async with Client(server_factory(read_only=True)) as c:
        assert {t.name for t in await c.list_tools()} == READ_TOOLS


def test_no_dangerous_surface() -> None:
    """Guard against tools for admin, users, keys, backups, feeds or raw assets."""
    banned = ("admin", "user", "key", "backup", "feed", "webhook", "asset", "summarize", "job")
    for name in READ_TOOLS | WRITE_TOOLS:
        assert not any(b in name for b in banned), name


async def test_sends_api_key_and_no_redirects(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/lists").respond(302, headers={"location": "https://evil.test/"})
    result = await call(client, "list_lists")
    assert "redirected" in result["error"]
    request = route.calls[-1].request
    assert request.headers["authorization"] == "Bearer karakeep-key"
    assert request.headers["user-agent"].startswith("karakeep-agent-mcp/")


async def test_search_defaults_to_hybrid(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/bookmarks/search").respond(
        json={"bookmarks": [bookmark()], "nextCursor": "c2"}
    )
    result = await call(client, "search_bookmarks", query="rust async")
    assert params(route) == {
        "q": "rust async",
        "searchMode": "hybrid",
        "sortOrder": "relevance",
        "limit": "10",
        "includeContent": "false",
    }
    assert result == {
        "bookmarks": [
            {
                "id": "b1",
                "type": "link",
                "title": "A Post",
                "url": "https://example.com/post",
                "description": "About things",
                "tags": ["python", "Web Dev"],
                "created": "2026-11-05",
                "link": f"{KARAKEEP}/dashboard/preview/b1",
            }
        ],
        "next_cursor": "c2",
    }


async def test_search_by_date_uses_fts(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/bookmarks/search").respond(json={"bookmarks": [], "nextCursor": None})
    result = await call(client, "search_bookmarks", query="#python", sort="newest", cursor="c2")
    assert params(route) | {} == {
        "q": "#python",
        "searchMode": "fts",
        "sortOrder": "desc",
        "limit": "10",
        "cursor": "c2",
        "includeContent": "false",
    }
    assert result == {"bookmarks": [], "search_mode": "fts"}


async def test_search_semantic(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/bookmarks/search").respond(json={"bookmarks": [], "nextCursor": None})
    await call(client, "search_bookmarks", query="calm music", search_mode="semantic", limit=50)
    assert params(route)["searchMode"] == "semantic"
    assert params(route)["limit"] == "50"
    assert "error" in await call(client, "search_bookmarks", query="x", limit=51)
    assert "error" in await call(client, "search_bookmarks", query="")


async def test_list_bookmarks_filters(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/bookmarks").respond(json={"bookmarks": [], "nextCursor": None})
    await call(client, "list_bookmarks", favourited=True, sort="oldest")
    assert params(route) == {
        "sortOrder": "asc",
        "limit": "20",
        "includeContent": "false",
        "favourited": "true",
    }


async def test_list_bookmarks_in_list_by_name(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").respond(json=LISTS)
    route = karakeep.get("/lists/l1/bookmarks").respond(
        json={"bookmarks": [bookmark()], "nextCursor": None}
    )
    result = await call(client, "list_bookmarks", list="reading")
    assert route.called
    assert result["list"] == {"id": "l1", "name": "Reading"}
    assert [b["id"] for b in result["bookmarks"]] == ["b1"]


async def test_list_bookmarks_by_tag_name(client: Client[Any], karakeep: respx.Router) -> None:
    tags = karakeep.get("/tags").respond(
        json={"tags": [{"id": "t9", "name": "Web Development"}, {"id": "t2", "name": "web dev"}]}
    )
    route = karakeep.get("/tags/t2/bookmarks").respond(json={"bookmarks": [], "nextCursor": None})
    assert await call(client, "list_bookmarks", tag="Web Dev") == {"bookmarks": []}
    assert params(tags)["nameContains"] == "Web Dev"
    assert route.called


async def test_list_bookmarks_unknown_tag(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/tags").respond(json={"tags": [], "nextCursor": None})
    karakeep.get("/tags/nope").respond(404, json={"code": "NOT_FOUND", "message": "Tag not found"})
    result = await call(client, "list_bookmarks", tag="nope")
    assert "No tag named 'nope'" in result["error"]


async def test_list_bookmarks_rejects_conflicting_filters(client: Client[Any]) -> None:
    assert "not both" in (await call(client, "list_bookmarks", list="a", tag="b"))["error"]
    result = await call(client, "list_bookmarks", list="a", favourited=True)
    assert "search_bookmarks" in result["error"]


async def test_get_bookmark_with_content_and_lists(
    client: Client[Any], karakeep: respx.Router
) -> None:
    detail = karakeep.get("/bookmarks/b1").respond(
        json=bookmark(note="mine", summary="Short.", title="My title")
    )
    karakeep.get("/bookmarks/b1/lists").respond(json={"lists": [LISTS["lists"][0]]})
    content = karakeep.get("/bookmarks/b1/content").respond(
        json={
            "bookmarkId": "b1",
            "bookmarkType": "link",
            "format": "markdown",
            "content": "# Hello\n\nWorld",
            "contentVersion": "sha256:x",
            "range": {"start": 0, "end": 14, "total": 9000},
            "nextCursor": "next",
            "truncated": True,
        }
    )
    result = await call(client, "get_bookmark", bookmark_id="b1", max_chars=1000)
    assert params(detail) == {"includeContent": "false"}
    assert params(content) == {"maxChars": "1000", "format": "markdown"}
    assert result == {
        "id": "b1",
        "type": "link",
        "title": "My title",
        "custom_title": True,
        "url": "https://example.com/post",
        "description": "About things",
        "author": "Ann",
        "summary": "Short.",
        "note": "mine",
        "tags": [{"name": "python", "by": "human"}, {"name": "Web Dev", "by": "ai"}],
        "favourited": False,
        "archived": False,
        "created": "2026-11-05T17:58Z",
        "modified": "2026-11-05T18:00Z",
        "link": f"{KARAKEEP}/dashboard/preview/b1",
        "lists": [{"id": "l1", "name": "Reading"}],
        "content": {
            "text": "# Hello\n\nWorld",
            "format": "markdown",
            "chars": "0-14 of 9000",
            "next_cursor": "next",
        },
    }


async def test_get_bookmark_next_chunk_only(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/bookmarks/b1/content").respond(
        json={
            "format": "markdown",
            "content": "more",
            "range": {"start": 14, "end": 18, "total": 18},
            "nextCursor": None,
            "truncated": False,
        }
    )
    result = await call(client, "get_bookmark", bookmark_id="b1", content_cursor="next")
    assert params(route) == {"maxChars": "8000", "cursor": "next"}
    assert result == {
        "id": "b1",
        "content": {"text": "more", "format": "markdown", "chars": "14-18 of 18"},
    }


async def test_get_bookmark_while_crawling(client: Client[Any], karakeep: respx.Router) -> None:
    pending = bookmark(taggingStatus="pending")
    pending["content"] = {
        "type": "link",
        "url": "https://example.com/new",
        "crawlStatus": "pending",
    }
    karakeep.get("/bookmarks/b1").respond(json=pending)
    karakeep.get("/bookmarks/b1/lists").respond(json={"lists": []})
    karakeep.get("/bookmarks/b1/content").respond(
        json={"format": "markdown", "content": "", "range": {"start": 0, "end": 0, "total": 0}}
    )
    result = await call(client, "get_bookmark", bookmark_id="b1")
    assert result["content"].startswith("not available yet")
    assert result["pending"] == {"crawl": "pending", "tagging": "pending"}


async def test_get_bookmark_content_error_is_soft(
    client: Client[Any], karakeep: respx.Router
) -> None:
    karakeep.get("/bookmarks/b1").respond(json=bookmark())
    karakeep.get("/bookmarks/b1/lists").respond(json={"lists": []})
    karakeep.get("/bookmarks/b1/content").respond(
        409, json={"code": "CONFLICT", "message": "no readable content"}
    )
    result = await call(client, "get_bookmark", bookmark_id="b1")
    assert result["content"] == "not available (CONFLICT)"


async def test_get_bookmark_without_content(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/bookmarks/b1").respond(json=bookmark())
    karakeep.get("/bookmarks/b1/lists").respond(json={"lists": []})
    content = karakeep.get("/bookmarks/b1/content")
    result = await call(client, "get_bookmark", bookmark_id="b1", include_content=False)
    assert "content" not in result and not content.called


async def test_get_bookmark_not_found(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/bookmarks/zz").respond(404, json={"code": "NOT_FOUND", "message": "nope"})
    karakeep.get("/bookmarks/zz/lists").respond(404, json={"code": "NOT_FOUND", "message": "nope"})
    karakeep.get("/bookmarks/zz/content").respond(404, json={"code": "NOT_FOUND", "message": "x"})
    result = await call(client, "get_bookmark", bookmark_id="zz")
    assert "HTTP 404" in result["error"]


async def test_ids_are_single_path_segments(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/bookmarks/..%2Fusers%2Fme").respond(json=bookmark())
    karakeep.get("/bookmarks/..%2Fusers%2Fme/lists").respond(json={"lists": []})
    await call(client, "get_bookmark", bookmark_id="../users/me", include_content=False)
    assert route.called
    assert "Missing" in (await call(client, "get_bookmark", bookmark_id=".."))["error"]


async def test_list_lists(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").respond(json=LISTS)
    assert await call(client, "list_lists") == {
        "lists": [
            {"id": "l1", "name": "Reading", "icon": "📚"},
            {"id": "l2", "name": "Favs", "icon": "⭐", "smart_query": "is:fav"},
        ]
    }


async def test_list_tags(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.get("/tags").respond(
        json={
            "tags": [
                {
                    "id": "t1",
                    "name": "python",
                    "numBookmarks": 5,
                    "numBookmarksByAttachedType": {"ai": 2, "human": 3},
                },
                {"id": "t3", "name": "old", "numBookmarks": 0, "numBookmarksByAttachedType": {}},
            ],
            "nextCursor": "c",
        }
    )
    result = await call(client, "list_tags", name_contains="py", sort="name")
    assert params(route) == {"nameContains": "py", "sort": "name", "limit": "100"}
    assert result == {
        "tags": [
            {"id": "t1", "name": "python", "bookmarks": 5, "by_ai": 2},
            {"id": "t3", "name": "old", "bookmarks": 0},
        ],
        "next_cursor": "c",
    }


async def test_list_highlights(client: Client[Any], karakeep: respx.Router) -> None:
    hl = {
        "id": "h1",
        "bookmarkId": "b1",
        "startOffset": 1,
        "endOffset": 9,
        "color": "yellow",
        "text": "quoted",
        "note": None,
        "userId": "u1",
        "createdAt": "2026-11-01T10:00:00Z",
    }
    karakeep.get("/highlights").respond(json={"highlights": [hl], "nextCursor": "n"})
    karakeep.get("/bookmarks/b1/highlights").respond(
        json={"highlights": [hl | {"color": "red", "note": "why"}]}
    )
    assert await call(client, "list_highlights") == {
        "highlights": [
            {"id": "h1", "bookmark_id": "b1", "text": "quoted", "created": "2026-11-01"}
        ],
        "next_cursor": "n",
    }
    one = await call(client, "list_highlights", bookmark_id="b1")
    assert one["highlights"][0] | {} == {
        "id": "h1",
        "bookmark_id": "b1",
        "text": "quoted",
        "note": "why",
        "color": "red",
        "created": "2026-11-01",
    }


async def test_save_url_with_tags_and_list(client: Client[Any], karakeep: respx.Router) -> None:
    new = bookmark("b2", taggingStatus="pending", tags=[])
    create = karakeep.post("/bookmarks").respond(201, json=new)
    tag = karakeep.post("/bookmarks/b2/tags").respond(json={"attached": ["t1"]})
    karakeep.get("/lists").respond(json=LISTS)
    add = karakeep.put("/lists/l1/bookmarks/b2").respond(204)
    refetch = karakeep.get("/bookmarks/b2").respond(
        json=new | {"tags": [{"id": "t1", "name": "python", "attachedBy": "human"}]}
    )
    result = await call(
        client,
        "save_url",
        url=" https://example.com/post ",
        note="read later",
        tags=["python", "python", " "],
        list="Reading",
    )
    assert body(create) == {
        "type": "link",
        "url": "https://example.com/post",
        "note": "read later",
        "favourited": False,
        "archived": False,
    }
    assert body(tag) == {"tags": [{"tagName": "python", "attachedBy": "human"}]}
    assert add.called and refetch.called
    assert result["tags"] == [{"name": "python", "by": "human"}]
    assert result["added_to_list"] == {"id": "l1", "name": "Reading"}
    assert result["pending"] == {"tagging": "pending"}
    assert "hint" in result and "already_saved" not in result


async def test_save_url_already_saved(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.post("/bookmarks").respond(200, json=bookmark())
    result = await call(client, "save_url", url="https://example.com/post", title="ignored")
    assert result["already_saved"] is True
    assert result["id"] == "b1"


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "example.com"])
async def test_save_url_rejects_non_http(
    client: Client[Any], karakeep: respx.Router, url: str
) -> None:
    create = karakeep.post("/bookmarks")
    assert "Not an http(s) URL" in (await call(client, "save_url", url=url))["error"]
    assert not create.called


async def test_save_url_smart_list_rejected(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.post("/bookmarks").respond(201, json=bookmark("b2"))
    karakeep.get("/lists").respond(json=LISTS)
    karakeep.put("/lists/l2/bookmarks/b2").respond(
        400, json={"code": "BAD_REQUEST", "message": "Smart lists cannot be added to"}
    )
    result = await call(client, "save_url", url="https://e.com", list="Favs")
    assert "Smart lists" in result["error"]


async def test_save_note(client: Client[Any], karakeep: respx.Router) -> None:
    note = bookmark(
        "n1",
        title="Idea",
        content={"type": "text", "text": "Sourdough at 78% hydration", "sourceUrl": None},
    )
    create = karakeep.post("/bookmarks").respond(201, json=note)
    result = await call(
        client, "save_note", text="Sourdough at 78% hydration", title="Idea", source_url=None
    )
    assert body(create) == {"type": "text", "text": "Sourdough at 78% hydration", "title": "Idea"}
    assert result["type"] == "text" and result["title"] == "Idea"


async def test_update_bookmark(client: Client[Any], karakeep: respx.Router) -> None:
    patch = karakeep.patch("/bookmarks/b1").respond(json={"id": "b1"})
    karakeep.get("/bookmarks/b1").respond(json=bookmark(archived=True))
    result = await call(client, "update_bookmark", bookmark_id="b1", title="", archived=True)
    assert body(patch) == {"title": None, "archived": True}
    assert result["archived"] is True
    assert "Nothing to change" in (await call(client, "update_bookmark", bookmark_id="b1"))["error"]
    await call(client, "update_bookmark", bookmark_id="b1", note="", summary=" New ")
    assert body(patch) == {"summary": "New", "note": ""}


async def test_tag_bookmark(client: Client[Any], karakeep: respx.Router) -> None:
    route = karakeep.post("/bookmarks/b1/tags").respond(json={"attached": ["t5"]})
    karakeep.get("/bookmarks/b1").respond(json=bookmark())
    result = await call(client, "tag_bookmark", bookmark_id="b1", tags=["to read", "python"])
    assert body(route) == {
        "tags": [
            {"tagName": "to read", "attachedBy": "human"},
            {"tagName": "python", "attachedBy": "human"},
        ]
    }
    assert result == {"id": "b1", "tags": ["python", "Web Dev"]}
    assert "error" in await call(client, "tag_bookmark", bookmark_id="b1", tags=[])


async def test_untag_bookmark(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/bookmarks/b1").respond(json=bookmark())
    route = karakeep.delete("/bookmarks/b1/tags").respond(json={"detached": ["t2"]})
    result = await call(client, "untag_bookmark", bookmark_id="b1", tags=["web dev", "missing"])
    assert body(route) == {"tags": [{"tagId": "t2"}]}
    assert result == {
        "id": "b1",
        "removed": ["Web Dev"],
        "not_on_bookmark": ["missing"],
        "tags": ["python"],
    }


async def test_untag_nothing_matching_skips_call(
    client: Client[Any], karakeep: respx.Router
) -> None:
    karakeep.get("/bookmarks/b1").respond(json=bookmark())
    route = karakeep.delete("/bookmarks/b1/tags")
    result = await call(client, "untag_bookmark", bookmark_id="b1", tags=["nope"])
    assert not route.called
    assert result["not_on_bookmark"] == ["nope"]


async def test_add_and_remove_from_list(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").respond(json=LISTS)
    add = karakeep.put("/lists/l1/bookmarks/b1").respond(204)
    remove = karakeep.delete("/lists/l1/bookmarks/b1").respond(204)
    assert await call(client, "add_to_list", bookmark_id="b1", list="l1") == {
        "bookmark_id": "b1",
        "added_to": {"id": "l1", "name": "Reading"},
    }
    assert await call(client, "remove_from_list", bookmark_id="b1", list="READING") == {
        "bookmark_id": "b1",
        "removed_from": {"id": "l1", "name": "Reading"},
    }
    assert add.called and remove.called
    smart = await call(client, "add_to_list", bookmark_id="b1", list="Favs")
    assert "smart list" in smart["error"]
    unknown = await call(client, "add_to_list", bookmark_id="b1", list="Nope")
    assert "list_lists" in unknown["error"]


async def test_ambiguous_list_name(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").respond(
        json={
            "lists": [
                {"id": "l1", "name": "Ideas", "parentId": None},
                {"id": "l7", "name": "ideas", "parentId": "l3"},
            ]
        }
    )
    result = await call(client, "add_to_list", bookmark_id="b1", list="Ideas")
    assert "l1" in result["error"] and "l7" in result["error"]


async def test_create_list(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").respond(json=LISTS)
    route = karakeep.post("/lists").respond(
        201,
        json={"id": "l9", "name": "Japan", "icon": "🗾", "parentId": "l1", "type": "manual"},
    )
    result = await call(client, "create_list", name=" Japan ", icon="🗾", parent="Reading")
    assert body(route) == {"name": "Japan", "icon": "🗾", "parentId": "l1"}
    assert result == {"id": "l9", "name": "Japan", "icon": "🗾", "parent_id": "l1"}
    await call(client, "create_list", name="Recipes", smart_query="#recipes")
    assert body(route) == {"name": "Recipes", "icon": "📁", "type": "smart", "query": "#recipes"}


async def test_delete_bookmark(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/bookmarks/b1").respond(json=bookmark())
    route = karakeep.delete("/bookmarks/b1").respond(204)
    assert await call(client, "delete_bookmark", bookmark_id="b1") == {
        "deleted": "b1",
        "title": "A Post",
    }
    assert route.called


async def test_karakeep_errors_are_readable(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").respond(401, text="Unauthorized")
    result = await call(client, "list_lists")
    assert result["error"] == (
        "Karakeep returned HTTP 401 for GET /lists: Unauthorized (check the Karakeep API key)"
    )


async def test_unreachable_karakeep(client: Client[Any], karakeep: respx.Router) -> None:
    karakeep.get("/lists").mock(side_effect=httpx.ConnectError("boom"))
    assert (await call(client, "list_lists"))["error"] == "Could not reach Karakeep: ConnectError"


async def test_public_url_used_for_links(
    server_factory: Callable[..., FastMCP], karakeep: respx.Router
) -> None:
    karakeep.get("/bookmarks").respond(json={"bookmarks": [bookmark()], "nextCursor": None})
    async with Client(server_factory(karakeep_public_url="https://keep.example.com")) as c:
        result = await call(c, "list_bookmarks")
    assert result["bookmarks"][0]["link"] == "https://keep.example.com/dashboard/preview/b1"
