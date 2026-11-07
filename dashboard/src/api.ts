import type { ClientConfigs, ConfigParams, ParamValue, ErrorBody, LogLine, MachineView, ModelConfig, Operation, OperationKind } from "./contracts";

const TOKEN_KEY = "pantheon.adminToken";

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: token lives only in memory for this tab */
  }
}

export class ApiError extends Error {
  constructor(public status: number, public body: ErrorBody) {
    super(body.message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}`, ...init.headers },
    });
  } catch {
    throw new ApiError(0, { code: "unreachable", message: "Gateway unreachable" });
  }
  if (!res.ok) {
    let body: ErrorBody = { code: "internal", message: `HTTP ${res.status}` };
    try {
      body = (await res.json()).error ?? body;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, body);
  }
  return res.json() as Promise<T>;
}

// Config ids may contain "/" (LM Studio model keys); keep slashes, encode each segment.
const configPath = (id: string) => id.split("/").map(encodeURIComponent).join("/");

export const api = {
  authInfo: () => call<{ required: boolean }>("/api/auth"),
  machines: () => call<MachineView[]>("/api/machines"),
  configs: (m: string) => call<ModelConfig[]>(`/api/machines/${encodeURIComponent(m)}/configs`),
  submit: (m: string, kind: OperationKind, config_id?: string) =>
    call<Operation>(`/api/machines/${encodeURIComponent(m)}/operations`, {
      method: "POST",
      body: JSON.stringify({ kind, config_id: config_id ?? null }),
    }),
  operation: (m: string, id: string) =>
    call<Operation>(`/api/machines/${encodeURIComponent(m)}/operations/${encodeURIComponent(id)}`),
  operations: (m: string, limit = 10) =>
    call<Operation[]>(`/api/machines/${encodeURIComponent(m)}/operations?limit=${limit}`),
  clientConfig: (m: string) => call<ClientConfigs>(`/api/machines/${encodeURIComponent(m)}/client-config`),
  gatewayClientConfig: () => call<ClientConfigs>("/api/client-config"),
  params: (m: string, configId: string) =>
    call<ConfigParams>(`/api/machines/${encodeURIComponent(m)}/configs/${configPath(configId)}/params`),
  setParams: (m: string, configId: string, values: Record<string, ParamValue>) =>
    call<ConfigParams>(`/api/machines/${encodeURIComponent(m)}/configs/${configPath(configId)}/params`, {
      method: "PUT",
      body: JSON.stringify({ values }),
    }),
  logs: (m: string, lines = 200) => call<LogLine[]>(`/api/machines/${encodeURIComponent(m)}/logs?lines=${lines}`),
};
