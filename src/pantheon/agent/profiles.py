"""Agent-owned launch profiles, persisted separately from engine configurations."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path


class ProfileStore:
    def __init__(self, path: str | Path | None):
        self.path = Path(path).expanduser() if path else None
        self._data: dict[str, dict[str, str]] = {}
        if self.path and self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._data = {k: v for k, v in data.items()
                                  if k.startswith("profile/") and isinstance(v, dict)
                                  and isinstance(v.get("label"), str)
                                  and isinstance(v.get("source_config_id"), str)}
            except (OSError, ValueError):
                pass

    def all(self) -> dict[str, dict[str, str]]:
        return {k: dict(v) for k, v in self._data.items()}

    def get(self, profile_id: str) -> dict[str, str] | None:
        record = self._data.get(profile_id)
        return dict(record) if record else None

    def create(self, label: str, source_config_id: str) -> str:
        profile_id = f"profile/{uuid.uuid4().hex[:12]}"
        self._data[profile_id] = {"label": label, "source_config_id": source_config_id}
        self._save()
        return profile_id

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".profiles-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
