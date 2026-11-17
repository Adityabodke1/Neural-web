// NEURAL WEB API client. All calls go to the FastAPI backend.
// API keys are never handled here — they live server-side only.

const BASE = "/api";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  get: <T>(p: string) => j<T>(p),
  post: <T>(p: string, body?: unknown) =>
    j<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body?: unknown) =>
    j<T>(p, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  del: <T>(p: string) => j<T>(p, { method: "DELETE" }),
};

export interface SessionSummary {
  id: string;
  prompt: string;
  depth: string;
  status: string;
  pages_scraped: number;
  records_found: number;
  created_at: string;
  error?: string;
}

export interface RecordField {
  value: unknown;
  source_url: string | null;
  evidence: string;
  confidence: number;
  verification: string;
  cross_check_source?: string;
  cross_check_evidence?: string;
}

export interface ResearchRecord {
  id: number;
  url: string;
  data: {
    entity_name: string | null;
    fields: Record<string, RecordField>;
    sources: string[];
    notes: string;
  };
  confidence: number;
  verification: string;
  verification_notes: string;
}

export interface SessionDetail {
  session: {
    id: string;
    prompt: string;
    depth: string;
    status: string;
    plan: { objective: string; sub_questions: string[]; target_fields: string[] } | null;
    report_md: string | null;
    pages_scraped: number;
    records_found: number;
    created_at: string;
    error: string | null;
  };
  queries: { query: string; engine: string; results_count: number }[];
  urls: { url: string; title: string; status: string; engine: string; score: number; error?: string }[];
  pages: { url: string; title: string; status: string; word_count: number; final_url?: string }[];
  records: ResearchRecord[];
}

export interface ProgressEvent {
  type: string;
  step: string;
  ts: string;
  payload: Record<string, unknown>;
}

export function openEventStream(
  sessionId: string,
  onEvent: (e: ProgressEvent) => void,
  onDone?: () => void
): EventSource {
  const es = new EventSource(`${BASE}/research/${sessionId}/events`);
  es.onmessage = (m) => {
    const evt = JSON.parse(m.data) as ProgressEvent;
    onEvent(evt);
    if (evt.type === "done") {
      es.close();
      onDone?.();
    }
  };
  es.onerror = () => {
    // closed when the run completes or connection drops; caller decides
  };
  return es;
}
