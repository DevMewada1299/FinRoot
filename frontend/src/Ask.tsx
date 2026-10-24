import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { post, pt } from "./api";

export default function Ask({ ctx, initial }: { ctx: Ctx; initial: string }) {
  const [q, setQ] = useState(initial);
  const [a, setA] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  async function run(text: string) {
    setQ(text);
    setBusy(true);
    try {
      setA(await post("/ask", { q: text }));
    } catch (e: any) {
      ctx.toast(e.message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    if (initial) run(initial);
  }, [initial]);
  const paid = ctx.items.find((i) => i.state === "POSTED");
  const held = ctx.items.find((i) => i.state === "HELD");
  const waiting = ctx.items.find((i) => i.state === "NEEDS_APPROVER");
  const chips = [
    paid && `Why was ${paid.id} paid?`,
    waiting && `Why is ${waiting.id} waiting on me?`,
    held && `Why was ${held.id} held?`,
    "What changed Pip's limit?",
  ].filter(Boolean) as string[];
  return (
    <div className="askwrap">
      <p className="eyebrow">Ask the record</p>
      <h1 style={{ fontSize: "clamp(24px,4vw,32px)", lineHeight: 1.15 }}>Why did Pip do that?</h1>
      <p className="meta" style={{ margin: "8px 0 16px", maxWidth: "60ch" }}>Answers come from the work record, one row per step, so they stay the same months later.</p>
      <form className="askform" onSubmit={(e) => { e.preventDefault(); run(q); }}>
        <label htmlFor="ask-q" style={{ position: "absolute", left: -9999 }}>Question</label>
        <input id="ask-q" type="text" autoComplete="off" placeholder="Try: Why was WI-000004 paid?" value={q} onChange={(e) => setQ(e.target.value)} />
        <button className="btn primary" type="submit" disabled={busy}>{busy ? "…" : "Ask"}</button>
      </form>
      <div className="chips">{chips.map((c) => <button key={c} type="button" onClick={() => run(c)}>{c}</button>)}</div>
      {a && (!a.found ? (
        <div className="card"><p className="answer">I could not find that in the work record. Try a work item id like WI-000004, an invoice number, a vendor name, or ask about Pip's limit.</p></div>
      ) : (
        <div className="card">
          <p className="eyebrow">Answer{a.worded_by === "llm" ? " · worded by Claude from the rows below" : " · from the rows below"}</p>
          <p className="answer">{a.plain}</p>
          <div className="sql">{a.sql}</div>
          <div className="logbox">
            <ul className="log">
              {a.rows.map((e: any) => (
                <li key={e.seq} className={e.allowed ? "" : "bad"}>
                  <span className="mono">#{e.seq}</span>
                  <span className="mono">{pt(e.at)}</span>
                  <span className="act">{e.action} · {e.actor}</span>
                  <span>{e.reason}</span>
                  <span className="mono">{e.hash.slice(0, 8)}</span>
                </li>
              ))}
            </ul>
          </div>
          {a.subject?.startsWith("WI-") && <div className="acts"><button className="btn" onClick={() => ctx.open(a.subject)}>Open the evidence</button></div>}
        </div>
      ))}
    </div>
  );
}
