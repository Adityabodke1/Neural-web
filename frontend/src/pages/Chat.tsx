import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { getWorkspace, onWorkspace, setWorkspace, shortPath } from "../workspace";

interface Turn {
  ts: string;
  role: "user" | "assistant";
  content: string;
  session?: string;
}

interface Msg {
  id: number;
  role: "user" | "assistant" | "error";
  text: string;
  model?: string;
}

interface ModelRef {
  ref: string;
  provider: string;
  model: string;
  default?: boolean;
}

interface Step {
  step: string;
  status: "pending" | "in_progress" | "completed";
}

interface Line {
  kind: "note" | "tool" | "cmd" | "warn" | "err";
  text: string;
}

interface Run {
  lines: Line[];
  plan: Step[];
  running: boolean;
}

interface BrowseData {
  path: string;
  parent: string;
  dirs: { name: string; path: string }[];
  roots: { name: string; path: string }[];
  authorized: string[];
}

const MODEL_KEY = "nw.model";

const VERB: Record<string, string> = {
  read_file: "Reading", write_file: "Writing", edit_file: "Editing",
  list_directory: "Listing", create_directory: "Creating folder",
  execute_command: "Running", run_tests: "Testing", build_project: "Building",
  install_dependency: "Installing", inspect_project: "Inspecting",
  update_plan: "Planning", git_checkpoint: "Saving checkpoint",
  git_diff: "Checking changes", search_files: "Searching",
  finish_task: "Wrapping up",
};

function target(args: Record<string, unknown> = {}): string {
  for (const k of ["path", "command", "app", "url", "name"]) {
    const v = args[k];
    if (v) return String(v).replace(/\n/g, " ").slice(0, 70);
  }
  return "";
}

