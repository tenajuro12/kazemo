import pytest

from kazemo.preprocess import (
    clean,
    deduplicate,
    detect_language,
    detect_script,
    fingerprint,
    normalize,
    pseudonymise,
)


def test_normalize_collapses_whitespace():
    assert normalize("  сәлем \n\t әлем  ") == "сәлем әлем"


def test_clean_replaces_urls_and_hashtags():
    out = clean("Қараңыз https://example.com және t.me/abc #жаңалық")
    assert "<URL>" in out
    assert "https" not in out
    assert "#" not in out and "жаңалық" in out


def test_clean_shortens_elongations():
    assert clean("ураааааа") == "урааа"


@pytest.mark.parametrize("keep, expected", [(True, True), (False, False)])
def test_clean_emoji_flag(keep, expected):
    assert ("🎉" in clean("той 🎉", keep_emoji=keep)) is expected


def test_pseudonymise_is_stable_and_hides_names():
    a = pseudonymise("@aidos_kz сәлем")
    b = pseudonymise("рахмет @AIDOS_KZ")
    assert "aidos" not in a.lower()
    assert a.split()[0] == b.split()[1]


def test_pseudonymise_email_and_phone():
    out = pseudonymise("жаз test@mail.kz немесе +7 701 123 45 67")
    assert "<EMAIL>" in out and "<PHONE>" in out


@pytest.mark.parametrize(
    "text, script",
    [("сәлем", "cyrillic"), ("salem", "latin"), ("сәлем salem", "mixed"), ("123 !!!", "none")],
)
def test_detect_script(text, script):
    assert detect_script(text) == script


@pytest.mark.parametrize(
    "text, lang",
    [
        ("Бүгін өте қуаныштымын", "kk"),
        ("Сегодня хороший день", "ru"),
        ("Короче бүгін автобус келмеді", "kk"),
        ("Bugin keremet kun", "unknown"),
        ("", "unknown"),
    ],
)
def test_detect_language(text, lang):
    assert detect_language(text) == lang


def test_fingerprint_ignores_case_and_punctuation():
    assert fingerprint("Сәлем, әлем!") == fingerprint("сәлем әлем")


def test_deduplicate_keeps_order():
    assert deduplicate(["а б", "в", "А Б!", "г"]) == ["а б", "в", "г"]
