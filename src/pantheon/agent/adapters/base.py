"""Backend adapter interface.

An adapter translates generic machine operations into one engine's controls
(Strata CLI, LM Studio `lms`, a llama-server process, or the simulator).

Rules for implementers:
- Adapters do not track operation state or serialize work; the agent core does that.
- `start`/`stop` may take minutes. Report progress through `report` and raise EngineError on failure.
- `probe` must reflect what is *actually* running, not what the adapter last did; the agent uses it
  to reconcile after restarts.
- Detect the installed engine version in `capabilities` and gate behavior on it. Never assume a
  feature that only newer upstream versions have.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pantheon.contracts import Capabilities, LogLine, ModelConfig, ParamSpec, Resources

ProgressFn = Callable[[float, str], Awaitable[None]]


class EngineError(Exception):
    """A start/stop/readiness failure with a message suitable for the dashboard."""

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


@dataclass
class EngineProbe:
    serving: bool
    config_id: str | None = None  # None if serving something the adapter can't map to a config
    served_models: list[str] = field(default_factory=list)
    engine_url: str | None = None  # OpenAI-compatible base, ending in /v1
    owned: bool = True  # False if the running engine was not started by this agent/adapter
    message: str | None = None


class Adapter(Protocol):
    async def capabilities(self) -> Capabilities: ...

    async def list_configs(self) -> list[ModelConfig]: ...

    async def probe(self) -> EngineProbe: ...

    async def start(self, config: ModelConfig, report: ProgressFn) -> EngineProbe:
        """Start `config` and return once it passes a readiness check."""
        ...

    async def stop(self, report: ProgressFn) -> None: ...

    async def resources(self) -> Resources | None: ...

    async def engine_logs(self, lines: int) -> list[LogLine]: ...

    # ---- editable parameters ---------------------------------------------
    # Declare only parameters the *installed* engine version accepts. Values may be None, meaning
    # "engine default". Overrides are stored by the agent core, never written into engine-owned files.

    def param_schema(self, config: ModelConfig) -> list[ParamSpec]: ...

    def base_params(self, config: ModelConfig) -> dict: ...

    def apply_params(self, config: ModelConfig, values: dict) -> ModelConfig:
        """Return the config to launch with `values` (effective = base + overrides) applied."""
        ...
