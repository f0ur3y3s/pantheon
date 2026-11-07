"""Machine-service HTTP API (v1). See docs/architecture.md for the contract."""

from __future__ import annotations

import asyncio
import contextlib
import hmac

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse

from pantheon.agent.core import AgentError, MachineAgent
from pantheon.contracts import (
    Capabilities,
    ConfigParams,
    ErrorBody,
    ErrorCode,
    LogLine,
    MachineStatus,
    ModelConfig,
    Operation,
    OperationRequest,
    ParamUpdate,
)

RECONCILE_SECONDS = 10.0


def build_agent_app(agent: MachineAgent, token: str, extra_routers: list[APIRouter] | None = None) -> FastAPI:
    if not token:
        raise ValueError("agent token must not be empty")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await agent.reconcile()

        async def loop():
            while True:
                await asyncio.sleep(RECONCILE_SECONDS)
                await agent.reconcile()

        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            task.cancel()
            await agent.shutdown()

    app = FastAPI(title=f"pantheon agent: {agent.name}", lifespan=lifespan)

    @app.exception_handler(AgentError)
    async def _agent_error(_: Request, exc: AgentError):
        return JSONResponse({"error": exc.body.model_dump()}, status_code=exc.status)

    def auth(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise AgentError(ErrorCode.UNAUTHORIZED, "missing or invalid agent token", 401)

    @app.get("/v1/health")
    async def health():
        return {"ok": True, "machine": agent.name}

    api = APIRouter(prefix="/v1", dependencies=[Depends(auth)])

    @api.get("/status", response_model=MachineStatus)
    async def status():
        return await agent.status()

    @api.get("/capabilities", response_model=Capabilities)
    async def capabilities():
        return await agent.adapter.capabilities()

    @api.get("/configs", response_model=list[ModelConfig])
    async def configs():
        return await agent.configs()

    @api.get("/configs/{config_id:path}/params", response_model=ConfigParams, response_model_by_alias=True)
    async def get_params(config_id: str):
        return await agent.get_params(config_id)

    @api.put("/configs/{config_id:path}/params", response_model=ConfigParams, response_model_by_alias=True)
    async def put_params(config_id: str, update: ParamUpdate):
        return await agent.set_params(config_id, update)

    @api.post("/operations", response_model=Operation, status_code=202)
    async def submit(req: OperationRequest):
        return await agent.submit(req)

    @api.get("/operations", response_model=list[Operation])
    async def operations(limit: int = 20):
        return agent.list_operations(limit)

    @api.get("/operations/{op_id}", response_model=Operation)
    async def operation(op_id: str):
        return agent.get_operation(op_id)

    @api.get("/logs", response_model=list[LogLine])
    async def logs(lines: int = 200):
        return await agent.logs(min(max(lines, 1), 2000))

    app.include_router(api)
    for r in extra_routers or []:
        app.include_router(r)
    return app


__all__ = ["build_agent_app", "ErrorBody"]
