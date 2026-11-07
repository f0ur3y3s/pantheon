"""Gateway-side view of the machine agents: clients, status polling, offline detection."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from pantheon.contracts import (
    Capabilities,
    EngineKind,
    ErrorBody,
    ErrorCode,
    MachineState,
    MachineStatus,
)
from pantheon.gateway.config import MachineEntry

log = logging.getLogger("pantheon.gateway")

OFFLINE_AFTER_FAILURES = 2


class AgentCallError(Exception):
    def __init__(self, status: int, body: ErrorBody):
        super().__init__(body.message)
        self.status = status
        self.body = body


@dataclass
class MachineView:
    entry: MachineEntry
    status: MachineStatus
    capabilities: Capabilities | None = None
    last_seen: float | None = None
    last_error: str | None = None
    failures: int = 0
    active_requests: int = 0  # gateway-tracked in-flight client requests (used for draining)
    extra: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {
            "name": self.entry.name,
            "status": self.status.model_dump(mode="json"),
            "capabilities": self.capabilities.model_dump(mode="json") if self.capabilities else None,
            "last_seen": self.last_seen,
            "last_error": self.last_error,
            "active_requests": self.active_requests,
        }


class AgentClient:
    def __init__(self, entry: MachineEntry, transport: httpx.AsyncBaseTransport | None = None):
        self.entry = entry
        self.http = httpx.AsyncClient(
            base_url=entry.url,
            headers={"Authorization": f"Bearer {entry.token}"},
            timeout=httpx.Timeout(5.0),
            transport=transport,
        )

    async def call(self, method: str, path: str, **kw):
        try:
            r = await self.http.request(method, path, **kw)
        except httpx.TimeoutException as exc:
            raise AgentCallError(504, ErrorBody(code=ErrorCode.TIMEOUT, message=f"{self.entry.name}: agent timed out")) from exc
        except httpx.HTTPError as exc:
            raise AgentCallError(502, ErrorBody(code=ErrorCode.UNREACHABLE, message=f"{self.entry.name}: agent unreachable ({type(exc).__name__})")) from exc
        if r.status_code >= 400:
            try:
                body = ErrorBody.model_validate(r.json()["error"])
            except Exception:
                body = ErrorBody(code=ErrorCode.INTERNAL, message=f"{self.entry.name}: agent returned HTTP {r.status_code}")
            raise AgentCallError(r.status_code, body)
        return r.json()

    async def aclose(self):
        await self.http.aclose()


class MachineRegistry:
    def __init__(self, entries: list[MachineEntry], poll_seconds: float = 2.0,
                 transports: dict[str, httpx.AsyncBaseTransport] | None = None):
        transports = transports or {}
        self.poll_seconds = poll_seconds
        self.clients = {e.name: AgentClient(e, transports.get(e.name)) for e in entries}
        self.views = {
            e.name: MachineView(entry=e, status=MachineStatus(machine=e.name, state=MachineState.OFFLINE,
                                                               engine=EngineKind.SIMULATED, message="not contacted yet"))
            for e in entries
        }
        self._tasks: list[asyncio.Task] = []

    def get(self, name: str) -> tuple[AgentClient, MachineView]:
        if name not in self.clients:
            raise AgentCallError(404, ErrorBody(code=ErrorCode.NOT_FOUND, message=f"unknown machine {name!r}"))
        return self.clients[name], self.views[name]

    async def refresh(self, name: str) -> MachineView:
        client, view = self.get(name)
        try:
            status = MachineStatus.model_validate(await client.call("GET", "/v1/status"))
            if view.capabilities is None:
                view.capabilities = Capabilities.model_validate(await client.call("GET", "/v1/capabilities"))
            view.status = status
            view.last_seen = time.time()
            view.last_error = None
            view.failures = 0
        except AgentCallError as exc:
            view.failures += 1
            view.last_error = exc.body.message
            if exc.body.code == ErrorCode.UNAUTHORIZED or view.failures >= OFFLINE_AFTER_FAILURES:
                view.status = view.status.model_copy(update={
                    "state": MachineState.OFFLINE, "engine_url": None, "message": exc.body.message,
                    "updated_at": time.time(),
                })
        return view

    async def refresh_all(self) -> None:
        await asyncio.gather(*(self.refresh(n) for n in self.clients))

    async def start(self) -> None:
        # One loop per machine: a slow or unreachable agent must not delay status for the others.
        async def loop(name: str):
            while True:
                try:
                    await self.refresh(name)
                except Exception:
                    log.exception("status poll for %s failed", name)
                await asyncio.sleep(self.poll_seconds)

        self._tasks = [asyncio.create_task(loop(n), name=f"poll-{n}") for n in self.clients]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*(c.aclose() for c in self.clients.values()))

    def ready_routes(self) -> list[tuple[str, str, str]]:
        """(machine, model, engine_url) for every READY machine."""
        out = []
        for name, v in self.views.items():
            if v.status.state == MachineState.READY and v.status.engine_url:
                out.extend((name, m, v.status.engine_url) for m in v.status.served_models)
        return out
