import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Card } from "../components/ui";

interface SessionRow {
  id: string;
  prompt: string;
  depth: string;
  status: string;
  pages_scraped: number;
  records_found: number;
  created_at: string;
  error?: string;
}

export default function History() {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [memq, setMemq] = useState("");
  const [mem, setMem] = useState<{ session_id: string; kind: string; text: string; score: number }[]>([]);

  useEffect(() => { load(); }, []);
  function load() {
    api.get<{ sessions: SessionRow[] }>("/history").then((r) => setRows(r.sessions)).catch(() => {});
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">Research History & Memory</h1>
      <Card title="Semantic memory search">
        <div className="flex gap-2">
          <input
            value={memq}
            onChange={(e) => setMemq(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && api
              .get<{ results: { session_id: string; kind: string; text: string; score: number }[] }>(`/memory/search?q=${encodeURIComponent(memq)}`)
              .then((r) => setMem(r.results))}
            placeholder="search previous research…"
            className="flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-cyan-400/40"
          />
          <button
            onClick={() => api
              .get<{ results: { session_id: string; kind: string; text: string; score: number }[] }>(`/memory/search?q=${encodeURIComponent(memq)}`)
              .then((r) => setMem(r.results))}
            className="rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-xs font-semibold text-cyan-200">
            Search
          </button>
        </div>
        {mem.length > 0 && (
          <div className="mt-3 space-y-1 text-xs">
            {mem.map((m, i) => (
              <div key={i} className="flex gap-3 rounded border border-white/5 bg-white/[0.03] p-2">
                <span className="text-cyan-300">{m.score.toFixed(3)}</span>
                <span className="text-slate-500">{m.kind}</span>
                <span className="flex-1 truncate text-slate-300">{m.text}</span>
                <Link className="text-sky-300 underline" to={`/results/${m.session_id}`}>open</Link>
              </div>
            ))}
          </div>
        )}
      </Card>
      <Card title={`${rows.length} sessions`}>
        <div className="space-y-2">
          {rows.map((s) => (
            <div key={s.id} className="flex flex-wrap items-center gap-3 rounded-lg border border-white/5 bg-white/[0.03] p-3 text-sm">
              <Link to={`/results/${s.id}`} className="flex-1 truncate text-slate-200 hover:text-cyan-200">
                {s.prompt}
              </Link>
              <span className="text-xs text-slate-500">{s.depth}</span>
              <span className={`text-xs ${s.status === "complete" ? "text-emerald-300" : s.status === "failed" ? "text-rose-300" : "text-cyan-300"}`}>{s.status}</span>
              <span className="text-xs text-slate-500">{s.pages_scraped}p · {s.records_found}r</span>
              <span className="text-xs text-slate-600">{s.created_at.slice(0, 16)}</span>
              <button onClick={() => api.del(`/history/${s.id}`).then(load)}
                className="rounded border border-rose-400/20 px-2 py-1 text-xs text-rose-300/70">delete</button>
            </div>
          ))}
          {rows.length === 0 && <div className="text-sm text-slate-500">No sessions yet.</div>}
        </div>
      </Card>
    </div>
  );
}
