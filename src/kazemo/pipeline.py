"""End-to-end processing of a JSONL file of posts plus corpus statistics."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
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


def plot_labels(records: list[Record], path: str | Path) -> None:
    """Save a horizontal bar chart of the label distribution."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    counts = Counter(r.label for r in records if r.label).most_common()
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
