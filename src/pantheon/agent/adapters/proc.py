"""Subprocess helpers shared by CLI-driven adapters."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def clean(text: str) -> str:
    return ANSI.sub("", text)


@dataclass
class ProcResult:
    code: int
    lines: list[str]  # cleaned, non-empty lines/frames (\r and \n both split)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def last_error(self) -> str | None:
        for line in reversed(self.lines):
            if line.lower().startswith("error"):
                return line.split(":", 1)[1].strip() if ":" in line else line
        return None


async def run(cmd: list[str], *, timeout: float = 60.0,
              on_frame: Callable[[str], Awaitable[None]] | None = None) -> ProcResult:
    """Run `cmd`, merging stderr into stdout. `on_frame` sees each \\r- or \\n-terminated frame as it arrives."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
    )
    lines: list[str] = []
    buf = ""

    async def emit(frame: str):
        frame = clean(frame).strip()
        if frame:
            lines.append(frame)
            if on_frame:
                await on_frame(frame)

    async def pump():
        nonlocal buf
        assert proc.stdout
        while chunk := await proc.stdout.read(4096):
            buf += chunk.decode("utf-8", "replace")
            parts = re.split(r"[\r\n]", buf)
            buf = parts.pop()
            for p in parts:
                await emit(p)
        await emit(buf)

    try:
        await asyncio.wait_for(pump(), timeout)
        code = await asyncio.wait_for(proc.wait(), 10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    # Collapse consecutive duplicate progress frames for readable logs.
    deduped = [l for i, l in enumerate(lines) if i == 0 or l != lines[i - 1]]
    return ProcResult(code=code, lines=deduped)
