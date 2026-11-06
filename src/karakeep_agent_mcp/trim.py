"""Reduce Karakeep's JSON to what an agent needs.

Bookmarks carry asset ids, crawler scores, user ids, AI status flags and, with
content, whole HTML pages. Agents pay for every token, so each shape below keeps
only fields that help answer a question or make a follow-up call.
"""

from __future__ import annotations

from typing import Any

Obj = dict[str, Any]

_EMPTY: tuple[Any, ...] = (None, "", [], {})


def compact(d: Obj) -> Obj:
    """Drop keys whose value is empty. False and 0 are kept."""
    return {k: v for k, v in d.items() if not any(v is e or v == e for e in _EMPTY)}


def truncate(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _date(value: Any) -> str | None:
    return value[:10] if isinstance(value, str) and len(value) >= 10 else None


def _minute(value: Any) -> str | None:
    # 2026-11-05T17:58:12.345Z -> 2026-11-05T17:58Z (Karakeep timestamps are UTC)
    if isinstance(value, str) and len(value) >= 16:
        return value[:16] + "Z"
    return None


def bookmark_link(base_url: str, bookmark_id: str | None) -> str | None:
    return f"{base_url}/dashboard/preview/{bookmark_id}" if bookmark_id else None


def title_of(b: Obj) -> str | None:
    content = b.get("content") or {}
    return b.get("title") or content.get("title") or content.get("fileName")


def _kind(content: Obj) -> str | None:
    kind = content.get("type")
    if kind == "asset":
        return content.get("assetType") or "asset"  # image | pdf
    return kind if isinstance(kind, str) else None


def _status(b: Obj) -> Obj:
    """Only unfinished or failed background work; success is the boring default."""
    content = b.get("content") or {}
    states = {
        "crawl": content.get("crawlStatus"),
        "tagging": b.get("taggingStatus"),
        "embedding": b.get("embeddingStatus"),
        "summary": b.get("summarizationStatus"),
    }
    return {k: v for k, v in states.items() if v in ("pending", "failure")}


def bookmark_summary(b: Obj, *, base_url: str) -> Obj:
    content = b.get("content") or {}
    return compact(
        {
            "id": b.get("id"),
            "type": _kind(content),
            "title": truncate(title_of(b), 200),
            "url": content.get("url") or content.get("sourceUrl"),
            "description": truncate(content.get("description"), 200),
            "summary": truncate(b.get("summary"), 300),
            "text": truncate(content.get("text"), 300) if content.get("type") == "text" else None,
            "note": truncate(b.get("note"), 300),
            "tags": [t.get("name") for t in b.get("tags") or [] if t.get("name")],
            "favourited": b.get("favourited") or None,
            "archived": b.get("archived") or None,
            "created": _date(b.get("createdAt")),
            "pending": _status(b),
            "link": bookmark_link(base_url, b.get("id")),
        }
    )


def bookmark_detail(b: Obj, *, base_url: str) -> Obj:
    content = b.get("content") or {}
    return compact(
        {
            "id": b.get("id"),
            "type": _kind(content),
            "title": title_of(b),
            "custom_title": b.get("title") is not None or None,
            "url": content.get("url") or content.get("sourceUrl"),
            "description": content.get("description"),
            "author": content.get("author"),
            "publisher": content.get("publisher"),
            "published": _date(content.get("datePublished")),
            "summary": b.get("summary"),
            "note": b.get("note"),
            "tags": [
                compact({"name": t.get("name"), "by": t.get("attachedBy")})
                for t in b.get("tags") or []
                if t.get("name")
            ],
            "favourited": b.get("favourited"),
            "archived": b.get("archived"),
            "created": _minute(b.get("createdAt")),
            "modified": _minute(b.get("modifiedAt")),
            "file_name": content.get("fileName"),
            "pending": _status(b),
            "link": bookmark_link(base_url, b.get("id")),
        }
    )


def bookmark_page(data: Obj, *, base_url: str) -> Obj:
    page: Obj = {
        "bookmarks": [bookmark_summary(b, base_url=base_url) for b in data.get("bookmarks") or []]
    }
    if data.get("nextCursor"):
        page["next_cursor"] = data["nextCursor"]
    return page


def readable_content(c: Obj) -> Obj:
    rng = c.get("range") or {}
    return compact(
        {
            "text": c.get("content"),
            "format": c.get("format"),
            "chars": f"{rng.get('start', 0)}-{rng.get('end', 0)} of {rng.get('total', 0)}"
            if rng
            else None,
            "next_cursor": c.get("nextCursor") if c.get("truncated") else None,
        }
    )


def list_item(lst: Obj) -> Obj:
    return compact(
        {
            "id": lst.get("id"),
            "name": lst.get("name"),
            "icon": lst.get("icon"),
            "description": lst.get("description"),
            "parent_id": lst.get("parentId"),
            "smart_query": lst.get("query") if lst.get("type") == "smart" else None,
            "role": lst.get("userRole") if lst.get("userRole") != "owner" else None,
        }
    )


def tag_item(t: Obj) -> Obj:
    by = t.get("numBookmarksByAttachedType") or {}
    return compact(
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "bookmarks": t.get("numBookmarks", 0),
            "by_ai": by.get("ai") or None,
        }
    )


def highlight_item(h: Obj) -> Obj:
    return compact(
        {
            "id": h.get("id"),
            "bookmark_id": h.get("bookmarkId"),
            "text": h.get("text"),
            "note": h.get("note"),
            "color": h.get("color") if h.get("color") != "yellow" else None,
            "created": _date(h.get("createdAt")),
        }
    )
