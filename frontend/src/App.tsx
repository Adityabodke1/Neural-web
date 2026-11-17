import { NavLink, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Research from "./pages/Research";
import Results from "./pages/Results";
import Sources from "./pages/Sources";
import Models from "./pages/Models";
import History from "./pages/History";
import Agent from "./pages/Agent";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Dashboard", icon: "◎" },
  { to: "/research", label: "New Research", icon: "✦" },
  { to: "/results", label: "Results", icon: "▦" },
  { to: "/sources", label: "Sources", icon: "⛓" },
  { to: "/agent", label: "Coding Agent", icon: "⌘" },
  { to: "/models", label: "Models & Quotas", icon: "◈" },
  { to: "/history", label: "History", icon: "◷" },
  { to: "/settings", label: "Settings", icon: "⚙" },
];

export default function App() {
  return (
    <div className="flex min-h-screen">
      <aside className="glass sticky top-0 z-10 hidden h-screen w-60 shrink-0 flex-col p-4 md:flex">
        <div className="mb-6 px-2">
          <div className="bg-gradient-to-r from-cyan-300 via-sky-400 to-fuchsia-400 bg-clip-text text-xl font-black tracking-tight text-transparent">
            NEURAL WEB
          </div>
          <div className="mt-0.5 text-[10px] font-semibold uppercase tracking-[0.25em] text-cyan-200/60">
            Web Intelligence Engine
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-1">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all ${
                  isActive
                    ? "bg-cyan-400/10 font-semibold text-cyan-200 shadow-[inset_0_0_0_1px_rgba(103,232,249,0.25)]"
                    : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
                }`
              }
            >
              <span className="w-4 text-center text-cyan-300/80">{n.icon}</span>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-4 border-t border-cyan-400/10 pt-3 text-center">
          <div className="text-[10px] uppercase tracking-[0.3em] text-slate-500">Created by</div>
          <div className="bg-gradient-to-r from-cyan-300 to-fuchsia-400 bg-clip-text font-bold text-transparent">
            ADITYA BODKE
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1 p-4 md:p-8">
        {/* mobile header */}
        <div className="mb-4 flex items-center justify-between md:hidden">
          <div className="bg-gradient-to-r from-cyan-300 to-fuchsia-400 bg-clip-text text-lg font-black text-transparent">
            NEURAL WEB
          </div>
          <div className="text-right text-[10px] uppercase tracking-[0.2em] text-slate-500">
            ADITYA BODKE
          </div>
        </div>
        <div className="mb-4 flex flex-wrap gap-2 md:hidden">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              className={({ isActive }) =>
                `glass px-3 py-1.5 text-xs ${isActive ? "text-cyan-200" : "text-slate-400"}`
              }
            >
              {n.label}
            </NavLink>
          ))}
        </div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/research" element={<Research />} />
          <Route path="/results" element={<Results />} />
          <Route path="/results/:sessionId" element={<Results />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/agent" element={<Agent />} />
          <Route path="/models" element={<Models />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
        <footer className="mt-10 border-t border-cyan-400/10 pt-4 text-center text-xs text-slate-500">
          NEURAL WEB · created by <span className="font-semibold text-fuchsia-300">ADITYA BODKE</span> ·
          autonomous research · real scraping · source-backed extraction
        </footer>
      </main>
    </div>
  );
}
