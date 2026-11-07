import copy

import pytest

from pantheon.agent.adapters.simulated import SCENARIOS, SimSettings


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fast(scenario: str, **overrides) -> SimSettings:
    s = copy.deepcopy(SCENARIOS[scenario])
    s.load_seconds = 0.2
    s.stop_seconds = 0.05
    s.tokens_per_second = 500
    for k, v in overrides.items():
        setattr(s, k, v)
    return s
