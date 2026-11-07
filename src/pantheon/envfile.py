"""Minimal .env loader (KEY=VALUE lines). Never overrides variables already set in the environment."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(*candidates: str | Path) -> Path | None:
    """Load the first existing file among `candidates`; return its path (or None)."""
    for c in candidates:
        path = Path(c).expanduser()
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            os.environ.setdefault(key, value)
        return path
    return None
