import { useEffect, useState } from "react";
import type { Ctx } from "./App";
import { get, post, pt, ptDate, usd0 } from "./api";
import { RecordBox, RefChips } from "./Drawer";

const PROMO: Record<number, string> = {
  1: "20 correct suggestions and no serious mistakes",
  2: "60 correct decisions and 2 approved lessons or policies",
  3: "150 correct decisions, none wrong in 60 days, and 98% on the replay exam",
};
const RULES: Record<number, string> = {
  0: "Suggests only. Nothing is paid unless a human approves it.",
  1: "Pays small matched bills from known vendors.",
  2: "Pays matched bills up to $5,000 without asking.",
  3: "Pays matched bills up to $25,000. Never new vendors.",
};

function TrustChart({ events, weeks, now }: { events: any[]; weeks: any[]; now: string }) {
  if (!events.length) return null;
  const W = 680, H = 300, Lm = 120, Rm = 24, Tm = 18, Bm = 90;
  const pw = W - Lm - Rm, ph = H - Tm - Bm;
  const t0 = new Date(events[0].at).getTime(), t1 = Math.max(new Date(now).getTime(), t0 + 86400000);
  const x = (iso: string) => Lm + (pw * (new Date(iso).getTime() - t0)) / (t1 - t0);
  const y = (l: number) => Tm + (ph * (3 - l)) / 3;
  // effective level over time: tighten/restore move the effective tier, promote/demote the base tier
  let base = 0, eff = 0;
  const pts: [string, number][] = [];
  for (const e of events) {
    if (e.kind === "hire") { base = 0; eff = 0; }
    else if (e.kind === "promote" || e.kind === "demote") { base = e.to_tier; eff = e.to_tier; }
    else if (e.kind === "tighten") eff = e.to_tier;
    pts.push([e.at, eff]);
  }
  let d = `M${x(pts[0][0])} ${y(pts[0][1])}`;
  for (let i = 1; i < pts.length; i++) d += ` H${x(pts[i][0])} V${y(pts[i][1])}`;
  d += ` H${x(now)}`;
  const months: string[] = [];
  const s = new Date(t0);
  for (let m = new Date(Date.UTC(s.getUTCFullYear(), s.getUTCMonth() + 1, 1)); m.getTime() < t1; m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))) months.push(m.toISOString());
  const barTop = H - Bm + 34, barH = 34;
  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Pip's level over time and ask-human rate">
      <title>Pip's level over time</title>
      {[0, 1, 2, 3].map((l) => (
        <g key={l}>
          <line x1={Lm} x2={W - Rm} y1={y(l)} y2={y(l)} stroke="var(--line)" />
          <text x={Lm - 10} y={y(l) + 4} textAnchor="end">L{l} · {usd0([0, 50000, 500000, 2500000][l])}</text>
        </g>
      ))}
      {months.map((m) => <text key={m} x={x(m)} y={Tm + ph + 16} textAnchor="middle">{ptDate(m).split(" ")[0]}</text>)}
      <path d={d} fill="none" stroke="var(--accent)" strokeWidth="2.5" strokeLinejoin="round" />
      {events.filter((e) => e.kind !== "restore").map((e, i) => {
        const down = e.kind === "demote" || e.kind === "tighten";
        return (
          <g key={i}>
            <circle cx={x(e.at)} cy={y(e.to_tier)} r="5" fill={down ? "var(--bad)" : "var(--accent)"} stroke="var(--surface)" strokeWidth="2" />
            {(e.kind !== "tighten" || i === events.length - 1) && (
              <text x={x(e.at) + (down ? -8 : 8)} y={y(e.to_tier) - 9} textAnchor={down ? "end" : "start"} style={{ fill: down ? "var(--bad)" : "var(--ink)" }}>
                {e.kind === "hire" ? "Hired" : e.kind === "tighten" ? "Tightened" : `L${e.to_tier}`} · {ptDate(e.at)}
              </text>
            )}
          </g>
        );
      })}
      <text x={Lm - 10} y={barTop + barH / 2 + 4} textAnchor="end">Asked a human</text>
      {weeks.map((w, i) => {
        const r = w.docs ? (w.asked + w.held) / w.docs : 0;
        const bx = x(w.at) - 5;
        return (
          <g key={i}>
            <rect x={bx} y={barTop + barH * (1 - r)} width="10" height={barH * r} rx="2" fill="var(--warn)" />
            {(i === 0 || i === weeks.length - 1) && <text x={bx + 5} y={barTop - 4} textAnchor="middle">{Math.round(r * 100)}%</text>}
          </g>
        );
      })}
      <line x1={Lm} x2={W - Rm} y1={barTop + barH} y2={barTop + barH} stroke="var(--line)" />
    </svg>
  );
}

