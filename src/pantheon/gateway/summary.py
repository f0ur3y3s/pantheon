"""Display-ready status summary (GET /api/summary), shaped for widgets such as Homepage's customapi.

Values are plain numbers and short strings so a widget can map fields without formatting logic:
percentages are 0-100, missing values are "—" for text and null for numbers.
"""

from __future__ import annotations

from pantheon.contracts import MachineState
from pantheon.gateway.machines import MachineRegistry

BUSY = {MachineState.LOADING, MachineState.STOPPING, MachineState.DRAINING}
DASH = "—"


def _pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return round(100 * used / total, 1)


def build_summary(registry: MachineRegistry) -> dict:
    machines: dict[str, dict] = {}
    models: set[str] = set()
    counts = {"serving": 0, "offline": 0, "busy": 0, "failed": 0}
    active_requests = 0

    for name, view in registry.views.items():
        st = view.status
        r = st.resources
        ready = st.state == MachineState.READY
        if ready:
            counts["serving"] += 1
            models.update(st.served_models)
        elif st.state == MachineState.OFFLINE:
            counts["offline"] += 1
        elif st.state == MachineState.FAILED:
            counts["failed"] += 1
        elif st.state in BUSY:
            counts["busy"] += 1
        active_requests += view.active_requests

        vram = _pct(r.vram_used_mib, r.vram_total_mib) if r else None
        ram = _pct(r.ram_used_mib, r.ram_total_mib) if r else None
        caps = view.capabilities
        model = ", ".join(st.served_models) if ready and st.served_models else DASH
        state = st.state.value.capitalize()
        machines[name] = {
            "state": state,
            "model": model,
            "summary": f"{state} · {model}" if ready else state,
            "requests": view.active_requests,
            "vram_pct": vram,
            "ram_pct": ram,
            "memory_pct": vram if vram is not None else ram,  # GPU memory where there is one, else RAM
            "gpu": (r.gpu_name if r and r.gpu_name else DASH),
            "engine": f"{caps.engine.value} {caps.engine_version}" if caps else DASH,
            "message": st.message or "",
        }

    total = len(machines)
    return {
        **counts,
        "machines_total": total,
        "serving_text": f"{counts['serving']} / {total}",
        "active_requests": active_requests,
        "models": len(models),
        "model_names": ", ".join(sorted(models)) or DASH,
        "machines": machines,
        "machine_list": [{"name": n, **m} for n, m in machines.items()],
    }
