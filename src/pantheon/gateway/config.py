from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pantheon.agent.config import resolve_secret
from pantheon.envfile import load_env_file

ADMIN_TOKEN_ENV = "PANTHEON_ADMIN_TOKEN"


def admin_token_from_env(env_name: str = ADMIN_TOKEN_ENV) -> str:
    """Dashboard sign-in token. Empty/unset means sign-in is not required."""
    return os.environ.get(env_name, "").strip()


@dataclass
class MachineEntry:
    name: str
    url: str  # agent base URL, e.g. http://192.0.2.11:9101
    token: str


@dataclass
class GatewayConfig:
    host: str = "127.0.0.1"
    port: int = 9000
    admin_token: str = ""  # dashboard / control API; empty = no sign-in required
    client_keys: list[str] = field(default_factory=list)  # OpenAI API; empty = open on the LAN
    poll_seconds: float = 2.0
    upstream_read_timeout: float = 600.0
    dashboard_dir: str | None = None
    public_url: str | None = None  # how clients reach the gateway, e.g. http://192.0.2.20:9000
    machines: list[MachineEntry] = field(default_factory=list)


def load_gateway_config(path: str | Path) -> GatewayConfig:
    path = Path(path)
    # .env in the working directory wins over one next to the config; real env vars win over both.
    load_env_file(Path.cwd() / ".env", path.parent / ".env")
    raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))  # tolerate a BOM (Windows editors)
    g = raw.get("gateway", {})
    keys_raw = resolve_secret(g, "client_keys") if ("client_keys" in g or "client_keys_env" in g) else ""
    keys = keys_raw if isinstance(keys_raw, list) else [k.strip() for k in str(keys_raw).split(",") if k.strip()]
    cfg = GatewayConfig(
        host=g.get("host", "127.0.0.1"),
        port=int(g.get("port", 9000)),
        admin_token=g.get("admin_token") or admin_token_from_env(g.get("admin_token_env", ADMIN_TOKEN_ENV)),
        client_keys=keys,
        poll_seconds=float(g.get("poll_seconds", 2.0)),
        upstream_read_timeout=float(g.get("upstream_read_timeout", 600.0)),
        dashboard_dir=g.get("dashboard_dir"),
        public_url=(g.get("public_url") or "").rstrip("/") or None,
        machines=[MachineEntry(name=m["name"], url=m["url"].rstrip("/"), token=resolve_secret(m, "token"))
                  for m in raw.get("machines", [])],
    )
    names = [m.name for m in cfg.machines]
    if len(names) != len(set(names)):
        raise ValueError("machine names must be unique")
    return cfg
