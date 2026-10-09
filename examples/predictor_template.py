"""Template: connect your own model to `kazemo annotate`.

Copy this file next to your model code, fill in `load_model` and `predict`, then run:

    kazemo annotate data/clean.jsonl -o data/review.csv --format csv \
        --model path/to/this_file.py:predict --model-name hier-v5 --sort review

`predict` receives a list of texts and must return one dict per text:

    {"label": "Anxiety",            # top emotion (required)
     "score": 0.71,                 # its probability (required unless probs is given)
     "probs": {"Anxiety": 0.71, "Fear": 0.18, ...},   # optional, enables margin/entropy
     "distress": True,              # optional, Stage 1 decision
     "distress_score": 0.83}        # optional, Stage 1 probability
"""

from functools import lru_cache


@lru_cache(maxsize=1)
def load_model():
    # Example for the thesis setup (LaBSE + trained heads). Replace with your own loading code,
    # e.g. `from predict_distress import load; return load("models/v5")`.
    raise NotImplementedError("load your encoder and Stage 1 / Stage 2 heads here")


def predict(texts: list[str]) -> list[dict]:
    model = load_model()
    results = []
    for out in model.predict(texts):  # adapt to what your model returns
        results.append(
            {
                "label": out["emotion"],
                "score": out["emotion_score"],
                "probs": out.get("emotion_probs"),
                "distress": out["distress"],
                "distress_score": out["distress_score"],
            }
        )
    return results
