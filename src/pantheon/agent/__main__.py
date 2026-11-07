import argparse
import logging

import uvicorn

from pantheon.agent.config import build_from_config, load_agent_config


def main() -> None:
    p = argparse.ArgumentParser(prog="pantheon-agent", description="pantheon machine service")
    p.add_argument("--config", required=True, help="agent TOML file")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request lines from status polling
    cfg = load_agent_config(args.config)
    uvicorn.run(build_from_config(cfg), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
