# Architecture

## Components

```
LLM clients ──► gateway (Proxmox LXC) ──► engine on each machine (Strata / LM Studio / llama-server)
                   │  /v1  OpenAI-compatible routing
                   │  /api dashboard control API + static dashboard
                   ▼
              machine agent on each machine (pantheon-agent) ──► adapter ──► engine CLI / process
```

| Area | Path | Owns |
|---|---|---|
| Shared interfaces | `src/pantheon/contracts.py` (mirror: `dashboard/src/contracts.ts`) | States, configs, operations, errors. Changes are made here first. |
| Machine agent | `src/pantheon/agent/` | Engine lifecycle on one machine; runs one operation at a time |
| Adapters | `src/pantheon/agent/adapters/` | Translating operations to one engine's controls |
| Gateway | `src/pantheon/gateway/` | Machine registry and status polling, the dashboard API, and client request routing and tracking |
| Dashboard | `dashboard/` | UI only; it requests actions through the gateway |

**Ownership rule:** the dashboard and gateway *request* operations; only the agent starts or stops engine processes. Nothing else should touch engines on a managed machine.

## Decisions (2026-11-06)

- **The central service runs in a dedicated LXC** on the least-loaded Proxmox node.
- **The MBP uses an LM Studio adapter** (`lms load/unload/ps/ls`). It is not migrated to llama-server.
- **Multiple Strata versions are supported.** The Strata adapter detects the version (`strata --version`) and gates behavior on it. Currently required: 0.2.8 (build 0.1.54, Pythia) and 0.2.11 (build 0.1.57, Zeus).
- **Pythia and Zeus are busy with experiments.** Nothing touches their engines until the owner says so.
- **Stack:** Python 3.12+ with FastAPI and httpx for the agent and gateway; React, TypeScript, and Vite for the dashboard.

## Machine states

`offline` (set only by the gateway when an agent can't be reached) · `idle` · `loading` · `ready` · `draining` · `stopping` · `failed`

`engine_url` is only published while the machine is `ready`, so the gateway never routes requests to a model that isn't ready.

## Machine-service API (v1)

All endpoints except `/v1/health` require `Authorization: Bearer <agent token>`. Errors use the shape `{"error": {"code", "message", "detail"}}`.

| Method | Path | |
|---|---|---|
| GET | `/v1/health` | liveness, no auth |
| GET | `/v1/status` | `MachineStatus` |
| GET | `/v1/capabilities` | `Capabilities`, including detected engine version and feature flags |
| GET | `/v1/configs` | `ModelConfig[]` the machine can launch |
| POST | `/v1/operations` | `{kind: start\|stop\|switch, config_id}` → **202** `Operation`, returned immediately |
| GET | `/v1/operations[/{id}]` | progress (`status`, `progress` 0–1, `step`, `error`) |
| GET | `/v1/logs?lines=N` | merged agent and engine log lines |

Operation rules:
- Only one operation runs per machine. A second request gets **409 `busy`**. It isn't queued, so callers always know whether their request took effect.
- `start` while a model is already serving → 409; use `switch` instead.
- Loading never holds a web request open. Clients poll the operation instead.
- After a failure the agent re-checks the engine. If the engine is still serving (for example, after a failed stop), the state stays `ready` and the message describes the failure.
- The agent reconciles with the engine at startup and every 10 seconds. An engine started outside pantheon is adopted as `ready` with a note, and is taken over on the next stop or switch.

## Gateway

- **Status:** each agent is polled on its own loop, so one slow agent can't delay the others. Two consecutive failures, or any auth failure, mark the machine `offline`.
- **`/api/*`** passes requests through to agents (with an admin token), adds `last_seen`, `last_error`, and `active_requests`, and passes agent error codes and statuses back unchanged.
- **`/v1/models`** lists `machine/model` for every ready route, plus the bare model id when only one machine serves it.
- **`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`** accept `model` as `machine/model` or a unique bare id. An ambiguous id returns 409 with a list of options. A model that isn't served returns 503 `not_ready` with the available models.
- **Streaming:** SSE is relayed chunk by chunk. If the client disconnects, the engine connection is closed, which tells the engine to stop generating. `active_requests` is decremented in every case; Phase 4 draining depends on it.
- **Client keys** are optional (`client_keys`). If none are configured, the API is open on the LAN.

## Simulated machines

`SCENARIOS` in `agent/adapters/simulated.py` is the shared fixture set every component develops against. It mirrors the real inventory:
- **pythia** starts with an engine that pantheon doesn't own, and loads slowly.
- **mbp** runs an LM Studio-like engine with 4 parallel slots.
- **zeus** has one configuration (`gpt-oss-120b`) that fails with an out-of-memory error while loading.
- The dev stack adds an always-offline machine.

## Client configs

`GET /api/client-config` (gateway) and `GET /api/machines/{m}/client-config` (direct to the engine) return opencode and pi provider snippets generated from live status. See `gateway/clients.py` for the format notes.

## Status and remaining work

See `plan.md`.
