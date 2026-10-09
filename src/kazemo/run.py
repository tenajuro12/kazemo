"""End-to-end pipeline driven by a TOML config: collect -> prepare -> report.

    kazemo run pipeline.toml              # collect, then prepare everything collected so far
    kazemo run pipeline.toml --skip-collect

Outputs in ``[output].dir``: raw.jsonl (anonymised, append-only), clean.jsonl,
rejected.jsonl (with a reason per post) and report.json.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from .collect import COLLECTORS, collect_to_jsonl
from .collect.base import ApiError
from .config import MissingCredential
from .pipeline import PrepConfig, corpus_report, prepare, read_jsonl, write_rows


def load_config(path: str | Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def collect_stage(cfg: dict, raw: Path, collectors: dict | None = None) -> dict:
    """Run every ``[[collect.source]]`` entry; a failing source is reported, not fatal."""
    c = cfg.get("collect", {})
    langs = set(c["lang"]) if c.get("lang") else None
    results = {}
    for entry in c.get("source", []):
        name = entry["name"]
        label = entry.get("label", name)
        opts = {k: entry[k] for k in ("mode", "search") if k in entry}
        try:
            factory = (collectors or {}).get(name) or COLLECTORS[name]
            report = collect_to_jsonl(
                factory(**opts),
                entry["targets"],
                raw,
                limit=entry.get("limit", c.get("limit", 200)),
                langs=langs,
                min_chars=c.get("min_chars", 5),
            )
            results[label] = {"written": report.written, "duplicates": report.duplicates, "filtered": report.filtered}
        except KeyError:
            results[label] = {"error": f"unknown source {name!r}; use one of {sorted(COLLECTORS)}"}
        except (MissingCredential, ApiError, RuntimeError) as e:
            results[label] = {"error": str(e)}
        _log(f"collect {label}: {json.dumps(results[label], ensure_ascii=False)}")
    return results


def agent_stage(cfg: dict, raw: Path) -> dict | None:
    a = cfg.get("agent")
    if not a or not a.get("enabled", True):
        return None
    from .agent import CollectionAgent, yields_by_source

    langs = cfg.get("collect", {}).get("lang") or None
    try:
        state = CollectionAgent(
            goal=a["goal"],
            output=raw,
            sources=a.get("sources", ["telegram", "youtube"]),
            max_posts=a.get("max_posts", 1000),
            max_steps=a.get("max_steps", 20),
            langs=set(langs) if langs else None,
            model=a.get("model", "claude-sonnet-5-5"),
            seeds=a.get("seeds"),
        ).run()
    except (MissingCredential, RuntimeError) as e:
        return {"error": str(e)}
    return {"kept": state.written, "by_source": yields_by_source(state), "summary": state.finished}


def prep_config(cfg: dict) -> PrepConfig:
    p = cfg.get("process", {})
    langs = p.get("lang", ["kk"])
    return PrepConfig(
        langs=set(langs) if langs else None,
        emoji=p.get("emoji", "keep"),
        min_words=p.get("min_words", 3),
        filter_ads=p.get("filter_ads", True),
        ad_threshold=p.get("ad_threshold", 2),
        filter_formulaic=p.get("filter_formulaic", True),
        strip_trailing_hashtags=p.get("strip_trailing_hashtags", True),
        deduplicate=p.get("deduplicate", True),
    )


def run(config_path: str | Path, skip_collect: bool = False, collectors: dict | None = None) -> dict:
    cfg = load_config(config_path)
    out_dir = Path(cfg.get("output", {}).get("dir", "data"))
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = out_dir / "raw.jsonl"

    report: dict = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "config": str(config_path)}
    if not skip_collect:
        report["collect"] = collect_stage(cfg, raw, collectors)
        agent = agent_stage(cfg, raw)
        if agent is not None:
            report["agent"] = agent

    rows = read_jsonl(raw) if raw.exists() else []
    kept, rejected, prep = prepare(rows, prep_config(cfg))
    write_rows(kept, out_dir / "clean.jsonl")
    write_rows(rejected, out_dir / "rejected.jsonl")
    report["process"] = {"total": prep.total, "kept": prep.kept, "rejected": prep.rejected}
    report["corpus"] = corpus_report(kept)
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
