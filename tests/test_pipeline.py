import json

from kazemo.cli import main
from kazemo.pipeline import plot_labels, process, read_jsonl, stats

SAMPLE = "examples/sample.jsonl"


def test_process_sample():
    records = process(read_jsonl(SAMPLE))
    assert len(records) == 8  # one duplicate and one too-short post removed
    assert all("@aidos" not in r.text for r in records)
    assert {r.lang for r in records} <= {"kk", "ru", "unknown"}


def test_stats_counts():
    s = stats(process(read_jsonl(SAMPLE)))
    assert s["n"] == 8
    assert s["label"]["Joy"] == 3
    assert sum(s["lang"].values()) == 8


def test_plot_labels(tmp_path):
    out = tmp_path / "labels.png"
    plot_labels(process(read_jsonl(SAMPLE)), out)
    assert out.exists() and out.stat().st_size > 0


def test_cli_end_to_end(tmp_path, capsys):
    out = tmp_path / "clean.jsonl"
    assert main([SAMPLE, "-o", str(out), "--plot", str(tmp_path / "p.png")]) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8 and json.loads(lines[0])["lang"] == "kk"
    assert json.loads(capsys.readouterr().out)["n"] == 8
