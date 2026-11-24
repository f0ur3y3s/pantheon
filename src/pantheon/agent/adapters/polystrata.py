"""PolyStrata adapter: a Strata fork run from a source checkout (e.g. ~/repos/PolyStrata/terminal).

Its serve/server.py has the same CLI, /health, ready file and drain behaviour as Strata, so this reuses
StrataAdapter. Differences: the server is run as a script from the checkout's .venv, launch presets are the
`strata-*.json` files beside it (written by polystrata.py) plus any preset_dirs, and the version is the checkout's git commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pantheon.agent.adapters.strata import StrataAdapter, StrataSettings
from pantheon.contracts import Capabilities, EngineKind, ModelConfig


class PolyStrataAdapter(StrataAdapter):
    check_version = False  # a source checkout: versions are commits

    def __init__(self, settings: StrataSettings):
        super().__init__(settings)
        # The checkout's own presets, plus any configured ones (e.g. an installed Strata's launch/ presets).
        settings.preset_files = [*(str(p) for p in sorted(self.control.glob("strata-*.json"))), *settings.preset_files]
        # Presets naming "component:strata-engine" need an engine package; PolyStrata looks in its own folder
        # (~/.local/share/polystrata/components), so default to an installed Strata's packages when present.
        strata_components = Path("~/.local/share/strata/components").expanduser()
        if "STRATA_COMPONENTS_DIR" not in settings.env and strata_components.is_dir():
            settings.env = {**settings.env, "STRATA_COMPONENTS_DIR": str(strata_components)}
        if settings.server_command is None:
            settings.server_command = [self._python(), str(self.control / "serve" / "server.py")]

    def version(self) -> str:
        if (self.control / "VERSION").exists():
            return super().version()
        try:
            out = subprocess.run(["git", "-C", str(self.control), "rev-parse", "--short", "HEAD"],
                                 capture_output=True, text=True, timeout=5)
            return f"git {out.stdout.strip()}" if out.returncode == 0 and out.stdout.strip() else "unknown"
        except (OSError, subprocess.SubprocessError):
            return "unknown"

    async def capabilities(self) -> Capabilities:
        caps = await super().capabilities()
        return caps.model_copy(update={"engine": EngineKind.POLYSTRATA,
                                       "extra": {**caps.extra, "supported_version": True}})

    async def list_configs(self) -> list[ModelConfig]:
        configs = await super().list_configs()
        for c in configs:
            preset = Path(c.details.get("preset_path", ""))
            c.engine = EngineKind.POLYSTRATA
            if preset.stem.startswith("strata-"):
                c.id = preset.stem.removeprefix("strata-")
                c.label = f"{c.model} · {c.id}"
        return configs
