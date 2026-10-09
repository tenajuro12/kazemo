import csv
import json

import pytest

from kazemo.annotate import (
    LabseJoblibPredictor,
    annotate,
    export_csv,
    export_labelstudio,
    load_predictor,
    review_order,
    summary,
)
from kazemo.cli import main

ROWS = [
    {"uid": "a", "source": "threads", "text": "Бүгін қатты қуаныштымын 🥹"},
    {"uid": "b", "source": "threads", "text": "Ертең емтихан, қатты уайымдаймын 😭"},
    {"uid": "c", "source": "telegram", "text": "Енді ешнәрсе қаламаймын, шаршадым"},
]

PREDICTOR = """
def predict(texts):
    out = []
    for t in texts:
        if "қуаныш" in t:
            out.append({"label": "Joy", "probs": {"Joy": 0.9, "Sadness": 0.05, "Anxiety": 0.05}, "distress": False})
        elif "уайым" in t:
            out.append({"label": "Anxiety", "score": 0.55, "distress": "no", "distress_score": 0.3})
        else:
            out.append({"label": "Sadness", "score": 0.8, "distress": True, "distress_score": 0.92})
    return out
"""


@pytest.fixture
def model_file(tmp_path):
    p = tmp_path / "my_model.py"
    p.write_text(PREDICTOR, encoding="utf-8")
    return f"{p}:predict"


def test_load_from_file_and_annotate(model_file):
    out = annotate(ROWS, load_predictor(model_file), model_name="hier-v5", batch_size=2)
    a, b, c = (r["pre"] for r in out)
    assert a == {
        "label": "Joy", "score": 0.9, "margin": 0.85, "entropy": a["entropy"],
        "probs": {"Joy": 0.9, "Sadness": 0.05, "Anxiety": 0.05},
        "distress": False, "model": "hier-v5", "needs_review": False,
    }  # fmt: skip
    assert 0 < a["entropy"] < 1
    assert b["needs_review"] and b["distress"] is False and b["distress_score"] == 0.3  # low confidence
    assert c["needs_review"] and c["distress"] is True  # distress is always reviewed
    assert out[0]["text"] == ROWS[0]["text"] and out[0]["source"] == "threads"


def test_review_order_and_summary(model_file):
    out = review_order(annotate(ROWS, load_predictor(model_file)))
    assert [r["uid"] for r in out] == ["c", "b", "a"]
    s = summary(out)
    assert s == {"posts": 3, "pre_label": {"Sadness": 1, "Anxiety": 1, "Joy": 1}, "distress": 1,
                 "needs_review": 2, "mean_score": 0.75}  # fmt: skip


@pytest.mark.parametrize(
    "spec, name",
    [
        (r"C:\Users\Forte\thesis\predict_distress.py:predict", "predict_distress"),
        ("models/hier.py:predict", "hier"),
        ("thesis.models.hier:predict", "hier"),
        ("labse-joblib:models/v5", "v5"),
    ],
)
def test_default_model_name(spec, name):
    from kazemo.annotate import default_model_name

    assert default_model_name(spec) == name


def test_bad_specs_and_predictions(tmp_path):
    with pytest.raises(ValueError):
        load_predictor("no_colon_here")
    with pytest.raises(FileNotFoundError):
        load_predictor(str(tmp_path / "missing.py") + ":predict")
    with pytest.raises(AttributeError):
        load_predictor("json:not_a_function")
    with pytest.raises(ValueError, match="2 predictions for 3"):
        annotate(ROWS, lambda texts: [{"label": "Joy", "score": 1}] * 2)
    with pytest.raises(ValueError, match="'label'"):
        annotate(ROWS[:1], lambda texts: [{"score": 1}])
    assert load_predictor("json:dumps") is json.dumps  # module:function form


