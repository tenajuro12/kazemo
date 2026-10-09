"""Common collector interface and the JSONL sink with resume and filtering."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..preprocess import clean, detect_language, pseudonymise


@dataclass
class Post:
    """One collected post. Authors are never stored."""

    uid: str  # hash of source + platform id: dedup key, not reversible to a person
    source: str  # telegram | threads | youtube
    channel: str  # public channel, query or video the post came from
    date: str  # ISO 8601
    text: str  # cleaned and pseudonymised
    lang: str = "unknown"
    meta: dict = field(default_factory=dict)


def make_uid(source: str, raw_id: object) -> str:
    return hashlib.sha256(f"{source}:{raw_id}".encode()).hexdigest()[:16]


def to_post(source: str, raw_id: object, channel: str, date: str, text: str, **meta) -> Post:
    cleaned = pseudonymise(clean(text or ""))
    return Post(
        uid=make_uid(source, raw_id),
        source=source,
        channel=channel,
        date=date,
        text=cleaned,
        lang=detect_language(cleaned),
        meta=meta,
    )


class Collector(ABC):
    """A source of posts. Subclasses implement :meth:`fetch`."""

    name: str = "base"

    @abstractmethod
    def fetch(self, target: str, limit: int) -> Iterator[Post]:
        """Yield up to ``limit`` posts from ``target`` (channel, query, video...)."""


# --- HTTP helper shared by API-based collectors -------------------------------------------------

HttpGet = Callable[[str, dict], dict]


class ApiError(RuntimeError):
    pass


def http_get_json(url: str, params: dict, retries: int = 3, backoff: float = 2.0) -> dict:
    """GET a JSON endpoint with retries on rate limits and server errors."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(full, timeout=30) as resp:  # noqa: S310 - fixed https hosts
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503) and attempt < retries:
                time.sleep(backoff * (2**attempt))
                continue
            body = e.read().decode("utf-8", "replace")[:300]
            raise ApiError(f"HTTP {e.code} from {url}: {body}") from None
    raise ApiError(f"giving up on {url}")  # pragma: no cover


# --- sink ----------------------------------------------------------------------------------------


def existing_uids(path: str | Path) -> set[str]:
    p = Path(path)
    if not p.exists():
        return set()
    with p.open(encoding="utf-8") as f:
        return {json.loads(line)["uid"] for line in f if line.strip()}


@dataclass
class CollectReport:
    written: int = 0
    duplicates: int = 0
    filtered: int = 0


def collect_to_jsonl(
    collector: Collector,
    targets: list[str],
    output: str | Path,
    limit: int = 500,
    langs: set[str] | None = None,
    min_chars: int = 5,
) -> CollectReport:
    """Run a collector over targets and append new posts to ``output``.

    Re-running the same command resumes: posts already in the file are skipped.
    """
    seen = existing_uids(output)
    report = CollectReport()
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    with open(output, "a", encoding="utf-8") as f:
        for target in targets:
            for post in collector.fetch(target, limit):
                if post.uid in seen:
                    report.duplicates += 1
                    continue
                if len(post.text) < min_chars or (langs and post.lang not in langs):
                    report.filtered += 1
                    continue
                seen.add(post.uid)
                f.write(json.dumps(asdict(post), ensure_ascii=False) + "\n")
                report.written += 1
    return report
