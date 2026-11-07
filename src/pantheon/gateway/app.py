"""Gateway: dashboard control API (/api) and the OpenAI-compatible client API (/v1)."""

from __future__ import annotations

import contextlib
import json
import hmac
import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pantheon.contracts import CreateProfile, ErrorBody, ErrorCode, OperationRequest, ParamUpdate
from pantheon.gateway.clients import ClientModel, client_configs
from pantheon.gateway.config import GatewayConfig
from pantheon.gateway.machines import AgentCallError, MachineRegistry

log = logging.getLogger("pantheon.gateway")

# Hop-by-hop / recomputed headers that must not be copied between client and engine.
_DROP_HEADERS = {"host", "content-length", "connection", "keep-alive", "transfer-encoding",
                 "authorization", "accept-encoding", "content-encoding"}


def _err(status: int, code: ErrorCode, message: str, **detail) -> JSONResponse:
    return JSONResponse({"error": ErrorBody(code=code, message=message, detail=detail).model_dump()}, status_code=status)


def build_gateway_app(cfg: GatewayConfig, registry: MachineRegistry | None = None,
                      engine_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    registry = registry or MachineRegistry(cfg.machines, cfg.poll_seconds)
    engine_http = httpx.AsyncClient(
        timeout=httpx.Timeout(connect=5.0, read=cfg.upstream_read_timeout, write=30.0, pool=5.0),
        transport=engine_transport,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        await registry.start()
        try:
            yield
        finally:
            await registry.stop()
            await engine_http.aclose()

    app = FastAPI(title="pantheon gateway", lifespan=lifespan)
    app.state.registry = registry

    @app.exception_handler(AgentCallError)
    async def _agent_err(_: Request, exc: AgentCallError):
        return JSONResponse({"error": exc.body.model_dump()}, status_code=exc.status)

    # ---- auth -------------------------------------------------------------

    if not cfg.admin_token:
        log.warning("PANTHEON_ADMIN_TOKEN is not set: the dashboard and control API do not require sign-in")

    def admin_auth(authorization: str | None = Header(default=None)) -> None:
        if not cfg.admin_token:
            return
        if authorization is None or not hmac.compare_digest(authorization, f"Bearer {cfg.admin_token}"):
            raise AgentCallError(401, ErrorBody(code=ErrorCode.UNAUTHORIZED, message="invalid admin token"))

    @app.get("/api/auth")
    async def auth_info():
        """Unauthenticated: tells the dashboard whether to show the sign-in screen."""
        return {"required": bool(cfg.admin_token)}

    def client_auth(authorization: str | None = Header(default=None)) -> None:
        if not cfg.client_keys:
            return
        presented = (authorization or "").removeprefix("Bearer ").strip()
        if not any(hmac.compare_digest(presented, k) for k in cfg.client_keys):
            raise AgentCallError(401, ErrorBody(code=ErrorCode.UNAUTHORIZED, message="invalid API key"))

    # ---- dashboard API ---------------------------------------------------------

    api = APIRouter(prefix="/api", dependencies=[Depends(admin_auth)])

    @api.get("/machines")
    async def machines():
        return [v.public() for v in registry.views.values()]

    @api.get("/machines/{name}")
    async def machine(name: str):
        return (await registry.refresh(name)).public()

    @api.get("/machines/{name}/configs")
    async def configs(name: str):
        client, _ = registry.get(name)
        return await client.call("GET", "/v1/configs")

    @api.post("/machines/{name}/configs", status_code=201)
    async def create_profile(name: str, request: CreateProfile):
        client, _ = registry.get(name)
        return await client.call("POST", "/v1/configs", json=request.model_dump(mode="json"))

    @api.get("/machines/{name}/configs/{config_id:path}/params")
    async def get_params(name: str, config_id: str):
        client, _ = registry.get(name)
        return await client.call("GET", f"/v1/configs/{config_id}/params")

    @api.put("/machines/{name}/configs/{config_id:path}/params")
    async def put_params(name: str, config_id: str, update: ParamUpdate):
        client, _ = registry.get(name)
        return await client.call("PUT", f"/v1/configs/{config_id}/params", json=update.model_dump(mode="json"))

    @api.post("/machines/{name}/operations", status_code=202)
    async def submit(name: str, req: OperationRequest):
        client, _ = registry.get(name)
        op = await client.call("POST", "/v1/operations", json=req.model_dump(mode="json"))
        await registry.refresh(name)
        return op

    @api.get("/machines/{name}/operations")
    async def operations(name: str, limit: int = 20):
        client, _ = registry.get(name)
        return await client.call("GET", "/v1/operations", params={"limit": limit})

    @api.get("/machines/{name}/operations/{op_id}")
    async def operation(name: str, op_id: str):
        client, _ = registry.get(name)
        return await client.call("GET", f"/v1/operations/{op_id}")

    @api.get("/machines/{name}/logs")
    async def logs(name: str, lines: int = 200):
        client, _ = registry.get(name)
        return await client.call("GET", "/v1/logs", params={"lines": lines})

    @api.get("/machines/{name}/client-config")
    async def machine_client_config(name: str):
        """Provider snippets that talk to this machine's engine directly (bypasses gateway draining)."""
        client, view = registry.get(name)
        st = view.status
        if st.state.value != "ready" or not st.engine_url:
            raise AgentCallError(409, ErrorBody(code=ErrorCode.NOT_READY,
                                                message=f"{name} is {st.state.value}; client configs need a serving model"))
        configs = {c["id"]: c for c in await client.call("GET", "/v1/configs")}
        active = configs.get(st.active_config or "", {})
        models = [ClientModel(id=m, name=f"{active.get('label') or m} ({name})", context=active.get("context"))
                  for m in st.served_models]
        out = client_configs(f"{name}", f"{name} (direct)", st.engine_url, models, api_key_env=None)
        out["direct"] = True
        return out

    @api.get("/client-config")
    async def gateway_client_config(request: Request):
        """Provider snippets for the gateway: one stable address for every machine."""
        base = cfg.public_url or str(request.base_url).rstrip("/")
        contexts: dict[str, int | None] = {}
        for name, v in registry.views.items():
            if v.status.state.value == "ready" and v.status.active_config:
                try:
                    cs = {c["id"]: c for c in await registry.clients[name].call("GET", "/v1/configs")}
                    contexts[name] = cs.get(v.status.active_config, {}).get("context")
                except AgentCallError:
                    pass
        routes = registry.ready_routes()
        counts: dict[str, int] = {}
        for _, n, _ in routes:
            counts[n] = counts.get(n, 0) + 1
        models = []
        for m, n, _ in routes:
            mid = n if counts[n] == 1 else f"{m}/{n}"
            models.append(ClientModel(id=mid, name=f"{n} ({m})", context=contexts.get(m)))
        out = client_configs("pantheon", "Pantheon gateway", f"{base}/v1", models,
                             api_key_env="PANTHEON_API_KEY" if cfg.client_keys else None)
        out["direct"] = False
        return out

    app.include_router(api)

    # ---- OpenAI-compatible client API -----------------------------------------

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(client_auth)])

    def resolve(model: str) -> tuple[str, str, str] | JSONResponse:
        routes = registry.ready_routes()
        if "/" in model:
            machine, _, bare = model.partition("/")
            for r in routes:
                if r[0] == machine and r[1] == bare:
                    return r
        matches = [r for r in routes if r[1] == model]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return _err(409, ErrorCode.INVALID, f"model {model!r} is served by several machines; use machine/model",
                        options=[f"{m}/{n}" for m, n, _ in matches])
        return _err(503, ErrorCode.NOT_READY, f"no ready machine is serving {model!r}",
                    available=sorted({f"{m}/{n}" for m, n, _ in routes}))

    @v1.get("/models")
    async def list_models():
        routes = registry.ready_routes()
        counts: dict[str, int] = {}
        for _, n, _ in routes:
            counts[n] = counts.get(n, 0) + 1
        data = []
        for m, n, _ in routes:
            data.append({"id": f"{m}/{n}", "object": "model", "owned_by": m})
            if counts[n] == 1:
                data.append({"id": n, "object": "model", "owned_by": m})
        return {"object": "list", "data": data}

    async def forward(request: Request, path: str):
        body = await request.body()
        try:
            payload = await request.json()
            model = payload.get("model")
        except Exception:
            return _err(400, ErrorCode.INVALID, "request body must be JSON with a 'model' field")
        if not isinstance(model, str) or not model:
            return _err(400, ErrorCode.INVALID, "missing 'model'")
        route = resolve(model)
        if isinstance(route, JSONResponse):
            return route
        machine, bare, engine_url = route
        if bare != model:  # rewrite machine/model to what the engine knows
            payload["model"] = bare
            body = json.dumps(payload).encode()

        _, view = registry.get(machine)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
        headers["content-type"] = "application/json"
        upstream = engine_http.build_request("POST", f"{engine_url}/{path}", content=body, headers=headers)

        view.active_requests += 1
        try:
            resp = await engine_http.send(upstream, stream=True)
        except httpx.TimeoutException:
            view.active_requests -= 1
            return _err(504, ErrorCode.TIMEOUT, f"{machine}: engine did not respond in time")
        except httpx.HTTPError as exc:
            view.active_requests -= 1
            return _err(502, ErrorCode.UNREACHABLE, f"{machine}: engine unreachable ({type(exc).__name__})")

        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _DROP_HEADERS}
        out_headers["x-pantheon-machine"] = machine

        async def relay():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            except httpx.HTTPError as exc:
                # Mid-stream engine failure: the client already has partial output; we can only end the stream.
                log.warning("%s: upstream stream broke: %s", machine, exc)
            finally:
                # Runs on normal completion *and* on client disconnect (generator closed),
                # which closes the engine connection so the engine can stop generating.
                await resp.aclose()
                view.active_requests -= 1

        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            return StreamingResponse(relay(), status_code=resp.status_code, headers=out_headers,
                                     media_type="text/event-stream")
        try:
            content = await resp.aread()
        finally:
            await resp.aclose()
            view.active_requests -= 1
        return Response(content, status_code=resp.status_code, headers=out_headers,
                        media_type=resp.headers.get("content-type"))

    @v1.post("/chat/completions")
    async def chat(request: Request):
        return await forward(request, "chat/completions")

    @v1.post("/completions")
    async def completions(request: Request):
        return await forward(request, "completions")

    @v1.post("/embeddings")
    async def embeddings(request: Request):
        return await forward(request, "embeddings")

    app.include_router(v1)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    if cfg.dashboard_dir and Path(cfg.dashboard_dir).is_dir():
        app.mount("/", StaticFiles(directory=cfg.dashboard_dir, html=True), name="dashboard")

    return app