export default function Record({ ctx }: { ctx: Ctx }) {
  const [tl, setTl] = useState<any[]>([]);
  const [weeks, setWeeks] = useState<any[]>([]);
  const [lessons, setLessons] = useState<any[]>([]);
  const [incidents, setIncidents] = useState<any[]>([]);
  const [policies, setPolicies] = useState<any[]>([]);
  const [skills, setSkills] = useState<any>({ skills: [], versions: [] });
  const [log, setLog] = useState<any[]>([]);
  const [verify, setVerify] = useState<any>(null);
  const [tampered, setTampered] = useState(false);
  const [rec, setRec] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = () => {
    get<any[]>("/agent/timeline").then(setTl).catch(() => {});
    get<any[]>("/sim/weeks").then(setWeeks).catch(() => {});
    get<any[]>("/lessons").then(setLessons).catch(() => {});
    get<any[]>("/incidents").then(setIncidents).catch(() => {});
    get<any[]>("/policies").then(setPolicies).catch(() => {});
    get("/skills").then(setSkills).catch(() => {});
    get<any[]>("/audit?limit=300").then(setLog).catch(() => {});
  };
  useEffect(load, [ctx.version]);
  const s = ctx.state;
  if (!s) return null;
  const a = s.agent;

  async function lessonDecision(id: string, decision: string) {
    setBusy(true);
    try {
      const r = await post(`/lessons/${id}/decision`, { decision, actor: "marisol.vega", reason: decision === "approve" ? "Approved in the Policy Lab" : "Rejected in the Policy Lab" });
      ctx.toast(decision === "approve" ? `Approved. ${r.applied.policy} is active, skill updated to v${r.applied.skill_version}, regression case ${r.applied.regression} added.` : "Rejected. It will have no effect.");
    } catch (e: any) {
      ctx.toast(e.message);
    } finally {
      setBusy(false);
      ctx.refresh();
    }
  }
  async function policyDecision(id: string, decision: string) {
    try {
      await post(`/policies/${id}/decision`, { decision, actor: "marisol.vega" });
      ctx.toast(decision === "approve" ? `${id} is active. The gateway enforces it from now on.` : `${id} rejected.`);
    } catch (e: any) {
      ctx.toast(e.message);
    }
    ctx.refresh();
  }
  async function doVerify() {
    setVerify(await post("/audit/verify"));
  }
  async function doTamper() {
    const r = await post("/audit/tamper");
    setTampered(r.tampered);
    setVerify(null);
    ctx.toast(r.tampered ? `Row #${r.seq} edited in place and nothing recomputed. Now press Verify the record.` : "Row restored.");
    load();
  }
  const proposedLessons = lessons.filter((l) => l.status === "proposed");
  const proposedPolicies = policies.filter((p) => p.status === "proposed");
  const active = policies.filter((p) => p.status === "active");
  return (
    <div className="stackv">
      <div className="grid2">
        <div className="card">
          <h2>How Pip earns authority</h2>
          <p className="lead">Limits are enforced in code. Pip moves up with a clean record and drops fast when it slips.</p>
          <ol className="ladder">
            {s.tiers.map((t) => (
              <li key={t.n} className={(t.n === a.effective_tier ? "cur" : "") + (t.n > a.tier ? " next" : "")}>
                <span className="dot"></span>
                <div>
                  <b>Level {t.n} · {t.name}</b> <span className="mono">{usd0(t.limit_cents)}</span>
                  <p>{RULES[t.n]}</p>
                  {t.n > 0 && <small>To get here: {PROMO[t.n]}</small>}
                </div>
              </li>
            ))}
          </ol>
          <p className="foot">A payment Pip should have stopped sends it straight back to Level 0. Untrusted instructions inside an email tighten it to Level 1 for 7 days and freeze the vendor. Record: {a.correct} correct, {a.serious_mistakes} serious mistakes, {a.learned_count} approved lessons or policies, replay exam {a.exam_score == null ? "not run" : Math.round(a.exam_score * 1000) / 10 + "%"}.</p>
        </div>
        <div className="card">
          <h2>Hard limits</h2>
          <p className="lead">No prompt, email or lesson can change these. The gateway checks every write.</p>
          <ul className="lims">{s.hard_limits.map(([k, v]) => <li key={k}><span className="mono">{k}</span><span>{v}</span></li>)}</ul>
          <h4 className="eyebrow" style={{ marginTop: 18 }}>Approval matrix</h4>
          <ul className="lims">
            <li><span className="mono">Pip</span><span>Up to its tier limit, policy-matched, no hard flag</span></li>
            <li><span className="mono">$10k</span><span>Department manager (Alicia Brandt)</span></li>
            <li><span className="mono">$50k</span><span>Controller (Marisol Vega)</span></li>
            <li><span className="mono">more</span><span>CFO (Renata Ellis). Requester, approver and poster are always three different actors.</span></li>
          </ul>
        </div>
      </div>

      <div className="card">
        <div className="row-gap" style={{ justifyContent: "space-between" }}>
          <div>
            <h2>Pip's authority over time</h2>
            <p className="lead">Each step up was earned under the rules. Each step down says why. Bars: share of bills that went to a human in each simulated week.</p>
          </div>
          <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { const r = await post("/sim/fast-forward", { weeks: 4 }); ctx.toast(`Simulated ${r.weeks.length} weeks through the real gateway.`); } finally { setBusy(false); ctx.refresh(); } }}>{busy ? "Simulating…" : "Fast-forward 4 weeks"}</button>
        </div>
        <TrustChart events={tl} weeks={weeks} now={s.now} />
        <p className="foot">History before Oct 24 is Pip reviewing past bills in shadow mode; fast-forward generates new weeks of bills and the simulated controller decides from the evidence. Pip runs in rules mode during fast-forward.</p>
      </div>

      <div className="card">
        <h2>Policy Lab</h2>
        <p className="lead">Pip drafts lessons and policies from its mistakes and your decisions, and replays them on past bills. Nothing takes effect until you approve it, and nothing it learns can loosen a hard limit.</p>
        {rec && <RecordBox id={rec} onClose={() => setRec(null)} />}
        <ul className="pols">
          {proposedLessons.map((l) => {
            const inc = incidents.find((i) => i.id === l.incident_id);
            const pa = l.proposed_action || {};
            const bt = l.backtest;
            return (
              <li key={l.id} className="lesson">
                <div className="pol-h"><span className="chip bad">Mistake</span><span className="chip info">Lesson proposed by Pip</span><span className="mono">{l.id} · from {l.incident_id}</span></div>
                <dl>
                  <dt>Incident</dt><dd>{inc ? `${inc.severity} ${inc.kind.replace("_", " ")}: ${inc.details.summary}` : l.incident_id}</dd>
                  <dt>What happened</dt><dd>{l.what_happened}</dd>
                  <dt>Root cause</dt><dd>{l.root_cause}</dd>
                  <dt>Rule</dt><dd><b>{l.rule_text}</b></dd>
                  <dt>Exact change</dt>
                  <dd>
                    {pa.type === "policy" && <div className="code">{`new policy (tighten only):\n${JSON.stringify({ when: pa.policy.when, then: pa.policy.then, route_on: pa.policy.route_on }, null, 1)}`}</div>}
                    {pa.skill && <div className="code" style={{ marginTop: 6 }}>{`skill ${pa.skill} v1 → v2, adds:\n${pa.patch_text.trim()}`}</div>}
                  </dd>
                  <dt>Evidence</dt><dd><RefChips ids={l.evidence || []} onOpen={setRec} /></dd>
                </dl>
                {bt && <div className="bt"><b>Replay test:</b> run on {bt.n} cases (recent paid history plus shuffled variants of this incident). Caught {bt.caught} of {bt.bad} bad ones, missed {bt.missed}, and wrongly held {bt.wrongly_held} good bill{bt.wrongly_held === 1 ? "" : "s"}.</div>}
                <div className="acts" style={{ border: 0, paddingTop: 0 }}>
                  <button className="btn" disabled={busy} onClick={() => lessonDecision(l.id, "reject")}>Reject</button>
                  <button className="btn primary" disabled={busy} onClick={() => lessonDecision(l.id, "approve")}>Approve lesson</button>
                </div>
              </li>
            );
          })}
          {proposedPolicies.map((p) => (
            <li key={p.id} className="pol prop">
              <div className="pol-h"><span className="chip info">Proposed by Pip</span><span className="mono">{p.id} · {p.source}</span></div>
              <p>{p.text}</p>
              <small>Evidence: <RefChips ids={p.evidence} onOpen={setRec} /></small>
              {p.backtest && <div className="bt"><b>Replay test:</b> {p.backtest.n} past cases. Would auto-pay {p.backtest.approved_good} good bills and {p.backtest.approved_bad} bad ones; would hold {p.backtest.caught} bad and {p.backtest.wrongly_held} good.</div>}
              <div className="acts" style={{ border: 0, paddingTop: 0 }}>
                <button className="btn" onClick={() => policyDecision(p.id, "reject")}>Reject</button>
                <button className="btn primary" onClick={() => policyDecision(p.id, "approve")}>Approve policy</button>
              </div>
            </li>
          ))}
          {active.map((p) => (
            <li key={p.id} className="pol">
              <div className="pol-h"><span className="chip">{p.id}</span><span className="pill good">Active</span><span className="mono muted">{p.body.then.replace("_", " ")}</span></div>
              <p>{p.text}</p>
              <small>{p.source === "onboarding" ? "Drafted by Pip from history, approved at onboarding" : p.source === "lesson" ? "Learned from a mistake" : "Learned from your corrections"} · approved by {p.decided_by}{p.backtest ? ` · replay: ${p.backtest.n} cases` : ""}{p.evidence?.length ? " · " : ""}<RefChips ids={p.evidence || []} onOpen={setRec} /></small>
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>Mistake log</h2>
        <p className="lead">Every incident, the lesson it produced, and what changed. Each approved lesson adds a regression case that must keep passing.</p>
        {incidents.length === 0 ? <p className="empty">No incidents yet.</p> : (
          <ul className="pols">
            {incidents.map((i) => {
              const l = lessons.find((x) => x.incident_id === i.id);
              return (
                <li key={i.id} className="pol">
                  <div className="pol-h"><span className={"chip " + (i.severity === "critical" ? "bad" : "warn")}>{i.severity}</span><span className="mono">{i.id} · {pt(i.at)} · detected by {i.detected_by}</span></div>
                  <p>{i.details.summary}</p>
                  <small>Bills: <RefChips ids={i.details.work_items || []} onOpen={(id) => ctx.open(id)} />{l ? ` → ${l.id} (${l.status})` : " → no lesson yet"}{l?.status === "approved" ? ` → ${l.rule_text}` : ""}</small>
                </li>
              );
            })}
          </ul>
        )}
        {skills.versions.length > 0 && (
          <>
            <h4 className="eyebrow" style={{ marginTop: 16 }}>Skill versions</h4>
            <ul className="lims">{skills.versions.map((v: any) => <li key={v.skill + v.version}><span className="mono">v{v.version}</span><span>{v.skill} · {v.source_lesson ? `from ${v.source_lesson}, approved by ${v.approved_by}` : "initial"} · {pt(v.at)}</span></li>)}</ul>
          </>
        )}
      </div>

      <div className="card">
        <h2>Work record</h2>
        <p className="lead">Every step Pip, the gateway and people took, including the ones that were denied. Each row is linked to the one before by a SHA-256 fingerprint.</p>
        <div className="ctrl">
          <button className="btn" onClick={doVerify}>Verify the record</button>
          <button className="btn ghost" onClick={doTamper}>{tampered ? "Restore the edited row" : "Edit an old row (demo)"}</button>
        </div>
        {verify && (verify.ok ? <div className="banner good">Chain intact. {verify.rows} entries, each linked to the one before.</div> :
          <div className="banner bad">Entry #{verify.first_bad_seq} ({pt(verify.at)}, {verify.action} {verify.subject}) was changed after it was written. Everything after it can no longer be trusted.</div>)}
        <div className="logbox">
          <ul className="log">
            {[...log].reverse().map((e) => (
              <li key={e.seq} className={(!e.allowed || (verify && !verify.ok && verify.first_bad_seq === e.seq)) ? "bad" : ""}>
                <span className="mono">#{e.seq}</span>
                <span className="mono">{pt(e.at)}</span>
                <span className="act">{e.action} · {e.actor}</span>
                <span><b>{e.subject}</b> {e.reason}</span>
                <span className="mono">{e.hash.slice(0, 8)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
