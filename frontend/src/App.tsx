import { useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import Chat from "./pages/Chat";
import Dashboard from "./pages/Dashboard";
import Research from "./pages/Research";
import Results from "./pages/Results";
import Sources from "./pages/Sources";
import Models from "./pages/Models";
import History from "./pages/History";
import Agent from "./pages/Agent";
import Settings from "./pages/Settings";
import { getWorkspace, onWorkspace, shortPath } from "./workspace";

const NAV: { section: string; items: { to: string; label: string }[] }[] = [
  {
    section: "Workspace",
    items: [
      { to: "/", label: "Chat" },
      { to: "/agent", label: "Coding agent" },
    ],
  },
  {
    section: "Intelligence",
    items: [
      { to: "/research", label: "Research" },
      { to: "/results", label: "Results" },
      { to: "/sources", label: "Sources" },
      { to: "/dashboard", label: "Overview" },
    ],
  },
  {
    section: "System",
    items: [
      { to: "/models", label: "Models" },
      { to: "/history", label: "History" },
      { to: "/settings", label: "Settings" },
    ],
  },
];

const ALL = NAV.flatMap((n) => n.items);

export default function App() {
  const location = useLocation();
  const isChat = location.pathname === "/";
  const [ws, setWs] = useState(getWorkspace());

  useEffect(() => onWorkspace((p) => setWs(p)), []);

  return (
    <div className="flex h-full min-h-screen">
      {/* sidebar */}
      <aside className="hidden h-screen w-56 shrink-0 flex-col border-r px-3 py-4 md:flex"
        style={{ borderColor: "var(--border)", background: "var(--panel)" }}>
        <div className="px-2">
          <div className="text-[15px] font-semibold tracking-tight">NEURAL WEB</div>
          <div className="mt-0.5 text-[10px] uppercase tracking-[0.18em]" style={{ color: "var(--faint)" }}>
            Aditya Bodke
          </div>
        </div>

        <nav className="mt-6 flex flex-1 flex-col gap-5">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="mb-1 px-2 text-[10px] font-medium uppercase tracking-[0.14em]"
                style={{ color: "var(--faint)" }}>
                {group.section}
              </div>
              <div className="flex flex-col">
                {group.items.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.to === "/"}
                    className={({ isActive }) =>
                      `relative flex items-center rounded-md px-2 py-1.5 text-[13px] transition-colors ${
                        isActive ? "" : "hover:bg-[var(--panel-2)]"
                      }`
                    }
                    style={({ isActive }) => ({
                      background: isActive ? "var(--panel-3)" : "transparent",
                      color: isActive ? "var(--text)" : "var(--muted)",
                      fontWeight: isActive ? 600 : 400,
                    })}
                  >
                    {n.label}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}
        </nav>

        <button
          className="mt-3 rounded-md border px-2.5 py-2 text-left"
          style={{ borderColor: "var(--border)", background: "var(--panel-2)" }}
          onClick={() => (window.location.href = "/")}
          title={ws || "no folder selected"}
        >
          <div className="text-[10px] uppercase tracking-[0.14em]" style={{ color: "var(--faint)" }}>
            Building in
          </div>
          <div className="mono mt-0.5 truncate text-[11.5px]" style={{ color: ws ? "var(--text)" : "var(--muted)" }}>
            {ws ? shortPath(ws, 26) : "choose a folder"}
          </div>
        </button>

        <div className="mt-3 px-2 text-[10px]" style={{ color: "var(--faint)" }}>
          v2.0 · local · free-tier friendly
        </div>
      </aside>

      {/* main */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* mobile bar */}
        <div className="border-b px-4 py-2 md:hidden" style={{ borderColor: "var(--border)" }}>
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-sm font-semibold">NEURAL WEB</span>
            <span className="text-[10px] uppercase tracking-widest" style={{ color: "var(--faint)" }}>
              ADITYA BODKE
            </span>
          </div>
          <div className="flex gap-1 overflow-x-auto pb-1">
            {ALL.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}
                className="whitespace-nowrap rounded-md px-2 py-1 text-xs"
                style={({ isActive }) => ({
                  background: isActive ? "var(--panel-3)" : "var(--panel-2)",
                  color: isActive ? "var(--text)" : "var(--muted)",
                })}>
                {n.label}
              </NavLink>
            ))}
          </div>
        </div>

        <main className={isChat ? "h-0 flex-1 overflow-hidden" : "min-h-0 flex-1 overflow-y-auto"}>
          {isChat ? (
            <Chat />
          ) : (
            <div className="px-5 py-6 md:px-10 md:py-8">
              <Routes>
                <Route path="/agent" element={<Agent />} />
                <Route path="/research" element={<Research />} />
                <Route path="/results" element={<Results />} />
                <Route path="/results/:sessionId" element={<Results />} />
                <Route path="/sources" element={<Sources />} />
                <Route path="/models" element={<Models />} />
                <Route path="/history" element={<History />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/dashboard" element={<Dashboard />} />
              </Routes>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
