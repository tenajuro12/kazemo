"""LLM agent that plans and runs data collection.

The model never sees credentials and never writes data itself. It can only
call the tools below; every call goes through the normal collectors, so the
same privacy rules apply (pseudonymisation at collection, no authors stored,
public sources only). Budgets are enforced in code, not by the prompt.

    kazemo agent --goal "Kazakh posts expressing fear, anxiety, joy, sadness, anger" \
                 --sources telegram youtube -o data/raw.jsonl --max-posts 3000
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .collect import COLLECTORS, collect_to_jsonl
from .collect.base import existing_uids
from .config import require

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_LIMIT_PER_CALL = 500

SYSTEM_PROMPT = """You are a data-collection planner for an academic NLP study of emotional language \
in Kazakh and code-mixed Kazakh-Russian social media. Your job is to find sources and search terms that \
yield as many relevant, natural Kazakh posts as possible within the budget, using the tools provided.

How to work:
- Start broad, then focus. Propose Kazakh search terms that people naturally use when expressing emotions \
(everyday words, colloquial forms, both Cyrillic and common code-mixed phrasing), spread across the emotions \
named in the goal, plus neutral everyday topics so the corpus is not only emotional.
- For Telegram, discover public channels and groups with find_telegram_channels, then collect from the \
promising ones. For YouTube, use search queries that lead to Kazakh videos with active comment sections. \
For Threads, use keyword search.
- After every collect call look at the yield per target: kept posts and kept_share \
(the share of fetched posts in the target language). Drop targets with a low kept_share \
or few new posts; try variations of targets that worked. Avoid collecting near-duplicate targets.
- Check corpus_stats now and then to balance sources and topics.
- Call finish when the budget is nearly used or when new targets stop adding Kazakh posts.

