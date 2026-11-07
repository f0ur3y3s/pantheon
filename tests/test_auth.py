import httpx
import pytest

from pantheon.gateway.app import build_gateway_app
from pantheon.gateway.config import GatewayConfig, load_gateway_config
from pantheon.gateway.machines import MachineRegistry

pytestmark = pytest.mark.anyio


def client_for(token: str) -> httpx.AsyncClient:
    cfg = GatewayConfig(admin_token=token)
    app = build_gateway_app(cfg, registry=MachineRegistry([]))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


async def test_no_token_means_no_sign_in():
    async with client_for("") as c:
        assert (await c.get("/api/auth")).json() == {"required": False}
        assert (await c.get("/api/machines")).status_code == 200


async def test_token_required_when_set():
    async with client_for("token") as c:
        assert (await c.get("/api/auth")).json() == {"required": True}
        assert (await c.get("/api/machines")).status_code == 401
        assert (await c.get("/api/machines", headers={"Authorization": "Bearer wrong"})).status_code == 401
        assert (await c.get("/api/machines", headers={"Authorization": "Bearer token"})).status_code == 200


def test_token_comes_from_env_file_and_env_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PANTHEON_ADMIN_TOKEN", raising=False)
    (tmp_path / "gw.toml").write_text("[gateway]\nport = 9000\n")
    assert load_gateway_config(tmp_path / "gw.toml").admin_token == ""  # nothing set: open

    (tmp_path / ".env").write_text("# comment\nPANTHEON_ADMIN_TOKEN=token\n")
    assert load_gateway_config(tmp_path / "gw.toml").admin_token == "token"

    monkeypatch.setenv("PANTHEON_ADMIN_TOKEN", "from-env")
    assert load_gateway_config(tmp_path / "gw.toml").admin_token == "from-env"

    monkeypatch.setenv("PANTHEON_ADMIN_TOKEN", "")  # explicitly empty: open, even with .env present
    assert load_gateway_config(tmp_path / "gw.toml").admin_token == ""
