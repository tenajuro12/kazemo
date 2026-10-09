import json

import pytest

from kazemo import filters
from kazemo.collect.base import Collector, to_post
from kazemo.pipeline import PrepConfig, corpus_report, prepare, prepare_row
from kazemo.preprocess import (
    anonymise,
    count_hashtags,
    emojis_to_text,
    extract_emojis,
    remove_emojis,
    strip_trailing_hashtags,
)
from kazemo.run import run

AD = (
    "Менің алғашқы Beauty UGC видеоларымның бірі 🤍✨ Бүгін өнімдерді түсірдім 🥹❤️ "
    "#ugckazakhstan #UGCCreator #BeautyUGC #SKIN1004 #мобилография"
)

# ---------- text helpers ------------------------------------------------------------------------


def test_anonymise_keeps_hashtags_and_emoji():
    out = anonymise("@aidos_kz қуаныштымын 🥹 https://threads.net/@someone #той")
    assert out.startswith("@user_") and "<URL>" in out and "#той" in out and "🥹" in out
    assert "someone" not in out and "aidos" not in out


def test_emoji_helpers():
    t = "жылап отырмын 😭👍🏽❤️"
    assert extract_emojis(t) == ["😭", "👍", "❤"]
    assert remove_emojis(t) == "жылап отырмын"
    assert emojis_to_text(t) == "жылап отырмын :loudly_crying_face: :thumbs_up_sign: :heavy_black_heart:"


def test_hashtag_helpers():
    assert count_hashtags(AD) == 5
    assert strip_trailing_hashtags(AD).endswith("🥹❤️")
    assert strip_trailing_hashtags("#жалғыз тег ортада") == "#жалғыз тег ортада"


# ---------- filters -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, is_ad",
    [
        (AD, True),
        ("Жеңілдік! Бағасы 5000 тг, тапсырыс директке", True),
        ("Скидка 20%, пишите в директ", True),
        ("Ватсапқа жазыңыз <PHONE>, бағасы арзан", True),
        ("Бүгін дүкеннен тапсырыс бердім, қуаныштымын", False),
        ("Бүгін өнімдерді түсірдім, қуаныштымын 🥹 ugckazakhstan UGCCreator мобилография", True),  # old export, no #
        ("Ертең емтихан, қатты уайымдаймын", False),
    ],
)
def test_is_ad(text, is_ad):
    assert (filters.is_ad(text) is not None) is is_ad


@pytest.mark.parametrize(
    "text, formulaic",
    [
        ("Пайдалы болғанына қуаныштымын", True),
        ("Рахмет!", True),
        ("Көп рахмет 🙏", True),
        ("Спасибо, да", True),
        ("Рахмет, бірақ мен әлі де қатты ренжулімін", False),
        ("Жап-жаңа біліп отырмын Сау болыңыз!", False),
    ],
)
def test_is_formulaic(text, formulaic):
    assert (filters.is_formulaic(text) is not None) is formulaic


def test_words_ignore_tokens():
    assert filters.words("@user_ab12 <URL> #тег жаңа 😭 сөз 123") == ["жаңа", "сөз"]
    assert filters.is_too_short("Иә 😭") == "too short (1 words)"
    assert filters.is_mostly_hashtags("#a #b #c сөз") == "mostly hashtags"


# ---------- prepare -----------------------------------------------------------------------------


def test_prepare_reasons_and_kept_fields():
    rows = [
        {"uid": "1", "source": "threads", "text": AD},
        {"uid": "2", "text": "Пайдалы болғанына қуаныштымын"},
        {
            "uid": "3",
            "source": "threads",
            "text": "Ертең емтихан, қатты уайымдаймын 😭😭😭😭 @aidos_kz",
            "meta": {"x": 1},
        },
        {"uid": "4", "text": "Ертең емтихан, ҚАТТЫ уайымдаймын!!! 😭"},
        {"uid": "5", "text": "Сегодня отличный день, всем добра"},
        {"uid": "6", "text": "Иә 😭"},
        {"uid": "7", "text": ""},
    ]
    kept, rejected, report = prepare(rows)
    assert [r["uid"] for r in kept] == ["3"]
    k = kept[0]
    assert k["text"].startswith("Ертең емтихан, қатты уайымдаймын 😭😭😭") and "aidos" not in k["text"]
    assert k["emojis"] == ["😭", "😭", "😭"] and k["meta"] == {"x": 1} and k["source"] == "threads"
    assert {r["uid"]: r["reason"].split(" (")[0] for r in rejected} == {
        "1": "ad",
        "2": "formulaic",
        "4": "duplicate",
        "5": "language ru",
        "6": "too short",
        "7": "empty",
    }
    assert report.total == 7 and report.kept == 1 and report.rejected["ad"] == 1


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("keep", "қатты қуаныштымын 😭"),
        ("remove", "қатты қуаныштымын"),
        ("text", "қатты қуаныштымын :loudly_crying_face:"),
    ],
)
def test_emoji_modes(mode, expected):
    out, reason = prepare_row({"text": "Бүгін қатты қуаныштымын 😭"}, PrepConfig(emoji=mode))
    assert reason is None and out["text"] == "Бүгін " + expected and out["emojis"] == ["😭"]

