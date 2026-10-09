"""YouTube comments collector using the YouTube Data API v3.

Needs ``YOUTUBE_API_KEY``. Targets are video IDs, or a search query when
``mode="search"`` (comments are then taken from the top matching videos).
"""

from __future__ import annotations

import time
from collections.abc import Iterator

from ..config import require
from . import base
from .base import Collector, HttpGet, Post, to_post

BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeCollector(Collector):
    name = "youtube"

    def __init__(
        self,
        mode: str = "video",
        api_key: str | None = None,
        http: HttpGet | None = None,
        max_videos: int = 10,
        region: str = "KZ",
        delay: float = 0.5,
    ):
        if mode not in ("video", "search"):
            raise ValueError("mode must be 'video' or 'search'")
        self.mode = mode
        self.key = api_key or require("YOUTUBE_API_KEY", "Create one in Google Cloud → YouTube Data API v3.")
        self.http = http or (lambda url, params: base.http_get_json(url, params))
        self.max_videos = max_videos
        self.region = region
        self.delay = delay

    def find_videos(self, query: str) -> list[str]:
        data = self.http(
            f"{BASE}/search",
            {
                "part": "id",
                "q": query,
                "type": "video",
                "maxResults": self.max_videos,
                "regionCode": self.region,
                "key": self.key,
            },
        )
        return [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]

    def comments(self, video_id: str, limit: int, channel: str) -> Iterator[Post]:
        params = {"part": "snippet", "videoId": video_id, "maxResults": 100, "textFormat": "plainText", "key": self.key}
        got = 0
        while True:
            data = self.http(f"{BASE}/commentThreads", params)
            for item in data.get("items", []):
                sn = item["snippet"]["topLevelComment"]["snippet"]
                yield to_post(
                    "youtube",
                    item["id"],
                    channel,
                    sn.get("publishedAt", ""),
                    sn.get("textOriginal", ""),
                    likes=sn.get("likeCount", 0),
                    video=video_id,
                )
                got += 1
                if got >= limit:
                    return
            token = data.get("nextPageToken")
            if not token:
                return
            params = {**params, "pageToken": token}
            time.sleep(self.delay)

    def fetch(self, target: str, limit: int) -> Iterator[Post]:
        if self.mode == "video":
            yield from self.comments(target, limit, channel=target)
            return
        remaining = limit
        for vid in self.find_videos(target):
            for post in self.comments(vid, remaining, channel=target):
                yield post
                remaining -= 1
            if remaining <= 0:
                return
