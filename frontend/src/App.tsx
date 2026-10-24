import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, FeedEvent, Item, get, post, subscribe, usd0 } from "./api";
import Today from "./Today";
import Drawer from "./Drawer";
import Inbox from "./Inbox";
import Record from "./Record";
import Scorecard from "./Scorecard";
import Ask from "./Ask";

export type Tab = "today" | "inbox" | "record" | "score" | "ask";
const TABS: [Tab, string][] = [["today", "Today"], ["inbox", "Inbox"], ["record", "Pip's record"], ["score", "Scorecard"], ["ask", "Ask Pip"]];

export interface Ctx {
  state: AppState | null;
  items: Item[];
  feed: FeedEvent[];
  refresh: () => Promise<void>;
  open: (id: string) => void;
  toast: (m: string) => void;
  go: (t: Tab, q?: string) => void;
  version: number;
}

export default function App() {
  const initial = (location.hash.slice(1) as Tab) || "today";
  const [tab, setTab] = useState<Tab>(TABS.some(([t]) => t === initial) ? initial : "today");
  const [state, setState] = useState<AppState | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [feed, setFeed] = useState<FeedEvent[]>([]);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [toastMsg, setToast] = useState<string | null>(null);
  const [askQ, setAskQ] = useState<string>("");
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const tt = useRef<number>();

  const toast = useCallback((m: string) => {
    setToast(m);
    window.clearTimeout(tt.current);
    tt.current = window.setTimeout(() => setToast(null), 4800);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [s, w] = await Promise.all([get<AppState>("/state"), get<Item[]>("/work-items")]);
      setState(s);
      setItems(w);
      setVersion((v) => v + 1);
      setErr(null);
    } catch (e: any) {
      setErr("Cannot reach the API on :8000. Run make dev.");
    }
  }, []);

  useEffect(() => {
    refresh();
    const unsub = subscribe((e) => {
      setFeed((f) => [...f.slice(-200), e]);
      if (["work_item.updated", "tier.changed", "sim.reset", "lesson.proposed"].includes(e.event)) refresh();
    });
    const poll = window.setInterval(refresh, 8000);
    return () => {
      unsub();
      window.clearInterval(poll);
    };
  }, [refresh]);

  const go = (t: Tab, q?: string) => {
    setTab(t);
    if (q !== undefined) setAskQ(q);
    setDrawer(null);
    try {
      history.replaceState(null, "", "#" + t);
    } catch {
      /* sandboxed */
    }
    window.scrollTo(0, 0);
  };

  const ctx: Ctx = { state, items, feed, refresh, open: setDrawer, toast, go, version };

  async function act(label: string, fn: () => Promise<any>, done: (r: any) => string) {
    setBusy(label);
    try {
      const r = await fn();
      toast(done(r));
      await refresh();
    } catch (e: any) {
      toast(e.message || String(e));
    } finally {
      setBusy(null);
    }
  }

  const a = state?.agent;
  const tight = a && a.effective_tier !== a.tier;
  return (
    <div className="wrap">
      <div className="demobar" role="group" aria-label="Demo controls">
        <span>Demo · synthetic company · SimBank, no real money{state && !state.llm_enabled ? " · rules mode (no API key)" : ""}</span>
        <span className="grow"></span>
        <button className="btn sm primary" disabled={!!busy} onClick={() => act("play", () => post("/sim/play/planted_email"), () => "A new email arrived in the AP inbox. Watch Pip work.")}>
          Play the planted email
        </button>
        <button className="btn sm" disabled={!!busy} onClick={() => act("repeat", () => post("/sim/play/split_repeat"), () => "Four bills from Northgate Packaging just arrived.")}>
          Play the split repeat
        </button>
        <button className="btn sm" disabled={!!busy} onClick={() => act("ff", () => post("/sim/fast-forward", { weeks: 4 }), (r) => `Simulated ${r.weeks.length} weeks: ${r.weeks.reduce((s: number, w: any) => s + w.docs, 0)} bills.`)}>
          {busy === "ff" ? "Simulating…" : "Fast-forward 4 weeks"}
        </button>
        <button className="btn sm ghost" disabled={!!busy} onClick={() => act("reset", () => post("/sim/reset"), () => "Reset to 9:00 AM, Oct 24.")}>
          {busy === "reset" ? "Resetting…" : "Reset"}
        </button>
      </div>
      <header className="top">
        <div className="brand">
          <div className="mark" aria-hidden="true"><i></i><i></i><i></i></div>
          <div>
            <b>Probation</b>
            <div className="co">Harborline Provisions · 3 entities · Marisol Vega, Controller</div>
          </div>
        </div>
        <div>
          {a && (
            <span className={"chip " + (tight ? "bad" : "info")}>
              Pip · Level {a.effective_tier} · {usd0(a.limit_cents)}
              {tight ? " · tightened" : ""}
            </span>
          )}
        </div>
      </header>
      <nav className="tabs" aria-label="Sections">
        {TABS.map(([t, l]) => (
          <button key={t} type="button" aria-current={tab === t ? "page" : "false"} onClick={() => go(t)}>
            {l}
            {t === "today" && state ? <span className="count" style={{ marginLeft: 6 }}>{(state.counts.NEEDS_APPROVER || 0) + (state.counts.HELD || 0) + (state.counts.BLOCKED || 0)}</span> : null}
          </button>
        ))}
      </nav>
      <main>
        {err && <div className="banner bad">{err}</div>}
        {tab === "today" && <Today ctx={ctx} />}
        {tab === "inbox" && <Inbox ctx={ctx} />}
        {tab === "record" && <Record ctx={ctx} />}
        {tab === "score" && <Scorecard ctx={ctx} />}
        {tab === "ask" && <Ask ctx={ctx} initial={askQ} />}
      </main>
      {drawer && <Drawer id={drawer} ctx={ctx} onClose={() => setDrawer(null)} />}
      {toastMsg && (
        <div className="toast" role="status" aria-live="polite">
          {toastMsg}
        </div>
      )}
    </div>
  );
}