def test_config_switches():
    cfg = PrepConfig(langs=None, filter_ads=False, filter_formulaic=False, min_words=1, deduplicate=False)
    kept, _, _ = prepare([{"text": AD}, {"text": "Рахмет!"}, {"text": "Рахмет!"}, {"text": "Спасибо всем"}], cfg)
    assert len(kept) == 4
    assert not kept[0]["text"].endswith("мобилография")  # trailing tags still stripped
    with pytest.raises(ValueError):
        PrepConfig(emoji="nope")


def test_corpus_report():
    kept, _, _ = prepare([{"source": "threads", "text": "Бүгін қатты қуаныштымын 🥹🥹", "label": "Joy"}])
    rep = corpus_report(kept)
    assert rep["posts"] == 1 and rep["source"] == {"threads": 1} and rep["top_emoji"] == {"🥹": 2}
    assert rep["with_emoji"] == 1 and rep["label"] == {"Joy": 1}


# ---------- kazemo run --------------------------------------------------------------------------


class Fake(Collector):
    POSTS = {
        "kk": ["Бүгін қатты қуаныштымын 🥹", "Ертең емтихан, уайымдаймын 😭", "Пайдалы болғанына қуаныштымын", AD],
        "ru": ["Сегодня отличный день, всем добра"],
    }

    def __init__(self, mode=None, search=None):
        self.mode = mode

    def fetch(self, target, limit):
        for i, t in enumerate(self.POSTS[target][:limit]):
            yield to_post("fake", f"{target}{i}", target, "2026-10-09", t)


def broken(**kw):
    raise RuntimeError("API quota exceeded")


def test_run_pipeline_from_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "p.toml").write_text(
        """
[output]
dir = "out"
[collect]
limit = 10
lang = []
[[collect.source]]
name = "fake"
targets = ["kk", "ru"]
mode = "recent"
[[collect.source]]
name = "broken"
targets = ["x"]
[[collect.source]]
name = "nope"
targets = ["x"]
[process]
lang = ["kk"]
emoji = "keep"
""",
        encoding="utf-8",
    )
    report = run("p.toml", collectors={"fake": Fake, "broken": broken})
    assert report["collect"]["fake"] == {"written": 5, "duplicates": 0, "filtered": 0}
    assert "quota" in report["collect"]["broken"]["error"]
    assert "unknown source" in report["collect"]["nope"]["error"]
    assert report["process"] == {"total": 5, "kept": 2, "rejected": {"formulaic": 1, "ad": 1, "language ru": 1}}
    clean = [json.loads(x) for x in (tmp_path / "out/clean.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [c["emojis"] for c in clean] == [["🥹"], ["😭"]]
    assert json.loads((tmp_path / "out/report.json").read_text(encoding="utf-8"))["corpus"]["posts"] == 2

    # second run: nothing new collected, processing is repeated on the same raw file
    again = run("p.toml", collectors={"fake": Fake, "broken": broken})
    assert again["collect"]["fake"]["duplicates"] == 5 and again["process"]["kept"] == 2
    only = run("p.toml", skip_collect=True)
    assert "collect" not in only and only["process"]["kept"] == 2


def test_example_config_is_valid():
    from kazemo.run import load_config, prep_config

    cfg = load_config("pipeline.example.toml")
    assert cfg["collect"]["source"][0]["name"] == "threads-apify"
    assert prep_config(cfg).emoji == "keep"
