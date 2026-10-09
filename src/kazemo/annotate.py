"""Pre-annotation with your own model: draft labels for human review.

A *predictor* is any callable ``predict(texts: list[str]) -> list[dict]``. Each dict has
at least ``label`` (emotion) and ``score`` (its probability); optionally ``distress``
(bool or label), ``distress_score`` and ``probs`` (label -> probability).

Plug a model in with ``--model``:

- ``path/to/predict_distress.py:predict`` or ``my_package.module:predict`` — your own function;
- ``labse-joblib:models/v5`` — built-in adapter: LaBSE sentence embeddings + scikit-learn heads
  saved with joblib (``stage1.joblib`` for distress, ``stage2.joblib`` for emotion).

Every row keeps its text and gets a ``pre`` block; nothing is decided automatically.
Pre-labels are drafts: a person reviews them before they enter the training data.
"""

from __future__ import annotations

import csv
import importlib
import importlib.util
import json
import math
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

Predictor = Callable[[list[str]], list[dict]]


# ----------------------------------------------------------------------------- loading


def load_predictor(spec: str) -> Predictor:
    """Load ``file.py:func``, ``package.module:func`` or ``labse-joblib:DIR``."""
    kind, sep, rest = spec.partition(":")
    if kind == "labse-joblib" and sep:
        return LabseJoblibPredictor(rest)
    module_ref, sep, func_name = spec.rpartition(":")
    if not sep or not module_ref or not func_name:
        raise ValueError(f"model spec must look like 'file.py:func', 'module:func' or 'labse-joblib:DIR', got {spec!r}")
    if module_ref.endswith(".py"):
        path = Path(module_ref).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        sys.path.insert(0, str(path.parent))  # let the file import its neighbours
        mod_spec = importlib.util.spec_from_file_location(f"kazemo_user_{path.stem}", path)
        module = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_ref)
    fn = getattr(module, func_name, None)
    if not callable(fn):
        raise AttributeError(f"{module_ref} has no callable {func_name!r}")
    return fn


def default_model_name(spec: str) -> str:
    """'models/v5' for labse-joblib, otherwise the file or module name (Windows paths included)."""
    kind, _, rest = spec.partition(":")
    if kind == "labse-joblib" and rest:
        return Path(rest).name or "labse-joblib"
    ref = (spec.rpartition(":")[0] or spec).replace("\\", "/")
    return Path(ref).stem if ref.endswith(".py") else ref.rsplit(".", 1)[-1]


class LabseJoblibPredictor:
    """LaBSE embeddings + scikit-learn heads saved with joblib.

    Directory layout::

        stage1.joblib   classifier with predict_proba; distress screening (optional)
        stage2.joblib   classifier with predict_proba; fine-grained emotion
        config.json     optional: {"encoder": "sentence-transformers/LaBSE",
                                   "distress_label": "distress", "normalize": true}
    """

    def __init__(self, directory: str | Path, encoder=None):
        import joblib

        d = Path(directory)
        cfg_path = d / "config.json"
        self.cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
        self.stage2 = joblib.load(d / "stage2.joblib")
        self.stage1 = joblib.load(d / "stage1.joblib") if (d / "stage1.joblib").exists() else None
        if encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:  # pragma: no cover
                raise RuntimeError('labse-joblib needs: pip install "kazemo[model]"') from e
            encoder = SentenceTransformer(self.cfg.get("encoder", "sentence-transformers/LaBSE"))
        self.encoder = encoder

    def __call__(self, texts: list[str]) -> list[dict]:
        emb = self.encoder.encode(texts, normalize_embeddings=self.cfg.get("normalize", True))
        p2 = self.stage2.predict_proba(emb)
        classes2 = [str(c) for c in self.stage2.classes_]
        p1 = self.stage1.predict_proba(emb) if self.stage1 is not None else None
        out = []
        for i in range(len(texts)):
            probs = {c: float(p) for c, p in zip(classes2, p2[i], strict=True)}
            label = max(probs, key=probs.get)
            row = {"label": label, "score": probs[label], "probs": probs}
            if p1 is not None:
                classes1 = [str(c) for c in self.stage1.classes_]
                d_label = self.cfg.get("distress_label", classes1[-1])
                d_score = float(p1[i][classes1.index(d_label)])
                row.update(distress=d_score >= 0.5, distress_score=d_score)
            out.append(row)
        return out


# ----------------------------------------------------------------------------- annotation


def _entropy(probs: dict) -> float:
    ps = [p for p in probs.values() if p > 0]
    if len(ps) < 2:
        return 0.0
    return -sum(p * math.log(p) for p in ps) / math.log(len(ps))


