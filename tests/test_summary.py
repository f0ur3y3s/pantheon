import httpx
import pytest

from test_gateway import ADMIN, Stack

pytestmark = pytest.mark.anyio


async def test_summary_is_flat_and_display_ready():
    async with Stack({"mbp": {}, "zeus": {}}) as st, httpx.AsyncClient(base_url=st.gateway_url, timeout=10) as c:
        assert (await c.get("/api/summary")).status_code == 401  # same auth as the rest of /api
        s = (await c.get("/api/summary", headers=ADMIN)).json()
        assert s["machines_total"] == 3 and s["serving"] == 1 and s["offline"] == 1
        assert s["serving_text"] == "1 / 3" and s["models"] == 1 and s["model_names"] == "qwen36"

        mbp, zeus, ghost = s["machines"]["mbp"], s["machines"]["zeus"], s["machines"]["ghost"]
        assert mbp["state"] == "Ready" and mbp["model"] == "qwen36" and mbp["summary"] == "Ready · qwen36"
        assert 0 < mbp["memory_pct"] <= 100 and mbp["engine"] == "simulated sim-1"
        assert zeus["state"] == "Idle" and zeus["model"] == "—" and zeus["summary"] == "Idle"
        assert ghost["state"] == "Offline" and ghost["memory_pct"] is None and ghost["gpu"] == "—"
        assert [m["name"] for m in s["machine_list"]] == ["mbp", "zeus", "ghost"]
