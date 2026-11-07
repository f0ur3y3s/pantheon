"""Host resource reporting: NVIDIA GPUs via nvidia-smi, unified memory on macOS, RAM via psutil."""

from __future__ import annotations

import asyncio
import shutil
import sys
import time

import psutil

from pantheon.agent.adapters.proc import run
from pantheon.contracts import Resources

_cache: tuple[float, Resources | None] = (0.0, None)


async def local_resources(max_age: float = 5.0) -> Resources | None:
    global _cache
    if _cache[0] > time.monotonic() - max_age:
        return _cache[1]
    res = await _collect()
    _cache = (time.monotonic(), res)
    return res


async def _collect() -> Resources | None:
    if sys.platform == "darwin":
        from pantheon.agent.adapters.lmstudio import mac_resources
        return await mac_resources()
    vm = psutil.virtual_memory()
    mib = 2**20
    res = Resources(ram_total_mib=vm.total // mib, ram_used_mib=(vm.total - vm.available) // mib)
    if shutil.which("nvidia-smi"):
        try:
            out = await run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"], timeout=5)
            first = out.lines[0].split(",") if out.code == 0 and out.lines else []
            if len(first) == 3:
                res.gpu_name = first[0].strip()
                res.vram_total_mib = int(first[1])
                res.vram_used_mib = int(first[2])
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
    return res
