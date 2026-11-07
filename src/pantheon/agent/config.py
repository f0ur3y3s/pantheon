"""Agent configuration (TOML) and construction."""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from pantheon.agent.adapters.simulated import SCENARIOS, SimulatedAdapter
from pantheon.agent.app import build_agent_app
from pantheon.agent.core import MachineAgent
from pantheon.agent.params import ParamStore
from pantheon.contracts import EngineKind


@dataclass
class AgentConfig:
    name: str
    host: str
    port: int
    public_url: str
    token: str
    adapter: dict
    state_dir: str | None = None  # agent-owned state (parameter overrides, ...); None = in memory only


def resolve_secret(table: dict, key: str) -> str:
    """Read `key` directly or from the env var named by `key_env`. Secrets should use the env form."""
    if env := table.get(f"{key}_env"):
        value = os.environ.get(env, "")
        if not value:
            raise ValueError(f"environment variable {env} (for {key}) is empty")
        return value
    return table.get(key, "")


def load_agent_config(path: str | Path) -> AgentConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))  # tolerate a BOM (Windows editors)
    host = raw.get("host", "127.0.0.1")
    port = int(raw.get("port", 9101))
    return AgentConfig(
        name=raw["name"],
        host=host,
        port=port,
        public_url=raw.get("public_url", f"http://{host}:{port}"),
        token=resolve_secret(raw, "token"),
        adapter=raw.get("adapter", {"kind": "simulated", "scenario": raw["name"]}),
        state_dir=raw.get("state_dir", str(Path("~/.local/state/pantheon").expanduser())),
    )


def build_from_config(cfg: AgentConfig) -> FastAPI:
    kind = cfg.adapter.get("kind")
    state = Path(cfg.state_dir).expanduser() if cfg.state_dir else None
    params = ParamStore(state / "params.json" if state else None)
    if kind == "simulated":
        settings = copy.deepcopy(SCENARIOS[cfg.adapter.get("scenario", cfg.name)])
        settings.public_url = cfg.public_url
        for k in ("load_seconds", "stop_seconds", "tokens_per_second"):
            if k in cfg.adapter:
                setattr(settings, k, float(cfg.adapter[k]))
        adapter = SimulatedAdapter(settings)
        agent = MachineAgent(cfg.name, adapter, EngineKind.SIMULATED, params)
        return build_agent_app(agent, cfg.token, [adapter.engine_router()])
    if kind == "lmstudio":
        from pantheon.agent.adapters.lmstudio import LMProfile, LMStudioAdapter, LMStudioSettings

        a = cfg.adapter
        settings = LMStudioSettings(
            engine_url=a["engine_url"].rstrip("/"),
            profiles=[LMProfile(**p) for p in a.get("profiles", [])],
            include_unprofiled=a.get("include_unprofiled", True),
            readiness=a.get("readiness", "chat"),
        )
        if "lms_command" in a:
            cmd = a["lms_command"]
            settings.lms_command = [os.path.expanduser(c) for c in (cmd if isinstance(cmd, list) else [cmd])]
        agent = MachineAgent(cfg.name, LMStudioAdapter(settings), EngineKind.LMSTUDIO, params)
        return build_agent_app(agent, cfg.token)
    if kind == "strata":
        from pantheon.agent.adapters.strata import StrataAdapter, StrataSettings

        known = set(StrataSettings.__dataclass_fields__)
        unknown = set(cfg.adapter) - known - {"kind"}
        if unknown:
            raise ValueError(f"unknown strata adapter settings: {sorted(unknown)}")
        a = {k: v for k, v in cfg.adapter.items() if k != "kind"}
        a.setdefault("state_dir", str((state or Path("~/.local/state/pantheon").expanduser()) / "strata"))
        agent = MachineAgent(cfg.name, StrataAdapter(StrataSettings(**a)), EngineKind.STRATA, params)
        return build_agent_app(agent, cfg.token)
    raise ValueError(f"unknown adapter kind {kind!r}")
