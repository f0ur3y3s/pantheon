import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { ConnectPanel } from "./ConnectPanel";
import { ParamEditor } from "./ParamEditor";
import type { LogLine, MachineView, ModelConfig, Operation } from "./contracts";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);
const BUSY_STATES = new Set(["loading", "stopping", "draining"]);

function mib(n: number | null | undefined): string {
  if (n == null) return "–";
  return n >= 1024 ? `${(n / 1024).toFixed(1)} GB` : `${n} MB`;
}

function Meter({ machine, label, used, total }: { machine: string; label: string; used: number | null; total: number | null }) {
  if (used == null || !total) return null;
  const pct = Math.min(100, Math.round((used / total) * 100));
  return (
    <div className="meter">
      <div className="meter-head">
        <span>{label}</span>
        <span className="num">
          {mib(used)} / {mib(total)}
        </span>
      </div>
      <div className="meter-track" role="progressbar" aria-label={`${machine} ${label}`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={`meter-fill ${pct > 90 ? "hot" : ""}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function MachineCard({ m, onChanged }: { m: MachineView; onChanged: () => void }) {
  const st = m.status;
  const offline = st.state === "offline";
  const [configs, setConfigs] = useState<ModelConfig[] | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [configRetry, setConfigRetry] = useState(0);
  const [selected, setSelected] = useState<string>("");
  const [op, setOp] = useState<Operation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showLogs, setShowLogs] = useState(false);
  const [showConnect, setShowConnect] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [profileName, setProfileName] = useState("");
  const [creating, setCreating] = useState(false);
  // Keyed on what is serving so the snippet regenerates after a switch.
  const servingKey = `${st.active_config}|${st.served_models.join(",")}`;
  const loadClientConfig = useCallback(() => api.clientConfig(m.name), [m.name]);
  const [logs, setLogs] = useState<LogLine[]>([]);
  const logRef = useRef<HTMLPreElement>(null);

  // Load configs once the machine is reachable.
  useEffect(() => {
    if (offline || configs) return;
    let alive = true;
    setConfigError(null);
    api.configs(m.name).then(
      (c) => {
        if (!alive) return;
        setConfigs(c);
        setSelected((s) => c.some((item) => item.id === s) ? s : st.active_config || c[0]?.id || "");
      },
      (e) => alive && setConfigError(e instanceof ApiError ? e.body.message : String(e)),
    );
    return () => { alive = false; };
  }, [offline, configs, configRetry, m.name, st.active_config]);

  // Adopt an operation already in flight (e.g. after a page reload).
  useEffect(() => {
    if (st.current_operation && (!op || op.id !== st.current_operation)) {
      api.operation(m.name, st.current_operation).then(setOp, () => {});
    }
  }, [st.current_operation, m.name, op]);

  // Follow the operation until it finishes.
  useEffect(() => {
    if (!op || TERMINAL.has(op.status)) return;
    const t = setInterval(async () => {
      try {
        const next = await api.operation(m.name, op.id);
        setOp(next);
        if (TERMINAL.has(next.status)) onChanged();
      } catch {
        /* transient; keep polling */
      }
    }, 600);
    return () => clearInterval(t);
  }, [op, m.name, onChanged]);

  useEffect(() => {
    if (!showLogs) return;
    let alive = true;
    const load = () =>
      api.logs(m.name, 300).then(
        (l) => alive && setLogs(l),
        () => {},
      );
    load();
    const t = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [showLogs, m.name]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [logs]);

  const running = op != null && !TERMINAL.has(op.status);
  const busy = running || BUSY_STATES.has(st.state);
  const serving = st.state === "ready";
  const action = serving ? (selected && selected !== st.active_config ? "switch" : null) : "start";

  async function submit(kind: "start" | "stop" | "switch", configId: string = selected) {
    setError(null);
    try {
      const o = await api.submit(m.name, kind, kind === "stop" ? undefined : configId);
      setOp(o);
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.body.message : String(e));
    }
  }

  async function createProfile(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selected || !profileName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const profile = await api.createProfile(m.name, { label: profileName.trim(), source_config_id: selected });
      setConfigs((current) => [...(current ?? []), profile]);
      setSelected(profile.id);
      setProfileName("");
      setShowCreate(false);
      setShowEdit(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.body.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  const activeLabel = useMemo(
    () => configs?.find((c) => c.id === st.active_config)?.label ?? st.active_config,
    [configs, st.active_config],
  );
  const r = st.resources;

  return (
    <article className={`card state-${st.state}`} aria-busy={busy}>
      <header className="card-head">
        <div>
          <h2>{m.name}</h2>
          <p className="sub">
            {m.capabilities ? `${m.capabilities.engine} ${m.capabilities.engine_version}` : st.engine}
            {r?.gpu_name ? ` · ${r.gpu_name}` : ""}
          </p>
        </div>
        <span className={`machine-state state-label-${st.state}`}><span className="state-dot" aria-hidden="true" />{st.state}</span>
      </header>

      <section className="serving">
        {serving ? (
          <>
            <div className="model">{st.served_models.join(", ") || "–"}</div>
            <div className="sub">
              {activeLabel}
              {m.active_requests > 0 ? ` · ${m.active_requests} active request${m.active_requests > 1 ? "s" : ""}` : ""}
            </div>
          </>
        ) : null}
        {st.message && <p className={`note ${st.state === "failed" || offline ? "bad" : ""}`}>{st.message}</p>}
      </section>

      {r && (
        <section className="meters">
          <Meter machine={m.name} label="VRAM" used={r.vram_used_mib} total={r.vram_total_mib} />
          <Meter machine={m.name} label="RAM" used={r.ram_used_mib} total={r.ram_total_mib} />
        </section>
      )}

      {op && (running || op.status === "failed" || Date.now() / 1000 - (op.finished_at ?? 0) < 8) && (
        <section className={`op op-${op.status}`} aria-live="polite">
          {running ? (
            <>
              <div className="op-head">
                <span><span className="op-kind">{op.kind}</span>{configs?.find((c) => c.id === op.config_id)?.label ? <span> · {configs.find((c) => c.id === op.config_id)?.label}</span> : null}</span>
                <span className="num">{Math.round(op.progress * 100)}%</span>
              </div>
              <div className="meter-track">
                <div className="meter-fill" style={{ width: `${Math.round(op.progress * 100)}%` }} />
              </div>
              <div className="sub">{op.step}</div>
            </>
          ) : (
            <p className="op-result">
              {op.status === "failed" ? `${op.kind} failed: ${op.error?.message ?? op.step}` : `${op.kind === "stop" ? "Stopped" : op.kind === "switch" ? "Switched" : "Started"} successfully`}
            </p>
          )}
        </section>
      )}

      {!offline && (
        <footer className="controls">
          <select
            aria-label={`Configuration for ${m.name}`}
            value={configs ? selected : ""}
            disabled={busy || !configs}
            onChange={(e) => setSelected(e.target.value)}
          >
            {!configs && <option value="">{configError ? "Configurations unavailable" : "Loading configurations…"}</option>}
            {(configs ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
                {c.profile ? " · profile" : ""}
                {c.edited ? " · edited" : ""}
                {c.id === st.active_config ? " (active)" : ""}
              </option>
            ))}
          </select>
          {configError && <div className="config-error"><span>{configError}</span><button type="button" onClick={() => setConfigRetry((n) => n + 1)}>Retry</button></div>}
          <div className="buttons">
            {action && (
              <button className="primary" disabled={busy || !selected} onClick={() => submit(action)}>
                {action === "switch" ? "Switch" : "Start"}
              </button>
            )}
            {serving && (
              <button disabled={busy} onClick={() => submit("stop")}>
                Stop
              </button>
            )}
            <button className="ghost" aria-expanded={showEdit} disabled={!selected} onClick={() => setShowEdit((v) => !v)}>
              Edit
            </button>
            <button className="ghost" aria-expanded={showCreate} disabled={!selected} onClick={() => setShowCreate((v) => !v)}>
              New profile
            </button>
            {serving && (
              <button className="ghost" aria-expanded={showConnect} onClick={() => setShowConnect((v) => !v)}>
                Direct connect
              </button>
            )}
            <button className="ghost" aria-expanded={showLogs} onClick={() => setShowLogs((v) => !v)}>
              Logs
            </button>
          </div>
          {showCreate && (
            <form className="create-profile" onSubmit={createProfile}>
              <label htmlFor={`profile-name-${m.name}`}>Profile name</label>
              <input id={`profile-name-${m.name}`} value={profileName} maxLength={80} autoFocus
                onChange={(e) => setProfileName(e.target.value)} placeholder="Name this configuration" />
              <p className="sub">Copies {configs?.find((c) => c.id === selected)?.label ?? selected} and its saved parameter edits</p>
              <div className="buttons">
                <button className="primary" type="submit" disabled={creating || !profileName.trim()}>Create profile</button>
                <button type="button" onClick={() => setShowCreate(false)}>Cancel</button>
              </div>
            </form>
          )}
          {error && <p className="note bad">{error}</p>}
        </footer>
      )}

      {showEdit && !offline && selected && (
        <ParamEditor
          machine={m.name}
          configId={selected}
          label={configs?.find((c) => c.id === selected)?.label ?? selected}
          canRestart={serving && selected === st.active_config}
          busy={busy}
          onRestart={() => submit("switch", selected)}
          onSaved={() => setConfigs(null)}
          refreshKey={`${st.state}|${st.current_operation ?? ""}|${st.active_config ?? ""}`}
        />
      )}

      {showConnect && serving && (
        <ConnectPanel
          key={servingKey}
          load={loadClientConfig}
          note="Connects directly to this machine's engine. Requests bypass gateway draining during a switch."
        />
      )}

      {showLogs && (
        <pre className="logs" ref={logRef}>
          {logs.length === 0
            ? "No log lines yet."
            : logs.map((l) => `${new Date(l.ts * 1000).toLocaleTimeString()} ${l.source.padEnd(6)} ${l.text}`).join("\n")}
        </pre>
      )}
    </article>
  );
}
