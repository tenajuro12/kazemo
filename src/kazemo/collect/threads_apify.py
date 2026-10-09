"""Threads collector through an Apify actor (no Threads API approval needed).

Default actor: ``aydar_cosmos/threads-scraper`` — keyword search of public posts,
no login required. Alternative: ``futurizerush/threads-search-scraper-api``, which
requires a ``sessionId`` tied to your own Threads account (``APIFY_THREADS_SESSION``).
Choose with ``APIFY_THREADS_ACTOR`` or the ``actor`` argument.

Needs ``APIFY_TOKEN``.

Notes:
- Paid per result; ``limit`` is also sent as Apify's ``maxItems`` so a run is not
  billed for more than asked.
- Synchronous runs are limited to 5 minutes by Apify; large limits may need to be split.
- This goes around the official Threads API; check the platform's terms and your
  ethics approval before using the data in publications.
- Author fields returned by the actor (username, ids, profile...) are dropped here
  and never written to disk.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone

from ..config import MissingCredential, require
from . import base
from .base import Collector, Post, to_post

API = "https://api.apify.com/v2/actors/{actor}/run-sync-get-dataset-items"

NO_LOGIN_ACTOR = "aydar_cosmos/threads-scraper"
SESSION_ACTOR = "futurizerush/threads-search-scraper-api"
DEFAULT_ACTOR = NO_LOGIN_ACTOR

# field names actors commonly use, tried in order
TEXT_KEYS = ("text", "caption", "content", "body", "postText", "post_text")
ID_KEYS = ("postId", "id", "pk", "code", "postCode", "shortcode", "url", "postUrl", "permalink")
DATE_KEYS = ("timestamp", "takenAt", "taken_at", "createdAt", "created_at", "publishedAt", "date", "time")
LIKE_KEYS = ("likeCount", "likes", "like_count", "likesCount")
REPLY_KEYS = ("replyCount", "replies", "reply_count", "repliesCount", "commentCount")


def _first(item: dict, keys: tuple[str, ...]):
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return None


def _text(item: dict) -> str | None:
    v = _first(item, TEXT_KEYS)
    if isinstance(v, dict):  # e.g. {"caption": {"text": "..."}}
        v = v.get("text")
    return v if isinstance(v, str) else None


def _count(v) -> int:
    if isinstance(v, dict):
        v = v.get("count")
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


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
        if self.actor == SESSION_ACTOR and not self.session_id:
            raise MissingCredential(
                f"{SESSION_ACTOR} needs APIFY_THREADS_SESSION (a key tied to your Threads account). "
                f"Use a secondary account, or switch to the no-login actor: APIFY_THREADS_ACTOR={NO_LOGIN_ACTOR}"
            )
        self.http = http or (
            lambda url, params, body: base.http_post_json(
                url, params, body, headers={"Authorization": f"Bearer {self.token}"}
            )
        )

    def build_input(self, keyword: str, limit: int) -> dict:
        if self.actor == SESSION_ACTOR:
            body = {"keywords": [keyword], "maxResults": max(10, min(limit, 2000)), "searchType": self.mode}
            if self.session_id:
                body["sessionId"] = self.session_id
            return body
        # aydar_cosmos/threads-scraper and actors with the same input shape
        return {"mode": "search", "query": keyword, "maxResults": max(1, min(limit, 1000))}

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

        yielded = 0
        for item in items[:limit]:
            if not isinstance(item, dict):
                continue
            text = _text(item)
            post_id = _first(item, ID_KEYS)
            if not text or post_id is None:
                continue
            yielded += 1
            yield to_post(
                "threads",
                post_id,
                target,
                _iso(_first(item, DATE_KEYS)),
                text,
                likes=_count(_first(item, LIKE_KEYS)),
                replies=_count(_first(item, REPLY_KEYS)),
                via="apify",
            )
        if items and not yielded:
            keys = sorted({k for it in items[:5] if isinstance(it, dict) for k in it})
            raise base.ApiError(
                f"Apify returned {len(items)} items but none had a recognised text and id field. "
                f"Field names in the reply: {keys}"
            )
