import json

import httpx
import pytest

from pantheon.agent.adapters.simulated import SimulatedAdapter
from pantheon.agent.core import AgentError, MachineAgent
from pantheon.agent.params import ParamStore
from pantheon.contracts import EngineKind, OperationKind, OperationRequest, OperationStatus, ParamUpdate

from conftest import fast
from test_agent import wait_op
from test_gateway import ADMIN, Stack
from test_lmstudio import lms_state, make as make_lms  # noqa: F401  (fixture)
from test_strata import make as make_strata, strata_env  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio


def sim_agent(store: ParamStore | None = None) -> MachineAgent:
    return MachineAgent("zeus", SimulatedAdapter(fast("zeus")), EngineKind.SIMULATED, store)


async def test_validation_rejects_bad_values_with_clear_messages():
    agent = sim_agent()
    for values, msg in [
        ({"context": 100}, "below the minimum"),
        ({"context": "lots"}, "expected int"),
        ({"context": 4096.5}, "expected int"),
        ({"temperature": 0.7}, "unknown parameter"),
        ({"parallel": 99}, "above the maximum"),
    ]:
        with pytest.raises(AgentError) as e:
            await agent.set_params("iq3_xxs-128k", ParamUpdate(values=values))
        assert e.value.status == 400 and msg in e.value.body.message


async def test_overrides_persist_shape_effective_configs_and_reset(tmp_path):
    path = tmp_path / "params.json"
    agent = sim_agent(ParamStore(path))
    p = await agent.set_params("iq3_xxs-128k", ParamUpdate(values={"context": 65536, "parallel": 2}))
    assert p.base == {"context": 131072, "parallel": 1}
    assert p.overrides == {"context": 65536, "parallel": 2} and p.values["context"] == 65536
    assert json.loads(path.read_text()) == {"iq3_xxs-128k": {"context": 65536, "parallel": 2}}

    cfg = next(c for c in await agent.configs() if c.id == "iq3_xxs-128k")
    assert cfg.edited and cfg.context == 65536 and cfg.parallel == 2

    # a fresh agent reads the file
    agent2 = sim_agent(ParamStore(path))
    assert (await agent2.get_params("iq3_xxs-128k")).overrides == {"context": 65536, "parallel": 2}

    # setting a value equal to base drops that override; null resets
    p = await agent2.set_params("iq3_xxs-128k", ParamUpdate(values={"parallel": 1, "context": None}))
    assert p.overrides == {}
    assert not next(c for c in await agent2.configs() if c.id == "iq3_xxs-128k").edited


async def test_restart_required_tracks_values_the_model_started_with():
    agent = sim_agent()
    op = await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-128k"))
    await wait_op(agent, op.id)
    p = await agent.get_params("iq3_xxs-128k")
    assert p.active and not p.restart_required

    p = await agent.set_params("iq3_xxs-128k", ParamUpdate(values={"context": 32768}))
    assert p.restart_required  # serving model still has the old context

    # "Save & apply" = switch to the same config
    op = await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="iq3_xxs-128k"))
    assert (await wait_op(agent, op.id)).status == OperationStatus.SUCCEEDED
    p = await agent.get_params("iq3_xxs-128k")
    assert p.active and not p.restart_required


async def test_lmstudio_edits_reach_lms_load(lms_state):  # noqa: F811
    agent, _ = make_lms()
    await agent.reconcile()
    await agent.set_params("qwen36", ParamUpdate(values={"context": 32768, "parallel": 2, "gpu": "max", "ttl": 600, "mtp": "off"}))
    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="qwen36"))).id)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    loaded = json.loads(lms_state.read_text())["loaded"][0]
    assert loaded["contextLength"] == 32768 and loaded["parallel"] == 2
    args = loaded["args"]
    assert args[args.index("--gpu") + 1] == "max" and args[args.index("--ttl") + 1] == "600"
    assert "--no-speculative-draft-mtp" in args

    with pytest.raises(AgentError) as e:
        await agent.set_params("qwen36", ParamUpdate(values={"gpu": "lots"}))
    assert "not one of" in e.value.body.message


async def test_strata_edits_launch_an_edited_copy_and_never_touch_the_preset(strata_env):  # noqa: F811
    control, presets, state = strata_env
    original = (presets / "iq3_xxs-32k.json").read_text()
    agent, a = make_strata(strata_env)
    p = await agent.get_params("iq3_xxs-32k")
    assert {s.key for s in p.schema_} == {"parallel", "context"}  # only flags the preset already uses
    await agent.set_params("iq3_xxs-32k", ParamUpdate(values={"context": 16384}))

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-32k"))).id, timeout=20)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    health = await a._health()
    assert health["max_context"] == 16384  # the server was launched from the edited copy
    assert (presets / "iq3_xxs-32k.json").read_text() == original
    copy = json.loads((state / "presets" / "iq3_xxs-32k.json").read_text())
    assert copy["args"][copy["args"].index("--max-context") + 1] == "16384"
    await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.STOP))).id, timeout=30)


async def test_gateway_proxies_params_including_slash_ids_and_errors():
    async with Stack({"mbp": {}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as c:
        r = await c.get("/api/machines/mbp/configs/qwen/qwen3.6-35b-a3b/params", headers=ADMIN)
        assert r.status_code == 200 and r.json()["schema"][0]["key"] == "context"
        r = await c.put("/api/machines/mbp/configs/qwen/qwen3.6-35b-a3b/params", headers=ADMIN,
                        json={"values": {"context": 65536}})
        assert r.status_code == 200 and r.json()["restart_required"] is True
        r = await c.put("/api/machines/mbp/configs/qwen/qwen3.6-35b-a3b/params", headers=ADMIN,
                        json={"values": {"context": 1}})
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid"
        cfgs = (await c.get("/api/machines/mbp/configs", headers=ADMIN)).json()
        assert next(x for x in cfgs if x["id"] == "qwen/qwen3.6-35b-a3b")["edited"] is True