export default function Chat() {
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"chat" | "agent">("chat");
  const [workspace, setWsState] = useState(getWorkspace());
  const [models, setModels] = useState<ModelRef[]>([]);
  const [model, setModel] = useState(() => localStorage.getItem(MODEL_KEY) || "");
  const [run, setRun] = useState<Run | null>(null);
  const [picker, setPicker] = useState(false);
  const [notice, setNotice] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(1);

  useEffect(() => {
    const off = onWorkspace((p) => setWsState(p));
    load();
    return off;
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, run?.lines.length, run?.plan.length]);

  async function load() {
    try {
      const h = await api.get<{ turns: Turn[] }>("/chat/history?limit=40");
      setMessages(
        (h.turns || []).map((t) => ({
          id: idRef.current++,
          role: t.role,
          text: t.content,
        }))
      );
    } catch { /* first run */ }
    try {
      const m = await api.get<{ model: string; models: ModelRef[] }>("/chat/meta");
      const list = m.models || [];
      setModels(list);
      const saved = localStorage.getItem(MODEL_KEY) || "";
      setModel(list.some((x) => x.ref === saved) ? saved : m.model || "");
    } catch { /* ignore */ }
    if (!getWorkspace()) {
      try {
        const f = await api.get<{ folders: { path: string; active: boolean }[] }>("/computer/folders");
        const first = f.folders?.find((x) => x.active);
        if (first) setWorkspace(first.path);
      } catch { /* ignore */ }
    }
  }

  function push(role: Msg["role"], text: string, modelRef?: string) {
    setMessages((m) => [...m, { id: idRef.current++, role, text, model: modelRef }]);
  }

  function switchModel(ref: string) {
    setModel(ref);
    localStorage.setItem(MODEL_KEY, ref);
    setNotice("");
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setNotice("");
    setInput("");
    push("user", text);
    setBusy(true);
    if (mode === "chat") {
      try {
        const r = await api.post<{ reply: string; model?: string }>("/chat", {
          message: text,
          workspace,
          model,
        });
        push("assistant", r.reply, r.model);
        if (r.model) {
          setModel(r.model);
          localStorage.setItem(MODEL_KEY, r.model);
        }
      } catch (e) {
        push("error", String(e));
      } finally {
        setBusy(false);
      }
      return;
    }
    // agent mode
    if (!workspace) {
      push("error", "Choose a folder first — the folder button below the chat box.");
      setBusy(false);
      setPicker(true);
      return;
    }
    try {
      const res = await api.post<{ task_id: number; progress_session: string }>(
        "/computer/tasks/start",
        { prompt: text, workspace, max_steps: 80, model: model || "auto" }
      );
      setRun({ lines: [], plan: [], running: true });
      const es = new EventSource(`/api/research/${res.progress_session}/events`);
      es.onmessage = (ev) => {
        const msg = JSON.parse(ev.data);
        const p = msg.payload || {};
        if (msg.type === "note" && p.text) {
          setRun((r) => r && { ...r, lines: [...r.lines.slice(-300), { kind: "note", text: p.text }] });
        } else if (msg.type === "tool" && p.tool) {
          const line = `${VERB[p.tool] || p.tool} ${target(p.args)}`.trim();
          setRun((r) => r && { ...r, lines: [...r.lines.slice(-300), { kind: "tool", text: line }] });
        } else if (msg.type === "command" && p.command) {
          const line = `$ ${p.command}${p.exit_code !== undefined && p.exit_code !== null ? `   exit=${p.exit_code}` : ""}`;
          setRun((r) => r && { ...r, lines: [...r.lines.slice(-300), { kind: "cmd", text: line }] });
        } else if (msg.type === "plan_update" && Array.isArray(p.steps)) {
          setRun((r) => r && { ...r, plan: p.steps });
        } else if (msg.type === "warning" && p.message) {
          setRun((r) => r && { ...r, lines: [...r.lines.slice(-300), { kind: "warn", text: p.message }] });
        } else if (msg.type === "error" && p.message) {
          setRun((r) => r && { ...r, lines: [...r.lines.slice(-300), { kind: "err", text: p.message }] });
        } else if (msg.type === "done") {
          es.close();
          setRun((r) => r && { ...r, running: false });
          const summary = p.summary ? String(p.summary) : "";
          push("assistant", summary || `Agent finished: ${p.status || "done"}`);
          setBusy(false);
          setRun(null);
        }
      };
      es.onerror = () => {
        es.close();
        setRun((r) => r && { ...r, running: false });
        setBusy(false);
      };
    } catch (e) {
      push("error", String(e));
      setBusy(false);
    }
  }

  async function newChat() {
    if (!confirm("Start a new chat? This clears the app conversation — your terminal and voice history stay saved.")) return;
    try { await api.del("/chat/history"); } catch { /* ignore */ }
    setMessages([]);
  }

  const grouped = useMemo(() => {
    const byProvider = new Map<string, ModelRef[]>();
    for (const m of models) {
      if (m.ref.includes(":batch")) continue;
      const list = byProvider.get(m.provider) || [];
      list.push(m);
      byProvider.set(m.provider, list);
    }
    return [...byProvider.entries()];
  }, [models]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* header */}
      <div className="flex items-center gap-3 border-b px-6 py-3" style={{ borderColor: "var(--border)" }}>
        <div>
          <div className="text-sm font-semibold">Chat</div>
          <div className="text-xs" style={{ color: "var(--faint)" }}>
            one memory shared with the terminal shell
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <select
            className="input !w-auto max-w-[260px]"
            value={model}
            onChange={(e) => switchModel(e.target.value)}
            title={model}
          >
            {!model && <option value="">model</option>}
            {grouped.map(([provider, list]) => (
              <optgroup key={provider} label={provider}>
                {list.map((m) => (
                  <option key={m.ref} value={m.ref}>{m.model}</option>
                ))}
              </optgroup>
            ))}
          </select>
          <button className="btn btn-ghost" onClick={newChat} title="Clear app conversation">New chat</button>
        </div>
      </div>

      {/* messages */}
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="mx-auto flex max-w-3xl flex-col gap-5">
          {messages.length === 0 && !run && (
            <Empty onPick={() => setPicker(true)} workspace={workspace} />
          )}
          {messages.map((m) => (
            <Message key={m.id} msg={m} />
          ))}
          {run && <AgentRun run={run} />}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* composer */}
      <div className="border-t px-6 py-4" style={{ borderColor: "var(--border)", background: "var(--bg)" }}>
        <div className="mx-auto max-w-3xl">
          {notice && <div className="mb-2 text-xs" style={{ color: "var(--warn)" }}>{notice}</div>}
          <div className="panel p-3">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={2}
              placeholder={mode === "agent" ? "Describe what to build — she plans, writes, tests and fixes it…" : "Message NEURAL WEB…"}
              className="w-full resize-none bg-transparent text-sm outline-none"
              style={{ color: "var(--text)" }}
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                className="flex max-w-[48%] items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs"
                style={{ borderColor: "var(--border-strong)", color: workspace ? "var(--text)" : "var(--muted)" }}
                onClick={() => setPicker(true)}
                title="Choose the folder she builds in"
              >
                <span aria-hidden>▭</span>
                <span className="truncate mono">{workspace ? shortPath(workspace) : "Choose folder"}</span>
              </button>
              <div className="flex overflow-hidden rounded-md border" style={{ borderColor: "var(--border-strong)" }}>
                {(["chat", "agent"] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setMode(m)}
                    className="px-2.5 py-1.5 text-xs capitalize"
                    style={{
                      background: mode === m ? "var(--panel-3)" : "transparent",
                      color: mode === m ? "var(--text)" : "var(--muted)",
                    }}
                  >
                    {m}
                  </button>
                ))}
              </div>
              {mode === "agent" && (
                <span className="text-xs" style={{ color: "var(--faint)" }}>
                  builds for real, tests and verifies inside the folder
                </span>
              )}
              <button className="btn btn-primary ml-auto" onClick={send} disabled={busy || !input.trim()}>
                {busy ? "Working…" : mode === "agent" ? "Build" : "Send"}
              </button>
            </div>
          </div>
          <div className="mt-2 text-center text-[11px]" style={{ color: "var(--faint)" }}>
            Enter to send · Shift+Enter for a new line · conversations are saved to ~/.neuralweb/conversations.jsonl
          </div>
        </div>
      </div>

      {picker && (
        <FolderPicker
          initial={workspace}
          onClose={() => setPicker(false)}
          onPick={(p) => {
            setWorkspace(p);
            setWsState(p);
            setPicker(false);
            setNotice("");
          }}
        />
      )}
    </div>
  );
}

