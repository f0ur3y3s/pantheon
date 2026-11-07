"""LM Studio adapter, driven through the `lms` CLI.

Verified against the MBP install (lms version 69d945a, LM Studio headless `llmster`):
- `lms ls --json` / `lms ps --json` / `lms server status --json` give machine-readable inventory.
- `lms load <key> --identifier ID -c CTX --parallel N -y` streams "Loading <key> NN%" frames separated by \\r,
  exits 0 on success and 1 with "Error: ..." on failure (e.g. resource guardrails).
- `lms unload <identifier>` unloads one instance. LM Studio does not drain in-flight requests on unload,
  so draining relies on the gateway (native_drain=False).

Policy: one LLM serves at a time (unified memory); embedding models are left alone.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from pantheon.agent.adapters.base import EngineError, EngineProbe, ProgressFn
from pantheon.agent.adapters.proc import run
from pantheon.contracts import Capabilities, EngineKind, LogLine, ModelConfig, ParamSpec, ParamType, Resources

PCT = re.compile(r"(\d{1,3})%")


@dataclass
class LMProfile:
    id: str
    model_key: str
    identifier: str
    label: str | None = None
    context: int | None = None
    parallel: int | None = None


@dataclass
class LMStudioSettings:
    engine_url: str  # OpenAI base as clients reach it, e.g. http://192.0.2.12:8080/v1
    lms_command: list[str] = field(default_factory=lambda: [os.path.expanduser("~/.lmstudio/bin/lms")])
    profiles: list[LMProfile] = field(default_factory=list)
    include_unprofiled: bool = True  # also offer every LLM from `lms ls` with LM Studio defaults
    readiness: str = "chat"  # "chat" (1-token completion) | "models" | "none"
    load_timeout: float = 900.0
    readiness_timeout: float = 180.0


class LMStudioAdapter:
    def __init__(self, settings: LMStudioSettings):
        self.s = settings
        self._log: deque[LogLine] = deque(maxlen=1000)
        self._version: str | None = None

    # ---- lms plumbing ------------------------------------------------------

    def _note(self, text: str) -> None:
        self._log.append(LogLine(ts=time.time(), source="engine", text=text))

    async def _lms_json(self, *args: str):
        res = await run([*self.s.lms_command, *args, "--json"], timeout=30)
        if res.code != 0:
            raise EngineError(f"lms {' '.join(args)} failed: {res.last_error() or res.text[-300:]}")
        try:
            return json.loads(res.text)
        except json.JSONDecodeError as exc:
            raise EngineError(f"lms {' '.join(args)} --json returned unparseable output; this lms version may be unsupported") from exc

    async def _lms(self, *args: str, timeout: float = 60, on_frame=None):
        res = await run([*self.s.lms_command, *args], timeout=timeout, on_frame=on_frame)
        for line in res.lines[-20:]:
            if not PCT.search(line) or "100%" in line:
                self._note(f"lms {args[0]}: {line}")
        return res

    async def _loaded_llms(self) -> list[dict]:
        return [m for m in await self._lms_json("ps") if m.get("type") == "llm"]

    def _match(self, inst: dict, configs: list[ModelConfig]) -> ModelConfig | None:
        for c in configs:
            if c.details.get("model_key") == inst.get("modelKey") and c.model == inst.get("identifier"):
                return c
        return None

    # ---- Adapter protocol ------------------------------------------------

    async def capabilities(self) -> Capabilities:
        if self._version is None:
            try:
                self._version = (await self._lms_json("version")).get("version", "unknown")
            except EngineError:
                self._version = "unknown"
        return Capabilities(engine=EngineKind.LMSTUDIO, engine_version=self._version, native_drain=False,
                            reports_slots=False, reports_memory=True, embeddings=True)

    async def list_configs(self) -> list[ModelConfig]:
        catalog = await self._lms_json("ls")
        max_ctx = {m["modelKey"]: m.get("maxContextLength") for m in catalog}
        configs = [
            ModelConfig(id=p.id, label=p.label or p.id, engine=EngineKind.LMSTUDIO, model=p.identifier,
                        context=p.context, parallel=p.parallel,
                        details={"model_key": p.model_key, "max_context": max_ctx.get(p.model_key)})
            for p in self.s.profiles
        ]
        if self.s.include_unprofiled:
            taken = {c.id for c in configs} | {p.model_key for p in self.s.profiles}
            for m in catalog:
                if m.get("type") != "llm" or m["modelKey"] in taken:
                    continue
                configs.append(ModelConfig(
                    id=m["modelKey"], label=f"{m.get('displayName', m['modelKey'])} · defaults",
                    engine=EngineKind.LMSTUDIO, model=m["modelKey"],
                    est_memory_mib=int(m.get("sizeBytes", 0) / 2**20) or None,
                    details={"model_key": m["modelKey"], "max_context": m.get("maxContextLength")},
                ))
        return configs

    async def probe(self) -> EngineProbe:
        server = await self._lms_json("server", "status")
        if not server.get("running"):
            return EngineProbe(serving=False, message="LM Studio server is not running")
        loaded = await self._loaded_llms()
        if not loaded:
            return EngineProbe(serving=False)
        configs = await self.list_configs()
        matched = next((c for inst in loaded if (c := self._match(inst, configs))), None)
        msg = None
        if len(loaded) > 1:
            msg = f"{len(loaded)} LLMs loaded; stop/switch unloads all of them"
        elif matched is None:
            msg = f"loaded with settings that match no saved config ({loaded[0].get('identifier')})"
        return EngineProbe(
            serving=True,
            config_id=matched.id if matched else None,
            served_models=[m["identifier"] for m in loaded],
            engine_url=self.s.engine_url,
            owned=True,  # LM Studio is shared state; anything loaded is ours to manage
            message=msg,
        )

    async def start(self, config: ModelConfig, report: ProgressFn) -> EngineProbe:
        await report(0.0, "checking LM Studio server")
        server = await self._lms_json("server", "status")
        if not server.get("running"):
            port = urlparse(self.s.engine_url).port or 1234
            await report(0.02, f"starting LM Studio server on :{port}")
            res = await self._lms("server", "start", "--port", str(port), timeout=60)
            if res.code != 0:
                raise EngineError(f"could not start LM Studio server: {res.last_error() or res.text[-300:]}")

        already = [m["identifier"] for m in await self._loaded_llms()]
        if already:
            raise EngineError(f"another model is loaded ({', '.join(already)}); use switch")

        args = ["load", config.details["model_key"], "--identifier", config.model, "-y"]
        if config.context:
            args += ["-c", str(config.context)]
        if config.parallel:
            args += ["--parallel", str(config.parallel)]
        p = config.details.get("params", {})
        if p.get("gpu") and p["gpu"] != "auto":
            args += ["--gpu", p["gpu"]]
        if p.get("ttl"):
            args += ["--ttl", str(p["ttl"])]
        if p.get("mtp") == "on":
            args += ["--speculative-draft-mtp"]
        elif p.get("mtp") == "off":
            args += ["--no-speculative-draft-mtp"]

        last = -1

        async def on_frame(frame: str):
            nonlocal last
            if (m := PCT.search(frame)) and frame.startswith("Loading"):
                pct = int(m.group(1))
                if pct != last:
                    last = pct
                    await report(0.05 + 0.85 * pct / 100, "loading weights")

        await report(0.05, "loading weights")
        res = await self._lms(*args, timeout=self.s.load_timeout, on_frame=on_frame)
        if res.code != 0:
            raise EngineError(res.last_error() or f"lms load exited with {res.code}", detail={"output": res.lines[-5:]})

        await report(0.92, "readiness check")
        await self._readiness(config.model)
        probe = await self.probe()
        if config.model not in probe.served_models:
            raise EngineError(f"{config.model} not listed as loaded after a successful load")
        return EngineProbe(serving=True, config_id=config.id, served_models=probe.served_models,
                           engine_url=self.s.engine_url, owned=True, message=probe.message)

    async def _readiness(self, identifier: str) -> None:
        if self.s.readiness == "none":
            return
        deadline = time.monotonic() + self.s.readiness_timeout
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=self.s.readiness_timeout)) as http:
            last_err = "no response"
            while time.monotonic() < deadline:
                try:
                    ids = [m["id"] for m in (await http.get(f"{self.s.engine_url}/models")).json().get("data", [])]
                    if identifier in ids:
                        if self.s.readiness == "models":
                            return
                        r = await http.post(f"{self.s.engine_url}/chat/completions", json={
                            "model": identifier, "max_tokens": 1,
                            "messages": [{"role": "user", "content": "ping"}]})
                        if r.status_code == 200:
                            self._note(f"readiness: {identifier} answered a 1-token request")
                            return
                        last_err = f"HTTP {r.status_code}: {r.text[:200]}"
                    else:
                        last_err = f"{identifier} not in /models yet"
                except (httpx.HTTPError, ValueError) as exc:
                    last_err = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(1.0)
        raise EngineError(f"model loaded but failed readiness check: {last_err}")

    async def stop(self, report: ProgressFn) -> None:
        loaded = await self._loaded_llms()
        for i, inst in enumerate(loaded):
            ident = inst["identifier"]
            await report(i / max(len(loaded), 1), f"unloading {ident}")
            res = await self._lms("unload", ident, timeout=120)
            if res.code != 0:
                raise EngineError(f"unload {ident} failed: {res.last_error() or res.text[-300:]}")
        remaining = await self._loaded_llms()
        if remaining:
            raise EngineError(f"still loaded after unload: {[m['identifier'] for m in remaining]}")
        await report(1.0, "unloaded")

    async def resources(self) -> Resources | None:
        return await mac_resources()

    # ---- editable parameters (flags verified in `lms load --help`, lms 69d945a) ----------

    def param_schema(self, config: ModelConfig) -> list[ParamSpec]:
        return [
            ParamSpec(key="context", label="Context length", type=ParamType.INT, min=512,
                      max=config.details.get("max_context") or 1_048_576,
                      help="-c / --context-length. Empty = LM Studio default."),
            ParamSpec(key="parallel", label="Parallel requests", type=ParamType.INT, min=1, max=32,
                      help="--parallel. More slots start faster under load; each is slower."),
            ParamSpec(key="gpu", label="GPU offload", type=ParamType.ENUM,
                      options=["auto", "max", "off", "0.25", "0.5", "0.75"], help="--gpu. auto = LM Studio decides."),
            ParamSpec(key="ttl", label="Idle unload (seconds)", type=ParamType.INT, min=0, max=7 * 86400,
                      help="--ttl. 0 = never unload."),
            ParamSpec(key="mtp", label="MTP speculative decoding", type=ParamType.ENUM, options=["auto", "on", "off"],
                      help="--speculative-draft-mtp, when the model supports it."),
        ]

    def base_params(self, config: ModelConfig) -> dict:
        return {"context": config.context, "parallel": config.parallel, "gpu": "auto", "ttl": 0, "mtp": "auto"}

    def apply_params(self, config: ModelConfig, values: dict) -> ModelConfig:
        return config.model_copy(update={
            "context": values.get("context"), "parallel": values.get("parallel"),
            "details": {**config.details, "params": {k: values.get(k) for k in ("gpu", "ttl", "mtp")}},
        })

    async def engine_logs(self, lines: int) -> list[LogLine]:
        return list(self._log)[-lines:]


_mac_cache: tuple[float, Resources | None] = (0.0, None)


async def mac_resources() -> Resources | None:
    """Unified-memory usage on macOS (sysctl + vm_stat), cached for 5s. None elsewhere."""
    global _mac_cache
    if _mac_cache[0] > time.monotonic() - 5:
        return _mac_cache[1]
    try:
        total = int((await run(["sysctl", "-n", "hw.memsize"], timeout=5)).text)
        chip = (await run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5)).text or None
        vm = (await run(["vm_stat"], timeout=5)).text
    except (OSError, ValueError, asyncio.TimeoutError):
        _mac_cache = (time.monotonic(), None)
        return None
    page = int(m.group(1)) if (m := re.search(r"page size of (\d+)", vm)) else 16384

    def pages(name: str) -> int:
        m = re.search(rf"{name}:\s+(\d+)", vm)
        return int(m.group(1)) if m else 0

    used = (pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")) * page
    mib = 2**20
    res = Resources(gpu_name=chip, vram_total_mib=None, vram_used_mib=None,
                    ram_total_mib=total // mib, ram_used_mib=used // mib)
    _mac_cache = (time.monotonic(), res)
    return res
