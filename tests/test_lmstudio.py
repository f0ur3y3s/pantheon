import asyncio
import json
import sys
from pathlib import Path

import pytest

from pantheon.agent.adapters.lmstudio import LMProfile, LMStudioAdapter, LMStudioSettings
from pantheon.agent.core import MachineAgent
from pantheon.contracts import EngineKind, MachineState, OperationKind, OperationRequest, OperationStatus

from test_agent import wait_op

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_lms.py"))


@pytest.fixture
def lms_state(tmp_path, monkeypatch):
    path = tmp_path / "lms.json"
    monkeypatch.setenv("FAKE_LMS_STATE", str(path))
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")  # real lms emits braille spinners
    path.write_text(json.dumps({"server": True, "loaded": []}))
    return path


def make(**kw) -> tuple[MachineAgent, LMStudioAdapter]:
    settings = LMStudioSettings(
        engine_url="http://mbp.test:8080/v1",
        lms_command=[sys.executable, FAKE],
        profiles=[LMProfile(id="qwen36", model_key="qwen/qwen3.6-35b-a3b", identifier="qwen36",
                            label="Qwen3.6 35B · 128k", context=131072, parallel=4)],
        readiness="none",
        **kw,
    )
    adapter = LMStudioAdapter(settings)
    return MachineAgent("mbp", adapter, EngineKind.LMSTUDIO), adapter


async def test_configs_merge_profiles_with_catalog(lms_state):
    _, adapter = make()
    ids = [c.id for c in await adapter.list_configs()]
    assert ids[0] == "qwen36"  # profile first
    assert "prism-ml/bonsai-27b" in ids
    assert "qwen/qwen3.6-35b-a3b" not in ids  # covered by the profile
    assert not any("nomic" in i for i in ids)  # embeddings are not launch targets
    caps = await adapter.capabilities()
    assert caps.engine_version == "69d945a" and not caps.native_drain


async def test_start_parses_progress_then_switch_and_stop(lms_state):
    agent, _ = make()
    await agent.reconcile()
    assert (await agent.status()).state == MachineState.IDLE

    op = await agent.submit(OperationRequest(kind=OperationKind.START, config_id="qwen36"))
    progress = set()
    while op.status not in (OperationStatus.SUCCEEDED, OperationStatus.FAILED):
        progress.add(round(op.progress, 2))
        await asyncio.sleep(0.005)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    st = await agent.status()
    assert st.state == MachineState.READY and st.active_config == "qwen36" and st.served_models == ["qwen36"]
    assert st.engine_url == "http://mbp.test:8080/v1"
    loaded = json.loads(lms_state.read_text())["loaded"][0]
    assert loaded["contextLength"] == 131072 and loaded["parallel"] == 4

    op = await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="prism-ml/bonsai-27b"))
    op = await wait_op(agent, op.id)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    assert [m["identifier"] for m in json.loads(lms_state.read_text())["loaded"]] == ["prism-ml/bonsai-27b"]

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.STOP))).id)
    assert op.status == OperationStatus.SUCCEEDED
    assert (await agent.status()).state == MachineState.IDLE
    assert json.loads(lms_state.read_text())["loaded"] == []
    logs = await agent.logs(100)
    assert any("Model loaded successfully" in l.text for l in logs)
    assert any('unloaded' in l.text for l in logs)


async def test_guardrail_failure_surfaces_lm_studio_message(lms_state):
    agent, _ = make()
    await agent.reconcile()
    op = await agent.submit(OperationRequest(kind=OperationKind.START, config_id="huge/model"))
    op = await wait_op(agent, op.id)
    assert op.status == OperationStatus.FAILED
    assert op.error.message.startswith("Model loading was stopped due to insufficient system resources")
    assert (await agent.status()).state == MachineState.FAILED


async def test_reconcile_maps_loaded_instance_and_flags_unknown(lms_state):
    lms_state.write_text(json.dumps({"server": True, "loaded": [
        {"type": "llm", "modelKey": "qwen/qwen3.6-35b-a3b", "identifier": "qwen36"}]}))
    agent, _ = make()
    await agent.reconcile()
    st = await agent.status()
    assert st.state == MachineState.READY and st.active_config == "qwen36"

    lms_state.write_text(json.dumps({"server": True, "loaded": [
        {"type": "llm", "modelKey": "prism-ml/bonsai-27b", "identifier": "custom-name"}]}))
    await agent.reconcile()
    st = await agent.status()
    assert st.state == MachineState.READY and st.active_config is None
    assert "match no saved config" in st.message


async def test_server_down_is_idle_and_start_brings_it_up(lms_state):
    lms_state.write_text(json.dumps({"server": False, "loaded": []}))
    agent, _ = make()
    await agent.reconcile()
    st = await agent.status()
    assert st.state == MachineState.IDLE and "not running" in st.message
    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="qwen36"))).id)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    assert json.loads(lms_state.read_text())["server"] is True
