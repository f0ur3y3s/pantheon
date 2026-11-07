// Mirrors src/pantheon/contracts.py — keep in sync (the Python file is the source of truth).

export type MachineState = "offline" | "idle" | "loading" | "ready" | "draining" | "stopping" | "failed";
export type EngineKind = "strata" | "llama.cpp" | "lmstudio" | "simulated";
export type OperationKind = "start" | "stop" | "switch";
export type OperationStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface ErrorBody {
  code: string;
  message: string;
  detail?: Record<string, unknown>;
}

export interface Capabilities {
  engine: EngineKind;
  engine_version: string;
  can_start_stop: boolean;
  native_drain: boolean;
  reports_slots: boolean;
  reports_memory: boolean;
  embeddings: boolean;
  max_parallel: number | null;
}

export interface ModelConfig {
  id: string;
  label: string;
  engine: EngineKind;
  model: string;
  context: number | null;
  parallel: number | null;
  est_memory_mib: number | null;
  edited: boolean;
}

export interface Resources {
  gpu_name: string | null;
  vram_total_mib: number | null;
  vram_used_mib: number | null;
  ram_total_mib: number | null;
  ram_used_mib: number | null;
}

export interface MachineStatus {
  machine: string;
  state: MachineState;
  engine: EngineKind;
  active_config: string | null;
  served_models: string[];
  engine_url: string | null;
  current_operation: string | null;
  message: string | null;
  resources: Resources | null;
  updated_at: number;
}

export interface Operation {
  id: string;
  machine: string;
  kind: OperationKind;
  config_id: string | null;
  status: OperationStatus;
  progress: number;
  step: string;
  error: ErrorBody | null;
  created_at: number;
  updated_at: number;
  finished_at: number | null;
}

export interface LogLine {
  ts: number;
  source: string;
  text: string;
}

/** Gateway view of a machine (/api/machines). */
export interface MachineView {
  name: string;
  status: MachineStatus;
  capabilities: Capabilities | null;
  last_seen: number | null;
  last_error: string | null;
  active_requests: number;
}

/** /api/client-config and /api/machines/{m}/client-config */
export interface ClientSnippet {
  path: string;
  merge_hint: string;
  config: Record<string, unknown>;
  usage: string;
}

export interface ClientConfigs {
  provider_id: string;
  base_url: string;
  api_key_env: string | null;
  models: string[];
  direct: boolean;
  opencode: ClientSnippet;
  pi: ClientSnippet;
}

export type ParamType = "int" | "float" | "bool" | "enum";
export type ParamValue = number | string | boolean | null;

export interface ParamSpec {
  key: string;
  label: string;
  type: ParamType;
  min: number | null;
  max: number | null;
  options: string[] | null;
  restart: boolean;
  help: string | null;
}

/** GET/PUT /api/machines/{m}/configs/{id}/params */
export interface ConfigParams {
  config_id: string;
  schema: ParamSpec[];
  base: Record<string, ParamValue>;
  overrides: Record<string, ParamValue>;
  values: Record<string, ParamValue>;
  active: boolean;
  restart_required: boolean;
}
