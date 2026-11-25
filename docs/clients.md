# Using pantheon from a coding agent

How to point a coding agent (omp, pi, opencode) at pantheon so long coding sessions stay stable. `<gateway-host>` is the gateway LXC (see `deploy.md`); real addresses are in the gitignored `local/`.

## The rule: connect through the gateway

Every client talks to `http://<gateway-host>:9000/v1` with model ids of the form `machine/model` (for example `pythia/inkling-2-mini-ud-q4_k_xl`). Do not point a client at an engine's own port (`<machine-ip>:8080`, `:8081`, LM Studio's `:1234`). Through the gateway a client gets:

- **Stream keep-alives.** Strata and PolyStrata send a tool call only once all of its arguments are written. At about 33 tokens/s a large file write is several minutes of silence, and clients abort a silent stream ("stream stalled while waiting for the next event"), then retry the same write forever. The gateway sends an empty chunk every 10 s (`stream_heartbeat`) while the engine is quiet.
- **Request tracking and draining.** Requests count toward **Gateway in flight** and each machine's request count, and a switch waits for them.
- **Stable addresses.** An engine's port can change (Pythia moved from Strata on :8080 to PolyStrata on :8081); the gateway follows it, a direct config breaks.

The one exception is Palimnesis, below.

## 1. Get the provider config

In the dashboard, open **Connect a client** (every serving machine) or a machine's **Connect** tab (that machine's models only; also through the gateway), pick the client, and copy the snippet. Both snippets use the provider id `pantheon`, so they merge into one block.

| Client | File | Format |
|---|---|---|
| omp | `~/.omp/agent/models.yml` (or a profile's `agent/models.yml`) | YAML |
| pi | `~/.pi/agent/models.json` | JSON |
| opencode | `~/.config/opencode/opencode.json` | JSON |

The snippet carries each model's context (`contextWindow`) and an output limit (`maxTokens`): a quarter of the context, up to 32,768. Thinking models need that room; with 8,192 a long answer is cut off.

For omp, the result looks like this (one entry per serving model):

```yaml
providers:
  pantheon:
    baseUrl: http://<gateway-host>:9000/v1
    api: openai-completions
    apiKey: none
    models:
    - id: pythia/inkling-2-mini-ud-q4_k_xl
      name: inkling-2-mini-ud-q4_k_xl (pythia)
      reasoning: true
      input:
      - text
      contextWindow: 131072
      maxTokens: 32768
```

Remove older providers that reach an engine directly. Back up the file first.

## 2. Assign models to roles

A Strata or PolyStrata machine usually serves **one request at a time** (`parallel = 1`); a second request waits until the first finishes, which can be minutes. Give the main coding session that machine, and send everything else to a machine with several slots.

omp, in `~/.omp/agent/config.yml`:

```yaml
modelRoles:
  default: pantheon/pythia/inkling-2-mini-ud-q4_k_xl   # the coding session
  smol: pantheon/mbp/qwen36                            # titles, summaries, quick tasks; 4 slots on mbp
compaction:
  enabled: true
```

- Run one coding session per single-slot machine.
- Keep `compaction.enabled: true` unless Palimnesis manages the context (its `setup` turns compaction off; switch it back on when you stop using it), or a long session overflows the context.
- The machine behind each role must be serving. An idle machine answers `503 not_ready`, which a client may retry silently: start the model in the dashboard first.

## 3. Ask for files in pieces

Add this to the agent's global instructions (omp and pi read `AGENTS.md` from their agent directory, e.g. `~/.omp/agent/AGENTS.md`; or put it in a project's `AGENTS.md`):

```markdown
# Writing files

The local models write about 30-40 tokens a second, and a tool call is sent only once all of its arguments are
written, so a large write is minutes with no visible progress.

- Keep each file write under about 300 lines.
- Build a larger file in steps: write a working skeleton first, then add or change sections with edit calls.
- Do not rewrite a whole file to change part of it; edit the part.
```

Keep-alives stop the client from giving up, but a 10,000-token write is still about 5 minutes before anything appears. Smaller writes show progress and lose less when one is cancelled.

## 4. Check it

```sh
omp -p --no-session "Reply with exactly: ok"                         # default role
omp -p --no-session --model pantheon/mbp/qwen36 "Reply with exactly: ok"
omp -p --no-session "What is the maximum number of lines for a single file write? Answer with just the number."
```

The first two answer `ok` (a model's first request can take a minute while it reads the agent's system prompt), the third answers `300`. While a request runs, **Gateway in flight** on the dashboard goes up; if it stays at 0, the client is not going through the gateway.

## Machine settings that matter for coding

- **Pythia:** PolyStrata `inkling-ud-q4_k_xl-128k-fp8` (128K context, `--kv fp8`). fp8 halves the attention cache (16 KB a token instead of 32 KB), which leaves VRAM to the expert cache as a conversation grows. Larger contexts fit but every long conversation gets slower.
- **mbp:** `qwen36` with 4 slots. A saved parameter edit overrides a configuration's defaults: check **Parameters** if a model starts with fewer slots than its label says.
- Start, stop and switch models from the dashboard. A server started by hand in the background ignores the drain signal; pantheon terminates it on stop instead of draining it.

## Palimnesis (direct to the engine)

[Palimnesis](https://github.com/f0ur3y3s/palimnesis) is a local proxy that bounds what the model sees in very long sessions. It needs a Strata engine's own origin, not an OpenAI `/v1` URL, so it is the one setup that bypasses the gateway: no keep-alives, no request tracking, and a switch to another model or engine needs a proxy restart with the new address.

A Strata or PolyStrata machine's **Connect → palimnesis** tab gives the commands for what it is serving now, with an input budget of the context minus the output limit:

```sh
uv run python -m palimnesis serve --upstream http://<machine-ip>:<engine-port> --input-budget 98304
uv run palimnesis setup omp --model <served model>
uv run palimnesis check --upstream http://<machine-ip>:<engine-port>
```

Run `check` before relying on it: Palimnesis was built against Strata 0.1.48 and has not been verified on PolyStrata.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `stream stalled while waiting for the next event`, the same write retried | The client talks to the engine directly, so it gets no keep-alives during a long tool call | Use the gateway snippet; confirm with **Gateway in flight** |
| A long answer stops at 8,192 tokens, or the agent compacts mid-answer | An old client config with `maxTokens: 8192` | Copy the snippet again (32,768 at a 128K context) |
| The client hangs; the gateway log shows `503` | The model's machine is idle | Start the model in the dashboard |
| A request waits minutes before its first token | Another request holds the machine's only slot | One session per single-slot machine; move background roles elsewhere |
| A stop sits at "draining in-flight requests" | The server ignores SIGINT (started by hand with `&`) | Current pantheon terminates such a server at once |
