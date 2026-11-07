import { useCallback, useEffect, useState } from "react";
import { api, ApiError, getToken, setToken } from "./api";
import type { MachineView } from "./contracts";
import { ConnectPanel } from "./ConnectPanel";
import { MachineCard } from "./MachineCard";

function Login({ onDone, error }: { onDone: () => void; error: string | null }) {
  const [value, setValue] = useState("");
  return (
    <main className="login">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setToken(value.trim());
          onDone();
        }}
      >
        <h1>Pantheon</h1>
        <label htmlFor="token">Admin token</label>
        <input id="token" type="password" autoComplete="current-password" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        {error && <p className="note bad">{error}</p>}
        <button className="primary" type="submit" disabled={!value.trim()}>
          Sign in
        </button>
      </form>
    </main>
  );
}

export function App() {
  const [machines, setMachines] = useState<MachineView[] | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  // null until /api/auth answers: whether the gateway has PANTHEON_ADMIN_TOKEN set.
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [gatewayError, setGatewayError] = useState<string | null>(null);
  const [showConnect, setShowConnect] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setMachines(await api.machines());
      setGatewayError(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setToken("");
        setAuthError("That token was not accepted.");
        setNeedsLogin(true);
      } else {
        setGatewayError(e instanceof ApiError ? e.body.message : String(e));
      }
    }
  }, []);

  useEffect(() => {
    api.authInfo().then(
      ({ required }) => {
        setAuthRequired(required);
        setNeedsLogin(required && !getToken());
      },
      () => setAuthRequired(true), // gateway unreachable: assume sign-in; refresh() reports the error
    );
  }, []);

  useEffect(() => {
    if (needsLogin || authRequired === null) return;
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [needsLogin, authRequired, refresh]);

  if (needsLogin) {
    return (
      <Login
        error={authError}
        onDone={() => {
          setAuthError(null);
          setNeedsLogin(false);
        }}
      />
    );
  }

  const ready = machines?.filter((m) => m.status.state === "ready").length ?? 0;
  // Regenerate the gateway snippet whenever the set of served models changes.
  const servingKey = (machines ?? [])
    .filter((m) => m.status.state === "ready")
    .map((m) => `${m.name}:${m.status.served_models.join(",")}`)
    .join("|");

  return (
    <div className="shell">
      <header className="top">
        <h1>Pantheon</h1>
        <p className="sub">
          {machines ? `${ready} of ${machines.length} machines serving` : "Connecting…"}
          {gatewayError && <span className="bad"> · {gatewayError}</span>}
        </p>
        {authRequired && (
          <button
            className="ghost"
            onClick={() => {
              setToken("");
              setNeedsLogin(true);
            }}
          >
            Sign out
          </button>
        )}
      </header>
      <section className="gateway-connect">
        <header>
          <div>
            <h2>Connect a client</h2>
            <p className="sub">One address for every machine. Copy into opencode or pi.</p>
          </div>
          <button className="ghost" aria-expanded={showConnect} onClick={() => setShowConnect((v) => !v)}>
            {showConnect ? "Hide" : "Show"}
          </button>
        </header>
        {showConnect && <ConnectPanel key={servingKey} load={api.gatewayClientConfig} />}
      </section>
      <main className="grid">
        {machines?.map((m) => <MachineCard key={m.name} m={m} onChanged={refresh} />)}
      </main>
    </div>
  );
}
