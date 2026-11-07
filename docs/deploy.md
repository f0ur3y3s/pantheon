# Deployment

Placeholders: `<gateway-host>` is the LXC running the gateway, and `<machine-ip>` is a model host's LAN address. The real values are in `local/deploy.md`, which is not committed.

## Gateway and dashboard (Proxmox LXC)

- **Container:** Debian 13, unprivileged, 2 cores, 2 GB RAM, start on boot, static address per the homelab IP plan.
- **Dashboard:** `http://<gateway-host>:9000`. **OpenAI-compatible API:** `http://<gateway-host>:9000/v1`.

| Path | Contents |
|---|---|
| `/opt/pantheon/app` | Code, `.venv` (system Python plus uv), `dashboard/dist`. Owned by the `pantheon` system user. |
| `/etc/pantheon/gateway.toml` | Machines (`http://<machine-ip>:9101`), `public_url`, `dashboard_dir`. See `config/gateway.example.toml`. |
| `/etc/pantheon/gateway.env` | `PANTHEON_ADMIN_TOKEN` (dashboard sign-in; delete it to turn sign-in off) and `PANTHEON_TOKEN_<MACHINE>` agent tokens. Mode 0640, root:pantheon. |
| `pantheon-gateway.service` | systemd unit, runs as `pantheon`, sandboxed (ProtectSystem=strict, ProtectHome) |

Update from a checkout:

```sh
cd dashboard && npm run build && cd ..
tar czf - --exclude=__pycache__ pyproject.toml uv.lock src dashboard/dist \
  | ssh root@<gateway-host> 'tar xzf - --no-same-owner -C /opt/pantheon/app \
      && chown -R pantheon:pantheon /opt/pantheon/app \
      && cd /opt/pantheon/app && runuser -u pantheon -- env HOME=/opt/pantheon uv sync -q --no-dev --python /usr/bin/python3 \
      && systemctl restart pantheon-gateway'
```

The client API has no keys unless `client_keys_env` is set in `[gateway]`.

## Machine agents

Each agent listens on `:9101` and needs the token from the matching `PANTHEON_TOKEN_<MACHINE>` line. Parameter edits are stored in `<state_dir>/params.json`, and launch profiles in `<state_dir>/profiles.json` (default `~/.local/state/pantheon`). Back up both files together.

- **macOS (LM Studio):** a per-user LaunchAgent `dev.pantheon.agent` runs a wrapper that loads `~/.config/pantheon/agent.env` and execs the agent. It uses RunAtLoad and KeepAlive. Build the venv on python.org Framework Python because of the application firewall.
- **Linux (Strata):** a systemd unit running as the user who owns the Strata install.
- **Windows (Strata):** a scheduled task at logon running `conhost.exe --headless cmd.exe /c run-agent.cmd`. The Strata server runs in its own hidden console, so restarting the agent never stops the model. Servers started by agents older than that fix share the agent's console: don't kill the agent's console host while one of them is serving. The headless console lets the agent send CTRL_BREAK, so the servers it launches can drain, and no window appears. Bind the agent to the LAN interface (`host = "<machine-ip>"`) and use a Python the firewall already allows on that network profile.

## Homepage widget

`GET /api/summary` (same auth as the rest of `/api`) returns display-ready status: counts (`serving`, `offline`, `busy`, `failed`, `machines_total`, `serving_text`, `active_requests`, `models`) and, per machine under `machines.<name>`, `state`, `model`, `summary`, `requests`, `memory_pct` (GPU memory when there is a GPU, else RAM), `vram_pct`, `ram_pct`, `gpu`, `engine`, `message`. `machine_list` holds the same records as an array for list widgets.

A [Homepage](https://gethomepage.dev) `customapi` card per machine:

```yaml
- AI:
    - Pantheon:
        icon: mdi-brain-#FAF9F6
        href: http://<gateway-host>:9000
        siteMonitor: http://<gateway-host>:9000/healthz
        widget:
          type: customapi
          url: http://<gateway-host>:9000/api/summary
          refreshInterval: 10000
          headers:
            Authorization: Bearer <PANTHEON_ADMIN_TOKEN>
          mappings:
            - { field: serving_text, label: Serving }
            - { field: active_requests, label: Requests, format: number }
            - { field: models, label: Models, format: number }
            - { field: offline, label: Offline, format: number }
    - Zeus:
        icon: si-nvidia-#76B900
        href: http://<gateway-host>:9000
        widget:
          type: customapi
          url: http://<gateway-host>:9000/api/summary
          refreshInterval: 10000
          headers:
            Authorization: Bearer <PANTHEON_ADMIN_TOKEN>
          mappings:
            - { field: { machines: { zeus: state } }, label: State }
            - { field: { machines: { zeus: model } }, label: Model }
            - { field: { machines: { zeus: memory_pct } }, label: VRAM, format: percent }
            - { field: { machines: { zeus: requests } }, label: Requests, format: number }
```

For a single card listing every machine, use `display: dynamic-list` with `mappings: { items: machine_list, name: name, label: summary }`.
