"""Strata adapter: the agent owns the Strata server process directly.

Launch (same shape the existing experiments use, verified on control 0.1.54 and 0.1.57):
    <python> -m serve.server --engine strata --config <launch preset .json>
             --host H --port P --parallel N --no-api-key --ready-file <file>
The ready file (JSON: api_state, model_state, pid, instance_id, ...) is the readiness signal.

Stop: SIGINT (POSIX) / CTRL_BREAK (Windows, process started in its own group) asks Strata to drain
in-flight requests and stop the engine. Control 0.1.54 prints "stopped" but the process does not exit,
so after the API goes away we escalate: terminate, then kill, including child engine processes.

Supported control versions are listed in SUPPORTED_VERSIONS; anything else starts with a warning.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psutil

from pantheon.agent.adapters.base import EngineError, EngineProbe, ProgressFn
from pantheon.agent.adapters.resources import local_resources
from pantheon.contracts import Capabilities, EngineKind, LogLine, ModelConfig, ParamSpec, ParamType, Resources

SUPPORTED_VERSIONS = {"0.1.54", "0.1.57"}

# Numeric engine flags that may be edited. A flag is offered only when the launch preset already uses it,
# so the editor never introduces an argument the installed engine version might not accept.
# flag: (key, label, type, min, max, help)
EDITABLE_ARGS: dict[str, tuple[str, str, ParamType, float, float, str]] = {
    "--max-context": ("context", "Context length", ParamType.INT, 2048, 1_048_576, "Maximum context in tokens."),
    "--spec": ("spec", "Speculative draft tokens", ParamType.INT, 0, 16, "MTP draft length; 0 disables."),
    "--spec-min-p": ("spec_min_p", "Draft min probability", ParamType.FLOAT, 0, 1, "Keep drafting while p >= this."),
    "--kv-resident": ("kv_resident", "Resident KV tokens", ParamType.INT, 0, 1_048_576, "KV kept in VRAM."),
    "--pcie-frac": ("pcie_frac", "PCIe fraction", ParamType.FLOAT, 0, 1, "Share of PCIe bandwidth for streaming."),
    "--vram-reserve-mib": ("vram_reserve_mib", "VRAM reserve (MiB)", ParamType.INT, 0, 65536, "Left free for other use."),
    "--conversation-cache-mib": ("conversation_cache_mib", "Conversation cache (MiB)", ParamType.INT, 0, 262144, ""),
    "--conversation-cache-slots": ("conversation_cache_slots", "Conversation cache slots", ParamType.INT, 0, 64, ""),
}
IS_WINDOWS = sys.platform == "win32"


@dataclass
class StrataSettings:
    control_dir: str  # Strata control install: contains serve/, VERSION, and usually python/
    engine_url: str  # OpenAI base as clients reach it, e.g. http://192.0.2.11:8080/v1
    state_dir: str  # agent-owned: ready file, pid file, server log
    python: str | None = None  # defaults to the control install's bundled python
    server_command: list[str] | None = None  # overrides [python, -m, serve.server]
    preset_dirs: list[str] = field(default_factory=list)  # launch preset JSON dirs (Strata's launch/)
    preset_files: list[str] = field(default_factory=list)
    engine: str = "strata"  # "mock" for testing without a GPU
    mock_configs: list[dict] = field(default_factory=list)  # engine=mock: [{id, label, model, context}]
    extra_args: list[str] = field(default_factory=list)
    bind_host: str = "0.0.0.0"
    port: int = 8080
    parallel: int = 1
    expected_load_seconds: float = 180.0  # for the progress estimate only
    load_timeout: float = 1800.0
    drain_timeout: float = 300.0  # max wait for in-flight requests after the stop signal
    exit_grace: float = 5.0  # after the API is gone, before terminate (0.1.54 never exits on its own)
    readiness: str = "chat"  # "chat" | "health" | "none"


class StrataAdapter:
    def __init__(self, settings: StrataSettings):
        self.s = settings
        self.state = Path(settings.state_dir).expanduser()
        self.state.mkdir(parents=True, exist_ok=True)
        self.ready_file = self.state / "ready.json"
        self.pid_file = self.state / "server.json"  # {"pid", "create_time", "config_id"}
        self.server_log = self.state / "server.log"
        self.control = Path(settings.control_dir).expanduser()
        self._proc: psutil.Process | None = None
        self._config_id: str | None = None
        self._log: deque[LogLine] = deque(maxlen=500)
        self._adopt_pid_file()

    # ---- helpers ---------------------------------------------------------

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.s.port}"

    def _note(self, text: str) -> None:
        self._log.append(LogLine(ts=time.time(), source="agent", text=text))

    def version(self) -> str:
        try:
            return (self.control / "VERSION").read_text().strip()
        except OSError:
            return "unknown"

    def _python(self) -> str:
        if self.s.python:
            return os.path.expanduser(self.s.python)
        for cand in ("python/bin/python3.12", "python/bin/python3", "python/python.exe", ".venv/Scripts/python.exe", ".venv/bin/python"):
            if (self.control / cand).exists():
                return str(self.control / cand)
        raise EngineError(f"no Python found in {self.control}; set adapter.python")

    def _effective_preset(self, config: ModelConfig) -> str:
        """The preset to launch: the original, or an agent-owned copy with edited args."""
        overrides = config.details.get("arg_overrides")
        if not overrides:
            return config.details["preset_path"]
        preset = json.loads(Path(config.details["preset_path"]).read_text(encoding="utf-8"))
        args = list(preset["args"])
        for flag, value in overrides.items():
            if flag in args:
                args[args.index(flag) + 1] = str(value)
        preset["args"] = args
        out = self.state / "presets" / f"{config.id}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(preset, indent=1), encoding="utf-8")
        self._note(f"launching edited copy of {config.details['preset_path']}: {overrides}")
        return str(out)

    def _command(self, config: ModelConfig) -> list[str]:
        base = [os.path.expanduser(c) for c in self.s.server_command] if self.s.server_command else [self._python(), "-m", "serve.server"]
        cmd = [*base, "--engine", self.s.engine]
        if self.s.engine == "strata":
            cmd += ["--config", self._effective_preset(config)]
        cmd += ["--host", self.s.bind_host, "--port", str(self.s.port), "--parallel", str(config.parallel or self.s.parallel),
                "--no-api-key", "--ready-file", str(self.ready_file), *self.s.extra_args, *config.details.get("extra_args", [])]
        return cmd

    def _alive(self) -> bool:
        try:
            return self._proc is not None and self._proc.is_running() and self._proc.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def _adopt_pid_file(self) -> None:
        """After an agent restart, re-attach to a server we started earlier (pid + create_time must match)."""
        try:
            info = json.loads(self.pid_file.read_text())
            p = psutil.Process(info["pid"])
            if abs(p.create_time() - info["create_time"]) < 1:  # pid + start time: not a recycled pid
                self._proc, self._config_id = p, info.get("config_id")
                self._note(f"re-attached to strata server pid {p.pid} ({self._config_id})")
                return
        except (OSError, ValueError, KeyError, psutil.Error):
            pass
        self.pid_file.unlink(missing_ok=True)

    def _find_foreign_server(self) -> psutil.Process | None:
        """The process listening on our port (e.g. a manual experiment). Callers confirm it is Strata via /health."""
        try:
            for c in psutil.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == self.s.port and c.pid:
                    return psutil.Process(c.pid)
        except (psutil.AccessDenied, PermissionError):
            pass  # macOS without root: fall back to scanning our own processes
        except psutil.Error:
            return None
        for p in psutil.process_iter(["pid", "name"]):
            try:
                if any(c.status == psutil.CONN_LISTEN and c.laddr.port == self.s.port for c in p.net_connections(kind="tcp")):
                    return p
            except (psutil.Error, OSError):
                continue
        return None

    async def _health(self, timeout: float = 3.0) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                r = await http.get(f"{self.local_url}/health")
                if r.status_code == 200:
                    body = r.json()
                    return body if body.get("service") == "strata" else None
        except (httpx.HTTPError, ValueError):
            return None
        return None

    async def _served_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as http:
                return [m["id"] for m in (await http.get(f"{self.local_url}/v1/models")).json().get("data", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return []

    def _log_tail(self, n: int = 5) -> list[str]:
        try:
            lines = self.server_log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [l for l in lines if l.strip()][-n:]

    # ---- Adapter protocol ------------------------------------------------

    async def capabilities(self) -> Capabilities:
        v = self.version()
        return Capabilities(engine=EngineKind.STRATA, engine_version=v, native_drain=True, reports_slots=True,
                            reports_memory=True, max_parallel=self.s.parallel,
                            extra={"supported_version": v in SUPPORTED_VERSIONS, "engine_mode": self.s.engine})

    async def list_configs(self) -> list[ModelConfig]:
        if self.s.engine == "mock":
            return [ModelConfig(id=c["id"], label=c.get("label", c["id"]), engine=EngineKind.STRATA,
                                model=c.get("model", "qwen3.8-flash-next"), context=c.get("context"),
                                parallel=c.get("parallel"), details={"extra_args": c.get("extra_args", [])})
                    for c in self.s.mock_configs]
        files = [Path(f).expanduser() for f in self.s.preset_files]
        for d in self.s.preset_dirs:
            files += sorted(Path(d).expanduser().glob("*.json"))
        configs = []
        for f in files:
            if f.name.endswith((".choices.json", ".tune.json")):
                continue
            try:
                preset = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if "args" not in preset:
                continue
            args = preset["args"]
            ctx = int(args[args.index("--max-context") + 1]) if "--max-context" in args else None
            configs.append(ModelConfig(
                id=f.stem, label=f"{preset.get('model_name', f.stem)} · {f.stem}", engine=EngineKind.STRATA,
                model=preset.get("model_name", f.stem), context=ctx, parallel=self.s.parallel,
                details={"preset_path": str(f), "args": args},
            ))
        return configs

    async def probe(self) -> EngineProbe:
        health = await self._health()
        if self._alive():
            if health and health.get("loaded"):
                return EngineProbe(serving=True, config_id=self._config_id, served_models=await self._served_models(),
                                   engine_url=self.s.engine_url, owned=True)
            return EngineProbe(serving=False, message="strata server process running but API not ready")
        if health and health.get("loaded"):
            models = await self._served_models()
            configs = await self.list_configs()
            match = next((c.id for c in configs if c.model in models and c.context == health.get("max_context")), None)
            return EngineProbe(serving=True, config_id=match, served_models=models, engine_url=self.s.engine_url,
                               owned=False, message="strata was started outside pantheon; stop/switch will take it over")
        return EngineProbe(serving=False)

    async def start(self, config: ModelConfig, report: ProgressFn) -> EngineProbe:
        if (v := self.version()) not in SUPPORTED_VERSIONS:
            self._note(f"warning: strata control {v} is not a verified version ({sorted(SUPPORTED_VERSIONS)})")
        if await self._health(timeout=1.5):
            raise EngineError(f"port {self.s.port} already has a strata server; stop it first")
        self.ready_file.unlink(missing_ok=True)
        cmd = self._command(config)
        self._note("launch: " + " ".join(cmd))
        await report(0.0, "starting strata server")

        log = open(self.server_log, "ab")
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start {config.id}\n".encode())
        log.flush()
        kwargs: dict = {"cwd": str(self.control), "stdout": log, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if IS_WINDOWS:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True  # survives agent restarts; re-attached via the pid file
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        try:
            popen = subprocess.Popen(cmd, env=env, **kwargs)
        except OSError as exc:
            raise EngineError(f"could not launch strata server: {exc}") from exc
        finally:
            log.close()
        self._proc = psutil.Process(popen.pid)
        self._popen = popen  # keep a handle so the child is reaped
        self._config_id = config.id
        self.pid_file.write_text(json.dumps({"pid": popen.pid, "create_time": self._proc.create_time(), "config_id": config.id}))

        started = time.monotonic()
        while True:
            elapsed = time.monotonic() - started
            if not self._alive():
                tail = self._log_tail(6)
                self.pid_file.unlink(missing_ok=True)
                raise EngineError(f"strata server exited while loading: {tail[-1] if tail else 'no output'}",
                                  detail={"log_tail": tail})
            ready = self._read_ready()
            if ready.get("api_state") == "ready" and ready.get("model_state") == "ready":
                break
            if elapsed > self.s.load_timeout:
                await self._terminate_tree()
                raise EngineError(f"strata did not become ready within {int(self.s.load_timeout)}s")
            tail = self._log_tail(1)
            step = (tail[0][:80] if tail else "loading") if elapsed > 2 else "starting strata server"
            await report(min(0.9, elapsed / self.s.expected_load_seconds * 0.9), step)
            await asyncio.sleep(1.0)

        await report(0.93, "readiness check")
        await self._readiness(config.model)
        return EngineProbe(serving=True, config_id=config.id, served_models=await self._served_models(),
                           engine_url=self.s.engine_url, owned=True)

    def _read_ready(self) -> dict:
        try:
            return json.loads(self.ready_file.read_text())
        except (OSError, ValueError):
            return {}

    async def _readiness(self, model: str) -> None:
        if self.s.readiness == "none":
            return
        health = await self._health(timeout=10)
        if not health or not health.get("loaded"):
            raise EngineError(f"ready file says ready but /health reports {health}")
        if self.s.readiness == "health":
            return
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=180)) as http:
            r = await http.post(f"{self.local_url}/v1/chat/completions", json={
                "model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]})
        if r.status_code != 200:
            raise EngineError(f"readiness request failed: HTTP {r.status_code}: {r.text[:200]}")
        self._note(f"readiness: {model} answered a 1-token request")

    async def stop(self, report: ProgressFn) -> None:
        proc = self._proc if self._alive() else self._find_foreign_server()
        if proc is None:
            if await self._health(timeout=1.5):
                raise EngineError(f"a strata server answers on port {self.s.port} but its process could not be found")
            self._clear()
            return
        self._proc = proc
        try:
            children = proc.children(recursive=True)
        except psutil.Error:
            children = []
        await report(0.05, f"draining (pid {proc.pid})")
        self._note(f"stop: signalling strata server pid {proc.pid} to drain")
        self._send_stop_signal(proc)

        deadline = time.monotonic() + self.s.drain_timeout
        api_gone_at: float | None = None
        while time.monotonic() < deadline:
            if not self._alive():
                break
            if await self._health(timeout=1.0) is None:
                api_gone_at = api_gone_at or time.monotonic()
                await report(0.6, "engine stopped; waiting for server exit")
                if time.monotonic() - api_gone_at > self.s.exit_grace:
                    self._note("server did not exit after stopping; terminating (known 0.1.54 behaviour)")
                    break
            else:
                await report(0.2, "draining in-flight requests")
            await asyncio.sleep(0.5)
        else:
            self._note(f"drain did not finish within {int(self.s.drain_timeout)}s; terminating")

        await self._terminate_tree(children)
        await report(1.0, "stopped")
        self._clear()

    def _send_stop_signal(self, proc: psutil.Process) -> None:
        try:
            if IS_WINDOWS:
                # CTRL_BREAK targets a console *process group*. Sending it to a pid that is not a group leader
                # can hit every process on our console (including the agent), so only signal servers we
                # launched with CREATE_NEW_PROCESS_GROUP in this agent process. Anything else is terminated
                # without a drain; the gateway drains its own traffic before asking for a stop.
                popen = getattr(self, "_popen", None)
                if popen is not None and popen.pid == proc.pid and popen.poll() is None:
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    self._note(f"pid {proc.pid} was not started by this agent; Windows cannot drain it, terminating")
                    proc.terminate()
            else:
                proc.send_signal(signal.SIGINT)
        except (OSError, psutil.Error) as exc:
            self._note(f"stop signal failed ({exc}); will terminate")

    async def _terminate_tree(self, children: list[psutil.Process] | None = None) -> None:
        procs = [p for p in [self._proc, *(children or [])] if p is not None]
        for p in procs:
            try:
                p.terminate()
            except psutil.Error:
                pass
        _, alive = await asyncio.to_thread(psutil.wait_procs, procs, 10)
        for p in alive:
            try:
                p.kill()
            except psutil.Error:
                pass
        if alive:
            await asyncio.to_thread(psutil.wait_procs, alive, 5)
        if popen := getattr(self, "_popen", None):
            popen.poll()

    def _clear(self) -> None:
        self._proc = None
        self._config_id = None
        self.pid_file.unlink(missing_ok=True)
        self.ready_file.unlink(missing_ok=True)

    async def resources(self) -> Resources | None:
        return await local_resources()

    # ---- editable parameters ---------------------------------------------

    def _preset_flags(self, config: ModelConfig) -> dict[str, str]:
        args = config.details.get("args", [])
        return {a: args[i + 1] for i, a in enumerate(args[:-1]) if a in EDITABLE_ARGS}

    def param_schema(self, config: ModelConfig) -> list[ParamSpec]:
        specs = [ParamSpec(key="parallel", label="Parallel slots", type=ParamType.INT, min=1, max=16,
                           help="serve.server --parallel: simultaneous engine slots.")]
        for flag in self._preset_flags(config):
            key, label, typ, lo, hi, help_ = EDITABLE_ARGS[flag]
            specs.append(ParamSpec(key=key, label=label, type=typ, min=lo, max=hi, help=f"{flag}. {help_}".strip()))
        return specs

    def base_params(self, config: ModelConfig) -> dict:
        values: dict = {"parallel": config.parallel or self.s.parallel}
        for flag, raw in self._preset_flags(config).items():
            key, _, typ, *_ = EDITABLE_ARGS[flag]
            try:
                values[key] = int(raw) if typ == ParamType.INT else float(raw)
            except ValueError:
                values[key] = None
        return values

    def apply_params(self, config: ModelConfig, values: dict) -> ModelConfig:
        base = self.base_params(config)
        by_key = {v[0]: flag for flag, v in EDITABLE_ARGS.items()}
        arg_overrides = {by_key[k]: v for k, v in values.items() if k in by_key and v != base.get(k) and v is not None}
        return config.model_copy(update={
            "parallel": values.get("parallel") or config.parallel,
            "context": values.get("context", config.context),
            "details": {**config.details, "arg_overrides": arg_overrides},
        })

    async def engine_logs(self, lines: int) -> list[LogLine]:
        try:
            mtime = self.server_log.stat().st_mtime
        except OSError:
            mtime = time.time()
        engine = [LogLine(ts=mtime, source="engine", text=l) for l in self._log_tail(lines)]
        return [*list(self._log)[-lines:], *engine][-lines:]
