"""Threads collector through an Apify actor (no Threads API approval needed).

Uses the actor ``futurizerush/threads-search-scraper-api`` by default: it searches
public Threads posts by keyword. Needs ``APIFY_TOKEN``; the actor's optional
``sessionId`` input is read from ``APIFY_THREADS_SESSION`` if it is set.

Notes:
- Paid per result (about $4 per 1000 posts at the time of writing); ``limit``
  is also sent as Apify's ``maxItems`` so a run is not billed for more than asked.
- Synchronous runs are limited to 5 minutes by Apify; large limits may need to be split.
- This goes around the official Threads API; check the platform's terms and your
  ethics approval before using the data in publications.
- Author fields returned by the actor (username, authorId, profile...) are dropped
  here and never written to disk.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import require
from . import base
from .base import Collector, Post, to_post

DEFAULT_ACTOR = "futurizerush/threads-search-scraper-api"
API = "https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items"


def _iso(ts) -> str:
    if ts in (None, ""):
        return ""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, tz=timezone.utc).isoformat()
    return str(ts)


class ApifyThreadsCollector(Collector):
    name = "threads-apify"

    def __init__(
        self,
        mode: str = "recent",
        token: str | None = None,
        actor: str | None = None,
        session_id: str | None = None,
        http=None,
    ):
        if mode not in ("recent", "top"):
            raise ValueError("mode must be 'recent' or 'top'")
        self.mode = mode
        self.token = token or require(
            "APIFY_TOKEN", "Create one at https://console.apify.com → Settings → API & Integrations."
        )
        self.actor = actor or os.environ.get("APIFY_THREADS_ACTOR") or DEFAULT_ACTOR
        self.session_id = session_id or os.environ.get("APIFY_THREADS_SESSION") or None
        self.http = http or (
            lambda url, params, body: base.http_post_json(
                url, params, body, headers={"Authorization": f"Bearer {self.token}"}
            )
        )

    def build_input(self, keyword: str, limit: int) -> dict:
        body = {"keywords": [keyword], "maxResults": max(10, min(limit, 2000)), "searchType": self.mode}
        if self.session_id:
            body["sessionId"] = self.session_id
        return body

    def fetch(self, target: str, limit: int) -> Iterator[Post]:
        url = API.format(actor=self.actor.replace("/", "~"))
        try:
            items = self.http(url, {"maxItems": limit, "timeout": 290}, self.build_input(target, limit))
        except base.ApiError as e:
            if "HTTP 408" in str(e):
                raise base.ApiError(
                    f"Apify run for {target!r} took longer than 5 minutes; try a smaller --limit"
                ) from None
            raise
        if isinstance(items, dict):  # Apify returns an error object instead of a list
            raise base.ApiError(f"Apify: {items.get('error', items)}")
        for item in items[:limit]:
            text = item.get("text")
            post_id = item.get("postId") or item.get("postCode") or item.get("postUrl")
            if not text or not post_id:
                continue
            yield to_post(
                "threads",
                post_id,
                target,
                _iso(item.get("timestamp")),
                text,
                likes=item.get("likeCount") or 0,
                replies=item.get("replyCount") or 0,
                is_reply=bool(item.get("isReply")),
                via="apify",
            )
