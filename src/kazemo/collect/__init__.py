"""Collectors for social networks. Each one yields :class:`Post` objects."""

from .base import Collector, CollectReport, Post, collect_to_jsonl
from .telegram import TelegramCollector
from .threads import ThreadsCollector
from .threads_apify import ApifyThreadsCollector
from .youtube import YouTubeCollector

COLLECTORS = {
    "telegram": TelegramCollector,
    "threads": ThreadsCollector,
    "threads-apify": ApifyThreadsCollector,
    "youtube": YouTubeCollector,
}

__all__ = [
    "ApifyThreadsCollector",
    "COLLECTORS",
    "CollectReport",
    "Collector",
    "Post",
    "TelegramCollector",
    "ThreadsCollector",
    "YouTubeCollector",
    "collect_to_jsonl",
]
