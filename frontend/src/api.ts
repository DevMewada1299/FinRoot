// Thin client for the Probation API. The UI only reads state and sends decisions; the gateway does the rest.
const BASE = "/api";

export class ApiError extends Error {
  rule?: string;
  constructor(msg: string, rule?: string) {
    super(msg);
    this.rule = rule;
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(BASE + path, { headers: { "content-type": "application/json" }, ...init });
  if (!r.ok) {
    let detail: any = await r.text();
    try {
      detail = JSON.parse(detail).detail;
    } catch {
      /* plain text */
    }
    if (detail && typeof detail === "object") throw new ApiError(detail.reason || JSON.stringify(detail), detail.rule);
    throw new ApiError(String(detail || r.statusText));
  }
  return r.json();
}

export const get = <T = any>(p: string) => req<T>(p);
export const post = <T = any>(p: string, body?: unknown) =>
  req<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export interface Item {
  id: string;
  doc_id: string;
  state: string;
  vendor: string;
  vendor_id: string | null;
  amount_cents: number;
  amount: string;
  bill_no: string;
  bill_date: string;
  due: string;
  chip: string;
  chip_kind: string;
  headline: string;
  flags: string[];
  suggest: string;
  question: string;
  pip_action: string;
  mode: string;
  required_role: string | null;
  required_role_name: string;
  rules: string[];
  policies: string[];
  approved_by: string;
  approved_by_name: string;
  paid_by_pip: boolean;
  record_invoice: string | null;
  record_payment: string | null;
  updated_at: string;
  entity: string | null;
  po: string | null;
}

export interface AgentState {
  tier: number;
  effective_tier: number;
  limit_cents: number;
  correct: number;
  score: number;
  tightened_until: string | null;
  frozen_vendors: string[];
  learned_count: number;
  exam_score: number | null;
  serious_mistakes: number;
}

export interface AppState {
  agent: AgentState;
  now: string;
  tiers: { n: number; name: string; limit_cents: number }[];
  hard_limits: [string, string][];
  counts: Record<string, number>;
  llm_enabled: boolean;
  people: Record<string, { name: string; role: string }>;
  frozen: { id: string; name: string }[];
}

export function usd(c: number): string {
  const s = c < 0 ? "-" : "";
  const a = Math.abs(c);
  return s + "$" + Math.floor(a / 100).toLocaleString("en-US") + "." + String(a % 100).padStart(2, "0");
}
export function usd0(c: number): string {
  return "$" + Math.round(c / 100).toLocaleString("en-US");
}

const PT = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const PTD = new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric" });
export const pt = (iso?: string | null) => (iso ? PT.format(new Date(iso)) : "");
export const ptDate = (iso?: string | null) => (iso ? PTD.format(new Date(iso.length === 10 ? iso + "T20:00:00Z" : iso)) : "");

export type FeedEvent = { event: string; data: any; t: number };

export function subscribe(onEvent: (e: FeedEvent) => void): () => void {
  const es = new EventSource(BASE + "/events");
  const kinds = ["agent.step", "gateway.decision", "tier.changed", "lesson.proposed", "work_item.updated", "sim.reset", "policy.proposed"];
  kinds.forEach((k) =>
    es.addEventListener(k, (m: MessageEvent) => {
      try {
        onEvent({ event: k, data: JSON.parse(m.data), t: Date.now() });
      } catch {
        /* ignore */
      }
    }),
  );
  return () => es.close();
}
