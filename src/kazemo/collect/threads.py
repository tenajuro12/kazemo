"""Threads collector using the official Threads API (graph.threads.net).

Needs ``THREADS_ACCESS_TOKEN``. Keyword search requires the app to have
the ``threads_keyword_search`` permission.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..config import require
from . import base
from .base import Collector, HttpGet, Post, to_post

BASE = "https://graph.threads.net/v1.0"
FIELDS = "id,text,timestamp,media_type"


class ThreadsCollector(Collector):
    name = "threads"

    def __init__(self, mode: str = "search", token: str | None = None, http: HttpGet | None = None, delay=1.0):
        if mode not in ("search", "replies"):
            raise ValueError("mode must be 'search' or 'replies'")
        self.mode = mode
        self.token = token or require("THREADS_ACCESS_TOKEN", "Create one in Meta for Developers → Threads API.")
        self.http = http or (lambda url, params: base.http_get_json(url, params))
        self.delay = delay

    def _first_page(self, target: str) -> tuple[str, dict]:
        if self.mode == "search":
            return f"{BASE}/keyword_search", {"q": target, "search_type": "RECENT", "fields": FIELDS}
        return f"{BASE}/{target}/replies", {"fields": FIELDS}

    def fetch(self, target: str, limit: int) -> Iterator[Post]:
        url, params = self._first_page(target)
        params = {**params, "access_token": self.token, "limit": min(limit, 100)}
        got = 0
        while True:
            data = self.http(url, params)
            for item in data.get("data", []):
                if not item.get("text"):
                    continue
                yield to_post("threads", item["id"], target, item.get("timestamp", ""), item["text"])
                got += 1
                if got >= limit:
                    return
            after = data.get("paging", {}).get("cursors", {}).get("after")
            if not after or not data.get("data"):
                return
            params = {**params, "after": after}
            time.sleep(self.delay)
