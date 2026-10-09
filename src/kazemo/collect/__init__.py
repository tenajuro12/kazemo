"""Collectors for social networks. Each one yields :class:`Post` objects."""

from .base import Collector, CollectReport, Post, collect_to_jsonl
from .telegram import TelegramCollector
from .threads import ThreadsCollector
from .youtube import YouTubeCollector

COLLECTORS = {"telegram": TelegramCollector, "threads": ThreadsCollector, "youtube": YouTubeCollector}

__all__ = [
    "COLLECTORS",
    "CollectReport",
    "Collector",
    "Post",
    "TelegramCollector",
    "ThreadsCollector",
    "YouTubeCollector",
    "collect_to_jsonl",
]
