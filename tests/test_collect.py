import json
from datetime import datetime, timezone

import pytest

from kazemo.cli import main
from kazemo.collect import TelegramCollector, ThreadsCollector, YouTubeCollector, collect_to_jsonl
from kazemo.collect.base import make_uid
from kazemo.config import MissingCredential, load_dotenv, require

# ---------- fakes -------------------------------------------------------------------------------


class FakeHttp:
    """Returns queued JSON pages and records every call."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, dict(params)))
        return self.pages.pop(0)


class Msg:
    def __init__(self, id_, text, views=10):
        self.id, self.message, self.views = id_, text, views
        self.date = datetime(2026, 10, 1, tzinfo=timezone.utc)


class FakeTelegram:
    def __init__(self, messages):
        self.messages = messages
        self.args = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def iter_messages(self, entity, limit=None, search=None):
        self.args = (entity, limit, search)
        for m in self.messages[:limit]:
            yield m


# ---------- Threads ----------------------------------------------------------------------------


def test_threads_search_paginates_and_pseudonymises():
    http = FakeHttp(
        [
            {
                "data": [
                    {"id": "1", "text": "@aidos_kz бүгін қатты уайымдаймын", "timestamp": "2026-10-01T10:00:00+0000"}
                ],
                "paging": {"cursors": {"after": "abc"}},
            },
            {"data": [{"id": "2", "text": "Сегодня отличный день"}, {"id": "3"}], "paging": {}},
        ]
    )
    posts = list(ThreadsCollector(token="t", http=http, delay=0).fetch("уайым", 10))
    assert [p.lang for p in posts] == ["kk", "ru"]
    assert "aidos" not in posts[0].text
    assert http.calls[0][0].endswith("/keyword_search") and http.calls[0][1]["q"] == "уайым"
    assert http.calls[1][1]["after"] == "abc"


def test_threads_respects_limit_and_replies_mode():
    http = FakeHttp(
        [{"data": [{"id": str(i), "text": f"жауап {i}"} for i in range(5)], "paging": {"cursors": {"after": "x"}}}]
    )
    posts = list(ThreadsCollector(mode="replies", token="t", http=http, delay=0).fetch("999", 3))
    assert len(posts) == 3 and http.calls[0][0].endswith("/999/replies")


def test_threads_bad_mode():
    with pytest.raises(ValueError):
        ThreadsCollector(mode="nope", token="t")


# ---------- YouTube ----------------------------------------------------------------------------


def _comment(cid, text):
    return {
        "id": cid,
        "snippet": {
            "topLevelComment": {"snippet": {"textOriginal": text, "publishedAt": "2026-10-01", "likeCount": 2}}
        },
    }


def test_youtube_video_comments_paginate():
    http = FakeHttp(
        [
            {"items": [_comment("a", "Керемет видео, рахмет!")], "nextPageToken": "p2"},
            {"items": [_comment("b", "Өте ұнады")]},
        ]
    )
    posts = list(YouTubeCollector(api_key="k", http=http, delay=0).fetch("VID1", 10))
    assert len(posts) == 2 and posts[0].meta == {"likes": 2, "video": "VID1"}
    assert http.calls[1][1]["pageToken"] == "p2"


def test_youtube_search_mode_uses_found_videos():
    http = FakeHttp(
        [
            {"items": [{"id": {"videoId": "V1"}}, {"id": {"videoId": "V2"}}]},
            {"items": [_comment("a", "бірінші пікір"), _comment("b", "екінші пікір")]},
            {"items": [_comment("c", "үшінші пікір")]},
        ]
    )
    posts = list(YouTubeCollector(mode="search", api_key="k", http=http, delay=0).fetch("қазақша", 3))
    assert [p.meta["video"] for p in posts] == ["V1", "V1", "V2"]
    assert all(p.channel == "қазақша" for p in posts)


# ---------- Telegram ---------------------------------------------------------------------------


def test_telegram_skips_empty_messages():
    fake = FakeTelegram([Msg(1, "Бүгін керемет күн!"), Msg(2, None), Msg(3, "Ертең емтихан, қорқып тұрмын")])
    posts = list(TelegramCollector(search="күн", client=fake).fetch("@kz_channel", 50))
    assert len(posts) == 2
    assert fake.args == ("kz_channel", 50, "күн")
    assert posts[0].uid == make_uid("telegram", "kz_channel/1")
    assert posts[0].date.startswith("2026-10-01") and posts[0].meta["views"] == 10


# ---------- sink: resume, filters --------------------------------------------------------------


def test_collect_to_jsonl_resumes_and_filters(tmp_path):
    out = tmp_path / "raw.jsonl"
    msgs = [Msg(1, "Бүгін керемет күн!"), Msg(2, "Сегодня хороший день"), Msg(3, "ок")]
    r1 = collect_to_jsonl(TelegramCollector(client=FakeTelegram(msgs)), ["ch"], out, langs={"kk"})
    assert (r1.written, r1.filtered) == (1, 2)
    r2 = collect_to_jsonl(TelegramCollector(client=FakeTelegram(msgs)), ["ch"], out, langs={"kk"})
    assert (r2.written, r2.duplicates) == (0, 1)
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["source"] == "telegram" and "author" not in rows[0]


# ---------- config and CLI ---------------------------------------------------------------------


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nKAZEMO_A='from_file'\nKAZEMO_B=2\n", encoding="utf-8")
    monkeypatch.setenv("KAZEMO_B", "already")
    monkeypatch.delenv("KAZEMO_A", raising=False)
    load_dotenv(env)
    assert require("KAZEMO_A") == "from_file" and require("KAZEMO_B") == "already"


def test_missing_credential_message(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    with pytest.raises(MissingCredential, match="YOUTUBE_API_KEY"):
        YouTubeCollector()


def test_cli_collect_without_key_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("THREADS_ACCESS_TOKEN", raising=False)
    assert main(["collect", "threads", "уайым", "-o", "raw.jsonl"]) == 2
    assert "THREADS_ACCESS_TOKEN" in capsys.readouterr().err


def test_cli_collect_youtube_with_fake_http(tmp_path, monkeypatch, capsys):
    import kazemo.collect.base as base

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setattr(base, "http_get_json", FakeHttp([{"items": [_comment("a", "Өте пайдалы видео екен")]}]))
    assert main(["collect", "youtube", "VID", "-o", "raw.jsonl", "--lang", "kk"]) == 0
    assert json.loads(capsys.readouterr().out)["written"] == 1
    assert main(["stats", "raw.jsonl"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == {"youtube": 1}


def test_http_get_json_retries_on_429(monkeypatch):
    import io
    import urllib.error

    import kazemo.collect.base as base

    calls = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 429, "slow down", {}, io.BytesIO(b"{}"))
        return Resp(b'{"ok": true}')

    monkeypatch.setattr(base.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    assert base.http_get_json("https://x.test/api", {"q": "сәлем"}) == {"ok": True}
    assert len(calls) == 2 and "q=" in calls[0]


def test_http_get_json_raises_on_403(monkeypatch):
    import io
    import urllib.error

    import kazemo.collect.base as base

    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url, 403, "forbidden", {}, io.BytesIO(b'{"error":"no permission"}'))

    monkeypatch.setattr(base.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(base.ApiError, match="403"):
        base.http_get_json("https://x.test/api", {})


def test_telegram_channel_search_keeps_only_public_channels(monkeypatch):
    import kazemo.collect.telegram as tg

    class Client(FakeTelegram):
        async def __call__(self, request):
            return type(
                "R",
                (),
                {
                    "chats": [
                        type(
                            "C", (), {"username": "kz_news", "title": "KZ", "broadcast": True, "participants_count": 5}
                        )(),
                        type("C", (), {"username": "kz_chat", "title": "Chat", "megagroup": True})(),
                        type("C", (), {"username": None, "title": "Private", "broadcast": True})(),
                        type("C", (), {"username": "small_group", "title": "Basic group"})(),
                    ]
                },
            )()

    monkeypatch.setattr(tg, "_search_request", lambda q, n: (q, n))
    found = TelegramCollector(client=Client([])).search_channels("қазақ", 10)
    assert [c["username"] for c in found] == ["kz_news", "kz_chat"]
    assert found[0]["members"] == 5


# ---------- Threads via Apify ------------------------------------------------------------------


class FakePost:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, url, params, body):
        self.calls.append((url, params, body))
        return self.reply


APIFY_ITEMS = [
    {
        "postId": "111",
        "text": "Бүгін қатты қуаныштымын, рахмет бәріне!",
        "timestamp": 1727776800,
        "username": "real_person",
        "authorId": "999",
        "displayName": "Real Person",
        "likeCount": 12,
        "replyCount": 3,
    },
    {"postId": "112", "text": "Сегодня просто устал", "timestamp": "2026-10-01T10:00:00Z", "likeCount": None},
    {"postId": "113", "text": ""},
    {"text": "без id"},
]


@pytest.fixture
def no_apify_env(monkeypatch):
    for k in ("APIFY_THREADS_SESSION", "APIFY_THREADS_ACTOR"):
        monkeypatch.delenv(k, raising=False)


def test_apify_default_actor_needs_no_login(no_apify_env):
    from kazemo.collect import ApifyThreadsCollector

    http = FakePost(APIFY_ITEMS)
    posts = list(ApifyThreadsCollector(token="tok", http=http).fetch("қуаныш", 50))

    url, params, body = http.calls[0]
    assert url.endswith("/actors/aydar_cosmos~threads-scraper/run-sync-get-dataset-items")
    assert params == {"maxItems": 50, "timeout": 290}  # token goes in a header, not the URL
    assert body == {"mode": "search", "query": "қуаныш", "maxResults": 50}
    assert len(posts) == 2
    p = posts[0]
    assert p.source == "threads" and p.channel == "қуаныш" and p.lang == "kk"
    assert p.date.startswith("2024-10-01") and p.meta == {"likes": 12, "replies": 3, "via": "apify"}
    dumped = json.dumps(p.__dict__, ensure_ascii=False)
    assert "real_person" not in dumped and "999" not in dumped and "Real Person" not in dumped
    assert posts[1].date == "2026-10-01T10:00:00Z" and posts[1].meta["likes"] == 0


def test_apify_alternative_field_names(no_apify_env):
    from kazemo.collect import ApifyThreadsCollector

    items = [
        {
            "code": "Cx1",
            "caption": {"text": "Ертең емтихан, уайымдаймын"},
            "taken_at": 1727776800000,
            "like_count": "7",
            "reply_count": {"count": 2},
            "user": {"username": "someone"},
        },
    ]
    (p,) = ApifyThreadsCollector(token="t", http=FakePost(items)).fetch("уайым", 10)
    assert p.text.startswith("Ертең") and p.date.startswith("2024-10-01") and p.meta["likes"] == 7
    assert p.meta["replies"] == 2 and "someone" not in json.dumps(p.__dict__)


def test_apify_unknown_fields_are_reported(no_apify_env):
    from kazemo.collect import ApifyThreadsCollector
    from kazemo.collect.base import ApiError

    with pytest.raises(ApiError, match=r"\['message', 'uuid'\]"):
        list(ApifyThreadsCollector(token="t", http=FakePost([{"uuid": "1", "message": "x"}])).fetch("x", 5))


def test_apify_session_actor(no_apify_env, monkeypatch):
    from kazemo.collect import ApifyThreadsCollector

    actor = "futurizerush/threads-search-scraper-api"
    with pytest.raises(MissingCredential, match="APIFY_THREADS_SESSION"):
        ApifyThreadsCollector(token="t", actor=actor)
    monkeypatch.setenv("APIFY_THREADS_SESSION", "sess")
    c = ApifyThreadsCollector(mode="top", token="t", actor=actor, http=FakePost([]))
    assert c.build_input("x", 5) == {"keywords": ["x"], "maxResults": 10, "searchType": "top", "sessionId": "sess"}
    assert c.build_input("x", 9999)["maxResults"] == 2000
    list(c.fetch("x", 5))
    assert "futurizerush~threads-search-scraper-api" in c.http.calls[0][0]


def test_apify_actor_from_env_and_bad_mode(no_apify_env, monkeypatch):
    from kazemo.collect import ApifyThreadsCollector

    monkeypatch.setenv("APIFY_THREADS_ACTOR", "someone/other-actor")
    c = ApifyThreadsCollector(token="t", http=FakePost([]))
    list(c.fetch("x", 5000))
    assert "someone~other-actor" in c.http.calls[0][0] and c.http.calls[0][2]["maxResults"] == 1000
    with pytest.raises(ValueError):
        ApifyThreadsCollector(mode="search", token="t")


def test_apify_threads_errors(no_apify_env, monkeypatch):
    from kazemo.collect import ApifyThreadsCollector
    from kazemo.collect.base import ApiError

    with pytest.raises(ApiError, match="not enough credit"):
        list(ApifyThreadsCollector(token="t", http=FakePost({"error": "not enough credit"})).fetch("x", 5))

    def timeout(url, params, body):
        raise ApiError("HTTP 408 from apify: run timed out")

    with pytest.raises(ApiError, match="smaller --limit"):
        list(ApifyThreadsCollector(token="t", http=timeout).fetch("x", 2000))

    def other(url, params, body):
        raise ApiError("HTTP 402 from apify")

    with pytest.raises(ApiError, match="402"):
        list(ApifyThreadsCollector(token="t", http=other).fetch("x", 5))
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(MissingCredential, match="APIFY_TOKEN"):
        ApifyThreadsCollector()


def test_cli_collect_threads_apify(tmp_path, monkeypatch, capsys, no_apify_env):
    import kazemo.collect.base as base

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APIFY_TOKEN", "tok")
    calls = []

    def fake(url, params, body, headers=None):
        calls.append((url, params, body, headers))
        return APIFY_ITEMS

    monkeypatch.setattr(base, "http_post_json", fake)
    assert main(["collect", "threads-apify", "қуаныш", "-o", "raw.jsonl", "--lang", "kk"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "source": "threads-apify",
        "written": 1,
        "duplicates": 0,
        "filtered": 1,
    }
    assert calls[0][2]["query"] == "қуаныш"
    assert calls[0][3] == {"Authorization": "Bearer tok"} and "tok" not in calls[0][0]


def test_http_post_json_retries_then_succeeds(monkeypatch):
    import io
    import urllib.error

    import kazemo.collect.base as base

    sent = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        sent.append(req)
        if len(sent) == 1:
            raise urllib.error.HTTPError(req.full_url, 503, "busy", {}, io.BytesIO(b""))
        return Resp(b'[{"postId": "1"}]')

    monkeypatch.setattr(base.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    out = base.http_post_json(
        "https://x.test/run", {"a": 1}, {"keywords": ["сәлем"]}, headers={"Authorization": "Bearer t"}
    )
    assert out == [{"postId": "1"}]
    assert sent[1].get_method() == "POST" and json.loads(sent[1].data) == {"keywords": ["сәлем"]}
    assert sent[1].get_header("Authorization") == "Bearer t"

    def always_401(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad token"))

    monkeypatch.setattr(base.urllib.request, "urlopen", always_401)
    with pytest.raises(base.ApiError, match="401"):
        base.http_post_json("https://x.test/run", {}, {})


def test_cli_api_error_is_one_line(tmp_path, monkeypatch, capsys, no_apify_env):
    import kazemo.collect.base as base

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APIFY_TOKEN", "tok")

    def fail(url, params, body, headers=None):
        raise base.ApiError("HTTP 400 from apify: invalid input")

    monkeypatch.setattr(base, "http_post_json", fail)
    assert main(["collect", "threads-apify", "x", "-o", "raw.jsonl"]) == 1
    assert capsys.readouterr().err.strip() == "error: HTTP 400 from apify: invalid input"
