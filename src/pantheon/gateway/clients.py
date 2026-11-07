"""Copy-paste provider configs for LLM clients (opencode, pi).

Formats are matched to the client versions in use (checked 2026-11-06 against working configs):
- opencode v2.1.4: `provider.<id>` with `npm: "@ai-sdk/openai-compatible"` and `options.baseURL`.
  (Newer opencode docs describe `providers`/`package`/`settings`; switch here if the installed version moves.)
- pi 1.5.0: `~/.pi/agent/models.json`, `providers.<id>` with `baseUrl`, `api: "openai-completions"`,
  and a `models` array. pi hides models it can't resolve credentials for, so a dummy key is used when
  the endpoint has no auth.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_OUTPUT_TOKENS = 8192


@dataclass
class ClientModel:
    id: str  # model id as the endpoint expects it
    name: str
    context: int | None = None
    reasoning: bool = True  # the current homelab models all think; becomes an editable parameter in phase 4


def _output_limit(context: int | None) -> int:
    if not context:
        return DEFAULT_OUTPUT_TOKENS
    return max(1024, min(DEFAULT_OUTPUT_TOKENS, context // 4))


def opencode_config(provider_id: str, label: str, base_url: str, models: list[ClientModel],
                    api_key_env: str | None) -> dict:
    options: dict = {"baseURL": base_url}
    if api_key_env:
        options["apiKey"] = f"{{env:{api_key_env}}}"
    entries = {}
    for m in models:
        e: dict = {"name": m.name, "tool_call": True, "reasoning": m.reasoning}
        if m.context:
            out = _output_limit(m.context)
            e["limit"] = {"context": m.context, "input": m.context - out, "output": out}
        entries[m.id] = e
    cfg: dict = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {provider_id: {"npm": "@ai-sdk/openai-compatible", "name": label, "options": options, "models": entries}},
    }
    if models:
        cfg["model"] = f"{provider_id}/{models[0].id}"
    return cfg


def pi_config(provider_id: str, base_url: str, models: list[ClientModel], api_key_env: str | None) -> dict:
    return {
        "providers": {
            provider_id: {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": f"${api_key_env}" if api_key_env else "none",
                "models": [
                    {
                        "id": m.id,
                        "name": m.name,
                        "reasoning": m.reasoning,
                        "input": ["text"],
                        **({"contextWindow": m.context} if m.context else {}),
                        "maxTokens": _output_limit(m.context),
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    }
                    for m in models
                ],
            }
        }
    }


def client_configs(provider_id: str, label: str, base_url: str, models: list[ClientModel],
                   api_key_env: str | None) -> dict:
    """Everything the dashboard's Connect section shows for one endpoint."""
    first = models[0].id if models else "<model>"
    return {
        "provider_id": provider_id,
        "base_url": base_url,
        "api_key_env": api_key_env,
        "models": [m.id for m in models],
        "opencode": {
            "path": "~/.config/opencode/opencode.json",
            "merge_hint": f'merge the "provider.{provider_id}" block into your existing file; "model" is optional',
            "config": opencode_config(provider_id, label, base_url, models, api_key_env),
            "usage": f"opencode --model {provider_id}/{first}",
        },
        "pi": {
            "path": "~/.pi/agent/models.json",
            "merge_hint": f'merge "providers.{provider_id}" into your existing file; /model reloads it',
            "config": pi_config(provider_id, base_url, models, api_key_env),
            "usage": f"pi --model {provider_id}/{first}",
        },
    }
