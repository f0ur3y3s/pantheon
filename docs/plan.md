# Implementation plan

The original six-phase plan, with decisions and status recorded as they happen. Details of the interfaces are in `architecture.md`, and the machine findings are in `inventory.md`. Host-specific details (addresses, users, paths) live in the gitignored `local/` folder.

## Status (2026-11-06)

| Phase | Status |
|---|---|
| 1 Inventory and architecture | Done. Exception: no test completion was sent to Pythia or Zeus, because both are busy with experiments. |
| 2 Shared foundation | Done. The exit criterion was verified in a browser against the simulated machines. |
| 3 Real machine control | In progress. The LM Studio adapter works end to end on the real MBP. The Strata adapter is built and verified against real Strata control 0.1.54 and 0.1.57 using the mock engine on the MBP. The client-config section is done. Done for the MBP (LM Studio, launchd service) and Zeus (Strata 0.1.57, takeover plus graceful drain verified on Windows). Parameter editing is done (moved forward from Phase 4). Both agents run as startup services (launchd on the MBP, a logon scheduled task on Zeus). Waiting on: Pythia (busy). |
| 4 Unified serving and safe switching | Not started |
| 5 Reliability and daily use | Not started |
| 6 Pilot and handoff | Early start: the gateway and dashboard are deployed in a Proxmox LXC as a systemd service, with the MBP as the first live machine. See `deploy.md`. |

## Decisions

- **The MBP uses an LM Studio adapter**, not llama-server.
- **The gateway and dashboard run on Proxmox** in a dedicated LXC whose static address follows the homelab IP plan.
- **Dashboard sign-in** uses `PANTHEON_ADMIN_TOKEN`, from the environment or `.env` (default `token`). If it is unset or empty, no sign-in is required.
- **Pythia is off limits** until the owner frees it. Zeus was freed and taken over on 2026-11-06. No requests are sent there and no processes are touched.
- **Multiple Strata versions are supported** rather than upgraded. Both 0.1.54 and 0.1.57 have the same `serve.server` and `runtime` CLI surface.
- **The Strata adapter owns the server process directly.** It runs `python -m serve.server --engine strata --config <launch preset> --host --port --parallel --ready-file`, the same way the existing experiments launch it. The ready-file JSON (`api_state`, `model_state`, `pid`, `instance_id`) is the readiness signal. To stop, it sends SIGINT, which drains in-flight requests. In 0.1.54 the process does not exit after printing "stopped", so the adapter escalates to SIGTERM and then SIGKILL after a grace period. On Windows it uses CTRL_BREAK on a new process group. The adapter is tested against `--engine mock` for both versions on the MBP (`~/strata-test`).
- **Client snippet formats follow the installed clients, not the latest docs.** opencode v2.1.4 uses `provider` / `npm: @ai-sdk/openai-compatible` / `options.baseURL`; newer docs describe `providers` / `package` / `settings`, and the generator should switch when the installed version does. pi 1.5.0 uses `~/.pi/agent/models.json` with a `models` array and needs a dummy `apiKey` when the endpoint has no auth.

## Added scope

### A. Copy-paste provider configs for opencode and pi

**Phase 3: done.** A "Connect" section in the dashboard has opencode and pi tabs and copy buttons:
- **Per host:** each serving machine's card has a Connect panel for its engine directly, generated from live status (engine URL, served model id, and the context of the active config). It notes that direct requests bypass gateway draining.
- **Gateway:** a panel at the top of the page covers every ready model behind one address. An API key appears as an env-var reference (`{env:PANTHEON_API_KEY}` / `$PANTHEON_API_KEY`), never as a literal.
- **API:** `GET /api/client-config` and `GET /api/machines/{m}/client-config`.

**Phase 4.** The gateway snippet lists aliases (`coding`, `fast`, `large`) and becomes the recommended setup, since an alias survives model switches. Output limits and reasoning flags come from the editable parameters (B), instead of the current defaults: output = min(8192, context/4) and reasoning = true.

**Phase 6.** Check the generated snippets against the real opencode and pi installs (`pi --list-models`, `opencode --model ...`) as part of "tested configurations for the actual LLM clients".

### B. Edit model parameters from the dashboard

There are two kinds of parameter:
- **Launch parameters** (context, parallel slots, KV format, GPU offload, drafting) take effect only after a restart.
- **Request defaults** (temperature, top_p, top_k, max_tokens, reasoning budget) apply per request.

**Phase 3.** Each adapter declares a parameter schema for its configurations: name, type, range or options, and whether a change needs a restart. The dashboard shows the current values read-only. The schema is per engine *version*, which is how multi-version Strata support stays explicit.
- LM Studio: context, parallel, GPU offload, TTL, MTP drafting.
- Strata: the args in its launch preset JSON.

**Phase 4.** The parameters become editable.
- **Launch parameters:** `PUT /v1/configs/{id}` on the agent validates the change against the schema and writes it to an agent-owned copy of the config. The engine's own preset files are never edited in place. "Save" or "Save and apply" runs switch-when-idle on the affected machine, so editing a live model is safe.
- **Request defaults:** stored in the gateway per model and alias, and merged into requests that don't set those fields themselves. This works the same on every engine.
- Fits next to the routing editor (Agent 3) and switching support (Agent 1).

**Phase 5.** Parameter history with undo, export and import with the configuration backup, and a resource-conflict check before applying (for example, a larger context that would not fit in VRAM).

## Remaining Phase 3 work

1. ~~Strata adapter~~: done. It finds a foreign server by its listening port, re-attaches after an agent restart (pid plus start time), and escalates SIGINT → terminate → kill. On Windows it drains only servers it launched itself.
2. **Agent install units:**
   - launchd on the MBP. This needs approval; the agent currently runs under `nohup`.
   - systemd on Pythia and a scheduled task on Zeus, once they are freed.
3. ~~The macOS firewall and Framework Python~~: recorded in `deploy.md`.
4. ~~Deploy the gateway to the LXC~~: done (`deploy.md`).
5. **Parameter schema (B, Phase 3 part).**
6. **Real Pythia and Zeus**, once the owner frees them. This needs a one-time restart under the agent; tokens are already generated.