def _normalise_prediction(pred: dict) -> dict:
    if "label" not in pred:
        raise ValueError(f"predictor must return dicts with 'label', got {pred!r}")
    probs = {str(k): float(v) for k, v in (pred.get("probs") or {}).items()}
    score = float(pred.get("score", probs.get(str(pred["label"]), 0.0)))
    out = {"label": str(pred["label"]), "score": round(score, 4)}
    if probs:
        ranked = sorted(probs.values(), reverse=True)
        out["margin"] = round(ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0), 4)
        out["entropy"] = round(_entropy(probs), 4)
        out["probs"] = {k: round(v, 4) for k, v in probs.items()}
    if "distress" in pred:
        d = pred["distress"]
        out["distress"] = (
            bool(d) if not isinstance(d, str) else d.lower() not in ("no", "none", "0", "false", "not_distress")
        )
    if "distress_score" in pred:
        out["distress_score"] = round(float(pred["distress_score"]), 4)
    return out


def annotate(
    rows: list[dict],
    predictor: Predictor,
    model_name: str = "model",
    batch_size: int = 64,
    review_below: float = 0.6,
    text_field: str = "text",
) -> list[dict]:
    """Add a ``pre`` block to every row. ``needs_review`` marks uncertain or distress posts."""
    out = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        preds = predictor([r.get(text_field) or "" for r in batch])
        if len(preds) != len(batch):
            raise ValueError(f"predictor returned {len(preds)} predictions for {len(batch)} texts")
        for row, pred in zip(batch, preds, strict=True):
            pre = _normalise_prediction(pred)
            pre["model"] = model_name
            pre["needs_review"] = pre["score"] < review_below or bool(pre.get("distress"))
            out.append({**row, "pre": pre})
    return out


def review_order(rows: list[dict]) -> list[dict]:
    """Most useful first for annotators: distress, then least confident."""
    return sorted(rows, key=lambda r: (not r["pre"].get("distress", False), r["pre"]["score"]))


def summary(rows: list[dict]) -> dict:
    pres = [r["pre"] for r in rows]
    return {
        "posts": len(rows),
        "pre_label": dict(Counter(p["label"] for p in pres).most_common()),
        "distress": sum(1 for p in pres if p.get("distress")),
        "needs_review": sum(1 for p in pres if p["needs_review"]),
        "mean_score": round(sum(p["score"] for p in pres) / len(pres), 3) if pres else 0.0,
    }


# ----------------------------------------------------------------------------- export


def export_jsonl(rows: list[dict], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def export_csv(rows: list[dict], path: str | Path) -> None:
    """Spreadsheet for manual review: fill ``final_label`` (and ``final_distress``)."""
    cols = ["uid", "text", "pre_label", "pre_score", "pre_distress", "needs_review", "final_label", "final_distress"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows Kazakh letters
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            p = r["pre"]
            w.writerow(
                [r.get("uid", ""), r.get("text", ""), p["label"], p["score"], p.get("distress", ""),
                 p["needs_review"], "", ""]
            )  # fmt: skip


def export_labelstudio(rows: list[dict], path: str | Path, labels: list[str] | None = None) -> Path:
    """Label Studio tasks with the model's guess as a prediction, plus the matching label config."""
    labels = labels or sorted({r["pre"]["label"] for r in rows})
    tasks = []
    for r in rows:
        p = r["pre"]
        result = [{"from_name": "emotion", "to_name": "text", "type": "choices", "value": {"choices": [p["label"]]}}]
        if "distress" in p:
            result.append(
                {"from_name": "distress", "to_name": "text", "type": "choices",
                 "value": {"choices": ["distress" if p["distress"] else "no distress"]}}
            )  # fmt: skip
        tasks.append(
            {
                "data": {"text": r.get("text", ""), "uid": r.get("uid", ""), "source": r.get("source", "")},
                "predictions": [{"model_version": p["model"], "score": p["score"], "result": result}],
            }
        )
    Path(path).write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    choices = "\n".join(f'    <Choice value="{c}"/>' for c in labels)
    config = (
        '<View>\n  <Text name="text" value="$text"/>\n'
        f'  <Choices name="emotion" toName="text" choice="single" showInLine="true">\n{choices}\n  </Choices>\n'
        '  <Choices name="distress" toName="text" choice="single">\n'
        '    <Choice value="distress"/>\n    <Choice value="no distress"/>\n  </Choices>\n</View>\n'
    )
    config_path = Path(path).with_suffix(".labelconfig.xml")
    config_path.write_text(config, encoding="utf-8")
    return config_path


EXPORTERS = {"jsonl": export_jsonl, "csv": export_csv, "labelstudio": export_labelstudio}
