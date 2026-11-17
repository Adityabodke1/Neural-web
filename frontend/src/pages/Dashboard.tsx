import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Card, Stat } from "../components/ui";

interface Stats {
  sessions: number;
  sessions_ok: number;
  pages_scraped: number;
  urls_discovered: number;
  records_extracted: number;
  ai_calls: number;
  ai_tokens: number;
  ai_failures: number;
  usage: { total_tokens: number; total_calls: number };
  sandbox?: { mode: string; backend: string };
}

export default function Dashboard() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [recent, setRecent] = useState<{ id: string; prompt: string; status: string }[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<Stats>("/research/stats/dashboard").then(setStats).catch((e) => setErr(String(e)));
    api.get<{ sessions: { id: string; prompt: string; status: string }[] }>("/history?limit=5")
      .then((r) => setRecent(r.sessions))
      .catch(() => {});
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-100">Research Dashboard</h1>
        <p className="text-sm text-slate-400">
          Autonomous web intelligence — every result traceable to its source.
        </p>
      </div>
      {err && <div className="glass border-rose-400/30 p-3 text-sm text-rose-300">{err}</div>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Research sessions" value={stats?.sessions ?? "—"} />
        <Stat label="Pages scraped" value={stats?.pages_scraped ?? "—"} accent="text-cyan-300" />
        <Stat label="Sources discovered" value={stats?.urls_discovered ?? "—"} accent="text-sky-300" />
        <Stat label="Extractions" value={stats?.records_extracted ?? "—"} accent="text-emerald-300" />
        <Stat label="AI calls" value={stats?.ai_calls ?? "—"} />
        <Stat label="AI tokens" value={(stats?.ai_tokens ?? 0).toLocaleString()} accent="text-fuchsia-300" />
        <Stat label="AI failures" value={stats?.ai_failures ?? "—"} accent="text-rose-300" />
        <Stat
          label="Sandbox"
          value={stats?.sandbox ? `${stats.sandbox.mode}` : "—"}
          accent="text-amber-300"
        />
      </div>
      <Card title="Recent research">
        {recent.length === 0 ? (
          <div className="text-sm text-slate-400">
            No research yet.{" "}
            <Link to="/research" className="text-cyan-300 underline">
              Start your first session →
            </Link>
          </div>
        ) : (
          <div className="space-y-2">
            {recent.map((s) => (
              <Link
                key={s.id}
                to={`/results/${s.id}`}
                className="slide-in block rounded-lg border border-white/5 bg-white/[0.03] p-3 text-sm transition-colors hover:border-cyan-400/30"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-slate-200">{s.prompt}</span>
                  <span className="shrink-0 text-xs text-slate-500">{s.status}</span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
