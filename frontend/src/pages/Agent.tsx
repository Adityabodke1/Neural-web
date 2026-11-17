import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Card } from "../components/ui";

interface TaskRow {
  id: number;
  prompt: string;
  workspace: string;
  status: string;
  files_touched: number;
  commands_run: number;
  summary: string | null;
  error: string | null;
  created_at: string;
}

interface Folder {
  path: string;
  os: string;
  active: boolean;
  files_accessed: number;
  last_activity: string | null;
}

export default function Agent() {
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [prompt, setPrompt] = useState("");
  const [ws, setWs] = useState("");
  const [running, setRunning] = useState(false);
  const [live, setLive] = useState<string[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => { load(); }, []);
  function load() {
    api.get<{ tasks: TaskRow[] }>("/computer/tasks").then((r) => setTasks(r.tasks)).catch(() => {});
    api.get<{ folders: Folder[] }>("/computer/folders").then((r) => setFolders(r.folders)).catch(() => {});
  }

  async function start() {
    setErr("");
    setRunning(true);
    setLive([]);
    try {
      const res = await api.post<{ task_id: number; progress_session: string }>(
        "/computer/tasks/start", { prompt, workspace: ws });
      const es = new EventSource(`/api/research/${res.progress_session}/events`);
      es.onmessage = (m) => {
        const evt = JSON.parse(m.data);
        const line =
          evt.type === "tool" ? `· ${evt.payload.tool} ${JSON.stringify(evt.payload.args).slice(0, 100)}` :
          evt.type === "command" ? `$ ${evt.payload.command}  exit=${evt.payload.exit_code}` :
          evt.type === "step" ? `» ${evt.payload.step} ${evt.payload.action ?? ""}` :
          evt.type === "done" ? `■ ${evt.payload.status}` :
          evt.type === "error" ? `[ERROR] ${evt.payload.message}` : null;
        if (line) setLive((p) => [...p.slice(-200), line]);
        if (evt.type === "done") { es.close(); setRunning(false); load(); }
      };
      es.onerror = () => { es.close(); setRunning(false); };
    } catch (e) {
      setErr(String(e));
      setRunning(false);
    }
  }

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold text-slate-100">Coding Agent</h1>
      <p className="text-sm text-slate-400">
        Builds real projects inside <span className="text-cyan-300">authorized folders only</span>.
        All commands run in the OS sandbox, are audited, and never run as root.
      </p>

      <Card title="Authorized folders (user-controlled access)">
        <div className="flex flex-wrap gap-2">
          {folders.filter((f) => f.active).map((f) => (
            <div key={f.path} className="flex items-center gap-2 rounded-lg border border-cyan-400/20 bg-cyan-400/5 px-3 py-1.5 text-xs">
              <span className="font-mono text-cyan-200">{f.path}</span>
              <span className="text-slate-500">{f.os}</span>
              <button onClick={() => api.del(`/computer/folders?path=${encodeURIComponent(f.path)}`).then(load)}
                className="text-rose-300/70 hover:text-rose-300">revoke</button>
            </div>
          ))}
        </div>
        <AddFolder onAdded={load} />
      </Card>

      <Card title="New coding task">
        <textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={3}
          placeholder={'e.g. "Build a modern portfolio website using React and Tailwind" or "Fix the failing tests in this project"'}
          className="w-full resize-none rounded-lg border border-white/10 bg-black/30 p-3 text-sm outline-none focus:border-cyan-400/40"
        />
        <div className="mt-3 flex gap-2">
          <select value={ws} onChange={(e) => setWs(e.target.value)}
            className="flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm outline-none">
            <option value="">— workspace —</option>
            {folders.filter((f) => f.active).map((f) => (
              <option key={f.path} value={f.path}>{f.path}</option>
            ))}
          </select>
          <button
            disabled={running || !prompt.trim() || !ws}
            onClick={start}
            className="rounded-lg bg-gradient-to-r from-fuchsia-500 to-cyan-500 px-6 py-2 text-sm font-bold text-slate-950 disabled:opacity-40">
            ▶ Run agent
          </button>
        </div>
        {err && <div className="mt-2 text-sm text-rose-300">{err}</div>}
      </Card>

      {live.length > 0 && (
        <Card title="Live execution (real tool calls, sandboxed)">
          <div className="max-h-80 overflow-y-auto rounded-lg border border-white/5 bg-black/40 p-3 font-mono text-xs">
            {live.map((l, i) => <div key={i} className="slide-in text-slate-300">{l}</div>)}
          </div>
        </Card>
      )}

      <Card title={`Task history (${tasks.length})`}>
        <div className="space-y-2">
          {tasks.map((t) => (
            <div key={t.id} className="rounded-lg border border-white/5 bg-white/[0.03] p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-slate-100">#{t.id}</span>
                <Badge status={t.status} />
                <span className="font-mono text-xs text-cyan-300/70">{t.workspace}</span>
                <span className="ml-auto text-xs text-slate-500">{t.files_touched} files · {t.commands_run} cmds</span>
              </div>
              <div className="mt-1 text-xs text-slate-400">{t.prompt}</div>
              {t.summary && <div className="mt-1 text-xs text-slate-500">{t.summary.slice(0, 300)}</div>}
            </div>
          ))}
          {tasks.length === 0 && <div className="text-sm text-slate-500">No coding tasks yet.</div>}
        </div>
      </Card>
    </div>
  );
}

function AddFolder({ onAdded }: { onAdded: () => void }) {
  const [path, setPath] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="mt-3 flex gap-2">
      <input
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="/home/user/Projects"
        className="flex-1 rounded-lg border border-white/10 bg-black/30 px-3 py-2 text-sm font-mono outline-none focus:border-cyan-400/40"
      />
      <button
        onClick={() => api.post("/computer/folders", { path }).then(onAdded).catch((e) => setMsg(String(e)))}
        className="rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-4 py-2 text-xs font-semibold text-cyan-200">
        + Add authorized folder
      </button>
      {msg && <span className="text-xs text-rose-300">{msg}</span>}
    </div>
  );
}
