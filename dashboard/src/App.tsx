import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, getToken, setToken } from "./api";
import type { MachineView, Operation } from "./contracts";
import { useConfirmation } from "./Confirmation";
import { ConnectPanel } from "./ConnectPanel";
import { MachineCard } from "./MachineCard";
import { CopyButton, Icon, MemoryLines, Status } from "./ui";

function Login({ onDone, error }: { onDone: () => void; error: string | null }) {
  const [value, setValue] = useState("");
  return <main className="login"><form onSubmit={e => { e.preventDefault(); setToken(value.trim()); onDone(); }}>
    <img className="brand-mark" src="/pantheon-mark.svg" alt=""/><h1>Pantheon</h1>
    <label htmlFor="token">Admin token</label><input id="token" type="password" autoComplete="current-password" value={value} onChange={e => setValue(e.target.value)} autoFocus/>
    {error && <p className="note bad" role="alert">{error}</p>}<button className="primary" disabled={!value.trim()}>Sign in</button>
  </form></main>;
}

export function App() {
  const { confirm, confirmation } = useConfirmation();
  const [machines, setMachines] = useState<MachineView[] | null>(null);
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [gatewayConnect, setGatewayConnect] = useState(false);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [historyError, setHistoryError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [compact, setCompact] = useState(() => matchMedia("(max-width: 1080px)").matches);
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("pantheon.theme") === "light" ? "light" : "dark"; } catch { return "dark"; } });
  const panel = useRef<HTMLElement>(null);
  const page = useRef<HTMLDivElement>(null);
  const top = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const open = selected !== null || gatewayConnect;
  const close = useCallback(() => { setSelected(null); setGatewayConnect(false); }, []);
  const choose = (name: string | null) => { opener.current = document.activeElement as HTMLElement; setSelected(name); setGatewayConnect(name === null); };
  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem("pantheon.theme", theme); } catch { /* optional persistence */ } }, [theme]);
  useEffect(() => { const q = matchMedia("(max-width: 1080px)"); const change = () => setCompact(q.matches); q.addEventListener("change", change); return () => q.removeEventListener("change", change); }, []);
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    panel.current?.querySelector<HTMLButtonElement>("button")?.focus();
    if (compact) { page.current?.setAttribute("inert", ""); top.current?.setAttribute("inert", ""); document.body.style.overflow = "hidden"; }
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key !== "Tab" || !compact) return;
      const nodes = Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex="0"]') ?? []).filter(n => n.getClientRects().length > 0);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); page.current?.removeAttribute("inert"); top.current?.removeAttribute("inert"); document.body.style.overflow = previousOverflow; opener.current?.focus(); };
  }, [open, compact, close]);
  const refresh = useCallback(async () => {
    setRefreshing(true);
    try { setMachines(await api.machines()); setError(null); }
    catch (e) { if (e instanceof ApiError && e.status === 401) { setToken(""); setAuthError("That token was not accepted."); setNeedsLogin(true); } else setError(e instanceof Error ? e.message : String(e)); }
    finally { setRefreshing(false); }
  }, []);
  useEffect(() => { api.authInfo().then(({ required }) => { setAuthRequired(required); setNeedsLogin(required && !getToken()); }, () => setAuthRequired(true)); }, []);
  useEffect(() => { if (needsLogin || authRequired === null) return; refresh(); const t = setInterval(refresh, 2000); return () => clearInterval(t); }, [needsLogin, authRequired, refresh]);
  const reachable = machines?.filter(m => m.status.state !== "offline").map(m => m.name).join("|") ?? "";
  useEffect(() => {
    if (needsLogin || !reachable) { setOperations([]); setHistoryError(false); return; }
    let alive = true;
    const load = async () => { const results = await Promise.allSettled(reachable.split("|").map(name => api.operations(name, 10).then(rows => rows.map(o => ({ ...o, machine: name }))))); if (!alive) return; setHistoryError(results.some(r => r.status === "rejected")); setOperations(results.flatMap(r => r.status === "fulfilled" ? r.value : []).sort((a,b) => b.created_at - a.created_at).slice(0, 8)); };
    load(); const t = setInterval(load, 2000); return () => { alive = false; clearInterval(t); };
  }, [reachable, needsLogin]);
  if (needsLogin) return <Login error={authError} onDone={() => { setAuthError(null); setNeedsLogin(false); }}/>;
  const list = machines ?? [];
  const serving = list.filter(m => m.status.state === "ready");
  const routes = serving.flatMap(m => m.status.served_models.map(model => ({ machine: m.name, id: `${m.name}/${model}`, engine: m.status.engine })));
  const current = list.find(m => m.name === selected);
  const endpoint = `${window.location.origin}/v1`;
  const servingKey = routes.map(r => r.id).join("|");
  return <>
    {confirmation}
    <header className="top" ref={top}>
      <div className="brand"><img className="brand-mark" src="/pantheon-mark.svg" alt=""/>Pantheon</div><span className="crumb" aria-hidden="true">/</span><span className="crumb">Machines</span>
      <div className="endpoint"><code className="mono">{endpoint}</code><CopyButton text={endpoint} label="Copy gateway endpoint"/></div><span className="spacer"/>
      <button className="iconbtn" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}><Icon name="theme"/></button>
      {authRequired && <button className="ghost small" onClick={async () => { if (await confirm({ title: "Sign out?", message: "You will need your admin token to sign in again. Any unsaved parameter edits will be discarded.", action: "Sign out" })) { close(); setToken(""); setNeedsLogin(true); } }}>Sign out</button>}
    </header>
    <main className={`page ${open ? "with-drawer" : ""}`}>
      <div ref={page} className="console-main">
        <div className="head"><div><h1>Machines</h1></div><button className="small" disabled={refreshing} onClick={refresh}><Icon name="refresh"/>Refresh</button></div>
        {error && <p className="note bad gateway-error" role="alert">{error} <button className="small" onClick={refresh}>Retry</button></p>}
        <dl className="strip"><div><dt>Serving</dt><dd>{machines ? serving.length : "–"}<small>of {list.length} machines</small></dd></div><div><dt>Models routed</dt><dd>{machines ? routes.length : "–"}<small>at one endpoint</small></dd></div><div><dt>In flight</dt><dd>{machines ? list.reduce((n,m) => n + m.active_requests, 0) : "–"}<small>requests</small></dd></div><div><dt>Offline</dt><dd>{machines ? list.filter(m => m.status.state === "offline").length : "–"}<small>{list.filter(m => m.status.state === "offline").length === 1 ? "machine" : "machines"}</small></dd></div></dl>
        <div className="tablewrap"><table aria-label="Machines"><thead><tr><th>Machine</th><th>Status</th><th>Model</th><th>Memory</th><th className="c-req">Requests</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>
          {list.map(m => { const busy = ["loading", "draining", "stopping"].includes(m.status.state); const operation = operations.find(o => o.machine === m.name && o.id === m.status.current_operation); return <tr key={m.name} className={m.status.state} aria-selected={selected === m.name} onClick={() => choose(m.name)}>
            <td className="c-machine"><button className="machine-link" onClick={e => { e.stopPropagation(); choose(m.name); }} aria-label={`Open ${m.name}`} aria-expanded={selected === m.name}>{m.name}</button><div className="machine-sub clip" title={`${m.capabilities?.engine ?? m.status.engine} ${m.capabilities?.engine_version ?? ""} · ${m.status.resources?.gpu_name ?? ""}`}>{m.capabilities ? `${m.capabilities.engine} ${m.capabilities.engine_version}` : m.status.engine}{m.status.resources?.gpu_name ? ` · ${m.status.resources.gpu_name}` : ""}</div></td>
            <td className="c-status"><Status m={m}/></td><td className="c-model"><span className={`model clip ${m.status.state === "offline" || m.status.state === "failed" ? "bad" : ""}`} title={m.status.served_models.join(", ") || m.status.message || "No model serving"}>{m.status.served_models.join(", ") || m.status.message || "No model serving"}</span>{busy && <div className="row-operation"><div className="op-step">{operation?.step ?? "Operation in progress"}</div>{operation && <div className="bar" role="progressbar" aria-label={`${m.name} operation`} aria-valuenow={Math.round(operation.progress * 100)} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${operation.progress * 100}%` }}/></div>}</div>}</td>
            <td className="c-mem"><MemoryLines m={m}/></td><td className="c-req num">{m.status.state === "offline" ? "—" : m.active_requests}</td>
            <td className="actions"><button className="small" disabled={m.status.state === "offline"} onClick={e => { e.stopPropagation(); choose(m.name); }}>{busy ? "View progress" : m.status.state === "ready" ? "Switch" : "Start"}</button></td>
          </tr>; })}
        </tbody></table>{!machines && <p className="empty" role="status">Loading machines…</p>}{machines?.length === 0 && <p className="empty">No machines configured.</p>}</div>
        <div className="lower"><section className="panel" aria-labelledby="routes-title"><header className="panel-head"><div><h2 id="routes-title">Gateway routes</h2><p>Models clients can request at the endpoint above.</p></div><button className="small" onClick={() => choose(null)}>Connect a client</button></header><ul className="routes">{routes.map(r => <li key={r.id}><span className="model clip" title={r.id}>{r.id}</span><span className="via">{r.engine}</span><CopyButton text={r.id} label={`Copy ${r.id}`}/></li>)}</ul>{routes.length === 0 && <p className="empty">{machines ? "No models are serving." : "Loading routes…"}</p>}</section>
        <section className="panel" aria-labelledby="activity-title"><header className="panel-head"><div><h2 id="activity-title">Recent operations</h2><p>Across reachable machines, newest first.</p></div></header><ul className="activity">{operations.map(o => <li key={`${o.machine}-${o.id}`}><time title={new Date(o.created_at * 1000).toLocaleString()}>{new Date(o.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time><div className="what"><b>{o.machine}</b> · {o.kind}<div className="model clip" title={o.config_id ?? ""}>{o.step || o.config_id}</div></div><span className={`res ${o.status === "succeeded" ? "ok" : o.status === "failed" ? "bad" : "run"}`}>{o.status}</span></li>)}</ul>{historyError && <p className="note bad history-note">Some operation history could not be loaded. Retrying…</p>}{!operations.length && !historyError && <p className="empty">No recent operations.</p>}</section></div>
      </div>
      {open && <><div className="scrim" aria-hidden="true" onClick={close}/><aside ref={panel} className="drawer" role={compact ? "dialog" : "region"} aria-modal={compact || undefined} aria-label={current ? `${current.name} controls` : "Connect through gateway"}>
        <header className="drawer-head"><div><h2>{current?.name ?? "Connect through gateway"}</h2>{current && <p className="machine-sub">{current.status.engine} · <Status m={current}/></p>}</div><span className="spacer"/><button className="iconbtn" onClick={close} aria-label="Close panel"><Icon name="close"/></button></header>
        {current ? <MachineCard key={current.name} m={current} onChanged={refresh}/> : <div className="tabpanel"><ConnectPanel key={servingKey} load={api.gatewayClientConfig}/></div>}
      </aside></>}
    </main>
  </>;
}