Rules you must follow:
- Only public channels, groups, public search results and comments on public videos. Never target a private \
person's account and never try to collect information about specific individuals.
- Do not try to circumvent rate limits, quotas or platform rules.
- Keep tool inputs short. Do not invent channel usernames: only use ones returned by find_telegram_channels \
or given by the user."""


@dataclass
class AgentState:
    output: Path
    sources: list[str]
    max_posts: int
    langs: set[str] | None
    written: int = 0
    calls: int = 0
    tried: dict[str, dict] = field(default_factory=dict)  # "source:target" -> yield
    input_tokens: int = 0
    output_tokens: int = 0
    finished: str | None = None

    @property
    def remaining(self) -> int:
        return max(self.max_posts - self.written, 0)


def tool_specs(sources: list[str]) -> list[dict]:
    tools = [
        {
            "name": "collect",
            "description": "Collect posts from one source for one or more targets and append them to the corpus. "
            "Returns, per target, how many new posts were kept, rejected (wrong language or too short) and "
            "already present, plus a few short samples of kept posts.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "enum": sources},
                    "targets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "telegram: public channel usernames; threads: keywords; youtube: search queries",
                        "maxItems": 5,
                    },
                    "limit": {"type": "integer", "description": f"max posts per target, 1-{MAX_LIMIT_PER_CALL}"},
                    "search": {"type": "string", "description": "telegram only: keep messages containing this word"},
                },
                "required": ["source", "targets", "limit"],
            },
        },
        {
            "name": "corpus_stats",
            "description": "Current corpus size, budget left, language and source distribution, and the yield of every "
            "target tried so far.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "finish",
            "description": "Stop collecting. Give a short summary of what worked and what did not.",
            "input_schema": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        },
    ]
    if "telegram" in sources:
        tools.insert(
            0,
            {
                "name": "find_telegram_channels",
                "description": "Search Telegram for public channels and groups by a word in their name or description. "
                "Returns usernames, titles and member counts.",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["query"],
                },
            },
        )
    return tools


class CollectionAgent:
    def __init__(
        self,
        goal: str,
        output: str | Path,
        sources: list[str],
        max_posts: int = 2000,
        max_steps: int = 30,
        langs: set[str] | None = frozenset({"kk"}),
        model: str = DEFAULT_MODEL,
        seeds: list[str] | None = None,
        llm=None,
        collectors: dict | None = None,
        log_path: str | Path | None = None,
        verbose: bool = True,
    ):
        unknown = set(sources) - set(COLLECTORS)
        if unknown:
            raise ValueError(f"unknown sources: {sorted(unknown)}")
        self.goal = goal
        self.sources = list(sources)
        self.max_steps = max_steps
        self.model = model
        self.seeds = seeds or []
        self.state = AgentState(Path(output), self.sources, max_posts, set(langs) if langs else None)
        self.llm = llm or _anthropic_client()
        self._collectors = collectors or {}
        self.log_path = Path(log_path) if log_path else self.state.output.with_suffix(".agent.jsonl")
        self.verbose = verbose

    # ---------------------------------------------------------------- tools

    def _collector(self, source: str, **opts):
        if source in self._collectors:
            return self._collectors[source]
        return COLLECTORS[source](**opts)

    def tool_find_telegram_channels(self, query: str, limit: int = 20) -> dict:
        channels = self._collector("telegram").search_channels(query, min(max(limit, 1), 50))
        return {"channels": channels}

    def tool_collect(self, source: str, targets: list[str], limit: int, search: str | None = None) -> dict:
        if source not in self.sources:
            return {"error": f"source {source!r} is not enabled; use one of {self.sources}"}
        if self.state.remaining == 0:
            return {"error": "post budget is used up; call finish"}
        targets = [t.strip() for t in targets if t and t.strip()][:5]
        limit = max(1, min(int(limit), MAX_LIMIT_PER_CALL, self.state.remaining))
        opts = {"search": search} if source == "telegram" else ({"mode": "search"} if source == "youtube" else {})
        collector = self._collector(source, **opts)
        if source == "telegram" and search is not None and hasattr(collector, "search"):
            collector.search = search

        before = existing_uids(self.state.output)
        per_target = {}
        for target in targets:
            if self.state.remaining == 0:
                break
            # one call per target so the agent can compare yields between targets
            report = collect_to_jsonl(
                collector,
                [target],
                self.state.output,
                limit=min(limit, self.state.remaining),
                langs=self.state.langs,
                min_chars=5,
            )
            self.state.written += report.written
            seen = report.written + report.filtered
            per_target[target] = self.state.tried[f"{source}:{target}"] = {
                "kept": report.written,
                "rejected": report.filtered,
                "duplicates": report.duplicates,
                "kept_share": round(report.written / seen, 2) if seen else 0.0,
            }
        new_rows = _rows_not_in(self.state.output, before)
        return {
            "kept": len(new_rows),
            "per_target": per_target,
            "samples": [r["text"][:160] for r in new_rows[:5]],
            "budget_left": self.state.remaining,
        }

    def tool_corpus_stats(self) -> dict:
        rows = _read(self.state.output)
        return {
            "posts": len(rows),
            "budget_left": self.state.remaining,
            "lang": dict(Counter(r["lang"] for r in rows)),
            "source": dict(Counter(r["source"] for r in rows)),
            "top_channels": Counter(r["channel"] for r in rows).most_common(10),
            "targets_tried": self.state.tried,
        }

    def tool_finish(self, summary: str) -> dict:
        self.state.finished = summary
        return {"ok": True}

    def call_tool(self, name: str, args: dict) -> dict:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}
        except Exception as e:  # a failing source must not kill the run; the model can adapt
            return {"error": f"{type(e).__name__}: {e}"}

    # ---------------------------------------------------------------- loop

    def _first_message(self) -> str:
        parts = [
            f"Goal: {self.goal}",
            f"Enabled sources: {', '.join(self.sources)}",
            f"Budget: at most {self.state.max_posts} posts kept; languages kept: "
            f"{', '.join(sorted(self.state.langs)) if self.state.langs else 'all'}.",
            f"You have at most {self.max_steps} tool calls.",
        ]
        if self.seeds:
            parts.append("Starting points from the researcher: " + "; ".join(self.seeds))
        if self.state.output.exists():
            parts.append(f"The corpus already has {len(_read(self.state.output))} posts; continue building it.")
        return "\n".join(parts)

    def run(self) -> AgentState:
        messages = [{"role": "user", "content": self._first_message()}]
        tools = tool_specs(self.sources)
        steps = 0
        while steps < self.max_steps and not self.state.finished:
            resp = self.llm.messages.create(
                model=self.model, max_tokens=2048, system=SYSTEM_PROMPT, tools=tools, messages=messages
            )
            usage = getattr(resp, "usage", None)
            if usage:
                self.state.input_tokens += getattr(usage, "input_tokens", 0) or 0
                self.state.output_tokens += getattr(usage, "output_tokens", 0) or 0
            messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in resp.content]})
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                self.state.finished = self.state.finished or _text_of(resp) or "model stopped"
                break
            results = []
            for call in calls:
                steps += 1
                result = self.call_tool(call.name, dict(call.input))
                self._log(steps, call.name, call.input, result)
                results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": json.dumps(result, ensure_ascii=False)}
                )
                if steps >= self.max_steps:
                    break
            if self.state.remaining == 0 and not self.state.finished:
                results.append({"type": "text", "text": "Budget reached. Call finish with a summary."})
            messages.append({"role": "user", "content": results})
        if not self.state.finished:
            self.state.finished = "stopped: step limit reached"
        return self.state

    def _log(self, step: int, tool: str, args, result: dict) -> None:
        entry = {"step": step, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "input": args}
        short = {k: v for k, v in result.items() if k != "samples"}
        entry["result"] = short
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        if self.verbose:
            print(
                f"[{step}] {tool} {json.dumps(args, ensure_ascii=False)[:120]} -> "
                f"{json.dumps(short, ensure_ascii=False, default=str)[:160]}",
                file=sys.stderr,
            )


# -------------------------------------------------------------------- helpers


def _anthropic_client():
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError('The agent needs the Anthropic SDK: pip install "kazemo[agent]"') from e
    return anthropic.Anthropic(api_key=require("ANTHROPIC_API_KEY", "Create one at https://platform.claude.com."))


def _block_to_dict(b) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": dict(b.input)}
    return {"type": b.type}  # pragma: no cover


def _text_of(resp) -> str:
    return " ".join(b.text for b in resp.content if b.type == "text").strip()


def _read(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _rows_not_in(path: Path, uids: set[str]) -> list[dict]:
    return [r for r in _read(path) if r["uid"] not in uids]


def yields_by_source(state: AgentState) -> dict:
    out: dict[str, int] = defaultdict(int)
    for key, y in state.tried.items():
        out[key.split(":", 1)[0]] += y["kept"]
    return dict(out)
