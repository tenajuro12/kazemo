"""Command-line interface: ``kazemo input.jsonl -o output.jsonl``."""

from __future__ import annotations

import argparse
import json
import sys

from .pipeline import plot_labels, process, read_jsonl, stats, write_jsonl


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kazemo", description="Clean and tag Kazakh social-media posts.")
    p.add_argument("input", help='JSONL file, one {"text": ..., "label": ...} per line')
    p.add_argument("-o", "--output", required=True, help="where to write processed JSONL")
    p.add_argument("--min-chars", type=int, default=5, help="drop posts shorter than this")
    p.add_argument("--plot", help="optional PNG path for the label distribution chart")
    args = p.parse_args(argv)

    records = process(read_jsonl(args.input), min_chars=args.min_chars)
    write_jsonl(records, args.output)
    if args.plot:
        plot_labels(records, args.plot)
    json.dump(stats(records), sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
