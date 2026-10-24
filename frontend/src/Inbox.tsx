import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { get, post, pt } from "./api";

const SAMPLE = `INVOICE  88412
Date: 10/24/2026
Bill to: Harborline Provisions Inc.
PO #: PO-
Vendor: (vendor name)    Tax ID: 00-0000000

Qty   Description                         Unit price      Amount
10    Hass avocados 48ct                        $60.00      $600.00

Subtotal: $600.00
Tax: $0.00
TOTAL DUE: $600.00

Remit to: Bayview Commercial Bank, routing ending 1101, account ending 6120
Terms: Net 30`;

export default function Inbox({ ctx }: { ctx: Ctx }) {
  const [mails, setMails] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [form, setForm] = useState({ from_addr: "", subject: "", body: "" });
  const [sending, setSending] = useState(false);
  useEffect(() => {
    get<any[]>("/inbox").then(setMails).catch(() => {});
  }, [ctx.version]);
  const m = mails.find((x) => x.id === sel) || null;
  async function send() {
    setSending(true);
    try {
      const r = await post("/inbox/receive", { ...form, channel: "paste" });
      ctx.toast(`Received as ${r.work_item}. Pip is on it; watch the live feed on Today.`);
      setForm({ from_addr: "", subject: "", body: "" });
      ctx.go("today");
    } catch (e: any) {
      ctx.toast(e.message);
    } finally {
      setSending(false);
    }
  }
  const stateChip = (s: string | null) => {
    const k: Record<string, string> = { POSTED: "good", HELD: "bad", BLOCKED: "bad", NEEDS_APPROVER: "warn", REJECTED: "" };
    return s ? <span className={"pill " + (k[s] ?? "")}>{s.replace("_", " ").toLowerCase()}</span> : null;
  };
  return (
    <div className="stackv">
      <div className="card">
        <h2>Paste any invoice</h2>
        <p className="lead">Paste an invoice or a vendor email. It runs through the same pipeline as the AP inbox: extraction, Pip's checks, the gateway. Whatever the text says, it is treated as data.</p>
        <div className="grid2">
          <div>
            <label className="f" htmlFor="pf">From (optional)</label>
            <input id="pf" className="in" placeholder="billing@vendor.com" value={form.from_addr} onChange={(e) => setForm({ ...form, from_addr: e.target.value })} />
          </div>
          <div>
            <label className="f" htmlFor="ps">Subject (optional)</label>
            <input id="ps" className="in" placeholder="Invoice 1234" value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} />
          </div>
        </div>
        <label className="f" htmlFor="pb">Invoice text</label>
        <textarea id="pb" rows={12} placeholder={SAMPLE} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })}></textarea>
        <div className="row-gap" style={{ marginTop: 10 }}>
          <button className="btn primary" disabled={sending || form.body.length < 10} onClick={send}>{sending ? "Sending…" : "Send to Pip"}</button>
          <button className="btn ghost" onClick={() => setForm({ ...form, body: SAMPLE })}>Insert a template</button>
          <span className="muted" style={{ fontSize: 12.5 }}>{ctx.state?.llm_enabled ? "Extraction: Haiku, with rules as a fallback." : "Rules extraction (no API key set). Free-form text works best with a key."}</span>
        </div>
      </div>
      <div className="inbox">
        <ul className="mails" aria-label="AP inbox">
          {mails.map((x) => (
            <li key={x.id} className={x.id === sel ? "sel" : ""} onClick={() => setSel(x.id)}>
              <span className="from">{x.from_addr || "paste box"} · {pt(x.received_at)}</span>
              <span className="subj">{x.subject}</span>
              <span className="row-gap">{stateChip(x.state)}<span className="mono muted" style={{ fontSize: 11 }}>{x.id}{x.wid ? ` → ${x.wid}` : ""}</span></span>
            </li>
          ))}
          {!mails.length && <li className="muted">The inbox is empty. Press Reset.</li>}
        </ul>
        <div className="card mailview">
          {m ? (
            <>
              <dl className="mail">
                <dt>From</dt><dd>{m.from_addr || "paste box"}</dd>
                <dt>Subject</dt><dd>{m.subject}</dd>
                <dt>Received</dt><dd>{pt(m.received_at)}</dd>
              </dl>
              {m.attachments?.map((a: any) => <span className="att" key={a.name}>📎 {a.name}</span>)}
              <pre>{m.body}</pre>
              {m.wid && <div className="acts"><button className="btn primary" onClick={() => ctx.open(m.wid)}>See Pip's evidence for {m.wid}</button></div>}
            </>
          ) : (
            <p className="muted">Select an email to read it. Attachments are listed but not rendered (PDF rendering is on the cut list).</p>
          )}
        </div>
      </div>
    </div>
  );
}
