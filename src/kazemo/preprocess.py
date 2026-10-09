"""Text pre-processing for Kazakh / Russian social-media posts.

Functions here are pure and deterministic, so every step of the
pipeline can be unit-tested and re-run on the raw data.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

# Letters that exist in the Kazakh Cyrillic alphabet but not in Russian.
KAZAKH_SPECIFIC = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")

URL_RE = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+)", re.IGNORECASE)
MENTION_RE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{3,}")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?7|8)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
HASHTAG_RE = re.compile(r"#(\w+)")
REPEAT_RE = re.compile(r"(.)\1{3,}")
SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Unicode NFC normalisation and whitespace collapsing."""
    text = unicodedata.normalize("NFC", text)
    return SPACE_RE.sub(" ", text).strip()


def pseudonymise(text: str, salt: str = "kazemo") -> str:
    """Replace mentions, e-mails and phone numbers with stable tokens.

    Mentions become ``@user_<hash>`` so the same account maps to the same
    token across posts, without keeping the real name.
    """

    def _hash(match: re.Match[str]) -> str:
        digest = hashlib.sha256((salt + match.group(0).lower()).encode()).hexdigest()[:8]
        return f"@user_{digest}"

    text = EMAIL_RE.sub("<EMAIL>", text)
    text = PHONE_RE.sub("<PHONE>", text)
    return MENTION_RE.sub(_hash, text)


def clean(text: str, keep_emoji: bool = True) -> str:
    """Clean one post: links, hashtags, elongations, whitespace.

    Emoji are kept by default because they carry emotional signal.
    """
    text = normalize(text)
    text = URL_RE.sub("<URL>", text)
    text = HASHTAG_RE.sub(r"\1", text)
    text = REPEAT_RE.sub(r"\1\1\1", text)  # "ааааа" -> "ааа"
    if not keep_emoji:
        text = "".join(ch for ch in text if unicodedata.category(ch) != "So")
    return normalize(text)


def detect_script(text: str) -> str:
    """Return ``cyrillic``, ``latin``, ``mixed`` or ``none``."""
    cyr = sum(1 for ch in text if "CYRILLIC" in unicodedata.name(ch, ""))
    lat = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    total = cyr + lat
    if total == 0:
        return "none"
    share = cyr / total
    if share >= 0.8:
        return "cyrillic"
    if share <= 0.2:
        return "latin"
    return "mixed"


def detect_language(text: str, threshold: float = 0.02) -> str:
    """Heuristic language ID: ``kk``, ``ru`` or ``unknown``.

    A Cyrillic post is labelled Kazakh when Kazakh-specific letters make up
    at least ``threshold`` of its letters; code-mixed posts usually pass.
    """
    letters = [ch for ch in text if ch.isalpha()]
    if not letters or detect_script(text) in ("latin", "none"):
        return "unknown"
    kk = sum(1 for ch in letters if ch in KAZAKH_SPECIFIC)
    return "kk" if kk / len(letters) >= threshold else "ru"


def fingerprint(text: str) -> str:
    """Hash of the lower-cased, punctuation-free text, used for dedup."""
    core = re.sub(r"[^\w]", "", text.lower())
    return hashlib.md5(core.encode()).hexdigest()


def deduplicate(texts: list[str]) -> list[str]:
    """Drop exact and near-exact duplicates, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[str] = []
    for t in texts:
        fp = fingerprint(t)
        if fp not in seen:
            seen.add(fp)
            out.append(t)
    return out
