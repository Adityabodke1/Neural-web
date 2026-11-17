import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Bar, Card } from "../components/ui";

interface ModelRow {
  model_ref: string;
  provider: string;
  label: string;
  is_default: boolean;
  enabled: boolean;
  quota: {
    limit_tokens: number | null;
    used_tokens: number;
    remaining_tokens: number | null;
    percent_used: number | null;
    period: string;
    source: string;
    used_calls: number;
  };
}
interface ProviderRow {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  enabled: boolean;
  api_key_masked: string;
  api_key_present: boolean;
  key_source: string;
}
interface Bench {
  model_ref: string;
  capability: string;
  score: number;
  source: string;
  tested_at: string;
}

export default function Models() {
  const [models, setModels] = useState<ModelRow[]>([]);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [bench, setBench] = useState<Bench[]>([]);
  const [err, setErr] = useState("");
  const [newModel, setNewModel] = useState({ provider: "groq", model_id: "" });
  const [newKey, setNewKey] = useState({ id: "groq", key: "" });

  useEffect(() => { load(); }, []);

  function load() {
    api.get<{ models: ModelRow[] }>("/usage").then((r) => setModels(r.models)).catch((e) => setErr(String(e)));
    api.get<ProviderRow[]>("/providers").then(setProviders).catch(() => {});
    api.get<Bench[]>("/benchmarks").then(setBench).catch(() => {});
  }

  const byModel: Record<string, Bench[]> = {};
  for (const b of bench) (byModel[b.model_ref] ??= []).push(b);

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">Models, Providers & Quotas</h1>
      {err && <div className="glass p-3 text-sm text-rose-300">{err}</div>}

      <Card title="Providers (keys stay server-side — masked here)">
        <div className="space-y-2">
          {providers.map((p) => (
            <div key={p.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-white/5 bg-white/[0.03] p-3 text-sm">
              <span className="font-semibold text-slate-100">{p.name}</span>
              <span className="text-xs text-slate-500">{p.kind}</span>
              <span className="truncate text-xs text-slate-500">{p.base_url || "(custom endpoint)"}</span>
              <span className="text-xs text-cyan-300/70">{p.api_key_masked || "no key"}</span>
              <span className="text-[10px] uppercase text-slate-600">{p.key_source}</span>
              <div className="flex-1" />
              <input
                type="password"
                placeholder="paste API key…"
                className="w-48 rounded border border-white/10 bg-black/30 px-2 py-1 text-xs outline-none focus:border-cyan-400/40"
                onChange={(e) => setNewKey({ id: p.id, key: e.target.value })}
              />
              <button
                onClick={() => api.patch(`/providers/${p.id}`, { api_key: newKey.id === p.id ? newKey.key : undefined })
                  .then(load)}
                className="rounded border border-cyan-400/30 bg-cyan-400/10 px-2 py-1 text-xs text-cyan-200">
                Save
              </button>
              <button
                onClick={() => api.post(`/providers/${p.id}/test?model_id=`).then((r: unknown) => alert(JSON.stringify(r)))
                  .catch((e) => alert(`failed: ${e}`))}
                className="rounded border border-white/10 px-2 py-1 text-xs text-slate-300">
                Test
              </button>
            </div>
          ))}
        </div>
      </Card>

      <Card title="Models — token quotas (bars update after every API request)">
        <div className="space-y-2">
          {models.map((m) => (
            <div key={m.model_ref} className="rounded-lg border border-white/5 bg-white/[0.03] p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-semibold text-slate-100">{m.model_ref}</span>
                {m.is_default && <span className="rounded bg-fuchsia-400/10 px-2 py-0.5 text-[10px] font-bold text-fuchsia-300">DEFAULT</span>}
                <span className="text-xs text-slate-500">{m.provider} · {m.quota.used_calls} calls · {m.quota.source}</span>
              </div>
              <div className="mt-2"><Bar used={m.quota.used_tokens} limit={m.quota.limit_tokens} /></div>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => api.post(`/models/${m.model_ref}/default`).then(load)}
                  className="rounded border border-white/10 px-2 py-1 text-xs text-slate-300">set default</button>
                <button
                  onClick={() => api.del(`/models/${m.model_ref}`).then(load)}
                  className="rounded border border-rose-400/20 px-2 py-1 text-xs text-rose-300/80">remove</button>
              </div>
            </div>
          ))}
          {models.length === 0 && <div className="text-sm text-slate-500">No models configured.</div>}
        </div>
        <div className="mt-4 flex gap-2">
          <select
            value={newModel.provider}
            onChange={(e) => setNewModel({ ...newModel, provider: e.target.value })}
            className="rounded border border-white/10 bg-black/30 px-2 py-1.5 text-xs">
            {providers.map((p) => <option key={p.id} value={p.id}>{p.id}</option>)}
          </select>
          <input
            value={newModel.model_id}
            onChange={(e) => setNewModel({ ...newModel, model_id: e.target.value })}
            placeholder="model id (e.g. openai/gpt-oss-120b)"
            className="flex-1 rounded border border-white/10 bg-black/30 px-2 py-1.5 text-xs outline-none focus:border-cyan-400/40" />
          <button
            onClick={() => api.post("/models", { ...newModel, is_default: true }).then(() => { setNewModel({ ...newModel, model_id: "" }); load(); })}
            className="rounded bg-cyan-400/20 px-3 py-1.5 text-xs font-bold text-cyan-100">+ Add model</button>
        </div>
      </Card>

      <Card title="Capability benchmarks (measured)">
        {Object.keys(byModel).length === 0 ? (
          <div className="text-sm text-slate-500">No benchmarks yet — run <code className="text-cyan-300">neural-web benchmarks --run provider/model</code></div>
        ) : (
          Object.entries(byModel).map(([ref, rows]) => (
            <div key={ref} className="mb-4">
              <div className="mb-1 text-sm font-semibold text-slate-200">{ref}</div>
              {rows.map((b) => (
                <div key={b.capability} className="flex items-center gap-3 py-1 text-xs">
                  <span className="w-52 text-slate-400">{b.capability}</span>
                  <div className="flex-1"><Bar used={b.score} limit={10} /></div>
                  <Badge status={b.source} />
                </div>
              ))}
            </div>
          ))
        )}
      </Card>
    </div>
  );
}
