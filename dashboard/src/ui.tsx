import { Copy, Moon, RefreshCw, Sun, X } from "lucide-react";
import { useClipboard } from "./ClipboardFeedback";
import type { MachineView } from "./contracts";

export function Icon({ name }: { name: "copy" | "close" | "theme" | "moon" | "refresh" }) {
  const Component = { copy: Copy, close: X, theme: Sun, moon: Moon, refresh: RefreshCw }[name];
  return <Component className="i" size={16} strokeWidth={1.5} aria-hidden="true"/>;
}
export function CopyButton({ text, label = "Copy", variant = "icon" }: { text: string; label?: string; variant?: "icon" | "text" }) {
  const copy = useClipboard();
  return <button type="button" className={variant === "icon" ? "iconbtn" : "ghost small copy-button"} aria-label={label} title={label} onClick={() => void copy(text)}>
    <Icon name="copy"/>{variant === "text" && <span>Copy</span>}
  </button>;
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
