import { useMemo, useState } from "react";
import type { Ctx } from "./App";
import { Item, post, pt, usd, usd0 } from "./api";

export function actorFor(it: Item): { actor: string; label: string } {
  if (it.required_role === "cfo") return { actor: "renata.ellis", label: "Approve as CFO (Renata Ellis)" };
  return { actor: "marisol.vega", label: "Approve" };
}

export async function decide(ctx: Ctx, it: Item, decision: string, actor = "marisol.vega", reason = "") {
  try {
    const r = await post(`/work-items/${it.id}/decision`, { decision, actor, reason });
    const msg: Record<string, string> = {
      approve: r.state === "POSTED" ? `Paid ${it.amount} to the account on file. Saved as a precedent.` : "Approved.",
      reject: "Rejected. Saved as a precedent.",
      verify: "Verified by phone. The new account is on file; the bill now needs an approver.",
      reopen: "Reopened for approval.",
    };
    ctx.toast(msg[decision] || "Done.");
  } catch (e: any) {
    ctx.toast(`Gateway said no${e.rule ? ` (${e.rule})` : ""}: ${e.message}`);
  }
  await ctx.refresh();
}

function BillCard({ it, ctx }: { it: Item; ctx: Ctx }) {
  const a = actorFor(it);
  let acts;
  if (it.state === "HELD")
    acts = (
      <>
        <button className="btn" onClick={() => ctx.open(it.id)}>See evidence</button>
        <button className="btn" onClick={() => decide(ctx, it, "reject", "marisol.vega", "Called the number on file: vendor did not send this")}>It wasn't {it.vendor?.split(" ")[0]}</button>
        <button className="btn primary" onClick={() => decide(ctx, it, "verify", "marisol.vega", "Confirmed by phone on the number already on file")}>Confirmed by phone</button>
      </>
    );
  else if (it.state === "BLOCKED")
    acts = (
      <>
        <button className="btn" onClick={() => ctx.open(it.id)}>See evidence</button>
        <button className="btn" onClick={() => decide(ctx, it, "reopen")}>Not a duplicate, reopen</button>
        <button className="btn primary" onClick={() => decide(ctx, it, "reject", "marisol.vega", "Duplicate confirmed")}>Reject duplicate</button>
      </>
    );
  else
    acts = (
      <>
        <button className="btn" onClick={() => ctx.open(it.id)}>See evidence</button>
        <button className="btn" onClick={() => decide(ctx, it, "reject")}>Reject</button>
        <button className="btn primary" onClick={() => decide(ctx, it, "approve", a.actor)}>{a.label}</button>
      </>
    );
  return (
    <li className="card bill">
      <div className="bill-h">
        <div>
          <h3>{it.vendor}</h3>
          <div className="meta">
            {it.bill_no} · {it.id} · due {it.due}
            {it.po ? ` · ${it.po}` : ""}
            {it.required_role_name ? ` · needs ${it.required_role_name}` : ""}
          </div>
        </div>
        <div className="amt">{it.amount}</div>
      </div>
      <div className="row">
        <span className={"chip " + it.chip_kind}>{it.chip}</span>
        <span>{it.headline}</span>
      </div>
      {it.suggest && (
        <p className="sug">
          <b>Pip suggests:</b> {it.suggest.replace(/^(PAY|ASK|HOLD|REJECT):\s*/, "")}
          {it.mode === "rules" ? <span className="muted"> (rules mode)</span> : null}
        </p>
      )}
      <div className="acts">{acts}</div>
    </li>
  );
}

