import { useEffect, useState } from "react";
import { api } from "../api";
import { Card } from "../components/ui";

interface SettingsShape {
  ai: { active_model: string | null; models_configured: number };
  search: { engine: string; tavily_configured: boolean };
  scraping: { engine: string; firecrawl_configured: boolean };
  memory: { embedder: string };
  sandbox: { os: string; mode: string; backend: string; note: string };
  switches: Record<string, boolean>;
  database: { path: string };
}

export default function Settings() {
  const [s, setS] = useState<SettingsShape | null>(null);
  const [audit, setAudit] = useState<{ id: number; ts: string; action: string; target: string; allowed: boolean }[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => { load(); }, []);
  function load() {
    api.get<SettingsShape>("/settings").then(setS).catch((e) => setErr(String(e)));
    api.get<{ entries: typeof audit }>("/audit?limit=50").then((r) => setAudit(r.entries)).catch(() => {});
  }

  async function toggle(name: string, value: boolean) {
    await api.post("/settings/switches", { name, value });
    load();
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">Settings & Security</h1>
      {err && <div className="glass p-3 text-sm text-rose-300">{err}</div>}
      {s && (
        <>
          <Card title="System status">
            <div className="grid gap-3 text-sm md:grid-cols-2">
              <Row label="Active model" value={s.ai.active_model ?? "— none —"} />
              <Row label="Models configured" value={String(s.ai.models_configured)} />
              <Row label="Search engine" value={s.search.engine} />
              <Row label="Scraping engine" value={`${s.scraping.engine} (concurrency-capped)`} />
              <Row label="Memory embeddings" value={s.memory.embedder} />
              <Row label="Database" value={s.database.path} />
              <Row label="Sandbox" value={`${s.sandbox.os} · ${s.sandbox.mode}`} />
              <Row label="Sandbox note" value={s.sandbox.note} />
            </div>
          </Card>
          <Card title="Emergency controls">
            <div className="flex flex-wrap gap-2">
              {Object.entries(s.switches).map(([name, on]) => (
                <button
                  key={name}
                  onClick={() => toggle(name, !on)}
                  className={`rounded-lg border px-4 py-2 text-xs font-semibold ${
                    on
                      ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                      : "border-rose-400/30 bg-rose-400/10 text-rose-300"
                  }`}>
                  {name}: {on ? "ON" : "OFF"}
                </button>
              ))}
            </div>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => api.post("/settings/emergency-stop").then(load).then(() => alert("EMERGENCY STOP executed: agents signalled, processes killed, subsystems disabled."))}
                className="rounded-lg border border-rose-400/50 bg-rose-500/20 px-4 py-2 text-sm font-bold text-rose-200">
                ⛔ GLOBAL EMERGENCY STOP
              </button>
              <button
                onClick={() => api.post("/settings/emergency-clear").then(load)}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm text-slate-300">
                Re-enable controls
              </button>
            </div>
          </Card>
          <Card title="Audit log (latest 50)">
            <div className="max-h-72 overflow-y-auto font-mono text-[11px]">
              {audit.map((a) => (
                <div key={a.id} className="flex gap-2 border-b border-white/5 py-1">
                  <span className="text-slate-600">{a.ts.slice(11, 19)}</span>
                  <span className={a.allowed ? "text-emerald-400/80" : "text-rose-400/80"}>
                    {a.allowed ? "ALLOW" : "DENY"}
                  </span>
                  <span className="text-cyan-300/80">{a.action}</span>
                  <span className="truncate text-slate-400">{a.target}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card title="Environment">
            <div className="text-xs text-slate-400">
              API keys are configured in <code className="text-cyan-300">.env</code> (never in the frontend)
              or stored via the Providers page (OS keyring when available, DB fallback with 0600 permissions).
              Optional: <code className="text-cyan-300">TAVILY_API_KEY</code> enables the Tavily engine (keyless
              fallback search runs otherwise); <code className="text-cyan-300">FIRECRAWL_API_KEY</code> enables
              Firecrawl (local engine runs otherwise).
            </div>
          </Card>
        </>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/5 pb-1.5">
      <span className="text-xs uppercase tracking-wider text-slate-500">{label}</span>
      <span className="truncate text-right font-mono text-xs text-slate-200">{value}</span>
    </div>
  );
}
