"""Prepare a fresh Karakeep container for development and integration tests.

Signs up a user (the first user of a new instance) with email and password and
exchanges the credentials for an API key, as Karakeep's browser extension does.

Usage:
    uv run python dev/bootstrap_karakeep.py [--url http://localhost:3010] [--github-env]

Prints `KARAKEEP_URL=...` and `KARAKEEP_API_KEY=...` lines (shell-eval friendly).
With --github-env the values are appended to $GITHUB_ENV instead. Only use it on
throwaway instances: it needs password sign-up enabled.
"""

from __future__ import annotations

import argparse
import os
import secrets

import httpx

EMAIL = "mcp-agent@example.com"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("KARAKEEP_URL", "http://localhost:3010"))
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args()
    password = secrets.token_urlsafe(18)

    with httpx.Client(base_url=args.url, timeout=30, headers={"origin": args.url}) as client:
        signup = client.post(
            "/api/auth/sign-up/email",
            json={"name": "MCP Agent", "email": EMAIL, "password": password},
        )
        if signup.status_code >= 400:
            raise SystemExit(f"sign-up failed ({signup.status_code}): {signup.text[:300]}")
        exchange = client.post(
            "/api/trpc/apiKeys.exchange",
            json={"json": {"keyName": "mcp-dev", "email": EMAIL, "password": password}},
        )
        exchange.raise_for_status()
        key = exchange.json()["result"]["data"]["json"]["key"]

    lines = [f"KARAKEEP_URL={args.url}", f"KARAKEEP_API_KEY={key}"]
    if args.github_env:
        print(f"::add-mask::{key}")
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
