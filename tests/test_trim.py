from __future__ import annotations

import json

from karakeep_agent_mcp import trim

from .test_tools import bookmark


def test_compact_keeps_false_and_zero() -> None:
    assert trim.compact({"a": None, "b": "", "c": [], "d": {}, "e": False, "f": 0}) == {
        "e": False,
        "f": 0,
    }


def test_truncate() -> None:
    assert trim.truncate("  short ", 10) == "short"
    assert trim.truncate("a" * 20, 10) == "a" * 9 + "…"
    assert trim.truncate(None, 5) is None


def test_title_falls_back_to_page_and_file_name() -> None:
    assert trim.title_of(bookmark(title="Mine")) == "Mine"
    assert trim.title_of(bookmark()) == "A Post"
    asset = bookmark(
        content={"type": "asset", "assetType": "pdf", "assetId": "x", "fileName": "f.pdf"}
    )
    assert trim.title_of(asset) == "f.pdf"
    assert trim.bookmark_summary(asset, base_url="http://k")["type"] == "pdf"


def test_summary_is_much_smaller_than_raw() -> None:
    raw = bookmark(
        summary="s" * 2000,
        note="n" * 2000,
        content=bookmark()["content"] | {"htmlContent": "<p>" + "x" * 20000 + "</p>"},
    )
    summary = trim.bookmark_summary(raw, base_url="http://k")
    assert len(json.dumps(summary)) * 10 < len(json.dumps(raw))
    assert len(summary["summary"]) == 300 and len(summary["note"]) == 300
    assert "htmlContent" not in json.dumps(trim.bookmark_detail(raw, base_url="http://k"))


def test_status_only_reports_unfinished_work() -> None:
    b = bookmark(taggingStatus="failure", embeddingStatus="pending", summarizationStatus=None)
    assert trim.bookmark_summary(b, base_url="http://k")["pending"] == {
        "tagging": "failure",
        "embedding": "pending",
    }
    assert "pending" not in trim.bookmark_summary(bookmark(), base_url="http://k")


def test_text_note_summary() -> None:
    note = bookmark(content={"type": "text", "text": "t" * 500, "sourceUrl": "https://src"})
    out = trim.bookmark_summary(note, base_url="http://k")
    assert out["type"] == "text" and out["url"] == "https://src" and len(out["text"]) == 300


def test_list_item_hides_owner_role() -> None:
    assert trim.list_item({"id": "l", "name": "n", "userRole": "owner", "type": "manual"}) == {
        "id": "l",
        "name": "n",
    }
    assert trim.list_item({"id": "l", "name": "n", "userRole": "viewer"})["role"] == "viewer"
