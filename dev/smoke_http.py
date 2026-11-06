"""Exercise every tool over streamable HTTP against a running server.

    uv run python dev/smoke_http.py http://127.0.0.1:8000/mcp <bearer-token>

Creates a note, a link bookmark, a tag and a list, then deletes the bookmarks.
Tags and lists have no delete tools; with KARAKEEP_URL and KARAKEEP_API_KEY set
the script removes them through the Karakeep API, otherwise it prints them.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport


async def main(url: str, token: str | None) -> None:
    run = uuid.uuid4().hex[:8]
    tag, list_name = f"mcp-smoke-{run}", f"MCP smoke {run}"
    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with Client(StreamableHttpTransport(url, headers=headers)) as client:

        async def call(tool: str, /, **args: Any) -> Any:
            result = await client.call_tool(tool, args, raise_on_error=False)
            text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
            status = "ERROR" if result.is_error else "ok"
            print(f"{status:5} {tool}: {text[:140]}")
            if result.is_error:
                raise SystemExit(f"{tool} failed")
            return json.loads(text)

        tools = await client.list_tools()
        print(f"{len(tools)} tools: {', '.join(t.name for t in tools)}")

        note = await call(
            "save_note", text=f"Smoke test {run}: tomatoes like warm nights.", tags=[tag]
        )
        link = await call(
            "save_url", url=f"https://example.com/?smoke={run}", note="smoke", tags=[tag]
        )
        await call("save_url", url=f"https://example.com/?smoke={run}")
        await call("create_list", name=list_name, icon="🧪")
        await call("add_to_list", bookmark_id=link["id"], list=list_name)
        await call("list_bookmarks", list=list_name)
        await call("list_bookmarks", limit=3)
        await call("list_lists")
        await call("list_tags", name_contains=run)
        for _ in range(30):
            found = await call("search_bookmarks", query=f"#{tag}", sort="newest")
            if len(found["bookmarks"]) == 2:
                break
            await asyncio.sleep(2)
        else:
            raise SystemExit("bookmarks never became searchable")
        await call("search_bookmarks", query="plants that grow in summer heat")
        await call("get_bookmark", bookmark_id=note["id"], max_chars=500)
        await call("update_bookmark", bookmark_id=link["id"], title=f"Smoke {run}", archived=True)
        await call("tag_bookmark", bookmark_id=link["id"], tags=[f"{tag}-b"])
        await call("untag_bookmark", bookmark_id=link["id"], tags=[f"{tag}-b"])
        await call("remove_from_list", bookmark_id=link["id"], list=list_name)
        await call("list_highlights", limit=3)
        await call("delete_bookmark", bookmark_id=link["id"])
        await call("delete_bookmark", bookmark_id=note["id"])
        print("all tools ok")

    base, key = os.environ.get("KARAKEEP_URL"), os.environ.get("KARAKEEP_API_KEY")
    if not (base and key):
        print(f"left behind: tags {tag!r}, {tag + '-b'!r}; list {list_name!r}")
        return
    async with httpx.AsyncClient(
        base_url=f"{base.rstrip('/')}/api/v1", headers={"Authorization": f"Bearer {key}"}
    ) as http:
        for t in (await http.get("/tags", params={"nameContains": run})).json()["tags"]:
            await http.delete(f"/tags/{t['id']}")
        for lst in (await http.get("/lists")).json()["lists"]:
            if run in lst["name"]:
                await http.delete(f"/lists/{lst['id']}")
    print("cleaned up tags and list")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
