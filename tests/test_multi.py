import json
import sys

import pytest

from pantheon.agent.adapters.multi import MultiAdapter
from pantheon.agent.adapters.polystrata import PolyStrataAdapter
from pantheon.agent.adapters.strata import StrataAdapter, StrataSettings
from pantheon.agent.core import MachineAgent
from pantheon.contracts import EngineKind, MachineState, OperationKind, OperationRequest, OperationStatus

from test_agent import wait_op
from test_strata import FAKE, free_port

pytestmark = pytest.mark.anyio


def preset(path, model, ctx):
    path.write_text(json.dumps({"exe": "x", "model_name": model, "args": ["--max-context", str(ctx)]}))


@pytest.fixture
def pythia(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_STRATA_LOAD", "0.2")
    control = tmp_path / "control"
    control.mkdir()
    (control / "VERSION").write_text("0.1.54\n")
    launch = tmp_path / "launch"
    launch.mkdir()
    preset(launch / "iq3_xxs-32k.json", "qwen3.8-flash-next-iq3_xxs", 32768)
    preset(launch / "shared.json", "qwen-shared", 8192)
    checkout = tmp_path / "terminal"
    checkout.mkdir()
    preset(checkout / "strata-inkling-q4.json", "inkling-2-mini-q4", 32768)
    preset(checkout / "strata-shared.json", "inkling-shared", 8192)

    def settings(control_dir, state, **kw):
        port = free_port()
        return StrataSettings(control_dir=str(control_dir), engine_url=f"http://127.0.0.1:{port}/v1",
                              state_dir=str(tmp_path / state), server_command=[sys.executable, FAKE],
                              bind_host="127.0.0.1", port=port, expected_load_seconds=1, exit_grace=1.0, **kw)

    strata = StrataAdapter(settings(control, "strata", preset_dirs=[str(launch)]))
    poly = PolyStrataAdapter(settings(checkout, "polystrata"))
    multi = MultiAdapter([("strata", EngineKind.STRATA, strata), ("polystrata", EngineKind.POLYSTRATA, poly)])
    return MachineAgent("pythia", multi, EngineKind.STRATA), strata, poly


async def test_configs_from_both_engines_with_clashing_ids_prefixed(pythia):
    agent, _, _ = pythia
    configs = {c.id: c.engine for c in await agent.configs()}
    assert configs == {"iq3_xxs-32k": EngineKind.STRATA, "shared": EngineKind.STRATA,
                       "inkling-q4": EngineKind.POLYSTRATA, "polystrata/shared": EngineKind.POLYSTRATA}
    caps = await agent.adapter.capabilities()
    assert caps.engine_version.startswith("0.1.54 + polystrata ")


async def test_switch_between_engines(pythia):
    agent, strata, poly = pythia
    await agent.reconcile()

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.START, config_id="iq3_xxs-32k"))).id, timeout=20)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    st = await agent.status()
    assert (st.state, st.engine, st.active_config) == (MachineState.READY, EngineKind.STRATA, "iq3_xxs-32k")
    assert st.engine_url == strata.s.engine_url

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.SWITCH, config_id="polystrata/shared"))).id, timeout=40)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    st = await agent.status()
    assert (st.engine, st.active_config, st.served_models) == (EngineKind.POLYSTRATA, "polystrata/shared", ["inkling-shared"])
    assert st.engine_url == poly.s.engine_url
    assert not (await strata.probe()).serving

    await agent.reconcile()  # probing maps the native id back to the listed one
    assert (await agent.status()).active_config == "polystrata/shared"

    op = await wait_op(agent, (await agent.submit(OperationRequest(kind=OperationKind.STOP))).id, timeout=30)
    assert op.status == OperationStatus.SUCCEEDED, op.error
    st = await agent.status()
    assert (st.state, st.engine) == (MachineState.IDLE, EngineKind.STRATA)


async def test_polystrata_presets_added_later_are_listed(pythia, tmp_path):
    agent, _, poly = pythia
    preset(poly.control / "strata-inkling-128k.json", "inkling-2-mini-q4", 131072)
    configs = {c.id: c.context for c in await agent.configs()}
    assert configs["inkling-128k"] == 131072
