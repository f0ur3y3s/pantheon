"""Local development stack: three simulated agents + gateway (+ one unreachable machine), one process.

    uv run python -m pantheon.dev            # gateway on http://127.0.0.1:9000; sign-in token from .env
    uv run python -m pantheon.dev --fast     # shorter load times
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn

from pantheon.agent.config import AgentConfig, build_from_config
from pantheon.envfile import load_env_file
from pantheon.gateway.app import build_gateway_app
from pantheon.gateway.config import GatewayConfig, MachineEntry, admin_token_from_env

AGENT_PORTS = {"pythia": 9101, "mbp": 9102, "zeus": 9103}


async def run(fast: bool) -> None:
    servers = []
    machines = []
    for name, port in AGENT_PORTS.items():
        token = f"dev-agent-{name}"
        adapter = {"kind": "simulated", "scenario": name}
        if fast:
            adapter["load_seconds"] = 2.0
        cfg = AgentConfig(name=name, host="127.0.0.1", port=port, public_url=f"http://127.0.0.1:{port}",
                          token=token, adapter=adapter, state_dir=None)
        servers.append(uvicorn.Server(uvicorn.Config(build_from_config(cfg), host=cfg.host, port=port, log_level="warning")))
        machines.append(MachineEntry(name=f"sim-{name}", url=cfg.public_url, token=token))
    # A machine whose agent is never running, to exercise the offline state.
    machines.append(MachineEntry(name="sim-offline", url="http://127.0.0.1:9199", token="unused"))

    dist = Path(__file__).resolve().parents[2] / "dashboard" / "dist"
    load_env_file(Path.cwd() / ".env")
    admin = admin_token_from_env()
    gcfg = GatewayConfig(host="127.0.0.1", port=9000, admin_token=admin, machines=machines,
                         dashboard_dir=str(dist) if dist.is_dir() else None)
    servers.append(uvicorn.Server(uvicorn.Config(build_gateway_app(gcfg), host=gcfg.host, port=gcfg.port, log_level="info")))
    print("gateway: http://127.0.0.1:9000  " + ("sign-in: PANTHEON_ADMIN_TOKEN from .env" if admin else "sign-in: not required (PANTHEON_ADMIN_TOKEN unset)"))
    await asyncio.gather(*(s.serve() for s in servers))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--fast", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run(args.fast))


if __name__ == "__main__":
    main()
