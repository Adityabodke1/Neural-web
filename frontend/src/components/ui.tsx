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
    <div className={`panel p-5 ${className}`}>
      {title && (
        <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.16em]"
          style={{ color: "var(--muted)" }}>
          {title}
        </div>
      )}
      {children}
    </div>
  );
}

export function Stat({ label, value, accent }: { label: string; value: ReactNode; accent?: string }) {
  return (
    <div className="panel p-4">
      <div className="text-[11px] uppercase tracking-wider" style={{ color: "var(--faint)" }}>{label}</div>
      <div className={`mt-1 text-2xl font-semibold ${accent ?? ""}`}
        style={{ color: accent ? undefined : "var(--text)" }}>{value}</div>
    </div>
  );
}

export function Badge({ status }: { status: string }) {
  const map: Record<string, { bg: string; fg: string; bd: string }> = {
    complete: { bg: "rgba(108,192,139,0.10)", fg: "var(--ok)", bd: "rgba(108,192,139,0.35)" },
    verified: { bg: "rgba(108,192,139,0.10)", fg: "var(--ok)", bd: "rgba(108,192,139,0.35)" },
    ok: { bg: "rgba(108,192,139,0.10)", fg: "var(--ok)", bd: "rgba(108,192,139,0.35)" },
    running: { bg: "rgba(139,157,255,0.12)", fg: "var(--accent)", bd: "rgba(139,157,255,0.35)" },
    queued: { bg: "rgba(139,157,255,0.08)", fg: "var(--accent)", bd: "rgba(139,157,255,0.25)" },
    partial: { bg: "rgba(217,164,65,0.10)", fg: "var(--warn)", bd: "rgba(217,164,65,0.35)" },
    failed: { bg: "rgba(224,108,117,0.10)", fg: "var(--err)", bd: "rgba(224,108,117,0.35)" },
    error: { bg: "rgba(224,108,117,0.10)", fg: "var(--err)", bd: "rgba(224,108,117,0.35)" },
    contradicted: { bg: "rgba(224,108,117,0.10)", fg: "var(--err)", bd: "rgba(224,108,117,0.35)" },
    blocked: { bg: "rgba(217,164,65,0.10)", fg: "var(--warn)", bd: "rgba(217,164,65,0.35)" },
    unverified: { bg: "rgba(154,161,171,0.08)", fg: "var(--muted)", bd: "rgba(154,161,171,0.22)" },
    missing: { bg: "rgba(154,161,171,0.06)", fg: "var(--faint)", bd: "rgba(154,161,171,0.20)" },
  };
  const c = map[status] ?? map.unverified;
  return (
    <span className="inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium"
      style={{ background: c.bg, color: c.fg, borderColor: c.bd }}>
      {status}
    </span>
  );
}

export function Bar({ used, limit }: { used: number; limit: number | null }) {
  if (!limit) return <span className="text-xs" style={{ color: "var(--faint)" }}>quota unavailable</span>;
  const pct = Math.min(100, (used / limit) * 100);
  const color = pct < 60 ? "var(--ok)" : pct < 85 ? "var(--warn)" : "var(--err)";
  return (
    <div className="w-full">
      <div className="h-2 w-full overflow-hidden rounded-full" style={{ background: "var(--panel-3)" }}>
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="mt-1 text-[11px]" style={{ color: "var(--faint)" }}>
        {used.toLocaleString()} / {limit.toLocaleString()} tokens ({pct.toFixed(0)}%)
      </div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
      <span className="pulse-dot text-lg" aria-hidden>◉</span> {label}
    </div>
  );
}
