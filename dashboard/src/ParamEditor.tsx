import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { ConfigParams, ParamSpec, ParamValue } from "./contracts";

type Draft = Record<string, string>;

function toDraft(p: ConfigParams): Draft {
  const d: Draft = {};
  for (const s of p.schema) {
    const v = p.overrides[s.key];
    d[s.key] = v === undefined || v === null ? "" : String(v);
  }
  return d;
}

function show(v: ParamValue | undefined): string {
  return v === null || v === undefined ? "engine default" : String(v);
}

/** Convert the draft into an update: "" means "no override" (null), otherwise a typed value. */
function toUpdate(schema: ParamSpec[], draft: Draft, current: ConfigParams): Record<string, ParamValue> | string {
  const out: Record<string, ParamValue> = {};
  for (const s of schema) {
    const raw = (draft[s.key] ?? "").trim();
    const had = current.overrides[s.key] !== undefined;
    if (raw === "") {
      if (had) out[s.key] = null;
      continue;
    }
    let v: ParamValue = raw;
    if (s.type === "int" || s.type === "float") {
      const n = Number(raw);
      if (!Number.isFinite(n) || (s.type === "int" && !Number.isInteger(n))) return `${s.label}: enter a ${s.type === "int" ? "whole number" : "number"}`;
      v = n;
    } else if (s.type === "bool") {
      v = raw === "true";
    }
    if (!had || current.overrides[s.key] !== v) out[s.key] = v;
  }
  return out;
}

export function ParamEditor({
  machine,
  configId,
  label,
  canRestart,
  busy,
  onRestart,
  onSaved,
  refreshKey,
}: {
  machine: string;
  configId: string;
  label: string;
  canRestart: boolean; // this config is the one serving
  busy: boolean;
  onRestart: () => Promise<void>;
  onSaved: () => void;
  refreshKey: string; // changes when the machine's state/operation changes; re-read restart_required
}) {
  const [params, setParams] = useState<ConfigParams | null>(null);
  const [draft, setDraft] = useState<Draft>({});
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    setParams(null);
    setError(null);
    api.params(machine, configId).then(
      (p) => {
        if (!alive) return;
        setParams(p);
        setDraft(toDraft(p));
      },
      (e) => alive && setError(e instanceof ApiError ? e.body.message : String(e)),
    );
    return () => {
      alive = false;
    };
  }, [machine, configId]);

  // Re-read after starts/stops so "restart required" reflects what is now serving. Keeps unsaved edits.
  useEffect(() => {
    let alive = true;
    api.params(machine, configId).then(
      (p) => alive && setParams(p),
      () => {},
    );
    return () => {
      alive = false;
    };
  }, [refreshKey, machine, configId]);

  if (error && !params) return <p className="note bad">{error}</p>;
  if (!params) return <p className="sub">Loading parameters…</p>;

  const pending = toUpdate(params.schema, draft, params);
  const dirty = typeof pending === "string" || Object.keys(pending).length > 0;

  async function save(thenRestart: boolean) {
    if (!params) return;
    const update = toUpdate(params.schema, draft, params);
    if (typeof update === "string") {
      setError(update);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      let p = params;
      if (Object.keys(update).length) {
        p = await api.setParams(machine, configId, update);
        setParams(p);
        setDraft(toDraft(p));
        onSaved();
      }
      if (thenRestart) await onRestart();
    } catch (e) {
      setError(e instanceof ApiError ? e.body.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const hasOverrides = Object.keys(params.overrides).length > 0;

  return (
    <section className="params" aria-label={`Parameters for ${label}`}>
      <div className="params-head">
        <span className="label">Parameters</span>
        <span className="sub model">{configId}</span>
      </div>
      {params.schema.map((s) => {
        const id = `p-${machine}-${configId}-${s.key}`;
        const overridden = (draft[s.key] ?? "") !== "";
        return (
          <div className="param" key={s.key}>
            <label htmlFor={id}>
              {s.label}
              {overridden && <span className="edited-dot" title="Changed from the engine's config" aria-label="edited" />}
            </label>
            {s.type === "enum" || s.type === "bool" ? (
              <select id={id} value={draft[s.key] ?? ""} disabled={saving} onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })}>
                <option value="">Default ({show(params.base[s.key])})</option>
                {(s.type === "bool" ? ["true", "false"] : s.options ?? []).map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            ) : (
              <input
                id={id}
                inputMode={s.type === "int" ? "numeric" : "decimal"}
                placeholder={show(params.base[s.key])}
                value={draft[s.key] ?? ""}
                disabled={saving}
                onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })}
              />
            )}
            <span className="sub hint">
              {s.help}
              {s.min != null && s.max != null ? ` Range ${s.min.toLocaleString()}–${s.max.toLocaleString()}.` : ""}
            </span>
          </div>
        );
      })}

      {params.restart_required && !dirty && (
        <p className="note warn">Saved. The running model still uses the previous values; restart it to apply them.</p>
      )}
      {error && <p className="note bad">{error}</p>}

      <div className="buttons">
        <button className="primary" disabled={!dirty || saving || typeof pending === "string"} onClick={() => save(false)}>
          Save
        </button>
        {canRestart && (
          <button disabled={busy || saving || (!dirty && !params.restart_required)} onClick={() => save(true)}>
            {dirty ? "Save & restart" : "Restart to apply"}
          </button>
        )}
        {hasOverrides && (
          <button
            className="ghost"
            disabled={saving}
            onClick={() => setDraft(Object.fromEntries(params.schema.map((s) => [s.key, ""])))}
          >
            Reset all
          </button>
        )}
      </div>
      <p className="sub">Edits are stored by pantheon. The engine's own config files are never changed.</p>
    </section>
  );
}
