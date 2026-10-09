# kazemo

[![CI](https://github.com/tenajuro12/kazemo/actions/workflows/ci.yml/badge.svg)](https://github.com/tenajuro12/kazemo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Pre-processing toolkit for **Kazakh and code-mixed Kazakh–Russian social-media text**.
It is the data-preparation module of a master's thesis on automatically determining
users' emotional state from social-network messages with NLP.

Raw Telegram / Threads posts are noisy: links, mentions, hashtags, elongated words,
duplicates, two scripts and two languages in one sentence. `kazemo` turns them into a
clean, pseudonymised, deduplicated corpus tagged with language and script, ready for
annotation and for encoders such as LaBSE.

## Features

| Step | Function | What it does |
|---|---|---|
| Normalise | `normalize` | Unicode NFC, whitespace collapsing |
| Clean | `clean` | links → `<URL>`, `#tag` → `tag`, `ааааа` → `ааа`, optional emoji removal |
| Pseudonymise | `pseudonymise` | `@user` → stable `@user_<hash>`, e-mails → `<EMAIL>`, phones → `<PHONE>` |
| Script ID | `detect_script` | `cyrillic` / `latin` / `mixed` / `none` |
| Language ID | `detect_language` | `kk` / `ru` / `unknown`, using Kazakh-specific letters (ә ғ қ ң ө ұ ү һ і) |
| Deduplicate | `deduplicate` | drops exact and near-exact duplicates (case and punctuation ignored) |
| Statistics | `stats`, `plot_labels` | language / script / label distribution, PNG chart |

## Installation

```bash
git clone https://github.com/tenajuro12/kazemo.git
cd kazemo
pip install -e ".[dev]"
```

Requires Python 3.10+.

## Usage

### Command line

```bash
kazemo examples/sample.jsonl -o processed.jsonl --plot labels.png
```

Input is JSONL with one post per line: `{"text": "...", "label": "Joy"}` (`label` is optional).
The command writes the processed file and prints corpus statistics:

```json
{"n": 8, "lang": {"kk": 6, "ru": 1, "unknown": 1}, "script": {"cyrillic": 7, "latin": 1}, ...}
```

### Python

```python
from kazemo import clean, pseudonymise, detect_language

text = pseudonymise(clean("@aidos_kz Бүгін өте қуаныштымын!!!! https://t.me/x #той"))
# '@user_cc88a9b1 Бүгін өте қуаныштымын!!! <URL> той'
detect_language(text)  # 'kk'
```

![Label distribution](docs/label_distribution.png)

## Project structure

```
src/kazemo/      preprocess.py (text functions), pipeline.py (JSONL + stats), cli.py
tests/           pytest unit and end-to-end tests
examples/        sample.jsonl — synthetic posts for demos and CI
.github/         CI and release workflows, issue and PR templates
```

## Development

```bash
pytest            # tests + coverage report
ruff check .      # lint
ruff format .     # format
```

## CI/CD

- **CI** (`.github/workflows/ci.yml`) — on every push and pull request to `main`:
  lint and format check with Ruff → tests on Python 3.10–3.13 with a 90 % coverage gate →
  CLI smoke test → coverage report and sample outputs uploaded as build artifacts.
- **CD** (`.github/workflows/release.yml`) — on a `v*` tag: tests, builds the wheel and sdist,
  and publishes a GitHub Release with the packages attached.

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## Data and ethics

The repository contains only synthetic example posts. Real collected data is never committed
(`data/raw/` is git-ignored). Pseudonymisation runs before anything is stored, and the tool
does not identify or profile individuals.

## License

MIT — see [LICENSE](LICENSE).
