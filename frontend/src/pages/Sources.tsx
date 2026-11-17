import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Card } from "../components/ui";

interface SourceRow {
  id: number;
  url: string;
  final_url: string;
  title: string;
  domain: string;
  status: string;
  method: string;
  http_status: number | null;
  word_count: number;
  content_hash: string;
  scraped_at: string;
  session_prompt: string | null;
}

export default function Sources() {
  const [rows, setRows] = useState<SourceRow[]>([]);
  const [domain, setDomain] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    load();
  }, []);

  function load() {
    const q = new URLSearchParams();
    if (domain) q.set("domain", domain);
    api.get<{ sources: SourceRow[] }>(`/sources?${q}`).then((r) => setRows(r.sources))
      .catch((e) => setErr(String(e)));
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">Sources</h1>
      <p className="text-sm text-slate-400">
        Full provenance: original URL, final URL after redirects, retrieval method, HTTP status,
        content hash and timestamp for every scraped page.
      </p>
      <Card>
        <div className="flex gap-3">
          <input
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
            placeholder="filter by domain…"
            className="w-64 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-cyan-400/40"
          />
          <button onClick={load}
            className="rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-xs font-semibold text-cyan-200">
            Filter
          </button>
        </div>
      </Card>
      {err && <div className="glass p-3 text-sm text-rose-300">{err}</div>}
      <Card title={`${rows.length} stored sources`}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-slate-500">
              <tr>
                <th className="py-2 pr-3">Title</th>
                <th className="py-2 pr-3">Final URL</th>
                <th className="py-2 pr-3">Domain</th>
                <th className="py-2 pr-3">Method</th>
                <th className="py-2 pr-3">HTTP</th>
                <th className="py-2 pr-3">Words</th>
                <th className="py-2 pr-3">Hash</th>
                <th className="py-2">Retrieved</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                  <td className="max-w-[220px] truncate py-2 pr-3 text-slate-200">{s.title || "—"}</td>
                  <td className="max-w-[260px] py-2 pr-3">
                    <a href={s.final_url || s.url} target="_blank" rel="noreferrer"
                       className="block truncate text-sky-300 underline">{s.final_url || s.url}</a>
                  </td>
                  <td className="py-2 pr-3 text-slate-400">{s.domain}</td>
                  <td className="py-2 pr-3 text-slate-400">{s.method || "local"}</td>
                  <td className="py-2 pr-3"><Badge status={s.status} /> {s.http_status ? <span className="text-slate-500">{s.http_status}</span> : null}</td>
                  <td className="py-2 pr-3 text-slate-400">{s.word_count}</td>
                  <td className="py-2 pr-3 font-mono text-slate-600">{s.content_hash?.slice(0, 10)}…</td>
                  <td className="py-2 text-slate-500">{s.scraped_at?.slice(0, 16)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <div className="py-6 text-center text-sm text-slate-500">No sources stored yet.</div>}
        </div>
      </Card>
    </div>
  );
}
