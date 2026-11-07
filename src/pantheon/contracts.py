"""Shared interface definitions between machine agents, the gateway, and the dashboard.

This module is the single source of truth for the machine-service API (v1).
The dashboard mirrors these shapes in dashboard/src/contracts.ts; keep them in sync.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

API_VERSION = "v1"


class MachineState(StrEnum):
    OFFLINE = "offline"  # agent unreachable (set by the gateway, never by an agent)
    IDLE = "idle"  # agent up, no model serving
    LOADING = "loading"  # a configuration is starting
    READY = "ready"  # a configuration passed its readiness check and can take requests
    DRAINING = "draining"  # no new requests; waiting for active ones to finish
    STOPPING = "stopping"  # engine process is being stopped
    FAILED = "failed"  # last operation failed; engine is not serving


class EngineKind(StrEnum):
    STRATA = "strata"
    LLAMA_CPP = "llama.cpp"
    LMSTUDIO = "lmstudio"
    SIMULATED = "simulated"


class Capabilities(BaseModel):
    engine: EngineKind
    engine_version: str
    # Feature flags detected from the installed engine; adapters must not assume newer features.
    can_start_stop: bool = True
    native_drain: bool = False  # engine itself drains in-flight requests on stop
    reports_slots: bool = False
    reports_memory: bool = False
    embeddings: bool = False
    max_parallel: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    """A launchable configuration known to a machine (Strata preset, LM Studio model, llama.cpp profile)."""

    id: str  # stable within a machine
    label: str
    engine: EngineKind
    model: str  # the model id clients see once it is serving
    context: int | None = None
    parallel: int | None = None
    est_memory_mib: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    edited: bool = False  # has pantheon overrides (context/parallel above already reflect them)


class ParamType(StrEnum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"


class ParamSpec(BaseModel):
    """One editable parameter of a configuration, declared by the adapter for its engine version."""

    key: str
    label: str
    type: ParamType
    min: float | None = None
    max: float | None = None
    options: list[str] | None = None  # ENUM
    restart: bool = True  # takes effect only when the model is (re)started
    help: str | None = None


class ConfigParams(BaseModel):
    """GET/PUT /v1/configs/{id}/params."""

    config_id: str
    schema_: list[ParamSpec] = Field(alias="schema")
    base: dict[str, Any]  # values from the engine's own config (LM Studio defaults, Strata preset, ...)
    overrides: dict[str, Any]  # values set from pantheon
    values: dict[str, Any]  # effective = base + overrides
    active: bool = False  # this config is currently serving
    restart_required: bool = False  # active, and the serving model was started with different values

    model_config = {"populate_by_name": True}


class ParamUpdate(BaseModel):
    # key -> new value; null removes the override (back to the base value)
    values: dict[str, Any]


class Resources(BaseModel):
    gpu_name: str | None = None
    vram_total_mib: int | None = None
    vram_used_mib: int | None = None
    ram_total_mib: int | None = None
    ram_used_mib: int | None = None


class MachineStatus(BaseModel):
    machine: str
    state: MachineState
    engine: EngineKind
    active_config: str | None = None
    served_models: list[str] = Field(default_factory=list)
    # OpenAI-compatible base URL of the engine (e.g. http://192.0.2.11:8080/v1); None unless READY.
    engine_url: str | None = None
    current_operation: str | None = None
    message: str | None = None
    resources: Resources | None = None
    updated_at: float = Field(default_factory=time.time)


class OperationKind(StrEnum):
    START = "start"
    STOP = "stop"
    SWITCH = "switch"  # stop current (if any) then start config_id


class OperationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OperationRequest(BaseModel):
    kind: OperationKind
    config_id: str | None = None


class Operation(BaseModel):
    id: str
    machine: str
    kind: OperationKind
    config_id: str | None = None
    status: OperationStatus = OperationStatus.PENDING
    progress: float = 0.0  # 0..1, best-effort
    step: str = "queued"  # short human-readable phase
    error: ErrorBody | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    finished_at: float | None = None


class ErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    BUSY = "busy"  # another operation is already running on this machine
    INVALID = "invalid"
    ENGINE_FAILED = "engine_failed"
    NOT_READY = "not_ready"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    INTERNAL = "internal"


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class LogLine(BaseModel):
    ts: float
    source: str  # "agent" | "engine"
    text: str


Operation.model_rebuild()
