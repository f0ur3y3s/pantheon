import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest

from pantheon.agent.adapters.strata import StrataAdapter, StrataSettings
from pantheon.agent.core import MachineAgent
from pantheon.contracts import EngineKind, MachineState, OperationKind, OperationRequest, OperationStatus

from test_agent import wait_op

pytestmark = pytest.mark.anyio
FAKE = str(Path(__file__).with_name("fake_strata_server.py"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def strata_env(tmp_path, monkeypatch):
    control = tmp_path / "control"
    control.mkdir()
    (control / "VERSION").write_text("0.1.54\n")
    presets = tmp_path / "launch"
    presets.mkdir()
    for name, ctx in (("iq3_xxs-32k", 32768), ("iq3_xxs-64k", 65536)):
        (presets / f"{name}.json").write_text(json.dumps({
            "exe": "component:strata-engine", "model_name": "qwen3.8-flash-next-iq3_xxs",
            "args": ["--pack", "/x", "--max-context", str(ctx), "--kv", "int8"]}))
    (presets / "iq3_xxs-64k.choices.json").write_text("{}")  # must be ignored
    (presets / "iq3_xxs-64k.tune.json").write_text("{}")
    monkeypatch.setenv("FAKE_STRATA_LOAD", "0.5")
    return control, presets, tmp_path / "state"


def make(strata_env, **kw) -> tuple[MachineAgent, StrataAdapter]:
    control, presets, state = strata_env
    port = kw.pop("port", None) or free_port()
    settings = StrataSettings(control_dir=str(control), engine_url=f"http://127.0.0.1:{port}/v1", state_dir=str(state),
                              server_command=[sys.executable, FAKE], preset_dirs=[str(presets)], bind_host="127.0.0.1",
                              port=port, expected_load_seconds=1, exit_grace=1.0, drain_timeout=20, **kw)
    adapter = StrataAdapter(settings)
    return MachineAgent("pythia", adapter, EngineKind.STRATA), adapter


async def test_presets_become_configs_and_version_is_reported(strata_env):
    _, a = make(strata_env)
    configs = {c.id: c for c in await a.list_configs()}
    assert set(configs) == {"iq3_xxs-32k", "iq3_xxs-64k"}
    assert configs["iq3_xxs-64k"].context == 65536
    assert configs["iq3_xxs-64k"].model == "qwen3.8-flash-next-iq3_xxs"
    caps = await a.capabilities()
    assert caps.engine_version == "0.1.54" and caps.extra["supported_version"] and caps.native_drain


@pytest.mark.parametrize("hang", ["0", "1"], ids=["exits-after-stop", "0.1.54-lingers-after-stop"])
async def test_start_switch_stop_including_lingering_server(strata_env, monkeypatch, hang):
    monkeypatch.setenv("FAKE_STRATA_HANG_AFTER_STOP", hang)
    agent, a = make(strata_env)
    await agent.reconcile()
    assert (await agent.status()).state == MachineState.IDLE

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-32k"))).id, timeout=20)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    st = await agent.status()
    assert st.state == MachineState.READY and st.active_config == "iq3_xxs-32k"
    assert st.served_models == ["qwen3.8-flash-next-iq3_xxs"]
    first_pid = a._proc.pid

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="iq3_xxs-64k"))).id, timeout=40)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    assert not psutil.pid_exists(first_pid) or psutil.Process(first_pid).status() == psutil.STATUS_ZOMBIE
    assert (await agent.status()).active_config == "iq3_xxs-64k"

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.STOP))).id, timeout=40)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    assert (await agent.status()).state == MachineState.IDLE
    assert not a.pid_file.exists() and not a.ready_file.exists()


async def test_stop_drains_in_flight_stream_first(strata_env, monkeypatch):
    monkeypatch.setenv("FAKE_STRATA_TPS", "20")
    agent, a = make(strata_env)
    await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-32k"))).id, timeout=20)

    async def stream():
        lines = 0
        async with httpx.AsyncClient(timeout=30) as http:
            async with http.stream("POST", f"{a.local_url}/v1/chat/completions",
                                   json={"model": "m", "stream": True, "max_tokens": 30, "messages": []}) as r:
                async for line in r.aiter_lines():
                    lines += line.startswith("data:")
        return lines

    task = asyncio.create_task(stream())
    await asyncio.sleep(0.3)
    op = await agent.submit(OperationRequest(kind=OperationKind.STOP))
    assert await task == 31  # all 30 tokens + [DONE]: the stop waited for the request
    assert (await wait_op(agent, op.id, timeout=30)).status == OperationStatus.SUCCEEDED


async def test_failed_load_reports_last_log_line(strata_env, monkeypatch):
    monkeypatch.setenv("FAKE_STRATA_FAIL", "CUDA out of memory allocating expert cache")
    agent, _ = make(strata_env)
    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-64k"))).id, timeout=20)
    assert op.status == OperationStatus.FAILED
    assert "CUDA out of memory" in op.error.message
    assert (await agent.status()).state == MachineState.FAILED


async def test_foreign_server_is_adopted_then_taken_over(strata_env, tmp_path):
    control, presets, _ = strata_env
    port = free_port()
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
    foreign = subprocess.Popen([sys.executable, FAKE, "--config", str(presets / "iq3_xxs-32k.json"), "--host", "127.0.0.1",
                                "--port", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **flags)
    try:
        agent, a = make(strata_env, port=port)
        for _ in range(50):
            if await a._health(timeout=0.5):
                break
            time.sleep(0.1)
        await agent.reconcile()
        st = await agent.status()
        assert st.state == MachineState.READY and st.active_config == "iq3_xxs-32k"
        assert "outside pantheon" in st.message

        op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="iq3_xxs-64k"))).id, timeout=40)
        assert op.status == OperationStatus.SUCCEEDED, op.error
        assert foreign.poll() is not None  # the experiment's server was stopped
        await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.STOP))).id, timeout=40)
    finally:
        if foreign.poll() is None:
            foreign.kill()


async def test_agent_restart_reattaches_to_its_server(strata_env):
    agent, a = make(strata_env)
    await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-32k"))).id, timeout=20)
    pid = a._proc.pid
    # a new agent process with the same state dir finds the server via the pid file
    agent2, a2 = make(strata_env, port=a.s.port)
    await agent2.reconcile()
    st = await agent2.status()
    assert a2._proc is not None and a2._proc.pid == pid
    assert st.state == MachineState.READY and st.active_config == "iq3_xxs-32k" and st.message is None
    op = await wait_op(agent2, (await agent2.submit(OperationRequest(kind=OperationKind.STOP))).id, timeout=40)
    assert op.status == OperationStatus.SUCCEEDED
