import { useState } from "react";
import type { MachineView } from "./contracts";

export function Icon({ name }: { name: "copy" | "close" | "theme" | "refresh" }) {
  const paths = { copy: <><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h8"/></>, close: <path d="M6 6l12 12M18 6L6 18"/>, theme: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2"/></>, refresh: <><path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/></> };
  return <svg className="i" viewBox="0 0 24 24" aria-hidden="true">{paths[name]}</svg>;
}
export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [result, setResult] = useState("");
  return <button className="iconbtn" aria-label={result || label} title={result || label} onClick={async () => {
    try { await navigator.clipboard.writeText(text); setResult("Copied"); } catch { setResult("Copy failed"); }
    setTimeout(() => setResult(""), 2000);
  }}><Icon name="copy"/><span className="sr-only" role="status">{result}</span></button>;
}
export function Status({ m }: { m: MachineView }) {
  return <span className="status"><span className={`dot ${m.status.state}`} aria-hidden="true"/>{m.status.state === "ready" ? "Serving" : m.status.state}</span>;
}
function memorySize(mib: number | null): string {
  if (mib == null) return "—";
  return mib >= 1024 ? `${(mib / 1024).toFixed(1)} GB` : `${Math.round(mib)} MB`;
}

export function MemoryLines({ m }: { m: MachineView }) {
  const resources = m.status.resources;
  if (!resources) return <span className="meter-label">—</span>;
  const lines = [
    { label: "VRAM", used: resources.vram_used_mib, total: resources.vram_total_mib },
    { label: "RAM", used: resources.ram_used_mib, total: resources.ram_total_mib },
  ].filter(line => line.used != null || line.total != null);
  if (!lines.length) return <span className="meter-label">—</span>;
  return <div className="memory-lines">{lines.map(line => {
    const percent = line.used != null && line.total ? Math.max(0, Math.min(100, Math.round(line.used / line.total * 100))) : null;
    return <div className="memory-line" key={line.label}>
      <div className="memory-caption"><span>{line.label}</span><span className="num">{memorySize(line.used)} / {memorySize(line.total)}</span></div>
      {percent != null && <div className="meter"><div className="bar" role="meter" aria-label={`${m.name} ${line.label}`} aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-valuetext={`${memorySize(line.used)} of ${memorySize(line.total)}, ${percent}%`}><i className={percent > 90 ? "hot" : ""} style={{ width: `${percent}%` }}/></div><span className="num">{percent}%</span></div>}
    </div>;
  })}</div>;
}
