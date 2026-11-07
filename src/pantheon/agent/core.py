"""Machine agent core: owns machine state and serializes operations.

Ownership: the dashboard/gateway *requests* operations; this process owns the engine lifecycle.
Exactly one operation runs per machine at a time. A second request while one is running is
rejected with BUSY rather than queued, so callers always know whether their request took effect.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict, deque

from pantheon.agent.adapters.base import Adapter, EngineError, EngineProbe
from pantheon.agent.params import ParamError, ParamStore, validate
from pantheon.contracts import (
    ConfigParams,
    EngineKind,
    ErrorBody,
    ErrorCode,
    LogLine,
    MachineState,
    MachineStatus,
    ModelConfig,
    Operation,
    OperationKind,
    OperationRequest,
    OperationStatus,
    ParamUpdate,
)

log = logging.getLogger("pantheon.agent")

MAX_OPERATIONS = 100
MAX_AGENT_LOG = 2000


class AgentError(Exception):
    def __init__(self, code: ErrorCode, message: str, status: int):
        super().__init__(message)
        self.body = ErrorBody(code=code, message=message)
        self.status = status


class MachineAgent:
    def __init__(self, name: str, adapter: Adapter, engine: EngineKind, params: ParamStore | None = None):
        self.name = name
        self.params = params or ParamStore(None)
        # Effective parameter values the serving model was started with (None: started outside pantheon).
        self._started_values: dict | None = None
        self.adapter = adapter
        self.engine = engine
        self._state = MachineState.IDLE
        self._probe = EngineProbe(serving=False)
        self._message: str | None = None
        self._ops: OrderedDict[str, Operation] = OrderedDict()
        self._current: Operation | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._log: deque[LogLine] = deque(maxlen=MAX_AGENT_LOG)

    # ---- lifecycle -------------------------------------------------------

    async def reconcile(self) -> None:
        """Align in-memory state with what the engine is actually doing (startup, periodic)."""
        if self._current is not None:
            return
        try:
            probe = await self.adapter.probe()
        except Exception as exc:  # adapter bugs must not kill the agent
            self._set(MachineState.FAILED, f"probe failed: {exc}")
            return
        self._probe = probe
        if probe.serving:
            msg = probe.message
            if not probe.owned:
                msg = msg or "engine was started outside pantheon; stop/switch will take it over"
            self._set(MachineState.READY, msg)
        elif self._state != MachineState.FAILED:
            self._set(MachineState.IDLE, probe.message)

    async def shutdown(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ---- queries ---------------------------------------------------------

    async def status(self) -> MachineStatus:
        resources = None
        try:
            resources = await self.adapter.resources()
        except Exception:
            pass
        ready = self._state == MachineState.READY
        return MachineStatus(
            machine=self.name,
            state=self._state,
            engine=self.engine,
            active_config=self._probe.config_id if self._probe.serving else None,
            served_models=self._probe.served_models if self._probe.serving else [],
            engine_url=self._probe.engine_url if ready else None,
            current_operation=self._current.id if self._current else None,
            message=self._message,
            resources=resources,
        )

    def get_operation(self, op_id: str) -> Operation:
        op = self._ops.get(op_id)
        if op is None:
            raise AgentError(ErrorCode.NOT_FOUND, f"operation {op_id} not found", 404)
        return op

    def list_operations(self, limit: int = 20) -> list[Operation]:
        return list(reversed(self._ops.values()))[:limit]

    async def logs(self, lines: int) -> list[LogLine]:
        engine = []
        try:
            engine = await self.adapter.engine_logs(lines)
        except Exception as exc:
            self._note(f"engine log read failed: {exc}")
        merged = sorted([*list(self._log)[-lines:], *engine], key=lambda l: l.ts)
        return merged[-lines:]

    # ---- operations ------------------------------------------------------

    async def submit(self, req: OperationRequest) -> Operation:
        async with self._lock:
            if self._current is not None:
                raise AgentError(
                    ErrorCode.BUSY,
                    f"operation {self._current.id} ({self._current.kind}) is still running",
                    409,
                )
            config = None
            if req.kind in (OperationKind.START, OperationKind.SWITCH):
                if not req.config_id:
                    raise AgentError(ErrorCode.INVALID, f"{req.kind} requires config_id", 400)
                config = await self._find_config(req.config_id)
            if req.kind == OperationKind.START and self._probe.serving:
                raise AgentError(
                    ErrorCode.INVALID,
                    f"{self._probe.config_id or 'a model'} is already serving; use switch",
                    409,
                )

            op = Operation(id=uuid.uuid4().hex[:12], machine=self.name, kind=req.kind, config_id=req.config_id)
            self._remember(op)
            self._current = op
            self._task = asyncio.create_task(self._run(op, config), name=f"op-{op.id}")
            return op

    async def _base_config(self, config_id: str) -> ModelConfig:
        for cfg in await self.adapter.list_configs():
            if cfg.id == config_id:
                return cfg
        raise AgentError(ErrorCode.NOT_FOUND, f"unknown config {config_id!r}", 404)

    def _values(self, base: ModelConfig) -> dict:
        return {**self.adapter.base_params(base), **self.params.get(base.id)}

    def _effective(self, base: ModelConfig) -> ModelConfig:
        overrides = self.params.get(base.id)
        if not overrides:
            return base
        cfg = self.adapter.apply_params(base, self._values(base))
        return cfg.model_copy(update={"edited": True})

    async def configs(self) -> list[ModelConfig]:
        return [self._effective(c) for c in await self.adapter.list_configs()]

    async def _find_config(self, config_id: str) -> ModelConfig:
        return self._effective(await self._base_config(config_id))

    async def get_params(self, config_id: str) -> ConfigParams:
        base = await self._base_config(config_id)
        values = self._values(base)
        active = self._probe.serving and self._probe.config_id == config_id
        if not active:
            restart = False
        elif self._started_values is None:
            restart = bool(self.params.get(config_id))  # adopted engine: unknown start values
        else:
            restart = self._started_values != values
        return ConfigParams(config_id=config_id, schema=self.adapter.param_schema(base),
                            base=self.adapter.base_params(base), overrides=self.params.get(config_id),
                            values=values, active=active, restart_required=restart)

    async def set_params(self, config_id: str, update: ParamUpdate) -> ConfigParams:
        base = await self._base_config(config_id)
        try:
            changes = validate(self.adapter.param_schema(base), update.values)
        except ParamError as exc:
            raise AgentError(ErrorCode.INVALID, str(exc), 400) from None
        # Drop overrides equal to the base value so "edited" means something.
        base_values = self.adapter.base_params(base)
        changes = {k: (None if v is not None and base_values.get(k) == v else v) for k, v in changes.items()}
        self.params.update(config_id, changes)
        self._note(f"params {config_id}: {changes}")
        return await self.get_params(config_id)

    async def _run(self, op: Operation, config: ModelConfig | None) -> None:
        self._update(op, status=OperationStatus.RUNNING, step="starting")
        try:
            if op.kind in (OperationKind.STOP, OperationKind.SWITCH):
                await self._do_stop(op, span=(0.0, 0.3) if op.kind == OperationKind.SWITCH else (0.0, 1.0))
            if op.kind in (OperationKind.START, OperationKind.SWITCH):
                assert config is not None
                await self._do_start(op, config, span=(0.3, 1.0) if op.kind == OperationKind.SWITCH else (0.0, 1.0))
            self._update(op, status=OperationStatus.SUCCEEDED, progress=1.0, step="done")
        except asyncio.CancelledError:
            self._update(op, status=OperationStatus.CANCELLED, step="cancelled")
            self._set(MachineState.FAILED, "operation cancelled; state unknown until next reconcile")
            raise
        except EngineError as exc:
            await self._fail(op, ErrorCode.ENGINE_FAILED, exc.message, exc.detail)
        except Exception as exc:
            log.exception("operation %s crashed", op.id)
            await self._fail(op, ErrorCode.INTERNAL, f"{type(exc).__name__}: {exc}", {})
        finally:
            self._current = None

    async def _do_stop(self, op: Operation, span: tuple[float, float]) -> None:
        probe = await self.adapter.probe()
        if not probe.serving:
            self._note("stop: nothing serving")
            self._probe = EngineProbe(serving=False)
            self._set(MachineState.IDLE)
            return
        self._set(MachineState.STOPPING, f"stopping {probe.config_id or 'engine'}")
        await self.adapter.stop(self._reporter(op, span))
        self._probe = EngineProbe(serving=False)
        self._started_values = None
        self._set(MachineState.IDLE)

    async def _do_start(self, op: Operation, config: ModelConfig, span: tuple[float, float]) -> None:
        self._set(MachineState.LOADING, f"loading {config.label}")
        probe = await self.adapter.start(config, self._reporter(op, span))
        if not probe.serving:
            raise EngineError("engine reported not serving after start")
        self._probe = probe
        self._started_values = self._values(await self._base_config(config.id))
        self._set(MachineState.READY)

    # ---- helpers ---------------------------------------------------------

    def _reporter(self, op: Operation, span: tuple[float, float]):
        lo, hi = span

        async def report(fraction: float, step: str) -> None:
            fraction = min(max(fraction, 0.0), 1.0)
            self._update(op, progress=lo + (hi - lo) * fraction, step=step)

        return report

    async def _fail(self, op: Operation, code: ErrorCode, message: str, detail: dict) -> None:
        # Don't assume the failure left nothing running (e.g. a failed stop): ask the engine.
        # Probe *before* publishing the failure so op and machine state change together.
        try:
            self._probe = await self.adapter.probe()
        except Exception:
            self._probe = EngineProbe(serving=False)
        if self._probe.serving:
            self._set(MachineState.READY, f"last operation failed: {message}")
        else:
            self._set(MachineState.FAILED, message)
        self._update(op, status=OperationStatus.FAILED, step="failed", error=ErrorBody(code=code, message=message, detail=detail))

    def _update(self, op: Operation, **fields) -> None:
        now = time.time()
        for k, v in fields.items():
            setattr(op, k, v)
        op.updated_at = now
        if op.status in (OperationStatus.SUCCEEDED, OperationStatus.FAILED, OperationStatus.CANCELLED):
            op.finished_at = now
        if "step" in fields:
            self._note(f"[{op.id}] {op.kind} {op.config_id or ''}: {op.step} ({op.progress:.0%})")

    def _set(self, state: MachineState, message: str | None = None) -> None:
        if state != self._state:
            self._note(f"state {self._state} -> {state}" + (f": {message}" if message else ""))
        self._state = state
        self._message = message

    def _remember(self, op: Operation) -> None:
        self._ops[op.id] = op
        while len(self._ops) > MAX_OPERATIONS:
            self._ops.popitem(last=False)

    def _note(self, text: str) -> None:
        log.info("%s: %s", self.name, text)
        self._log.append(LogLine(ts=time.time(), source="agent", text=text))
