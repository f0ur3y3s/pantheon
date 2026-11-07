"""Parameter overrides: validation against adapter-declared specs, persisted per machine as JSON."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pantheon.contracts import ParamSpec, ParamType


class ParamError(ValueError):
    pass


def validate(specs: list[ParamSpec], updates: dict[str, Any]) -> dict[str, Any]:
    """Coerce and check `updates` (None = remove override). Raises ParamError with a user-facing message."""
    by_key = {s.key: s for s in specs}
    out: dict[str, Any] = {}
    for key, raw in updates.items():
        spec = by_key.get(key)
        if spec is None:
            raise ParamError(f"unknown parameter {key!r}; editable: {sorted(by_key)}")
        if raw is None:
            out[key] = None
            continue
        try:
            if spec.type == ParamType.INT:
                if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
                    raise ValueError
                value: Any = int(raw)
            elif spec.type == ParamType.FLOAT:
                if isinstance(raw, bool):
                    raise ValueError
                value = float(raw)
            elif spec.type == ParamType.BOOL:
                if not isinstance(raw, bool):
                    raise ValueError
                value = raw
            else:
                value = str(raw)
        except (TypeError, ValueError):
            raise ParamError(f"{spec.label}: expected {spec.type.value}, got {raw!r}") from None
        if spec.type in (ParamType.INT, ParamType.FLOAT):
            if spec.min is not None and value < spec.min:
                raise ParamError(f"{spec.label}: {value} is below the minimum {spec.min:g}")
            if spec.max is not None and value > spec.max:
                raise ParamError(f"{spec.label}: {value} is above the maximum {spec.max:g}")
        if spec.type == ParamType.ENUM and value not in (spec.options or []):
            raise ParamError(f"{spec.label}: {value!r} is not one of {spec.options}")
        out[key] = value
    return out


class ParamStore:
    """{config_id: {key: value}} in a JSON file (or memory when path is None). Writes are atomic."""

    def __init__(self, path: str | Path | None):
        self.path = Path(path).expanduser() if path else None
        self._data: dict[str, dict[str, Any]] = {}
        if self.path and self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._data = {}

    def get(self, config_id: str) -> dict[str, Any]:
        return dict(self._data.get(config_id, {}))

    def update(self, config_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self._data.get(config_id, {})
        for k, v in changes.items():
            if v is None:
                current.pop(k, None)
            else:
                current[k] = v
        if current:
            self._data[config_id] = current
        else:
            self._data.pop(config_id, None)
        self._save()
        return dict(current)

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".params-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)
