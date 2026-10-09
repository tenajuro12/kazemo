"""Telegram collector for public channels and groups using Telethon.

Needs ``TELEGRAM_API_ID`` and ``TELEGRAM_API_HASH`` from my.telegram.org.
The first run asks for a phone number and a code and stores a local session
file; for servers or CI set ``TELEGRAM_SESSION`` to a string session
(``kazemo login telegram`` prints one).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

from ..config import require
from .base import Collector, Post, to_post


def _client(session_file: str = "kazemo.session"):
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError('Telegram support needs Telethon: pip install "kazemo[telegram]"') from e
    api_id = int(require("TELEGRAM_API_ID", "Get it at https://my.telegram.org → API development tools."))
    api_hash = require("TELEGRAM_API_HASH")
    string = os.environ.get("TELEGRAM_SESSION")
    session = StringSession(string) if string else session_file
    return TelegramClient(session, api_id, api_hash)


class TelegramCollector(Collector):
    name = "telegram"

    def __init__(self, search: str | None = None, client=None):
        self.search = search
        self._client = client  # injectable for tests

    async def _fetch(self, target: str, limit: int) -> list[Post]:
        client = self._client or _client()
        posts: list[Post] = []
        async with client:
            async for msg in client.iter_messages(target, limit=limit, search=self.search):
                text = getattr(msg, "message", None)
                if not text:
                    continue  # photos without captions, service messages
                posts.append(
                    to_post(
                        "telegram",
                        f"{target}/{msg.id}",
                        target,
                        msg.date.isoformat(),
                        text,
                        views=getattr(msg, "views", None) or 0,
                    )
                )
        return posts

    def fetch(self, target: str, limit: int) -> Iterator[Post]:
        yield from asyncio.run(self._fetch(target.lstrip("@"), limit))


def login() -> str:  # pragma: no cover - interactive
    """Log in interactively and return a reusable string session."""
    from telethon.sessions import StringSession
    from telethon.sync import TelegramClient

    api_id = int(require("TELEGRAM_API_ID"))
    api_hash = require("TELEGRAM_API_HASH")
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        return client.session.save()
