import argparse
import logging

import uvicorn

from pantheon.gateway.app import build_gateway_app
from pantheon.gateway.config import load_gateway_config


def main() -> None:
    p = argparse.ArgumentParser(prog="pantheon-gateway", description="pantheon gateway and dashboard API")
    p.add_argument("--config", required=True, help="gateway TOML file")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # per-request lines from status polling
    cfg = load_gateway_config(args.config)
    uvicorn.run(build_gateway_app(cfg), host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
