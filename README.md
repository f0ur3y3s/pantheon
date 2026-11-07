# pantheon

A central dashboard and a single OpenAI-compatible endpoint for the homelab LLM machines (Pythia, MBP, Zeus).

- `docs/inventory.md`: what is installed on each machine (host-specific details are kept in the gitignored `local/`)
- `docs/deploy.md`: how the gateway and agents are deployed
- `docs/architecture.md`: components, decisions, and the machine-service API

## Develop

```sh
uv sync
uv run pytest                      # agent + gateway tests (real sockets, simulated engines)
uv run python -m pantheon.dev      # 3 simulated agents + 1 offline machine + gateway on :9000
cd dashboard && npm install && npm run build   # gateway serves dashboard/dist at http://127.0.0.1:9000
# or live-reload UI: npm run dev  → http://localhost:5173 (proxies /api and /v1 to :9000)
```

Sign-in: the dashboard token is `PANTHEON_ADMIN_TOKEN`, read from the environment or `.env` (copy `.env.example`; default `token`). If it is unset or empty, no sign-in is required.

## Run a single component

```sh
uv run pantheon-agent   --config agent.toml
uv run pantheon-gateway --config gateway.toml
```

See `config/*.example.toml`. Secrets should be supplied through `*_env` keys rather than written into the file.