/* ---------------------------------------------------------------- pieces */

function Message({ msg }: { msg: Msg }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg px-4 py-2.5 text-sm"
          style={{ background: "var(--panel-3)" }}>
          {msg.text}
        </div>
      </div>
    );
  }
  if (msg.role === "error") {
    return (
      <div className="rounded-lg border px-4 py-2.5 text-sm"
        style={{ borderColor: "rgba(224,108,117,0.4)", color: "var(--err)", background: "rgba(224,108,117,0.06)" }}>
        {msg.text}
      </div>
    );
  }
  return (
    <div>
      <RichText text={msg.text} />
      {msg.model && (
        <div className="mt-1.5 text-[11px]" style={{ color: "var(--faint)" }}>
          {msg.model}
        </div>
      )}
    </div>
  );
}

function RichText({ text }: { text: string }) {
  const parts = text.split(/```/);
  return (
    <div className="whitespace-pre-wrap text-sm leading-relaxed">
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre key={i} className="mono my-2 overflow-x-auto rounded-md border p-3 text-xs"
            style={{ borderColor: "var(--border)", background: "var(--panel-2)" }}>
            {part.replace(/^[a-zA-Z0-9_-]*\n/, "")}
          </pre>
        ) : (
          <span key={i}>{inline(part)}</span>
        )
      )}
    </div>
  );
}

function inline(text: string) {
  const bits = text.split("`");
  return bits.map((b, i) =>
    i % 2 === 1 ? (
      <code key={i} className="mono rounded px-1 py-0.5 text-[12.5px]"
        style={{ background: "var(--panel-3)" }}>{b}</code>
    ) : (
      <span key={i}>{b}</span>
    )
  );
}

function AgentRun({ run }: { run: Run }) {
  return (
    <div className="panel p-4">
      <div className="mb-2 flex items-center gap-2 text-xs" style={{ color: "var(--muted)" }}>
        <span className="inline-block h-2 w-2 rounded-full"
          style={{ background: run.running ? "var(--accent)" : "var(--ok)" }} />
        {run.running ? "Agent working" : "Agent finished"}
      </div>
      {run.plan.length > 0 && (
        <div className="mb-3 rounded-md border p-3" style={{ borderColor: "var(--border)" }}>
          {run.plan.map((s, i) => (
            <div key={i} className="flex items-start gap-2 py-0.5 text-xs">
              <span style={{ color: s.status === "completed" ? "var(--ok)" : s.status === "in_progress" ? "var(--accent)" : "var(--faint)" }}>
                {s.status === "completed" ? "☑" : s.status === "in_progress" ? "◐" : "☐"}
              </span>
              <span style={{ color: s.status === "completed" ? "var(--faint)" : "var(--text)" }}>{s.step}</span>
            </div>
          ))}
        </div>
      )}
      <div className="mono max-h-72 overflow-y-auto text-xs leading-6">
        {run.lines.map((l, i) => (
          <div key={i} style={{
            color: l.kind === "err" ? "var(--err)" : l.kind === "warn" ? "var(--warn)"
              : l.kind === "note" ? "var(--text)" : l.kind === "cmd" ? "var(--muted)" : "var(--muted)",
          }}>
            {l.kind === "tool" ? "· " : l.kind === "note" ? "" : l.kind === "warn" ? "⚠ " : l.kind === "err" ? "✗ " : ""}
            {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function Empty({ onPick, workspace }: { onPick: () => void; workspace: string }) {
  return (
    <div className="mt-16 text-center">
      <div className="text-lg font-semibold">What are we building?</div>
      <div className="mx-auto mt-1 max-w-md text-sm" style={{ color: "var(--muted)" }}>
        Ask anything, or switch to <b>Agent</b> and describe a project — she plans,
        writes the files, runs the tests and fixes failures.
      </div>
      <div className="mt-5 flex flex-wrap items-center justify-center gap-2 text-xs" style={{ color: "var(--faint)" }}>
        <span>Building in</span>
        <button className="btn" onClick={onPick}>
          {workspace ? shortPath(workspace) : "Choose folder"}
        </button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ folder picker */

function FolderPicker({
  initial,
  onClose,
  onPick,
}: {
  initial: string;
  onClose: () => void;
  onPick: (path: string) => void;
}) {
  const [data, setData] = useState<BrowseData | null>(null);
  const [path, setPath] = useState(initial || "");
  const [err, setErr] = useState("");
  const [newName, setNewName] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => { void load(path); }, [path]);

  async function load(p: string) {
    setErr("");
    try {
      const d = await api.get<BrowseData>(`/computer/browse?path=${encodeURIComponent(p || "")}`);
      setData(d);
      if (!p) setPath(d.path);
    } catch (e) {
      setErr(String(e));
    }
  }

  async function choose() {
    if (!data) return;
    setSaving(true);
    try {
      await api.post("/computer/folders", { path: data.path });
      onPick(data.path);
    } catch (e) {
      setErr(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function mkdir() {
    const name = newName.trim();
    if (!name || !data) return;
    try {
      const r = await api.post<{ path: string }>("/computer/browse/mkdir", {
        path: `${data.path}/${name}`,
      });
      setNewName("");
      setPath(r.path);
      setData(null);
    } catch (e) {
      setErr(String(e));
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.55)" }} onClick={onClose}>
      <div className="panel flex max-h-[80vh] w-full max-w-xl flex-col p-0"
        onClick={(e) => e.stopPropagation()}>
        <div className="border-b px-4 py-3" style={{ borderColor: "var(--border)" }}>
          <div className="text-sm font-semibold">Choose a folder</div>
          <div className="mono mt-1 truncate text-xs" style={{ color: "var(--muted)" }}>
            {data?.path || path || "…"}
          </div>
        </div>

        <div className="flex flex-wrap gap-1 border-b px-4 py-2" style={{ borderColor: "var(--border)" }}>
          {(data?.roots || []).map((r) => (
            <button key={r.path} className="btn btn-ghost !px-2 !py-1 !text-xs"
              onClick={() => setPath(r.path)}>{r.name}</button>
          ))}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          {data?.parent && (
            <button className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm hover:bg-[var(--panel-2)]"
              onClick={() => setPath(data.parent)}>
              <span style={{ color: "var(--muted)" }}>↰</span> ..
            </button>
          )}
          {(data?.dirs || []).map((d) => {
            const isAuth = data?.authorized.includes(d.path);
            return (
              <button key={d.path}
                className="flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-left text-sm hover:bg-[var(--panel-2)]"
                onClick={() => setPath(d.path)}>
                <span style={{ color: "var(--faint)" }}>▸</span>
                <span className="truncate">{d.name}</span>
                {isAuth && <span className="ml-auto text-[10px]" style={{ color: "var(--ok)" }}>authorized</span>}
              </button>
            );
          })}
          {data && data.dirs.length === 0 && (
            <div className="px-3 py-4 text-xs" style={{ color: "var(--faint)" }}>no subfolders</div>
          )}
        </div>

        <div className="border-t px-4 py-3" style={{ borderColor: "var(--border)" }}>
          {err && <div className="mb-2 text-xs" style={{ color: "var(--err)" }}>{err}</div>}
          <div className="mb-3 flex gap-2">
            <input className="input" placeholder="new folder name"
              value={newName} onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void mkdir(); }} />
            <button className="btn" onClick={mkdir} disabled={!newName.trim()}>Create</button>
          </div>
          <div className="flex items-center justify-end gap-2">
            <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" onClick={choose} disabled={!data || saving}>
              {saving ? "Saving…" : "Use this folder"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
