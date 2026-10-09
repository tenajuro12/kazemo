import json

from kazemo.cli import main
from kazemo.dashboard import build_payload, no_external_resources, render
from kazemo.pipeline import prepare, write_rows

RAW = [
    {"uid": "1", "source": "threads", "channel": "қуаныштымын", "date": "2026-10-08T14:51:02Z",
     "text": "тағы қандай контент жасайық, идеяларыңыз бар ма 😁", "meta": {"likes": 1603, "replies": 34}},
    {"uid": "2", "source": "threads", "channel": "қуаныштымын", "date": "2026-10-09T07:02:48Z",
     "text": "Пайдалы болғанына қуаныштымын", "meta": {"likes": 1}},
    {"uid": "3", "source": "threads", "channel": "уайымдаймын", "date": "2026-10-09T08:00:00Z",
     "text": "Ертең емтихан, қатты уайымдаймын 😭😭 </script><b>x</b>", "meta": {"likes": 4}},
    {"uid": "4", "source": "telegram", "channel": "kz_news", "date": "2026-10-07T10:00:00Z",
     "text": "Сегодня отличный день, всем добра"},
]  # fmt: skip


def make_data(tmp_path, report=None):
    kept, rejected, _ = prepare(RAW)
    write_rows(RAW, tmp_path / "raw.jsonl")
    write_rows(kept, tmp_path / "clean.jsonl")
    write_rows(rejected, tmp_path / "rejected.jsonl")
    if report is not None:
        (tmp_path / "report.json").write_text(json.dumps(report), encoding="utf-8")
    return tmp_path


def test_payload_aggregates(tmp_path):
    p = build_payload(make_data(tmp_path))
    assert p["counts"] == {"raw": 4, "kept": 2, "rejected": 2}
    assert p["reasons"] == {"formulaic": 1, "language ru": 1}
    assert dict(p["emoji"]) == {"😭": 2, "😁": 1}
    assert p["days"] == [("2026-10-08", 1), ("2026-10-09", 1)]
    y = {r["target"]: r for r in p["yields"]}
    assert y["қуаныштымын"] == {"source": "threads", "target": "қуаныштымын", "collected": 2, "kept": 1,
                                 "rate": 0.5, "rejected": {"formulaic": 1}}  # fmt: skip
    assert y["kz_news"]["rate"] == 0 and y["kz_news"]["rejected"] == {"language ru": 1}
    words = dict(p["words"])
    assert "емтихан" in words and "user" not in words
    k = p["kept"][0]
    assert set(k) >= {"t", "d", "s", "c", "lk", "rp", "e"} and p["rejected"][0]["why"] in ("formulaic", "language ru")


def test_render_is_offline_and_safe(tmp_path):
    html = render(build_payload(make_data(tmp_path)))
    assert no_external_resources(html)
    assert "</script><b>" not in html  # post text cannot close the script tag
    assert "<\\/script>" in html and "/*__DATA__*/" not in html
    assert html.count("<script>") == 1


def test_empty_folder(tmp_path):
    p = build_payload(tmp_path)
    assert p["counts"] == {"raw": 0, "kept": 0, "rejected": 0} and p["report"] == {}
    assert "kazemo dashboard" in render(p)


def test_cli_and_run_write_dashboard(tmp_path, capsys, monkeypatch):
    data = make_data(tmp_path / "data", report={"collect": {"threads-apify": {"error": "Threads was not responding"}}})
    out = tmp_path / "board.html"
    assert main(["dashboard", str(data), "-o", str(out)]) == 0
    assert "dashboard:" in capsys.readouterr().out and "Threads was not responding" in out.read_text(encoding="utf-8")
    assert main(["dashboard", str(data)]) == 0 and (data / "dashboard.html").exists()

    from kazemo.run import run

    monkeypatch.chdir(tmp_path)
    (tmp_path / "p.toml").write_text('[output]\ndir = "data"\n', encoding="utf-8")
    rep = run("p.toml", skip_collect=True)
    assert rep["dashboard"].endswith("dashboard.html")
    (tmp_path / "q.toml").write_text('[output]\ndir = "data"\ndashboard = false\n', encoding="utf-8")
    assert "dashboard" not in run("q.toml", skip_collect=True)
