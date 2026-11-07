# Machine inventory (Phase 1)

Captured 2026-11-06 through read-only checks. Host-specific details (addresses, users, paths) are in `local/phase1-inventory.md`, which is not committed.

| | Pythia | MBP | Zeus |
|---|---|---|---|
| OS | Ubuntu 26.04 | macOS 27 (Apple M2 Pro) | Windows 11 |
| Memory | 45 GiB RAM | 32 GB unified | 63 GB RAM |
| GPU | RTX 3060 12 GB | M2 Pro (Metal) | RTX 4070 Ti SUPER 16 GB |
| Engine | Strata, control 0.1.54 | LM Studio, headless (`lms` 69d945a) | Strata, control 0.1.57, plus a source-tree build |
| Typical model | Qwen3.8 Flash Next IQ3_XXS (about 70 GB across 2 shards), 32k, 1 slot | qwen3.6-35b-a3b, 128k, 4 slots | Qwen3.8 Flash Next IQ3_XXS, 128k, 1 slot |
| Other engines | llama-server (build 12004) | llama-server 0.6.0 (Homebrew) | Docker's bundled llama-server; Ollama data |

## Findings that shaped the design

1. **The MBP runs LM Studio, not Strata or llama.cpp**, so it has its own adapter (`lms` CLI).
2. **Experiments launch Strata by hand** (`serve.server --config <preset> --ready-file …`), outside Strata's own `runtime` owner. The agent therefore owns that server process directly and can take over one it didn't start.
3. **Strata versions differ between machines.** Both supported control versions have the same `serve.server` and `runtime` CLI.
4. **Strata runs one slot (`--parallel 1`).** One long generation blocks everything else, so gateway queueing matters.
5. **The engines have no API keys** and listen on all interfaces.
6. **Zeus has several network interfaces** (VMware, WSL, Hyper-V), so its agent binds the LAN interface explicitly.
7. **No engine starts automatically**, so every agent needs its own startup unit.
8. **The macOS application firewall blocks incoming connections to uv-managed Python.** The MBP agent runs on python.org Framework Python, which already has a firewall entry.
9. **Strata control 0.1.54 drains on SIGINT and prints "stopped", but the process does not exit.** The adapter escalates to terminate and then kill.
10. **On Windows, CTRL_BREAK targets a console process group.** It is only safe for servers the agent launched in their own group. Others are terminated without a drain.