def test_exports(tmp_path, model_file):
    out = annotate(ROWS, load_predictor(model_file), model_name="v5")
    export_csv(out, tmp_path / "r.csv")
    raw = (tmp_path / "r.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    rows = list(csv.DictReader((tmp_path / "r.csv").open(encoding="utf-8-sig")))
    assert rows[0]["pre_label"] == "Joy" and rows[0]["final_label"] == "" and rows[2]["pre_distress"] == "True"

    cfg = export_labelstudio(out, tmp_path / "ls.json", labels=["Anger", "Anxiety", "Joy", "Sadness"])
    tasks = json.loads((tmp_path / "ls.json").read_text(encoding="utf-8"))
    pred = tasks[2]["predictions"][0]
    assert tasks[2]["data"]["uid"] == "c" and pred["model_version"] == "v5"
    assert pred["result"][0]["value"]["choices"] == ["Sadness"]
    assert pred["result"][1]["value"]["choices"] == ["distress"]
    xml = cfg.read_text(encoding="utf-8")
    assert '<Choice value="Anger"/>' in xml and 'name="distress"' in xml


def test_labse_joblib_adapter(tmp_path):
    joblib = pytest.importorskip("joblib")
    np = pytest.importorskip("numpy")
    pytest.importorskip("sklearn")
    from sklearn.linear_model import LogisticRegression

    X = np.array([[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9]])
    joblib.dump(LogisticRegression().fit(X, ["Joy", "Joy", "Sadness", "Sadness"]), tmp_path / "stage2.joblib")
    joblib.dump(LogisticRegression().fit(X, ["none", "none", "distress", "distress"]), tmp_path / "stage1.joblib")
    (tmp_path / "config.json").write_text('{"distress_label": "distress"}', encoding="utf-8")

    class Encoder:
        def encode(self, texts, normalize_embeddings=True):
            return np.array([[1, 0] if "қуаныш" in t else [0, 1] for t in texts])

    pred = LabseJoblibPredictor(tmp_path, encoder=Encoder())(["қуаныштымын", "шаршадым"])
    assert [p["label"] for p in pred] == ["Joy", "Sadness"]
    assert pred[0]["distress"] is False and pred[1]["distress"] is True
    assert set(pred[0]["probs"]) == {"Joy", "Sadness"} and pred[0]["score"] == max(pred[0]["probs"].values())


def test_cli_annotate(tmp_path, model_file, capsys):
    src = tmp_path / "clean.jsonl"
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in ROWS), encoding="utf-8")
    out = tmp_path / "pre.jsonl"
    assert main(["annotate", str(src), "-o", str(out), "--model", model_file, "--sort", "review"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["needs_review"] == 2 and s["output"] == str(out)
    first = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert first["uid"] == "c" and first["pre"]["model"] == "my_model"

    ls = tmp_path / "ls.json"
    assert main(["annotate", str(src), "-o", str(ls), "--model", model_file, "--format", "labelstudio"]) == 0
    assert json.loads(capsys.readouterr().out)["label_config"].endswith("ls.labelconfig.xml")


def test_run_with_annotate_stage(tmp_path, monkeypatch, model_file):
    from kazemo.run import run

    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out/raw.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ROWS), encoding="utf-8"
    )
    (tmp_path / "p.toml").write_text(
        f'[output]\ndir = "out"\n[annotate]\nmodel = "{model_file.replace(chr(92), "/")}"\n'
        'model_name = "v5"\nexport = ["csv", "labelstudio"]\n',
        encoding="utf-8",
    )
    rep = run("p.toml", skip_collect=True)
    assert rep["annotate"]["posts"] == 3 and rep["annotate"]["distress"] == 1
    assert rep["annotate"]["files"] == ["prelabeled.jsonl", "review.csv", "labelstudio.json"]
    assert (tmp_path / "out/review.csv").exists() and (tmp_path / "out/labelstudio.labelconfig.xml").exists()

    (tmp_path / "bad.toml").write_text('[output]\ndir = "out"\n[annotate]\nmodel = "nope.py:predict"\n')
    assert "FileNotFoundError" in run("bad.toml", skip_collect=True)["annotate"]["error"]
