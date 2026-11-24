"""Several engines on one machine (e.g. Strata and PolyStrata on Pythia), one serving at a time.

Every engine's configurations are listed together, and each keeps its own `engine` kind. Starting a
configuration starts it on its engine; stop stops whichever engine is serving, so a switch between
configurations of different engines unloads one engine and loads the other (they share the GPU).
Config ids keep their engine's id; on a clash, the later engine's id is prefixed with `<engine name>/`.
"""

from __future__ import annotations

from pantheon.agent.adapters.base import EngineError, EngineProbe, ProgressFn
from pantheon.contracts import Capabilities, EngineKind, LogLine, ModelConfig, ParamSpec, Resources


class MultiAdapter:
    def __init__(self, engines: list[tuple[str, EngineKind, object]]):
        if not engines:
            raise ValueError("multi adapter needs at least one engine")
        self.engines = engines  # (name, kind, adapter)
        self.active_engine: EngineKind | None = None  # engine of the serving model, shown in status

    def _sub(self, config: ModelConfig):
        name = config.details.get("engine_name")
        for entry in self.engines:
            if entry[0] == name:
                return entry
        raise EngineError(f"config {config.id} names unknown engine {name!r}")

    @staticmethod
    def _native(config: ModelConfig) -> ModelConfig:
        return config.model_copy(update={"id": config.details.get("native_id", config.id)})

    async def capabilities(self) -> Capabilities:
        caps = [(n, await a.capabilities()) for n, _, a in self.engines]
        first = caps[0][1]
        return first.model_copy(update={
            "engine": first.engine,
            "engine_version": " + ".join(f"{c.engine.value} {c.engine_version}" for _, c in caps),
            "max_parallel": max(c.max_parallel or 1 for _, c in caps),
            "extra": {"engines": {n: c.model_dump(mode="json") for n, c in caps},
                      "supported_version": all(c.extra.get("supported_version", True) for _, c in caps)},
        })

    async def list_configs(self) -> list[ModelConfig]:
        out: list[ModelConfig] = []
        seen: set[str] = set()
        for name, _, a in self.engines:
            for c in await a.list_configs():
                cid = c.id if c.id not in seen else f"{name}/{c.id}"
                seen.add(cid)
                out.append(c.model_copy(update={"id": cid, "label": f"{name} · {c.label}", "details": {**c.details, "engine_name": name, "native_id": c.id}}))
        return out

    async def probe(self) -> EngineProbe:
        configs = await self.list_configs()
        serving: list[tuple[EngineKind, EngineProbe]] = []
        quiet: EngineProbe | None = None
        for name, kind, a in self.engines:
            p = await a.probe()
            if p.serving:
                # map the engine's native id back to the listed id
                p.config_id = next((c.id for c in configs if c.details["engine_name"] == name
                                    and c.details["native_id"] == p.config_id), p.config_id)
                serving.append((kind, p))
            elif p.message and quiet is None:
                quiet = p
        if not serving:
            self.active_engine = None
            return quiet or EngineProbe(serving=False)
        self.active_engine, probe = serving[0]
        if len(serving) > 1:
            others = ", ".join(k.value for k, _ in serving[1:])
            probe.message = f"{probe.message + '; ' if probe.message else ''}also serving on {others}"
        return probe

    async def start(self, config: ModelConfig, report: ProgressFn) -> EngineProbe:
        name, kind, a = self._sub(config)
        for n, _, other in self.engines:  # one engine at a time: they share the GPU
            if n != name and (await other.probe()).serving:
                raise EngineError(f"engine {n} is still serving; use switch")
        probe = await a.start(self._native(config), report)
        probe.config_id = config.id
        self.active_engine = kind
        return probe

    async def stop(self, report: ProgressFn) -> None:
        n = len(self.engines)
        for i, (_, _, a) in enumerate(self.engines):  # each treats "nothing running" as success

            async def sub_report(f: float, step: str, lo=i / n, hi=(i + 1) / n) -> None:
                await report(lo + (hi - lo) * f, step)

            await a.stop(sub_report)
        self.active_engine = None

    async def resources(self) -> Resources | None:
        return await self.engines[0][2].resources()

    async def engine_logs(self, lines: int) -> list[LogLine]:
        logs: list[LogLine] = []
        for _, _, a in self.engines:
            logs += await a.engine_logs(lines)
        return sorted(logs, key=lambda l: l.ts)[-lines:]

    def param_schema(self, config: ModelConfig) -> list[ParamSpec]:
        return self._sub(config)[2].param_schema(config)

    def base_params(self, config: ModelConfig) -> dict:
        return self._sub(config)[2].base_params(config)

    def apply_params(self, config: ModelConfig, values: dict) -> ModelConfig:
        return self._sub(config)[2].apply_params(config, values)
