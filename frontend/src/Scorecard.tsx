import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { get, pt } from "./api";

const NAMES: Record<string, string> = {
  duplicate_exact: "Exact duplicate", duplicate_near: "Near duplicate", po_price_mismatch: "Price over PO", po_qty_mismatch: "Qty over PO",
  receipt_shortfall: "Short receipt (3-way)", amount_tamper: "Total tampered", bank_change: "Bank change", lookalike_domain: "Look-alike sender",
  injection: "Injected instructions", split_to_dodge_limit: "Split to dodge limit", new_vendor_legit: "New vendor (legit)",
  intercompany: "Intercompany", tax_id_mismatch: "Tax id mismatch",
};
const pct = (v: number | null | undefined) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);

export default function Scorecard({ ctx }: { ctx: Ctx }) {
  const [r, setR] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    get("/eval/latest").then(setR).catch((e) => setErr(e.message));
  }, [ctx.version]);
  if (err) return <div className="card"><h2>Scorecard</h2><p className="lead">No eval run yet. Run <code>make eval</code>; this page only shows numbers from real runs.</p></div>;
  if (!r) return null;
  const S = r.stages;
  const A = S.A.metrics, B = S.B.metrics, C = S.C.metrics;
  const kinds = Object.keys(C.per_problem);
  const inj = r.injection_resistance;
  return (
    <div className="stackv">
      <div>
        <p className="eyebrow">make eval · {pt(r.at)} · {r.docs} labeled bills · {r.mode_note} · {r.llm_calls} live LLM calls</p>
        <h1 style={{ fontSize: "clamp(24px,4vw,32px)", lineHeight: 1.15 }}>Before and after learning</h1>
        <p className="meta" style={{ maxWidth: "70ch", marginTop: 8 }}>The same {r.docs} bills ({r.clean} clean, {r.planted} with a planted problem) run three times: before learning, after onboarding policies, and after Pip's lesson from its split-bill mistake was approved. Pip fixed at Level 2.</p>
      </div>
      <div className="kpis">
        <div className="kpi"><div className="l">Split-bill recall</div><div className="v">{pct(B.per_problem.split_to_dodge_limit.recall)} → {pct(C.per_problem.split_to_dodge_limit.recall)}</div><div className="d muted">one approved lesson</div></div>
        <div className="kpi"><div className="l">Bad bills auto-paid</div><div className="v">{B.bad_invoices_auto_paid} → {C.bad_invoices_auto_paid}</div><div className="d muted">${(B.bad_auto_paid_cents / 100).toLocaleString()} → ${(C.bad_auto_paid_cents / 100).toLocaleString()}</div></div>
        <div className="kpi"><div className="l">Auto-pay rate, clean bills</div><div className="v">{pct(C.auto_pay_rate)}</div><div className="d muted">false holds {pct(C.false_hold_rate)}</div></div>
        <div className="kpi"><div className="l">Dollars protected</div><div className="v">${Math.round(C.dollars_protected_cents / 100).toLocaleString()}</div><div className="d muted">bad bills stopped before payment</div></div>
        <div className="kpi"><div className="l">Injection attempts that changed anything</div><div className="v">{inj.changed_anything} of {inj.attacks}</div><div className="d muted">run with full side effects</div></div>
      </div>
      <div className="card">
        <h2>Three stages</h2>
        <div style={{ overflowX: "auto" }}>
          <table className="stagetab">
            <thead><tr><th></th><th>A. Before learning</th><th>B. Onboarding policies</th><th>C. After lessons</th></tr></thead>
            <tbody>
              <tr><td>Auto-pay rate (clean)</td><td>{pct(A.auto_pay_rate)}</td><td>{pct(B.auto_pay_rate)}</td><td>{pct(C.auto_pay_rate)}</td></tr>
              <tr><td>Ask-human rate (all)</td><td>{pct(A.ask_human_rate)}</td><td>{pct(B.ask_human_rate)}</td><td>{pct(C.ask_human_rate)}</td></tr>
              <tr><td>False-hold rate (clean)</td><td>{pct(A.false_hold_rate)}</td><td>{pct(B.false_hold_rate)}</td><td>{pct(C.false_hold_rate)}</td></tr>
              <tr><td>Bad bills auto-paid</td><td>{A.bad_invoices_auto_paid}</td><td>{B.bad_invoices_auto_paid}</td><td>{C.bad_invoices_auto_paid}</td></tr>
              <tr><td>Replay exam</td><td>{pct(A.exam_score)}</td><td>{pct(B.exam_score)}</td><td>{pct(C.exam_score)}</td></tr>
            </tbody>
          </table>
        </div>
        <p className="foot">Stage A asks a human about everything (no policies yet), so it is safe but does no work. B does the work and lets split bills through. C keeps the work and closes the gap.</p>
      </div>
      <div className="card">
        <h2>Recall per planted problem</h2>
        <p className="lead">Caught means the right flag was raised and the bill was not auto-paid. Grey: before the lesson (B). Colour: after (C). Precision after learning on the right.</p>
        <div className="bars">
          {kinds.map((k) => {
            const b = B.per_problem[k], c = C.per_problem[k];
            return (
              <div className="barrow" key={k}>
                <span>{NAMES[k] || k} <span className="muted mono" style={{ fontSize: 11 }}>n={c.n}</span></span>
                <span className="track">
                  <i style={{ width: `${(c.recall || 0) * 100}%`, background: c.recall >= 0.9 ? "var(--good)" : c.recall >= 0.5 ? "var(--warn)" : "var(--bad)" }}></i>
                  <i style={{ width: `${(b.recall || 0) * 100}%`, background: "var(--ink-2)", opacity: 0.35 }}></i>
                </span>
                <span className="mono">{pct(c.recall)} · p {c.precision == null ? "–" : Math.round(c.precision * 100)}</span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="card">
        <h2>How these numbers were produced</h2>
        <p style={{ fontSize: 14 }}>{r.method}</p>
        <p className="foot">Labels live in data/labels/ and are read only by eval/ and tests. The documents are synthetic: generated from templates with typos and mixed formats{r.mode_note.startsWith("rules") ? ", and decided in rules mode because no API key was set for this run" : ""}.</p>
      </div>
    </div>
  );
}
