"""Secrets and settings from environment variables or a local ``.env`` file.

Keys are never hard-coded or committed: ``.env`` is git-ignored and
GitHub Actions passes them in as repository secrets.
"""

from __future__ import annotations

import os
from pathlib import Path


class MissingCredential(RuntimeError):
    """Raised when a collector needs a key that is not configured."""


def load_dotenv(path: str | Path = ".env") -> None:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding set values."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def require(name: str, hint: str = "") -> str:
    """Return an environment variable or raise a readable error."""
    value = os.environ.get(name, "").strip()
    if not value:
        msg = f"{name} is not set. Add it to .env or export it."
        raise MissingCredential(f"{msg} {hint}".strip())
    return value
