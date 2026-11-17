import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, openEventStream, type ProgressEvent } from "../api";
import { Badge, Card, Spinner } from "../components/ui";

const DEPTHS = ["quick", "standard", "deep"];

export default function Research() {
  const nav = useNavigate();
  const [prompt, setPrompt] = useState("");
  const [depth, setDepth] = useState("standard");
  const [maxPages, setMaxPages] = useState(10);
  const [fields, setFields] = useState("");
  const [domains, setDomains] = useState("");
  const [running, setRunning] = useState(false);
  const [sessionId, setSessionId] = useState("");
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [stopFlag, setStopFlag] = useState(false);
  const [err, setErr] = useState("");
  const esRef = useRef<EventSource | null>(null);
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [events]);

  function start() {
    setErr("");
    if (prompt.trim().length < 3) {
      setErr("Please describe your research request.");
      return;
    }
    setRunning(true);
    setEvents([]);
    api
      .post<{ session_id: string }>("/research/start", {
        prompt: prompt.trim(),
        depth,
        max_pages: maxPages,
        target_fields: fields.split(",").map((f) => f.trim()).filter(Boolean),
        include_domains: domains.split(",").map((d) => d.trim()).filter(Boolean),
      })
      .then(({ session_id }) => {
        setSessionId(session_id);
        esRef.current = openEventStream(
          session_id,
          (e) => setEvents((prev) => [...prev.slice(-400), e]),
          () => setRunning(false)
        );
      })
      .catch((e) => {
        setErr(String(e));
        setRunning(false);
      });
  }

  function stop() {
    setStopFlag(true);
    api.post(`/research/${sessionId}/stop`).catch(() => {});
  }

  function finish() {
    esRef.current?.close();
    setRunning(false);
    nav(`/results/${sessionId}`);
  }

  const step = events.filter((e) => e.type === "step").at(-1)?.payload as
    | { step?: string; action?: string }
    | undefined;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">New Research</h1>
      <Card>
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={'e.g. "Find 30 Indian AI startups and extract company name, website, founder, industry, location, product and contact email."'}
          rows={4}
          className="w-full resize-none rounded-lg border border-white/10 bg-black/30 p-4 text-sm text-slate-100 outline-none transition-colors placeholder:text-slate-600 focus:border-cyan-400/40"
        />
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wider text-slate-400">
              Research depth
            </label>
            <div className="flex gap-2">
              {DEPTHS.map((d) => (
                <button
                  key={d}
                  onClick={() => setDepth(d)}
                  className={`flex-1 rounded-lg border px-3 py-2 text-xs font-semibold capitalize transition-all ${
                    depth === d
                      ? "border-cyan-400/50 bg-cyan-400/10 text-cyan-200"
                      : "border-white/10 text-slate-400 hover:border-white/25"
                  }`}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wider text-slate-400">
              Max pages ({maxPages})
            </label>
            <input
              type="range"
              min={2}
              max={40}
              value={maxPages}
              onChange={(e) => setMaxPages(+e.target.value)}
              className="mt-2 w-full accent-cyan-400"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wider text-slate-400">
              Domain filter (optional, csv)
            </label>
            <input
              value={domains}
              onChange={(e) => setDomains(e.target.value)}
              placeholder="gov.in, .edu"
              className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-cyan-400/40"
            />
          </div>
        </div>
        <div className="mt-4">
          <label className="mb-1 block text-xs uppercase tracking-wider text-slate-400">
            Target fields (optional, csv)
          </label>
          <input
            value={fields}
            onChange={(e) => setFields(e.target.value)}
            placeholder="company name, website, founder, location, email"
            className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none focus:border-cyan-400/40"
          />
        </div>
        <div className="mt-5 flex gap-3">
          {!running ? (
            <button
              onClick={start}
              className="rounded-lg bg-gradient-to-r from-cyan-500 to-sky-500 px-6 py-2.5 text-sm font-bold text-slate-950 transition-all hover:shadow-[0_0_24px_rgba(34,211,238,0.4)]"
            >
              ▶ Start research
            </button>
          ) : (
            <>
              <button
                onClick={stop}
                disabled={stopFlag}
                className="rounded-lg border border-rose-400/40 bg-rose-400/10 px-6 py-2.5 text-sm font-bold text-rose-300 disabled:opacity-40"
              >
                ■ Stop
              </button>
              <button
                onClick={finish}
                className="rounded-lg border border-cyan-400/40 bg-cyan-400/10 px-6 py-2.5 text-sm font-bold text-cyan-200"
              >
                Go to results →
              </button>
            </>
          )}
        </div>
        {err && <div className="mt-3 text-sm text-rose-300">{err}</div>}
      </Card>

      {sessionId && (
        <Card title={running ? `Live progress — ${sessionId.slice(0, 8)}` : `Session ${sessionId.slice(0, 8)}`}>
          <div className="mb-3 flex items-center gap-3 text-sm">
            {running && <Spinner label="agent working" />}
            {step?.step && <Badge status={step.step} />}
            {step?.action && <span className="truncate text-slate-400">{step.action}</span>}
          </div>
          <div
            ref={feedRef}
            className="max-h-[420px] space-y-1 overflow-y-auto rounded-lg border border-white/5 bg-black/40 p-3 font-mono text-xs"
          >
            {events.map((e, i) => (
              <EventLine key={i} e={e} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

function EventLine({ e }: { e: ProgressEvent }) {
  const p = e.payload as Record<string, string | number>;
  let content: React.ReactNode = null;
  switch (e.type) {
    case "step":
      content = <span className="text-cyan-400/90">» {String(p.step)} <span className="text-slate-500">{String(p.action ?? "")}</span></span>;
      break;
    case "search":
      content = <span className="text-sky-300">[SEARCH] {String(p.query)} <span className="text-slate-500">→ {String(p.new_results)} new</span></span>;
      break;
    case "page_scraped":
      content = <span className="text-emerald-300">[+] {String(p.url)} <span className="text-slate-500">{String(p.words)} words · {String(p.method)}</span></span>;
      break;
    case "page_failed":
      content = <span className="text-rose-300">[✗] {String(p.url)} <span className="text-slate-500">{String(p.error ?? "")}</span></span>;
      break;
    case "record":
      content = <span className="text-fuchsia-300">[REC] {String(p.entity ?? p.url)} <span className="text-slate-500">#{String(p.record_id)}</span></span>;
      break;
    case "verification":
      content = <span className="text-amber-300">[VERIFY] record #{String(p.record_id)} → {String(p.status)}</span>;
      break;
    case "error":
      content = <span className="text-rose-300">[ERROR] {String(p.message)}</span>;
      break;
    case "done":
      content = <span className="font-bold text-emerald-300">■ {String(p.status)}</span>;
      break;
    default:
      content = <span className="text-slate-600">· {e.type}</span>;
  }
  return <div className="slide-in">{content}</div>;
}
