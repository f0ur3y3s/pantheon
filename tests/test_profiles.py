import json

import httpx
import pytest

from pantheon.agent.adapters.simulated import SimulatedAdapter
from pantheon.agent.core import AgentError, MachineAgent
from pantheon.agent.params import ParamStore
from pantheon.agent.profiles import ProfileStore
from pantheon.contracts import CreateProfile, EngineKind, OperationKind, OperationRequest, OperationStatus, ParamUpdate

from conftest import fast
from test_agent import wait_op
from test_gateway import ADMIN, Stack, wait_for
from test_lmstudio import lms_state, make as make_lms  # noqa: F401
from test_strata import make as make_strata, strata_env  # noqa: F401

pytestmark = pytest.mark.anyio


async def test_profile_copies_edits_persists_and_launches_without_changing_source(tmp_path):
    params_path = tmp_path / "params.json"
    profiles_path = tmp_path / "profiles.json"
    agent = MachineAgent("zeus", SimulatedAdapter(fast("zeus")), EngineKind.SIMULATED,
                         ParamStore(params_path), ProfileStore(profiles_path))
    await agent.set_params("iq3_xxs-128k", ParamUpdate(values={"context": 65536}))
    profile = await agent.create_profile(CreateProfile(label="Long session", source_config_id="iq3_xxs-128k"))
    assert profile.profile and profile.source_config_id == "iq3_xxs-128k"
    assert profile.context == 65536 and profile.id.startswith("profile/")

    await agent.set_params(profile.id, ParamUpdate(values={"context": 32768}))
    assert (await agent.get_params("iq3_xxs-128k")).values["context"] == 65536
    assert (await agent.get_params(profile.id)).values["context"] == 32768

    op = await agent.submit(OperationRequest(kind=OperationKind.START, config_id=profile.id))
    assert (await wait_op(agent, op.id)).status == OperationStatus.SUCCEEDED
    assert (await agent.status()).active_config == profile.id

    fresh = MachineAgent("zeus", SimulatedAdapter(fast("zeus")), EngineKind.SIMULATED,
                         ParamStore(params_path), ProfileStore(profiles_path))
    assert next(c for c in await fresh.configs() if c.id == profile.id).context == 32768


async def test_profile_validation_and_copy_from_profile():
    agent = MachineAgent("zeus", SimulatedAdapter(fast("zeus")), EngineKind.SIMULATED)
    for label, source, status in [("  ", "iq3_xxs-128k", 400), ("Valid", "missing", 404)]:
        with pytest.raises(AgentError) as exc:
            await agent.create_profile(CreateProfile(label=label, source_config_id=source))
        assert exc.value.status == status
    original = await agent.create_profile(CreateProfile(label="First", source_config_id="iq3_xxs-128k"))
    await agent.set_params(original.id, ParamUpdate(values={"parallel": 2}))
    copied = await agent.create_profile(CreateProfile(label="Second", source_config_id=original.id))
    assert copied.source_config_id == "iq3_xxs-128k" and copied.parallel == 2
    with pytest.raises(AgentError) as exc:
        await agent.create_profile(CreateProfile(label="first", source_config_id="iq3_xxs-128k"))
    assert exc.value.status == 409


async def test_gateway_creates_profile_from_slash_id_and_launches_it():
    async with Stack({"mbp": {}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as client:
        path = "/api/machines/mbp/configs"
        request = {"label": "Review", "source_config_id": "qwen/qwen3.6-35b-a3b"}
        assert (await client.post(path, json=request)).status_code == 401
        response = await client.post(path, json=request, headers=ADMIN)
        assert response.status_code == 201, response.text
        profile = response.json()
        assert profile["profile"] is True and profile["source_config_id"] == request["source_config_id"]
        configs = (await client.get(path, headers=ADMIN)).json()
        assert any(c["id"] == profile["id"] for c in configs)
        params = await client.get(f"{path}/{profile['id']}/params", headers=ADMIN)
        assert params.status_code == 200
        response = await client.post("/api/machines/mbp/operations", headers=ADMIN,
                                     json={"kind": "switch", "config_id": profile["id"]})
        assert response.status_code == 202
        assert (await wait_for(client, "mbp", response.json()["id"]))["status"] == "succeeded"
        status = (await client.get("/api/machines/mbp", headers=ADMIN)).json()["status"]
        assert status["active_config"] == profile["id"]


async def test_lmstudio_profile_uses_its_own_launch_params(lms_state):  # noqa: F811
    agent, _ = make_lms()
    profile = await agent.create_profile(CreateProfile(label="Short context", source_config_id="qwen36"))
    await agent.set_params(profile.id, ParamUpdate(values={"context": 32768}))
    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id=profile.id))).id)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    assert json.loads(lms_state.read_text())["loaded"][0]["contextLength"] == 32768
    await agent.reconcile()
    assert (await agent.status()).active_config == profile.id


async def test_strata_profile_launches_edited_preset_copy(strata_env):  # noqa: F811
    agent, adapter = make_strata(strata_env)
    profile = await agent.create_profile(CreateProfile(label="Short context", source_config_id="iq3_xxs-32k"))
    await agent.set_params(profile.id, ParamUpdate(values={"context": 16384}))
    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id=profile.id))).id,
                       timeout=20)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    assert (await adapter._health())["max_context"] == 16384
    assert (await agent.status()).active_config == profile.id
    assert (strata_env[2] / "presets" / f"{profile.id}.json").exists()
    await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.STOP))).id, timeout=30)
