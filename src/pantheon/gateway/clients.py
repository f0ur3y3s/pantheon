"""Copy-paste provider configs for LLM clients (opencode, pi).

Formats are matched to the client versions in use (checked 2026-11-06 against working configs):
- opencode v2.1.4: `provider.<id>` with `npm: "@ai-sdk/openai-compatible"` and `options.baseURL`.
  (Newer opencode docs describe `providers`/`package`/`settings`; switch here if the installed version moves.)
- pi 1.5.0: `~/.pi/agent/models.json`, `providers.<id>` with `baseUrl`, `api: "openai-completions"`,
  and a `models` array. pi hides models it can't resolve credentials for, so a dummy key is used when
  the endpoint has no auth.
- omp (oh-my-pi, a pi fork): the same provider shape as pi, but as YAML in `~/.omp/agent/models.yml`
  (checked 2026-11-24 against a working file). Sent pre-rendered as `text`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

DEFAULT_OUTPUT_TOKENS = 8192  # when the context is unknown
MAX_OUTPUT_TOKENS = 32768  # thinking models need room: a quarter of the context, up to this


@dataclass
class ClientModel:
    id: str  # model id as the endpoint expects it
    name: str
    context: int | None = None
    reasoning: bool = True  # the current homelab models all think; becomes an editable parameter in phase 4


def _output_limit(context: int | None) -> int:
    if not context:
        return DEFAULT_OUTPUT_TOKENS
    return max(1024, min(MAX_OUTPUT_TOKENS, context // 4))


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


def to_yaml(value, indent: int = 0) -> str:
    """Block-style YAML for plain dicts/lists/scalars. Strings are JSON-quoted, which YAML reads as-is."""
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if isinstance(v, (dict, list)) and v:
                # list items sit level with their key, as in omp's own models.yml
                lines.append(f"{pad}{k}:\n{to_yaml(v, indent if isinstance(v, list) else indent + 1)}")
            else:
                lines.append(f"{pad}{k}: {_yaml_scalar(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict) and item:
                first, *rest = to_yaml(item, indent + 1).split("\n")
                lines.append(f"{pad}- {first.lstrip()}")
                lines += rest
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return pad + _yaml_scalar(value)


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return "{}" if isinstance(v, dict) else "[]"
    return json.dumps(str(v))


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
        "omp": {
            "path": "~/.omp/agent/models.yml",
            "merge_hint": f'merge "providers.{provider_id}" into your existing file (or a profile\'s agent/models.yml), then restart omp',
            "config": (omp := pi_config(provider_id, base_url, models, api_key_env)),
            "text": to_yaml(omp) + "\n",
            "usage": f"omp --model {provider_id}/{first}",
        },
    }
