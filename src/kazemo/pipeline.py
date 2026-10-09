"""End-to-end processing of a JSONL file of posts plus corpus statistics."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .preprocess import clean, deduplicate, detect_language, detect_script, pseudonymise


@dataclass
class Record:
    text: str
    lang: str
    script: str
    label: str | None = None


def process(posts: list[dict], min_chars: int = 5) -> list[Record]:
    """Clean, pseudonymise, deduplicate and tag a list of ``{"text": ..}`` posts."""
    texts = [pseudonymise(clean(p.get("text", ""))) for p in posts]
    labels = {t: p.get("label") for t, p in zip(texts, posts, strict=True)}
    out = []
    for t in deduplicate(texts):
        if len(t) < min_chars:
            continue
        out.append(Record(text=t, lang=detect_language(t), script=detect_script(t), label=labels.get(t)))
    return out


def read_jsonl(path: str | Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_jsonl(records: list[Record], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def stats(records: list[Record]) -> dict:
    """Corpus statistics: size, language, script and label distributions."""
    return {
        "n": len(records),
        "lang": dict(Counter(r.lang for r in records)),
        "script": dict(Counter(r.script for r in records)),
        "label": dict(Counter(r.label for r in records if r.label)),
    }


def plot_labels(records: list, path: str | Path) -> None:
    """Save a horizontal bar chart of the label distribution."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def label(r):
        return r.get("label") if isinstance(r, dict) else r.label

    counts = Counter(label(r) for r in records if label(r)).most_common()
    if not counts:
        raise ValueError("no labelled records to plot")
    names, values = zip(*counts, strict=True)
    fig, ax = plt.subplots(figsize=(7, 0.5 * len(names) + 1.2))
    ax.barh(names[::-1], values[::-1], color="#2E5C8A")
    ax.set_xlabel("Posts")
    ax.set_title("Label distribution")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# --------------------------------------------------------------------------------------------------
# Full preprocessing stage: anonymise -> filter -> clean -> emoji handling -> language -> dedup
# --------------------------------------------------------------------------------------------------


@dataclass
class PrepConfig:
    langs: set[str] | None = field(default_factory=lambda: {"kk"})
    emoji: str = "keep"  # keep | remove | text
    min_words: int = 3
    filter_ads: bool = True
    ad_threshold: int = 2
    filter_formulaic: bool = True
    strip_trailing_hashtags: bool = True
    deduplicate: bool = True

    def __post_init__(self):
        if self.emoji not in ("keep", "remove", "text"):
            raise ValueError("emoji must be keep, remove or text")


@dataclass
class PrepReport:
    total: int = 0
    kept: int = 0
    rejected: dict = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        key = reason.split(" (")[0]
        self.rejected[key] = self.rejected.get(key, 0) + 1


def prepare_row(row: dict, cfg: PrepConfig) -> tuple[dict | None, str | None]:
    """Process one row. Returns ``(clean_row, None)`` or ``(None, reason)``."""
    from . import filters
    from .preprocess import (
        anonymise,
        emojis_to_text,
        extract_emojis,
        remove_emojis,
        strip_trailing_hashtags,
    )

    text = anonymise(row.get("text") or "")
    if not text:
        return None, "empty"

    # filters that need the hashtags, before cleaning removes them
    if cfg.filter_ads and (reason := filters.is_ad(text, cfg.ad_threshold)):
        return None, reason
    if reason := filters.is_mostly_hashtags(text):
        return None, reason

    if cfg.strip_trailing_hashtags:
        text = strip_trailing_hashtags(text)
    text = clean(text)
    emojis = extract_emojis(text)
    if cfg.emoji == "remove":
        text = remove_emojis(text)
    elif cfg.emoji == "text":
        text = emojis_to_text(text)

    lang = detect_language(text)
    if cfg.langs and lang not in cfg.langs:
        return None, f"language {lang}"
    if reason := filters.is_too_short(text, cfg.min_words):
        return None, reason
    if cfg.filter_formulaic and (reason := filters.is_formulaic(text)):
        return None, reason

    out = {k: v for k, v in row.items() if k not in ("text", "lang")}
    out.update(text=text, lang=lang, script=detect_script(text), emojis=emojis)
    return out, None


def prepare(rows: list[dict], cfg: PrepConfig | None = None) -> tuple[list[dict], list[dict], PrepReport]:
    """Run the preprocessing stage. Returns kept rows, rejected rows (with ``reason``) and a report."""
    import hashlib

    from .filters import words

    cfg = cfg or PrepConfig()
    report = PrepReport(total=len(rows))
    kept, rejected, seen = [], [], set()
    for row in rows:
        out, reason = prepare_row(row, cfg)
        if out is not None and cfg.deduplicate:
            # near-duplicates: same words, ignoring punctuation, emoji, mentions, links and case
            fp = hashlib.md5(" ".join(words(out["text"])).lower().encode()).hexdigest()
            if fp in seen:
                out, reason = None, "duplicate"
            else:
                seen.add(fp)
        if out is None:
            report.reject(reason)
            rejected.append({**row, "reason": reason})
        else:
            kept.append(out)
    report.kept = len(kept)
    return kept, rejected, report


def write_rows(rows: list[dict], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def corpus_report(rows: list[dict]) -> dict:
    """Statistics for a prepared corpus."""
    emoji_posts = sum(1 for r in rows if r.get("emojis"))
    top_emoji = Counter(e for r in rows for e in r.get("emojis", [])).most_common(10)
    return {
        "posts": len(rows),
        "source": dict(Counter(r.get("source", "file") for r in rows)),
        "lang": dict(Counter(r.get("lang") for r in rows)),
        "script": dict(Counter(r.get("script") for r in rows)),
        "with_emoji": emoji_posts,
        "top_emoji": dict(top_emoji),
        "label": dict(Counter(r["label"] for r in rows if r.get("label"))),
    }
