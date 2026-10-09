"""Offline HTML dashboard for a data folder: reports, yields, posts.

    kazemo dashboard data            # writes data/dashboard.html
    kazemo dashboard data --open     # and opens it in the browser

The page is a single self-contained file: no server, no internet, no external
scripts. Data stays on the researcher's machine.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import __version__
from .filters import words

STOPWORDS = set(
    """
    мен сен ол біз сіз сендер олар менің сенің оның біздің сіздің бұл сол осы анау мына ана
    және мен да де та те ма ме ба бе па пе ғой қой ғана тек әлі енді бірақ себебі үшін деп
    болып болды бар жоқ емес еді екен ғой ше шы ал сондай қалай неге не кім қай қашан қандай
    барлық бәрі өте тым бір екі көп аз сияқты дейін кейін соң бойы арқылы туралы
    и в во на не что он она оно они мы вы я ты это как так но а с со к по за из у же ли бы
    то все всё его её их мой моя мои твой был была были быть есть нет да ещё уже очень
    the a an and or of to in is it i you this that for on with
    url user phone email
    """.split()
)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _compact(row: dict, reason: str | None = None) -> dict:
    meta = row.get("meta") or {}
    out = {
        "t": row.get("text", ""),
        "d": (row.get("date") or "")[:19],
        "s": row.get("source", "file"),
        "c": row.get("channel", ""),
        "lk": meta.get("likes") or 0,
        "rp": meta.get("replies") or 0,
        "e": "".join(row.get("emojis") or []),
        "lg": row.get("lang", ""),
    }
    if reason:
        out["why"] = reason.split(" (")[0]
    if row.get("label"):
        out["lb"] = row["label"]
    return out


def build_payload(data_dir: str | Path) -> dict:
    d = Path(data_dir)
    raw = _read(d / "raw.jsonl")
    kept = _read(d / "clean.jsonl")
    rejected = _read(d / "rejected.jsonl")
    report_path = d / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}

    # yield per source + target (keyword or channel)
    yields: dict[tuple, dict] = defaultdict(lambda: {"collected": 0, "kept": 0, "rejected": Counter()})
    for r in raw:
        yields[(r.get("source", "file"), r.get("channel", ""))]["collected"] += 1
    for r in kept:
        yields[(r.get("source", "file"), r.get("channel", ""))]["kept"] += 1
    for r in rejected:
        yields[(r.get("source", "file"), r.get("channel", ""))]["rejected"][r.get("reason", "?").split(" (")[0]] += 1
    yield_rows = []
    for (src, ch), y in yields.items():
        base = y["collected"] or (y["kept"] + sum(y["rejected"].values()))
        yield_rows.append(
            {
                "source": src,
                "target": ch,
                "collected": base,
                "kept": y["kept"],
                "rate": round(y["kept"] / base, 3) if base else 0,
                "rejected": dict(y["rejected"]),
            }
        )
    yield_rows.sort(key=lambda r: (-r["kept"], r["target"]))

    word_counts = Counter(
        w for r in kept for w in (x.lower() for x in words(r.get("text", ""))) if len(w) > 2 and w not in STOPWORDS
    )
    days = Counter((r.get("date") or "")[:10] for r in kept if r.get("date"))

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "version": __version__,
        "folder": str(d),
        "counts": {"raw": len(raw), "kept": len(kept), "rejected": len(rejected)},
        "reasons": dict(Counter(r.get("reason", "?").split(" (")[0] for r in rejected).most_common()),
        "lang": dict(Counter(r.get("lang", "?") for r in kept).most_common()),
        "sources": dict(Counter(r.get("source", "file") for r in kept).most_common()),
        "emoji": Counter(e for r in kept for e in r.get("emojis") or []).most_common(20),
        "words": [(w, n) for w, n in word_counts.most_common(40) if n > 1] or word_counts.most_common(20),
        "days": sorted(days.items()),
        "yields": yield_rows,
        "report": report,
        "kept": [_compact(r) for r in kept],
        "rejected": [_compact(r, r.get("reason")) for r in rejected],
    }


def template() -> str:
    from importlib.resources import files

    return files("kazemo").joinpath("dashboard.html").read_text(encoding="utf-8")


def render(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    data = data.replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return template().replace("/*__DATA__*/null", data)


def write_dashboard(data_dir: str | Path, output: str | Path | None = None) -> Path:
    out = Path(output) if output else Path(data_dir) / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build_payload(data_dir)), encoding="utf-8")
    return out


def no_external_resources(html: str) -> bool:
    """True if the page loads nothing from the network (used in tests)."""
    return not re.search(r"""(src|href)\s*=\s*["']https?://""", html)
