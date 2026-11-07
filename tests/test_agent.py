import asyncio

import httpx
import pytest

from pantheon.agent.adapters.simulated import SimulatedAdapter
from pantheon.agent.app import build_agent_app
from pantheon.agent.core import AgentError, MachineAgent
from pantheon.contracts import (
    EngineKind,
    ErrorCode,
    MachineState,
    OperationKind,
    OperationRequest,
    OperationStatus,
)

from conftest import fast

pytestmark = pytest.mark.anyio


async def wait_op(agent: MachineAgent, op_id: str, timeout: float = 5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        op = agent.get_operation(op_id)
        if op.status in (OperationStatus.SUCCEEDED, OperationStatus.FAILED, OperationStatus.CANCELLED):
            return op
        assert asyncio.get_running_loop().time() < deadline, f"operation stuck at {op.step}"
        await asyncio.sleep(0.02)


def make(scenario="zeus", **kw) -> MachineAgent:
    return MachineAgent(scenario, SimulatedAdapter(fast(scenario, **kw)), EngineKind.SIMULATED)


async def test_start_reports_progress_and_becomes_ready():
    agent = make()
    await agent.reconcile()
    assert (await agent.status()).state == MachineState.IDLE

    op = await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-128k"))
    assert op.status in (OperationStatus.PENDING, OperationStatus.RUNNING)
    seen = set()
    while op.status not in (OperationStatus.SUCCEEDED, OperationStatus.FAILED):
        seen.add((await agent.status()).state)
        await asyncio.sleep(0.01)
    assert op.status == OperationStatus.SUCCEEDED and op.progress == 1.0
    assert MachineState.LOADING in seen
    st = await agent.status()
    assert st.state == MachineState.READY
    assert st.active_config == "iq3_xxs-128k"
    assert st.engine_url and st.engine_url.endswith("/sim/v1")


async def test_second_operation_is_rejected_while_one_runs():
    agent = make()
    await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-128k"))
    with pytest.raises(AgentError) as e:
        await agent.submit(OperationRequest(kind=OperationKind.STOP))
    assert e.value.body.code == ErrorCode.BUSY and e.value.status == 409


async def test_failed_load_is_reported_with_engine_message():
    agent = make()
    op = await agent.submit(OperationRequest(kind=OperationKind.START, config_id="gpt-oss-120b"))
    op = await wait_op(agent, op.id)
    assert op.status == OperationStatus.FAILED
    assert op.error.code == ErrorCode.ENGINE_FAILED
    assert "out of memory" in op.error.message
    st = await agent.status()
    assert st.state == MachineState.FAILED and st.engine_url is None
    # a failed machine can be started again
    op2 = await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-128k"))
    assert (await wait_op(agent, op2.id)).status == OperationStatus.SUCCEEDED


async def test_reconcile_adopts_engine_started_elsewhere_and_switch_takes_over():
    agent = make("pythia")  # scenario starts with an unowned engine already serving
    await agent.reconcile()
    st = await agent.status()
    assert st.state == MachineState.READY and st.active_config == "iq3_xxs-32k"
    assert "outside pantheon" in (st.message or "")

    op = await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="iq3_xxs-64k"))
    op = await wait_op(agent, op.id)
    assert op.status == OperationStatus.SUCCEEDED
    assert (await agent.status()).active_config == "iq3_xxs-64k"


async def test_start_while_serving_requires_switch_and_unknown_config_404():
    agent = make("mbp")
    await agent.reconcile()
    with pytest.raises(AgentError) as e:
        await agent.submit(OperationRequest(kind=OperationKind.START, config_id="qwen3.8-27b"))
    assert e.value.status == 409
    with pytest.raises(AgentError) as e:
        await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="nope"))
    assert e.value.status == 404


async def test_http_api_requires_token_and_returns_operation_id_immediately():
    adapter = SimulatedAdapter(fast("zeus", load_seconds=1.0))
    agent = MachineAgent("zeus", adapter, EngineKind.SIMULATED)
    app = build_agent_app(agent, "secret", [adapter.engine_router()])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as c:
        assert (await c.get("/v1/health")).status_code == 200
        r = await c.get("/v1/status")
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"

        h = {"Authorization": "Bearer secret"}
        r = await c.post("/v1/operations", json={"kind": "start", "config_id": "iq3_xxs-128k"}, headers=h)
        assert r.status_code == 202
        op = r.json()
        assert op["status"] in ("pending", "running") and op["progress"] < 1.0

        r = await c.post("/v1/operations", json={"kind": "stop"}, headers=h)
        assert r.status_code == 409 and r.json()["error"]["code"] == "busy"

        assert (await c.get(f"/v1/operations/{op['id']}", headers=h)).json()["id"] == op["id"]
        await wait_op(agent, op["id"])
        logs = (await c.get("/v1/logs", headers=h)).json()
        assert any("ready" in l["text"] for l in logs)
