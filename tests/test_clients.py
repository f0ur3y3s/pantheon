import httpx
import pytest

from pantheon.gateway.clients import ClientModel, client_configs

from test_gateway import ADMIN, Stack

pytestmark = pytest.mark.anyio


def test_opencode_and_pi_shapes_match_installed_client_formats():
    out = client_configs("mbp", "mbp (direct)", "http://192.0.2.12:8080/v1",
                         [ClientModel(id="qwen36", name="Qwen3.6", context=131072)], api_key_env=None)
    oc = out["opencode"]["config"]
    prov = oc["provider"]["mbp"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"] == {"baseURL": "http://192.0.2.12:8080/v1"}  # no apiKey when unauthenticated
    m = prov["models"]["qwen36"]
    assert m["limit"] == {"context": 131072, "input": 131072 - 8192, "output": 8192}
    assert m["tool_call"] is True
    assert oc["model"] == "mbp/qwen36"

    pi = out["pi"]["config"]["providers"]["mbp"]
    assert pi["api"] == "openai-completions" and pi["baseUrl"] == "http://192.0.2.12:8080/v1"
    assert pi["apiKey"] == "none"  # pi hides models without a resolvable key
    assert pi["models"][0] == {"id": "qwen36", "name": "Qwen3.6", "reasoning": True, "input": ["text"],
                               "contextWindow": 131072, "maxTokens": 8192,
                               "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}


def test_keyed_endpoint_uses_env_references_never_literal_keys():
    out = client_configs("pantheon", "Pantheon", "http://gw/v1", [ClientModel(id="m", name="m", context=4096)], "PANTHEON_API_KEY")
    assert out["opencode"]["config"]["provider"]["pantheon"]["options"]["apiKey"] == "{env:PANTHEON_API_KEY}"
    assert out["pi"]["config"]["providers"]["pantheon"]["apiKey"] == "$PANTHEON_API_KEY"
    assert out["pi"]["config"]["providers"]["pantheon"]["models"][0]["maxTokens"] == 1024  # small ctx -> small output


async def test_endpoints_follow_live_status():
    async with Stack({"mbp": {}, "zeus": {}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as c:
        r = await c.get("/api/machines/mbp/client-config", headers=ADMIN)
        assert r.status_code == 200
        body = r.json()
        assert body["direct"] is True and body["models"] == ["qwen36"]
        assert body["base_url"].endswith("/sim/v1")
        assert body["opencode"]["config"]["provider"]["mbp"]["models"]["qwen36"]["limit"]["context"] == 131072

        r = await c.get("/api/machines/zeus/client-config", headers=ADMIN)  # idle
        assert r.status_code == 409 and r.json()["error"]["code"] == "not_ready"

        gw = (await c.get("/api/client-config", headers=ADMIN)).json()
        assert gw["direct"] is False and gw["base_url"] == f"{st.gateway_url}/v1"
        assert gw["models"] == ["mbp/qwen36"] and gw["api_key_env"] is None
        assert "mbp/qwen36" in gw["opencode"]["config"]["provider"]["pantheon"]["models"]
