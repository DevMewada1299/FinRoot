import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { get, ptDate, pt, usd } from "./api";
import { actorFor, decide } from "./Today";

function pill(st: string) {
  return st === "ok" ? <span className="pill good">Matches</span> : st === "warn" ? <span className="pill warn">Check</span> : <span className="pill bad">Mismatch</span>;
}

export function RefChips({ ids, onOpen }: { ids: string[]; onOpen: (id: string) => void }) {
  return (
    <>
      {ids.filter(Boolean).map((r) => (
        <button key={r} className="refchip" onClick={() => onOpen(r)} title="Open the source record">
          {r}
        </button>
      ))}
    </>
  );
}

export function RecordBox({ id, onClose }: { id: string; onClose: () => void }) {
  const [rec, setRec] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setRec(null);
    setErr(null);
    get(`/records/${id}`).then(setRec).catch((e) => setErr(e.message));
  }, [id]);
  return (
    <div className="recbox">
      <div className="row-gap" style={{ justifyContent: "space-between", fontFamily: "var(--f-body)" }}>
        <b>{id}{rec ? ` · ${rec.table}` : ""}</b>
        <button className="btn sm" onClick={onClose}>Close</button>
      </div>
      {err ? `Not found: ${err}` : rec ? JSON.stringify({ ...rec.row, ...(rec.lines ? { lines: rec.lines } : {}) }, null, 1) : "Loading…"}
    </div>
  );
}