function LiveCard({ ctx }: { ctx: Ctx }) {
  const recent = ctx.feed.filter((e) => Date.now() - e.t < 120000 && (e.event === "agent.step" || e.event === "gateway.decision"));
  if (!recent.length) return null;
  const wid = [...recent].reverse().find((e) => e.data.work_item)?.data.work_item;
  const rows = recent.filter((e) => e.data.work_item === wid || e.data.subject === wid || (e.event === "gateway.decision" && e.data.subject === "pip"));
  return (
    <div className="card incoming" aria-live="polite">
      <span className="chip warn">Live · {wid}</span>
      <ul className="feed">
        {rows.slice(-14).map((e, i) => {
          const d = e.data;
          let k = d.kind || e.event.split(".")[1];
          let text = d.text || "";
          if (e.event === "gateway.decision") {
            k = d.allowed ? "gateway" : "denied";
            text = `${d.action.replace(/_/g, " ")}${d.to ? " → " + d.to : ""}${d.reason ? " · " + d.reason : ""}`;
          } else if (d.kind === "tool") text = `${d.tool}(${JSON.stringify(d.input).slice(0, 140)})`;
          return (
            <li key={i}>
              <span className="k" style={{ color: k === "denied" ? "var(--bad)" : undefined }}>{k}</span>
              <span>{String(text).slice(0, 320)}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PipCard({ ctx }: { ctx: Ctx }) {
  const s = ctx.state;
  if (!s) return null;
  const a = s.agent;
  const t = s.tiers[a.effective_tier];
  const next = s.tiers[a.tier + 1];
  const need = [20, 60, 150][a.tier] || 150;
  const tight = a.effective_tier !== a.tier;
  return (
    <div className="card">
      <div className="pip-h">
        <div className="avatar" aria-hidden="true">P</div>
        <div>
          <b>Pip</b>
          <span>AP specialist · on probation since Apr 6</span>
        </div>
      </div>
      <div className="limit">{usd0(t.limit_cents)}</div>
      <div className="limit-l">Level {a.effective_tier} · {t.name}. The most Pip pays on its own.</div>
      {tight ? (
        <div className="note-box">Limit cut from {usd0(s.tiers[a.tier].limit_cents)} to {usd0(t.limit_cents)} until {pt(a.tightened_until)} because an email tried to give Pip instructions. A precaution, not a penalty.</div>
      ) : next ? (
        <>
          <div className="prog" role="img" aria-label={`${a.correct} of ${need}`}><i style={{ width: Math.min(100, (a.correct / need) * 100) + "%" }}></i></div>
          <p className="limit-l">{a.correct} of {need} correct decisions toward Level {next.n} ({usd0(next.limit_cents)})</p>
        </>
      ) : null}
      {s.frozen.length > 0 && <div className="note-box"><b>Frozen:</b> {s.frozen.map((f) => f.name).join(", ")}. No payments until a phone check.</div>}
      <h4>Pays without asking</h4>
      <ul>{a.effective_tier === 0 ? <li>Nothing. Pip suggests, you approve.</li> : <><li>Known vendors, PO and receipt match</li><li>Same bank account as before</li><li>Up to {usd0(t.limit_cents)} per bill</li></>}</ul>
      <h4>Always asks</h4>
      <ul><li>New vendors and intercompany</li><li>Changed bank details (phone check)</li><li>Totals, prices or receipts that do not match</li><li>Anything over its limit or the approval matrix</li></ul>
    </div>
  );
}

export default function Today({ ctx }: { ctx: Ctx }) {
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [reporting, setReporting] = useState(false);
  const by = (st: string) => ctx.items.filter((i) => i.state === st);
  const held = by("HELD"), blocked = by("BLOCKED"), needs = by("NEEDS_APPROVER"), paid = by("POSTED");
  const totals = useMemo(() => {
    const sum = (xs: Item[]) => xs.reduce((s, i) => s + i.amount_cents, 0);
    return { paid: sum(paid), wait: sum(needs), held: sum(held) + sum(blocked) };
  }, [ctx.items]);
  const all = totals.paid + totals.wait + totals.held || 1;
  const now = ctx.state ? new Date(ctx.state.now) : new Date();
  const hr = Number(new Intl.DateTimeFormat("en-US", { timeZone: "America/Los_Angeles", hour: "numeric", hour12: false }).format(now));
  const greet = hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
  const head = held.length ? "Pip stopped a payment and needs your call." : needs.length + blocked.length ? `${needs.length + blocked.length} bills need your call.` : "Nothing needs you right now.";
  const byPip = paid.filter((p) => p.paid_by_pip).length;

  async function report() {
    try {
      const r = await post("/incidents", { work_items: [...sel], pattern: "split_to_dodge_limit", actor: "marisol.vega" });
      ctx.toast(`Incident ${r.incident} opened. Pip wrote a post-mortem: ${r.lesson} is waiting for you in Pip's record.`);
      setSel(new Set());
      setReporting(false);
      await ctx.refresh();
    } catch (e: any) {
      ctx.toast(e.message);
    }
  }
  const seg = (v: number, c: string) => (v > 0 ? <span style={{ flex: v / all, background: `var(--${c})` }}></span> : null);
  return (
    <div className="cols">
      <div className="stack">
        <div>
          <div className="hello">
            <p className="eyebrow">{greet}, Marisol · {pt(ctx.state?.now)} PT</p>
            <h1>{head}</h1>
            <p className="sub">Pip paid {byPip} bills without asking. Every one of them is in the work record with the policy that allowed it.</p>
          </div>
          <div className="card week">
            <div className="week-h"><span>Bills in the queue</span><b className="mono">{usd(all === 1 ? 0 : all)}</b></div>
            <div className="seg" role="img" aria-label="Bills by status">{seg(totals.paid, "good")}{seg(totals.wait, "warn")}{seg(totals.held, "bad")}</div>
            <div className="legend">
              <div><span><i style={{ background: "var(--good)" }}></i>Paid</span><span className="mono">{usd(totals.paid)}</span></div>
              <div><span><i style={{ background: "var(--warn)" }}></i>Waiting on you</span><span className="mono">{usd(totals.wait)}</span></div>
              <div><span><i style={{ background: "var(--bad)" }}></i>Held or blocked</span><span className="mono">{usd(totals.held)}</span></div>
            </div>
          </div>
        </div>
        <LiveCard ctx={ctx} />
        {held.length > 0 && <section><h2 className="sec">Held back <span className="count">{held.length}</span></h2><ul className="bills">{held.map((i) => <BillCard key={i.id} it={i} ctx={ctx} />)}</ul></section>}
        {blocked.length > 0 && <section><h2 className="sec">Blocked <span className="count">{blocked.length}</span></h2><ul className="bills">{blocked.map((i) => <BillCard key={i.id} it={i} ctx={ctx} />)}</ul></section>}
        <section>
          <h2 className="sec">Needs you <span className="count">{needs.length}</span></h2>
          {needs.length ? <ul className="bills">{needs.map((i) => <BillCard key={i.id} it={i} ctx={ctx} />)}</ul> : <div className="card empty">Nothing waiting. Pip asks when a bill is new, over its limit, or does not match.</div>}
        </section>
        <section>
          <h2 className="sec">
            Paid <span className="count">{paid.length}</span>
            <span className="grow" style={{ flex: 1 }}></span>
            <button className="btn sm ghost" onClick={() => setReporting(!reporting)}>{reporting ? "Cancel" : "Report a problem"}</button>
          </h2>
          {reporting && (
            <div className="split-banner" style={{ marginBottom: 10 }}>
              <span>Select the bills that belong together, then report them. Pip will write the post-mortem.</span>
              <button className="btn sm primary" disabled={sel.size < 2} onClick={report}>One purchase, split to dodge the limit ({sel.size})</button>
            </div>
          )}
          <div className="card" style={{ padding: "4px 0" }}>
            <ul className="paid">
              {paid.map((i) => (
                <li key={i.id} style={reporting ? { gridTemplateColumns: "24px minmax(0,1fr) auto auto" } : undefined}>
                  {reporting && <input type="checkbox" className="sel-box" checked={sel.has(i.id)} onChange={(e) => { const n = new Set(sel); e.target.checked ? n.add(i.id) : n.delete(i.id); setSel(n); }} aria-label={`select ${i.id}`} />}
                  <div>
                    <b>{i.vendor}</b>
                    <div className="meta">{i.bill_no} · {i.po || "no PO"} · {pt(i.updated_at)} · {i.paid_by_pip ? `Pip, ${i.approved_by.replace("policy:", "")}` : `approved by ${i.approved_by_name}`}</div>
                  </div>
                  <div className="amt s">{i.amount}</div>
                  <button className="link" onClick={() => ctx.open(i.id)}>Why?</button>
                </li>
              ))}
            </ul>
          </div>
        </section>
      </div>
      <aside className="rail"><PipCard ctx={ctx} /></aside>
    </div>
  );
}
