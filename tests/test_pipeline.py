import json

import pytest

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
    assert main(["process", SAMPLE, "-o", str(out), "--plot", str(tmp_path / "p.png")]) == 0
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 6 and all(r["lang"] == "kk" for r in lines)
    report = json.loads(capsys.readouterr().out)
    assert report["kept"] == 6 and report["rejected"] == {"duplicate": 1, "language ru": 2, "language unknown": 1}
    rejected = (tmp_path / "clean.rejected.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rejected) == 4 and all("reason" in json.loads(x) for x in rejected)
    assert (tmp_path / "p.png").exists()


def test_cli_process_all_languages(tmp_path, capsys):
    out = tmp_path / "o.jsonl"
    assert main([SAMPLE, "-o", str(out), "--lang", "any", "--min-words", "1"]) == 0  # legacy form, no subcommand
    assert json.loads(capsys.readouterr().out)["kept"] == 8  # duplicate and "ок" removed


def test_cli_stats_and_version(tmp_path, capsys):
    assert main(["stats", SAMPLE]) == 0
    assert json.loads(capsys.readouterr().out)["posts"] == 10
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "kazemo 0.4.0" in capsys.readouterr().out
