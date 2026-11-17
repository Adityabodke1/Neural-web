import type { ReactNode } from "react";

export function Card({
  children,
  className = "",
  title,
}: {
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <div className={`glass glass-hover p-5 ${className}`}>
      {title && (
        <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-cyan-200/70">
          {title}
        </div>
      )}
      {children}
    </div>
  );
}

export function Stat({ label, value, accent }: { label: string; value: ReactNode; accent?: string }) {
  return (
    <div className="glass glass-hover p-4">
      <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${accent ?? "text-slate-100"}`}>{value}</div>
    </div>
  );
}

export function Badge({ status }: { status: string }) {
  const map: Record<string, string> = {
    complete: "bg-emerald-400/10 text-emerald-300 border-emerald-400/30",
    verified: "bg-emerald-400/10 text-emerald-300 border-emerald-400/30",
    ok: "bg-emerald-400/10 text-emerald-300 border-emerald-400/30",
    running: "bg-cyan-400/10 text-cyan-300 border-cyan-400/30 pulse-dot",
    queued: "bg-sky-400/10 text-sky-300 border-sky-400/30",
    partial: "bg-amber-400/10 text-amber-300 border-amber-400/30",
    failed: "bg-rose-400/10 text-rose-300 border-rose-400/30",
    error: "bg-rose-400/10 text-rose-300 border-rose-400/30",
    contradicted: "bg-rose-400/10 text-rose-300 border-rose-400/30",
    blocked: "bg-amber-400/10 text-amber-300 border-amber-400/30",
    unverified: "bg-slate-400/10 text-slate-400 border-slate-400/20",
    missing: "bg-slate-400/10 text-slate-500 border-slate-500/20",
  };
  const cls = map[status] ?? "bg-slate-400/10 text-slate-400 border-slate-400/20";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {status}
    </span>
  );
}

export function Bar({ used, limit }: { used: number; limit: number | null }) {
  if (!limit) return <span className="text-xs text-slate-500">quota unavailable</span>;
  const pct = Math.min(100, (used / limit) * 100);
  const color = pct < 60 ? "bg-emerald-400" : pct < 85 ? "bg-amber-400" : "bg-rose-400";
  return (
    <div className="w-full">
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-700/50">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 text-[11px] text-slate-400">
        {used.toLocaleString()} / {limit.toLocaleString()} tokens ({pct.toFixed(0)}%)
      </div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-cyan-200/80">
      <span className="pulse-dot text-lg">◉</span> {label}
    </div>
  );
}
