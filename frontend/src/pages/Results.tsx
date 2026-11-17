import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type ResearchRecord, type SessionDetail } from "../api";
import { Badge, Card } from "../components/ui";

export default function Results() {
  const { sessionId } = useParams();
  const [sessions, setSessions] = useState<{ id: string; prompt: string }[]>([]);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState<ResearchRecord | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api.get<{ sessions: { id: string; prompt: string }[] }>("/history").then((r) => {
      setSessions(r.sessions);
      if (!sessionId && r.sessions.length) {
        load(r.sessions[0].id);
      }
    }).catch((e) => setErr(String(e)));
    if (sessionId) load(sessionId);
  }, [sessionId]);

  function load(id: string) {
    setDetail(null);
    api.get<SessionDetail>(`/research/${id}`).then(setDetail).catch((e) => setErr(String(e)));
  }

  const records: ResearchRecord[] = (detail?.records ?? []).filter((r) => {
    if (!filter) return true;
    const hay = JSON.stringify(r.data).toLowerCase();
    return hay.includes(filter.toLowerCase());
  });

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">Results</h1>
      {err && <div className="glass p-3 text-sm text-rose-300">{err}</div>}
      <Card>
        <div className="flex flex-wrap gap-3">
          <select
            value={sessionId ?? ""}
            onChange={(e) => load(e.target.value)}
            className="max-w-xs flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-cyan-400/40"
          >
            <option value="">— select session —</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.prompt.slice(0, 60)}
              </option>
            ))}
          </select>
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="filter records…"
            className="w-48 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-cyan-400/40"
          />
          <div className="flex-1" />
          {detail && (
            <div className="flex gap-2">
              {(["json", "csv", "xlsx"] as const).map((f) => (
                <a
                  key={f}
                  href={`/api/research/${detail.session.id}/export?format=${f}`}
                  className="rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-400/20"
                >
                  ⭳ {f.toUpperCase()}
                </a>
              ))}
            </div>
          )}
        </div>
      </Card>

      {detail && (
        <>
          <Card title="Session">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge status={detail.session.status} />
              <span className="text-slate-300">{detail.session.prompt}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-500">
              <span>depth: {detail.session.depth}</span>
              <span>pages: {detail.session.pages_scraped}</span>
              <span>records: {detail.session.records_found}</span>
              <span>queries: {detail.queries.length}</span>
            </div>
          </Card>

          {detail.session.report_md && (
            <Card title="Report">
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-slate-300">
                {detail.session.report_md}
              </pre>
            </Card>
          )}

          <Card title={`Extracted records (${records.length}) — every field carries its source`}>
            {records.length === 0 ? (
              <div className="text-sm text-slate-500">No records extracted in this session.</div>
            ) : (
              <div className="space-y-2">
                {records.map((r) => (
                  <div key={r.id} className="rounded-lg border border-white/5 bg-white/[0.03]">
                    <button
                      onClick={() => setExpanded(expanded?.id === r.id ? null : r)}
                      className="flex w-full items-center gap-3 p-3 text-left text-sm hover:bg-white/[0.02]"
                    >
                      <span className="font-semibold text-slate-100">
                        {r.data.entity_name ?? "(no entity)"}
                      </span>
                      <Badge status={r.verification} />
                      <span className="ml-auto text-xs text-slate-500">
                        conf {r.confidence.toFixed(2)} · {Object.values(r.data.fields).filter((f) => f.value != null).length} fields
                      </span>
                      <span className="text-cyan-400/60">{expanded?.id === r.id ? "▾" : "▸"}</span>
                    </button>
                    {expanded?.id === r.id && (
                      <div className="slide-in border-t border-white/5 p-3">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="text-left text-slate-500">
                              <th className="py-1 pr-3">field</th>
                              <th className="py-1 pr-3">value</th>
                              <th className="py-1 pr-3">verification</th>
                              <th className="py-1">source / evidence</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(r.data.fields).map(([k, f]) => (
                              <tr key={k} className="border-t border-white/5 align-top">
                                <td className="py-1.5 pr-3 font-medium text-cyan-200/80">{k}</td>
                                <td className="py-1.5 pr-3 text-slate-200">
                                  {f.value == null ? <span className="text-slate-600">null</span> : String(f.value)}
                                </td>
                                <td className="py-1.5 pr-3"><Badge status={f.verification} /></td>
                                <td className="py-1.5">
                                  {f.source_url && (
                                    <a href={f.source_url} target="_blank" rel="noreferrer"
                                       className="block truncate text-sky-300 underline">
                                      {f.source_url}
                                    </a>
                                  )}
                                  {f.evidence && (
                                    <div className="mt-0.5 text-slate-500">“{f.evidence}”</div>
                                  )}
                                  {f.cross_check_source && (
                                    <div className="mt-0.5 text-emerald-400/70">
                                      ✓ cross-check: {f.cross_check_source}
                                    </div>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
      {!detail && !err && <div className="text-sm text-slate-500">Select a session above.</div>}
    </div>
  );
}
