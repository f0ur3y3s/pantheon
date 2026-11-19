import { useEffect, useState } from "react";
import { CopyButton } from "./ui";
import { ApiError } from "./api";
import type { ClientConfigs } from "./contracts";

type Client = "opencode" | "pi";

export function ConnectPanel({ load, note }: { load: () => Promise<ClientConfigs>; note?: string }) {
  const [data, setData] = useState<ClientConfigs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [client, setClient] = useState<Client>("opencode");

  useEffect(() => {
    let alive = true;
    load().then(
      (d) => alive && setData(d),
      (e) => alive && setError(e instanceof ApiError ? e.body.message : String(e)),
    );
    return () => {
      alive = false;
    };
  }, [load]);

  if (error) return <p className="note bad">{error}</p>;
  if (!data) return <p className="sub">Loading…</p>;
  if (data.models.length === 0) return <p className="sub">Nothing is serving yet, so there is nothing to connect to.</p>;

  const c = data[client];
  const json = JSON.stringify(c.config, null, 2);

  return (
    <section className="connect">
      <div className="tabs" role="group" aria-label="Client">
        {(["opencode", "pi"] as Client[]).map((k) => (
          <button key={k} aria-pressed={client === k} className={client === k ? "tab active" : "tab"} onClick={() => setClient(k)}>
            {k}
          </button>
        ))}
      </div>
      {note && <p className="note">{note}</p>}
      <div className="snippet-head">
        <span className="model">{c.path}</span>
        <CopyButton variant="text" text={json} label={`Copy ${client} config`} />
      </div>
      <pre className="snippet">{json}</pre>
      <p className="sub">{c.merge_hint}</p>
      {data.api_key_env && (
        <p className="sub">
          Set <span className="model">{data.api_key_env}</span> in your shell to your gateway API key.
        </p>
      )}
      <div className="snippet-head">
        <code className="model">{c.usage}</code>
        <CopyButton variant="text" text={c.usage} label={`Copy ${client} command`} />
      </div>
    </section>
  );
}
