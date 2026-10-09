"""Command-line interface.

kazemo collect telegram some_channel other_channel -o data/raw.jsonl --limit 1000 --lang kk
kazemo collect threads "қуаныш" "уайым" -o data/raw.jsonl
kazemo collect youtube "қазақша влог" --mode search -o data/raw.jsonl
kazemo process data/raw.jsonl -o data/clean.jsonl --plot labels.png
kazemo stats data/clean.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import MissingCredential, load_dotenv
from .pipeline import plot_labels, process, read_jsonl, stats, write_jsonl

SOURCES = ["telegram", "threads", "threads-apify", "youtube"]
COMMANDS = {"collect", "process", "stats", "login", "agent"}


def _print_json(obj: dict) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    print()


def cmd_collect(args: argparse.Namespace) -> int:
    from .collect import COLLECTORS, collect_to_jsonl

    opts: dict = {}
    if args.source == "telegram":
        opts["search"] = args.search
    elif args.mode:
        opts["mode"] = args.mode
    collector = COLLECTORS[args.source](**opts)
    report = collect_to_jsonl(
        collector,
        args.targets,
        args.output,
        limit=args.limit,
        langs=set(args.lang) if args.lang else None,
        min_chars=args.min_chars,
    )
    _print_json(
        {"source": args.source, "written": report.written, "duplicates": report.duplicates, "filtered": report.filtered}
    )
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    records = process(read_jsonl(args.input), min_chars=args.min_chars)
    write_jsonl(records, args.output)
    if args.plot:
        plot_labels(records, args.plot)
    _print_json(stats(records))
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    rows = read_jsonl(args.input)
    out = stats(process(rows, min_chars=0))
    sources: dict[str, int] = {}
    for r in rows:
        if "source" in r:
            sources[r["source"]] = sources.get(r["source"], 0) + 1
    if sources:
        out["source"] = sources
    _print_json(out)
    return 0


def cmd_agent(args: argparse.Namespace) -> int:
    from .agent import CollectionAgent, yields_by_source

    langs = None if args.lang == ["any"] else set(args.lang)
    agent = CollectionAgent(
        goal=args.goal,
        output=args.output,
        sources=args.sources,
        max_posts=args.max_posts,
        max_steps=args.max_steps,
        langs=langs,
        model=args.model,
        seeds=args.seed,
        log_path=args.log,
    )
    state = agent.run()
    _print_json(
        {
            "kept": state.written,
            "by_source": yields_by_source(state),
            "targets_tried": len(state.tried),
            "summary": state.finished,
            "log": str(agent.log_path),
            "tokens": {"input": state.input_tokens, "output": state.output_tokens},
        }
    )
    return 0


def cmd_login(args: argparse.Namespace) -> int:  # pragma: no cover - interactive
    from .collect.telegram import login

    print("TELEGRAM_SESSION=" + login())
    print("Store this value in .env or as a GitHub secret. Treat it like a password.", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kazemo", description="Collect and pre-process Kazakh social-media text.")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("collect", help="collect posts from a social network into JSONL")
    c.add_argument("source", choices=SOURCES)
    c.add_argument(
        "targets",
        nargs="+",
        help="channels (telegram), keywords or media IDs (threads), video IDs or queries (youtube)",
    )
    c.add_argument("-o", "--output", required=True, help="JSONL file; new posts are appended, re-runs resume")
    c.add_argument("--limit", type=int, default=500, help="max posts per target (default 500)")
    c.add_argument("--lang", nargs="+", choices=["kk", "ru", "unknown"], help="keep only these languages")
    c.add_argument("--min-chars", type=int, default=5)
    c.add_argument("--mode", help="threads: search | replies; threads-apify: recent | top; youtube: video | search")
    c.add_argument("--search", help="telegram: only messages containing this word")
    c.set_defaults(func=cmd_collect)

    pr = sub.add_parser("process", help="clean, pseudonymise, deduplicate and tag a JSONL file")
    pr.add_argument("input")
    pr.add_argument("-o", "--output", required=True)
    pr.add_argument("--min-chars", type=int, default=5)
    pr.add_argument("--plot", help="PNG path for the label distribution chart")
    pr.set_defaults(func=cmd_process)

    s = sub.add_parser("stats", help="print corpus statistics for a JSONL file")
    s.add_argument("input")
    s.set_defaults(func=cmd_stats)

    a = sub.add_parser("agent", help="let an LLM plan and run the collection within a budget")
    a.add_argument("--goal", required=True, help="what the corpus should contain, in plain words")
    a.add_argument("-o", "--output", required=True)
    a.add_argument("--sources", nargs="+", default=["telegram", "threads", "youtube"], choices=SOURCES)
    a.add_argument("--max-posts", type=int, default=2000, help="stop after this many kept posts")
    a.add_argument("--max-steps", type=int, default=30, help="max tool calls the agent may make")
    a.add_argument("--lang", nargs="+", default=["kk"], choices=["kk", "ru", "unknown", "any"])
    a.add_argument("--model", default="claude-sonnet-5-5", help="e.g. claude-haiku-5-5 for a cheaper run")
    a.add_argument(
        "--seed", action="append", help="starting point, e.g. 'telegram:some_channel' or 'youtube:қазақша подкаст'"
    )
    a.add_argument("--log", help="JSONL log of every agent step (default: <output>.agent.jsonl)")
    a.set_defaults(func=cmd_agent)

    lg = sub.add_parser("login", help="log in to Telegram once and print a reusable session string")
    lg.add_argument("service", choices=["telegram"])
    lg.set_defaults(func=cmd_login)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "process")  # backward compatible: `kazemo file.jsonl -o out.jsonl`
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MissingCredential as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
