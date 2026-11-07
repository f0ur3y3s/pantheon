import asyncio
import json
import socket

import httpx
import pytest
import uvicorn

from pantheon.agent.adapters.simulated import SimulatedAdapter
from pantheon.agent.app import build_agent_app
from pantheon.agent.core import MachineAgent
from pantheon.contracts import EngineKind
from pantheon.gateway.app import build_gateway_app
from pantheon.gateway.config import GatewayConfig, MachineEntry

from conftest import fast

pytestmark = pytest.mark.anyio
ADMIN = {"Authorization": "Bearer admin"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    """Real sockets: sim agents + gateway under uvicorn, so streaming and disconnects behave for real."""

    def __init__(self, scenarios: dict[str, dict]):
        self.scenarios = scenarios
        self.servers: list[uvicorn.Server] = []
        self.adapters: dict[str, SimulatedAdapter] = {}
        self.gateway_url = ""

    async def __aenter__(self):
        machines = []
        for name, overrides in self.scenarios.items():
            port = free_port()
            url = f"http://127.0.0.1:{port}"
            adapter = SimulatedAdapter(fast(name, public_url=url, **overrides))
            self.adapters[name] = adapter
            app = build_agent_app(MachineAgent(name, adapter, EngineKind.SIMULATED), f"t-{name}", [adapter.engine_router()])
            self._serve(app, port)
            machines.append(MachineEntry(name=name, url=url, token=f"t-{name}"))
        await self._wait_started()  # agents first: a refused connect costs ~2s on Windows
        machines.append(MachineEntry(name="ghost", url=f"http://127.0.0.1:{free_port()}", token="x"))
        gport = free_port()
        cfg = GatewayConfig(port=gport, admin_token="admin", poll_seconds=0.1, machines=machines)
        self._serve(build_gateway_app(cfg), gport)
        self.gateway_url = f"http://127.0.0.1:{gport}"
        await self._wait_started()
        await asyncio.sleep(0.4)  # let the gateway poll a few times
        return self

    async def _wait_started(self):
        for s in self.servers:
            while not s.started:
                await asyncio.sleep(0.01)

    def _serve(self, app, port):
        s = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        s.config.setup_event_loop = lambda: None
        self.servers.append(s)
        self._tasks = getattr(self, "_tasks", []) + [asyncio.create_task(s.serve())]

    async def __aexit__(self, *exc):
        for s in self.servers:
            s.should_exit = True
        await asyncio.gather(*self._tasks, return_exceptions=True)


async def wait_for(c: httpx.AsyncClient, machine: str, op_id: str):
    for _ in range(200):
        op = (await c.get(f"/api/machines/{machine}/operations/{op_id}", headers=ADMIN)).json()
        if op["status"] in ("succeeded", "failed"):
            return op
        await asyncio.sleep(0.05)
    raise AssertionError("operation did not finish")


async def test_dashboard_flow_offline_start_progress_and_routing():
    async with Stack({"zeus": {}, "mbp": {}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as c:
        assert (await c.get("/api/machines")).status_code == 401

        machines = {m["name"]: m for m in (await c.get("/api/machines", headers=ADMIN)).json()}
        assert machines["ghost"]["status"]["state"] == "offline"
        assert machines["zeus"]["status"]["state"] == "idle"
        assert machines["mbp"]["status"]["state"] == "ready"
        assert machines["zeus"]["capabilities"]["engine"] == "simulated"

        configs = (await c.get("/api/machines/zeus/configs", headers=ADMIN)).json()
        assert {x["id"] for x in configs} >= {"iq3_xxs-128k", "gpt-oss-120b"}

        # not served yet -> clear unavailable error listing what is available
        r = await c.post("/v1/chat/completions", json={"model": "qwen3.8-flash-next-iq3_xxs", "messages": []})
        assert r.status_code == 503 and "mbp/qwen36" in r.json()["error"]["detail"]["available"]

        r = await c.post("/api/machines/zeus/operations", json={"kind": "start", "config_id": "iq3_xxs-128k"}, headers=ADMIN)
        assert r.status_code == 202
        r2 = await c.post("/api/machines/zeus/operations", json={"kind": "stop"}, headers=ADMIN)
        assert r2.status_code == 409 and r2.json()["error"]["code"] == "busy"
        op = await wait_for(c, "zeus", r.json()["id"])
        assert op["status"] == "succeeded"
        await asyncio.sleep(0.3)

        ids = {m["id"] for m in (await c.get("/v1/models")).json()["data"]}
        assert {"zeus/qwen3.8-flash-next-iq3_xxs", "qwen3.8-flash-next-iq3_xxs", "mbp/qwen36", "qwen36"} <= ids

        r = await c.post("/v1/chat/completions", json={"model": "zeus/qwen3.8-flash-next-iq3_xxs", "max_tokens": 3, "messages": []})
        assert r.status_code == 200 and r.headers["x-pantheon-machine"] == "zeus"
        assert r.json()["model"] == "qwen3.8-flash-next-iq3_xxs"


async def test_failed_start_surfaces_engine_error():
    async with Stack({"zeus": {}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as c:
        r = await c.post("/api/machines/zeus/operations", json={"kind": "start", "config_id": "gpt-oss-120b"}, headers=ADMIN)
        op = await wait_for(c, "zeus", r.json()["id"])
        assert op["status"] == "failed" and "out of memory" in op["error"]["message"]
        await asyncio.sleep(0.3)
        m = (await c.get("/api/machines/zeus", headers=ADMIN)).json()
        assert m["status"]["state"] == "failed"


async def test_streaming_passthrough_and_client_disconnect_releases_engine():
    async with Stack({"mbp": {"tokens_per_second": 20}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as c:
        body = {"model": "qwen36", "stream": True, "max_tokens": 5, "messages": []}
        chunks = []
        async with c.stream("POST", "/v1/chat/completions", json=body) as r:
            assert r.headers["content-type"].startswith("text/event-stream")
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    chunks.append(line[6:])
        assert chunks[-1] == "[DONE]" and len(chunks) == 7
        assert json.loads(chunks[0])["choices"][0]["delta"]["content"].startswith("tok0")

        # disconnect mid-stream: the engine must see its request end, and gateway tracking must return to 0
        adapter = st.adapters["mbp"]
        body["max_tokens"] = 500
        async with c.stream("POST", "/v1/chat/completions", json=body) as r:
            async for line in r.aiter_lines():
                if line.startswith("data: "):
                    break
            assert adapter.active_requests == 1
        for _ in range(50):
            if adapter.active_requests == 0:
                break
            await asyncio.sleep(0.05)
        assert adapter.active_requests == 0
        m = (await c.get("/api/machines", headers=ADMIN)).json()
        assert next(x for x in m if x["name"] == "mbp")["active_requests"] == 0