export default function Drawer({ id, ctx, onClose }: { id: string; ctx: Ctx; onClose: () => void }) {
  const [ev, setEv] = useState<any>(null);
  const [rec, setRec] = useState<string | null>(null);
  useEffect(() => {
    get(`/work-items/${id}/evidence`).then(setEv).catch((e) => ctx.toast(e.message));
  }, [id, ctx.version]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [onClose]);
  if (!ev) return null;
  const it = ev.item;
  const p = ev.proposal || {};
  const a = actorFor(it);
  let acts = null;
  if (it.state === "NEEDS_APPROVER")
    acts = (<><button className="btn" onClick={() => decide(ctx, it, "reject")}>Reject</button><button className="btn primary" onClick={() => decide(ctx, it, "approve", a.actor)}>{a.label}</button></>);
  else if (it.state === "HELD")
    acts = (<><button className="btn" onClick={() => decide(ctx, it, "reject", "marisol.vega", "Called the number on file: vendor did not send this")}>It wasn't them</button><button className="btn primary" onClick={() => decide(ctx, it, "verify", "marisol.vega", "Confirmed by phone on the number already on file")}>Confirmed by phone on file number</button></>);
  else if (it.state === "BLOCKED")
    acts = (<><button className="btn" onClick={() => decide(ctx, it, "reopen")}>Reopen</button><button className="btn primary" onClick={() => decide(ctx, it, "reject", "marisol.vega", "Duplicate confirmed")}>Reject duplicate</button></>);
  else acts = <button className="btn" onClick={() => ctx.go("ask", `Why is ${it.id} ${it.state.toLowerCase().replace("_", " ")}?`)}>Open in Ask Pip</button>;
  return (
    <>
      <div className="scrim" onClick={onClose}></div>
      <aside className="drawer" role="dialog" aria-modal="true" aria-labelledby="drawer-title">
        <div className="drawer-top">
          <div>
            <span className={"chip " + it.chip_kind}>{it.chip}</span>
            <h2 id="drawer-title" style={{ marginTop: 10 }}>{it.vendor}</h2>
            <div className="meta">{it.bill_no} · {it.id} · {it.amount} · {it.state.replace("_", " ").toLowerCase()}</div>
          </div>
          <button className="btn sm" onClick={onClose} autoFocus>Close</button>
        </div>
        <div className="read">
          <b>Pip's read{p.mode ? ` · ${p.mode}` : ""}</b>
          {p.summary || "Pip has not proposed yet."}
          {p.question_for_human ? <p style={{ marginTop: 6 }}><i>{p.question_for_human}</i></p> : null}
          {p.evidence?.length ? <div style={{ marginTop: 8 }}>Cited: <RefChips ids={p.evidence} onOpen={setRec} /></div> : null}
        </div>
        {ev.routing?.reasons?.length ? (
          <div className="check">
            <div className="check-h"><span className="check-l">Gateway decision</span><span className="pill warn">{(ev.routing.action || "").replace(/_/g, " ")}</span></div>
            <p style={{ fontSize: 13.5 }}>{ev.routing.reasons.join(" ")}</p>
            <p className="note">Rules: {ev.routing.rules?.length ? ev.routing.rules.join(", ") : "none"} · policies matched: {ev.routing.policies_matched?.join(", ") || "none"}{it.required_role_name ? ` · approver: ${it.required_role_name}` : ""}</p>
          </div>
        ) : null}
        {rec && <RecordBox id={rec} onClose={() => setRec(null)} />}
        {ev.checks.map((c: any) => (
          <div className="check" key={c.key}>
            <div className="check-h"><span className="check-l">{c.label}</span>{pill(c.st)}</div>
            <div className="trio">
              <div><i>Invoice says</i>{c.inv}</div>
              <div><i>Our records say</i>{c.rec}</div>
              <div><i>Bank history says</i>{c.bank || "n/a"}</div>
            </div>
            <p className="note">{c.note} {c.refs?.length ? <RefChips ids={c.refs} onOpen={setRec} /> : null}</p>
          </div>
        ))}
        {ev.three_way?.length ? (
          <div className="check">
            <div className="check-h"><span className="check-l">Three-way match</span><span className="muted" style={{ fontSize: 12 }}>invoice vs {it.po} vs goods receipt</span></div>
            <div style={{ overflowX: "auto" }}>
              <table className="tw">
                <thead><tr><th>Line</th><th>Billed</th><th>Price</th><th>PO qty</th><th>PO price</th><th>Billed before</th><th>Received</th></tr></thead>
                <tbody>
                  {ev.three_way.map((l: any, i: number) => {
                    const over = l.po_rate != null && l.inv_rate > l.po_rate * 1.005;
                    const qbad = l.po_qty != null && (l.billed_before || 0) + l.inv_qty > l.po_qty;
                    const rbad = l.received != null && (l.billed_before || 0) + l.inv_qty > l.received;
                    return (
                      <tr key={i}>
                        <td>{l.desc}</td>
                        <td className={qbad || rbad ? "bad" : ""}>{l.inv_qty}</td>
                        <td className={over ? "warn" : ""}>{usd(l.inv_rate)}</td>
                        <td>{l.po_qty ?? "—"}</td>
                        <td>{l.po_rate != null ? usd(l.po_rate) : "—"}</td>
                        <td>{l.billed_before ?? 0}</td>
                        <td className={rbad ? "bad" : ""}>{l.received ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
        {ev.bank_history?.length ? (
          <div className="check">
            <div className="check-h"><span className="check-l">Bank accounts on file</span></div>
            {ev.bank_history.map((b: any) => (
              <p key={b.id} style={{ fontSize: 13 }}>
                <button className="refchip" onClick={() => setRec(b.id)}>{b.id}</button>
                ••{b.account_last4} {b.bank_name} · from {b.valid_from}{b.valid_to ? ` to ${b.valid_to}` : " (current)"} · {b.source.replace("_", " ")} · {b.n_payments} payments{b.last_paid ? `, last ${ptDate(b.last_paid)}` : ""}
              </p>
            ))}
          </div>
        ) : null}
        {ev.recent_invoices?.length ? (
          <div className="check">
            <div className="check-h"><span className="check-l">Recent bills from this vendor</span></div>
            <p style={{ fontSize: 13 }}>
              {ev.recent_invoices.map((r: any) => (
                <span key={r.id} style={{ display: "inline-block", marginRight: 10 }}>
                  <button className="refchip" onClick={() => setRec(r.id)}>{r.id}</button>
                  {r.vendor_invoice_no} · {ptDate(r.invoice_date)} · {usd(r.total_cents)} · {r.status}
                </span>
              ))}
            </p>
          </div>
        ) : null}
        <p className="limitline">{ev.limit_line}</p>
        <div className="acts">{acts}</div>
        <h4 className="eyebrow" style={{ marginTop: 20 }}>Work record for {it.id}</h4>
        <div className="logbox">
          <ul className="log">
            {ev.audit.map((r: any) => (
              <li key={r.seq} className={r.allowed ? "" : "bad"}>
                <span className="mono">#{r.seq}</span>
                <span className="mono">{pt(r.at)}</span>
                <span className="act">{r.action} · {r.actor}</span>
                <span>{r.reason}</span>
                <span className="mono">{r.hash.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </div>
        <details style={{ marginTop: 14 }}>
          <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>Original email (untrusted data)</summary>
          <div className="mailview"><pre>{`From: ${ev.doc.from_addr}\nSubject: ${ev.doc.subject}\n\n${ev.doc.body}`}</pre></div>
        </details>
      </aside>
    </>
  );
}
