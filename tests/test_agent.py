import copy
import json
from types import SimpleNamespace as NS

import pytest

import kazemo.agent as agent_mod
from kazemo.agent import CollectionAgent, tool_specs, yields_by_source
from kazemo.cli import main
from kazemo.collect.base import Collector, to_post

KK = ["Бүгін қатты қуаныштымын", "Ертең емтихан, уайымдаймын", "Өте шаршадым бүгін", "Рахмет бәріңізге, керемет күн"]
RU = ["Сегодня отличный день", "Очень устал после работы"]


class FakeSource(Collector):
    """Returns Kazakh posts for targets containing 'kz', Russian ones otherwise."""

    def __init__(self, name):
        self.name = name
        self.search = None
        self.fetch_calls = []

    def fetch(self, target, limit):
        self.fetch_calls.append((target, limit))
        texts = KK if "kz" in target else RU
        for i, t in enumerate(texts[:limit]):
            yield to_post(self.name, f"{target}/{i}", target, "2026-10-01", t)

    def search_channels(self, query, limit):
        return [{"username": f"{query}_kz", "title": query, "members": 1200}]


def tool_use(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def reply(*blocks):
    return NS(content=list(blocks), usage=NS(input_tokens=100, output_tokens=20))


class FakeLLM:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def create(self, **kw):
        self.requests.append(copy.deepcopy(kw))  # the agent keeps appending to the same list
        return self.script.pop(0)


def make_agent(tmp_path, script, sources=("telegram", "youtube"), **kw):
    fakes = {s: FakeSource(s) for s in sources}
    llm = FakeLLM(script)
    a = CollectionAgent(
        goal="Kazakh emotional posts",
        output=tmp_path / "raw.jsonl",
        sources=list(sources),
        llm=llm,
        collectors=fakes,
        verbose=False,
        **kw,
    )
    return a, llm, fakes


def test_full_run_discovers_collects_and_finishes(tmp_path):
    script = [
        reply(
            NS(type="text", text="Let me look for channels."), tool_use("1", "find_telegram_channels", query="қуаныш")
        ),
        reply(tool_use("2", "collect", source="telegram", targets=["қуаныш_kz", "news_ru"], limit=10)),
        reply(tool_use("3", "corpus_stats")),
        reply(tool_use("4", "finish", summary="Telegram channel worked, Russian news did not.")),
    ]
    a, llm, fakes = make_agent(tmp_path, script, seeds=["youtube:қазақша подкаст"])
    state = a.run()

    assert state.written == 4  # only Kazakh kept
    assert state.tried["telegram:қуаныш_kz"]["kept_share"] == 1.0
    assert state.tried["telegram:news_ru"]["kept"] == 0 and state.tried["telegram:news_ru"]["rejected"] == 2
    assert state.finished.startswith("Telegram channel worked")
    assert (state.input_tokens, state.output_tokens) == (400, 80)
    first = llm.requests[0]["messages"][0]["content"]
    assert "қазақша подкаст" in first and "telegram, youtube" in first
    # tool results go back to the model with the matching id
    assert llm.requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "1"
    stats = json.loads(llm.requests[3]["messages"][-1]["content"][0]["content"])
    assert stats["posts"] == 4 and stats["lang"] == {"kk": 4}
    log = [json.loads(x) for x in a.log_path.read_text(encoding="utf-8").splitlines()]
    assert [e["tool"] for e in log] == ["find_telegram_channels", "collect", "corpus_stats", "finish"]
    assert yields_by_source(state) == {"telegram": 4}


def test_budget_caps_collection(tmp_path):
    script = [
        reply(tool_use("1", "collect", source="telegram", targets=["a_kz", "b_kz"], limit=500)),
        reply(tool_use("2", "collect", source="telegram", targets=["c_kz"], limit=10)),
        reply(tool_use("3", "finish", summary="done")),
    ]
    a, llm, fakes = make_agent(tmp_path, script, max_posts=3)
    state = a.run()
    assert state.written == 3
    assert fakes["telegram"].fetch_calls == [("a_kz", 3)]  # limit clipped to budget, b_kz skipped
    last = llm.requests[1]["messages"][-1]["content"]
    assert json.loads(last[0]["content"])["budget_left"] == 0
    assert "Budget reached" in last[-1]["text"]
    third = json.loads(llm.requests[2]["messages"][-1]["content"][0]["content"])
    assert "budget" in third["error"]


def test_errors_are_returned_to_the_model(tmp_path):
    class Broken(FakeSource):
        def fetch(self, target, limit):
            raise RuntimeError("API quota exceeded")
            yield  # pragma: no cover

    script = [
        reply(
            tool_use("1", "collect", source="threads", targets=["x"], limit=5),
            tool_use("2", "collect", source="youtube", targets=["x"], limit=5),
            tool_use("3", "nonexistent"),
            tool_use("4", "collect", source="telegram"),
        ),
        reply(NS(type="text", text="Nothing more to do.")),
    ]
    a, llm, fakes = make_agent(tmp_path, script)
    a._collectors["youtube"] = Broken("youtube")
    state = a.run()
    results = [json.loads(r["content"]) for r in llm.requests[1]["messages"][-1]["content"]]
    assert "not enabled" in results[0]["error"]
    assert "quota" in results[1]["error"]
    assert "unknown tool" in results[2]["error"]
    assert "bad arguments" in results[3]["error"]
    assert state.finished == "Nothing more to do."


def test_step_limit(tmp_path):
    script = [reply(tool_use(str(i), "corpus_stats")) for i in range(10)]
    a, llm, _ = make_agent(tmp_path, script, max_steps=3)
    state = a.run()
    assert len(llm.requests) == 3 and state.finished == "stopped: step limit reached"


def test_tool_specs_follow_enabled_sources():
    names = [t["name"] for t in tool_specs(["youtube"])]
    assert "find_telegram_channels" not in names and names[0] == "collect"
    collect = tool_specs(["telegram", "youtube"])[1]
    assert collect["input_schema"]["properties"]["source"]["enum"] == ["telegram", "youtube"]


def test_unknown_source_rejected(tmp_path):
    with pytest.raises(ValueError):
        CollectionAgent(goal="g", output=tmp_path / "x.jsonl", sources=["vk"], llm=FakeLLM([]))


def test_missing_api_key(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert main(["agent", "--goal", "g", "-o", "raw.jsonl"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_agent(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    llm = FakeLLM(
        [
            reply(tool_use("1", "collect", source="youtube", targets=["қазақша kz"], limit=10)),
            reply(tool_use("2", "finish", summary="ok")),
        ]
    )
    monkeypatch.setattr(agent_mod, "_anthropic_client", lambda: llm)
    monkeypatch.setitem(agent_mod.COLLECTORS, "youtube", lambda **kw: FakeSource("youtube"))
    code = main(["agent", "--goal", "g", "-o", "raw.jsonl", "--sources", "youtube", "--model", "claude-haiku-5-5"])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["kept"] == 4 and out["by_source"] == {"youtube": 4}
    assert llm.requests[0]["model"] == "claude-haiku-5-5"
    assert (tmp_path / "raw.agent.jsonl").exists()
