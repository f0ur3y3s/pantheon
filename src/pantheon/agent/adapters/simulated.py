"""Simulated engine for development and tests.

Behaves like a real adapter (slow loads, failures, resource numbers) and also serves a fake
OpenAI-compatible engine under /sim/v1 so the gateway can be exercised end to end.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from pantheon.agent.adapters.base import EngineError, EngineProbe, ProgressFn
from pantheon.contracts import Capabilities, EngineKind, LogLine, ModelConfig, ParamSpec, ParamType, Resources


@dataclass
class SimSettings:
    configs: list[ModelConfig]
    load_seconds: float = 4.0
    stop_seconds: float = 1.0
    fail_configs: set[str] = field(default_factory=set)  # these configs fail partway through loading
    initial_config: str | None = None  # serving at agent start (simulates an engine started elsewhere)
    initial_owned: bool = False
    tokens_per_second: float = 25.0
    max_parallel: int = 1
    vram_total_mib: int = 12288
    ram_total_mib: int = 46080
    gpu_name: str = "Simulated GPU"
    public_url: str = "http://127.0.0.1:9101"  # where this agent is reachable; engine_url derives from it


class SimulatedAdapter:
    def __init__(self, settings: SimSettings):
        self.s = settings
        self._serving: ModelConfig | None = None
        self._owned = settings.initial_owned
        if settings.initial_config:
            self._serving = self._config(settings.initial_config)
        self._log: deque[LogLine] = deque(maxlen=1000)
        self.active_requests = 0

    def _config(self, config_id: str) -> ModelConfig:
        for c in self.s.configs:
            if c.id == config_id:
                return c
        raise KeyError(config_id)

    def _engine_log(self, text: str) -> None:
        self._log.append(LogLine(ts=time.time(), source="engine", text=text))

    def _probe_now(self) -> EngineProbe:
        if self._serving is None:
            return EngineProbe(serving=False)
        return EngineProbe(
            serving=True,
            config_id=self._serving.id,
            served_models=[self._serving.model],
            engine_url=f"{self.s.public_url}/sim/v1",
            owned=self._owned,
        )

    # ---- Adapter protocol ---------------------------------------------

    async def capabilities(self) -> Capabilities:
        return Capabilities(
            engine=EngineKind.SIMULATED,
            engine_version="sim-1",
            native_drain=True,
            reports_slots=True,
            reports_memory=True,
            max_parallel=self.s.max_parallel,
        )

    async def list_configs(self) -> list[ModelConfig]:
        return list(self.s.configs)

    async def probe(self) -> EngineProbe:
        return self._probe_now()

    async def start(self, config: ModelConfig, report: ProgressFn) -> EngineProbe:
        steps = 10
        self._engine_log(f"loading {config.model} (ctx={config.context})")
        for i in range(1, steps + 1):
            await asyncio.sleep(self.s.load_seconds / steps)
            if config.id in self.s.fail_configs and i == 6:
                self._engine_log("CUDA error: out of memory while allocating KV cache")
                raise EngineError(
                    "engine exited while loading: out of memory allocating KV cache",
                    detail={"exit_code": 1, "at": "kv-cache"},
                )
            step = "loading weights" if i < 8 else "warming up" if i < steps else "readiness check"
            await report(i / steps, step)
        self._serving = config
        self._owned = True
        self._engine_log(f"ready: {config.model}")
        return self._probe_now()

    async def stop(self, report: ProgressFn) -> None:
        # native drain: wait for in-flight fake requests, bounded
        deadline = time.monotonic() + 30
        while self.active_requests and time.monotonic() < deadline:
            await report(0.1, f"draining {self.active_requests} request(s)")
            await asyncio.sleep(0.2)
        await report(0.5, "stopping engine")
        await asyncio.sleep(self.s.stop_seconds)
        self._engine_log(f"stopped {self._serving.model if self._serving else 'engine'}")
        self._serving = None
        await report(1.0, "stopped")

    async def resources(self) -> Resources:
        used = (self._serving.est_memory_mib or 0) if self._serving else 0
        return Resources(
            gpu_name=self.s.gpu_name,
            vram_total_mib=self.s.vram_total_mib,
            vram_used_mib=min(used + 300, self.s.vram_total_mib),
            ram_total_mib=self.s.ram_total_mib,
            ram_used_mib=min(max(used - self.s.vram_total_mib, 0) + 4000, self.s.ram_total_mib),
        )

    async def engine_logs(self, lines: int) -> list[LogLine]:
        return list(self._log)[-lines:]

    # ---- editable parameters ---------------------------------------------

    def param_schema(self, config: ModelConfig) -> list[ParamSpec]:
        return [
            ParamSpec(key="context", label="Context length", type=ParamType.INT, min=2048, max=262144),
            ParamSpec(key="parallel", label="Parallel slots", type=ParamType.INT, min=1, max=8),
        ]

    def base_params(self, config: ModelConfig) -> dict:
        return {"context": config.context, "parallel": config.parallel}

    def apply_params(self, config: ModelConfig, values: dict) -> ModelConfig:
        return config.model_copy(update={"context": values.get("context"), "parallel": values.get("parallel")})

    # ---- fake OpenAI engine ---------------------------------------------

    def engine_router(self) -> APIRouter:
        router = APIRouter(prefix="/sim/v1")

        def unavailable():
            return JSONResponse({"error": {"message": "model not loaded", "type": "unavailable"}}, status_code=503)

        @router.get("/models")
        async def models():
            if self._serving is None:
                return {"object": "list", "data": []}
            return {"object": "list", "data": [{"id": self._serving.model, "object": "model", "owned_by": "sim"}]}

        @router.post("/chat/completions")
        async def chat(request: Request):
            if self._serving is None:
                return unavailable()
            body = await request.json()
            model = self._serving.model
            max_tokens = int(body.get("max_tokens") or 16)
            words = [f"tok{i}" for i in range(max_tokens)]
            cid = f"chatcmpl-{uuid.uuid4().hex[:10]}"
            delay = 1.0 / self.s.tokens_per_second

            if not body.get("stream"):
                self.active_requests += 1
                try:
                    await asyncio.sleep(delay * max_tokens)
                finally:
                    self.active_requests -= 1
                return {
                    "id": cid,
                    "object": "chat.completion",
                    "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": " ".join(words)}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": max_tokens, "total_tokens": max_tokens + 1},
                }

            async def gen():
                self.active_requests += 1
                try:
                    for w in words:
                        await asyncio.sleep(delay)
                        chunk = {"id": cid, "object": "chat.completion.chunk", "model": model,
                                 "choices": [{"index": 0, "delta": {"content": w + " "}, "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk)}\n\n"
                    done = {"id": cid, "object": "chat.completion.chunk", "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}
                    yield f"data: {json.dumps(done)}\n\n"
                    yield "data: [DONE]\n\n"
                finally:
                    self.active_requests -= 1

            return StreamingResponse(gen(), media_type="text/event-stream")

        return router


# ---- shared scenarios ------------------------------------------------------
# Every component develops against these. They mirror the real inventory (docs/phase1-inventory.md)
# and cover: slow loading, a failing configuration, a pre-existing unowned engine, fast parallel serving.

def _strata(id_: str, ctx: int, mem: int, label: str) -> ModelConfig:
    return ModelConfig(id=id_, label=label, engine=EngineKind.STRATA, model="qwen3.8-flash-next-iq3_xxs",
                       context=ctx, parallel=1, est_memory_mib=mem)


SCENARIOS: dict[str, SimSettings] = {
    "pythia": SimSettings(
        configs=[
            _strata("iq3_xxs-32k", 32768, 52000, "Qwen3.8 Flash Next IQ3_XXS · 32k"),
            _strata("iq3_xxs-64k", 65536, 56000, "Qwen3.8 Flash Next IQ3_XXS · 64k"),
        ],
        load_seconds=8.0,
        initial_config="iq3_xxs-32k",
        initial_owned=False,
        gpu_name="NVIDIA GeForce RTX 3060",
        vram_total_mib=12288,
        ram_total_mib=46080,
    ),
    "mbp": SimSettings(
        configs=[
            ModelConfig(id="qwen/qwen3.6-35b-a3b", label="Qwen3.6 35B A3B", engine=EngineKind.LMSTUDIO,
                        model="qwen36", context=131072, parallel=4, est_memory_mib=20900),
            ModelConfig(id="qwen3.8-27b", label="Qwen3.8 27B", engine=EngineKind.LMSTUDIO,
                        model="qwen3.8-27b", context=32768, parallel=4, est_memory_mib=16500),
            ModelConfig(id="prism-ml/bonsai-27b", label="Bonsai 27B", engine=EngineKind.LMSTUDIO,
                        model="prism-ml/bonsai-27b", context=32768, parallel=4, est_memory_mib=8700),
        ],
        load_seconds=3.0,
        initial_config="qwen/qwen3.6-35b-a3b",
        initial_owned=True,
        max_parallel=4,
        tokens_per_second=40.0,
        gpu_name="Apple M2 Pro",
        vram_total_mib=32768,
        ram_total_mib=32768,
    ),
    "zeus": SimSettings(
        configs=[
            _strata("iq3_xxs-128k", 131072, 72000, "Qwen3.8 Flash Next IQ3_XXS · 128k"),
            _strata("ud-iq2_xs-128k", 131072, 70000, "Qwen3.8 Flash Next UD-IQ2_XS · 128k"),
            ModelConfig(id="gpt-oss-120b", label="gpt-oss 120B MXFP4", engine=EngineKind.LLAMA_CPP,
                        model="gpt-oss-120b", context=65536, parallel=1, est_memory_mib=62000),
        ],
        load_seconds=10.0,
        fail_configs={"gpt-oss-120b"},
        gpu_name="NVIDIA GeForce RTX 4070 Ti SUPER",
        vram_total_mib=16376,
        ram_total_mib=64600,
    ),
}
