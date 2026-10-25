"""Record the demo video automatically: drive the real app with Playwright, narrate with macOS `say`, burn in captions.

  uv run --with playwright python video/record_demo.py          # app on :5173 (API :8000), ERPNext on :8080
  -> video/probation-demo.mp4 (via video/make_video.sh)

Every click goes to the running app; nothing on screen is mocked. The narration is a synthetic voice (macOS Samantha).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.async_api import Page, async_playwright

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "video"
WORK = OUT / "build"
APP = "http://localhost:5173"
ERP = "http://localhost:8080"
VOICE, RATE = "Samantha", 172
W, H = 1920, 1080

SHOTS = [
    ("open", "This is Pip, an A.I. accounts-payable specialist at Harborline Provisions, a food distributor. "
             "An email just arrived: our dairy supplier says it changed banks, and asks us to pay this invoice to a new account. "
             "The amount matches a real purchase order, to the cent."),
    ("held", "Pip reads it, checks the records, and holds it. The bank account is new, the sender's domain is one character off, "
             "and the email tells the A.P. system to skip the callback."),
    ("evidence", "Every decision comes with evidence: what the invoice says, what our records say, and what the bank history says. "
                 "Pip has no write access to anything. A separate gateway, written in plain code, decides. Rule G-04 blocked this "
                 "payment: changed bank details wait for a phone call to the number already on file."),
    ("tighten", "Because the email tried to give Pip instructions, its spending limit drops from five thousand dollars to five "
                "hundred for a week, and the vendor is frozen. A precaution, not a penalty."),
    ("report", "Pip isn't perfect. Yesterday it paid these three bills from Tamarack Provisions. Each was under its limit, but they "
               "are the same purchase order, the same week, about thirteen and a half thousand dollars together. "
               "The controller reports it."),
    ("lab", "Pip writes its own post-mortem: what happened, the root cause, one rule, and the exact change it wants. It replays the "
            "rule on past bills, and on shuffled versions of the mistake, before a human sees it."),
    ("approve", "Lessons can only make Pip stricter. One that tried to raise a limit or skip a check would be rejected. "
                "Nothing changes until the controller approves."),
    ("repeat", "Now a different vendor tries the same trick, split four ways. Pip catches it before a cent moves, and the gateway "
               "routes all four bills to the controller, on the combined amount."),
    ("score", "We measure this on three hundred labeled bills. Before the lesson, split bills got through every time. After one "
              "approved lesson, every one is caught and no bad bill is paid, while three quarters of clean bills are still paid "
              "without anyone touching them."),
    ("audit", "Every step is in a hash-chained work record. If anyone edits an old row, verification points straight at it."),
    ("erp", "And the payments are real entries in E.R.P. Next, the system of record. The model proposes. Code decides."),
]

OVERLAY_JS = """
() => {
  if (window.__ov) return;
  window.__ov = true;
  document.body.style.zoom = '1.2';
  const s = document.createElement('style');
  s.textContent = `
    #__cap{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);max-width:1500px;z-index:99999;
      background:rgba(17,23,20,.88);color:#e7eee9;font:500 25px/1.35 'Public Sans',Arial,sans-serif;padding:14px 22px;
      border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.35);text-align:center;transition:opacity .25s}
    #__dot{position:fixed;width:26px;height:26px;border-radius:50%;background:rgba(31,75,143,.30);border:3px solid #1f4b8f;
      z-index:99998;pointer-events:none;transform:translate(-50%,-50%);transition:left .55s ease,top .55s ease;left:50%;top:40%}
    .__hl{outline:4px solid #e0a845 !important;outline-offset:4px !important;border-radius:8px;transition:outline .2s}`;
  document.head.appendChild(s);
  const c = document.createElement('div'); c.id = '__cap'; c.style.opacity = 0; document.body.appendChild(c);
  const d = document.createElement('div'); d.id = '__dot'; document.body.appendChild(d);
}
"""


def say(text: str, path: Path) -> float:
    subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(path), text], check=True)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out)


def api(path: str, body: dict | None = None):
    req = urllib.request.Request(APP + "/api" + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"}, method="POST" if body is not None else "GET")
    return json.load(urllib.request.urlopen(req, timeout=300))


async def overlay(page: Page) -> None:
    await page.evaluate(OVERLAY_JS)


async def caption(page: Page, text: str) -> None:
    await overlay(page)
    await page.evaluate("t => { const c = document.getElementById('__cap'); c.textContent = t; c.style.opacity = t ? 1 : 0; }", text)


async def point(page: Page, loc, click: bool = True, hl: bool = True) -> None:
    await loc.scroll_into_view_if_needed()
    box = await loc.bounding_box()
    if box:
        await page.evaluate("([x, y]) => { const d = document.getElementById('__dot'); if (d) { d.style.left = x + 'px'; d.style.top = y + 'px'; } }",
                            [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        await page.wait_for_timeout(650)
    if hl:
        await loc.evaluate("e => { e.classList.add('__hl'); setTimeout(() => e.classList.remove('__hl'), 1600); }")
        await page.wait_for_timeout(350)
    if click:
        await loc.click()


async def scroll_to(page: Page, loc, offset: int = 120) -> None:
    await loc.evaluate(f"e => window.scrollTo({{top: e.getBoundingClientRect().top + window.scrollY - {offset}, behavior: 'smooth'}})")
    await page.wait_for_timeout(700)


async def tab(page: Page, name: str) -> None:
    await point(page, page.locator("nav.tabs button", has_text=name).first)
    await page.wait_for_timeout(500)
    await overlay(page)


async def run_shot(page: Page, key: str) -> None:
    if key == "open":
        await page.wait_for_timeout(1200)
        await point(page, page.get_by_role("button", name="Play the planted email"))
    elif key == "held":
        await page.wait_for_timeout(1500)
        await page.locator("h2.sec", has_text="Held back").wait_for(timeout=120000)
        await scroll_to(page, page.locator("h2.sec", has_text="Held back"))
        await point(page, page.locator("section", has_text="Held back").locator("li.card").first, click=False)
    elif key == "evidence":
        await point(page, page.locator("section", has_text="Held back").get_by_role("button", name="See evidence").first)
        drawer = page.locator("aside.drawer")
        await drawer.wait_for()
        await page.wait_for_timeout(1500)
        for frac in (0.25, 0.5, 0.75):
            await drawer.evaluate(f"d => d.scrollTo({{top: d.scrollHeight * {frac}, behavior: 'smooth'}})")
            await page.wait_for_timeout(2600)
    elif key == "tighten":
        await point(page, page.locator("aside.drawer").get_by_role("button", name="Close").first)
        await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        await page.wait_for_timeout(800)
        await point(page, page.locator("header .chip").first, click=False)
        await page.wait_for_timeout(1200)
        await point(page, page.locator("aside.rail .note-box").first, click=False)
    elif key == "report":
        paid = page.locator("h2.sec", has_text="Paid")
        await scroll_to(page, paid)
        await point(page, page.get_by_role("button", name="Report a problem"))
        await page.wait_for_timeout(600)
        rows = page.locator("ul.paid li", has_text="Tamarack Provisions")
        for i in range(await rows.count()):
            await point(page, rows.nth(i).locator("input[type=checkbox]"))
            await page.wait_for_timeout(300)
        await point(page, page.get_by_role("button", name="One purchase, split to dodge the limit"))
    elif key == "lab":
        await page.wait_for_timeout(1500)
        await tab(page, "Pip's record")
        lab = page.locator("h2", has_text="Policy Lab")
        await lab.wait_for()
        await scroll_to(page, lab, 90)
        await page.wait_for_timeout(1200)
        await point(page, page.locator(".lesson dl").first, click=False)
    elif key == "approve":
        await point(page, page.locator(".lesson .code").first, click=False)
        await page.wait_for_timeout(1500)
        bt = page.locator(".lesson .bt").first
        if await bt.count():
            await point(page, bt, click=False)
            await page.wait_for_timeout(1500)
        await point(page, page.get_by_role("button", name="Approve lesson"))
    elif key == "repeat":
        await page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        await page.wait_for_timeout(600)
        await point(page, page.get_by_role("button", name="Play the split repeat"))
        await tab(page, "Today")
        chips = page.locator("li.card .chip", has_text="Split to dodge limit")
        for _ in range(240):
            if await chips.count() >= 4:
                break
            await page.wait_for_timeout(500)
        needs = page.locator("h2.sec", has_text="Needs you")
        await scroll_to(page, needs)
        await point(page, chips.first, click=False)
    elif key == "score":
        await tab(page, "Scorecard")
        await page.wait_for_timeout(1000)
        await point(page, page.locator(".kpi").first, click=False)
        await page.wait_for_timeout(1200)
        await point(page, page.locator(".kpi").nth(1), click=False)
        await page.wait_for_timeout(1500)
        await scroll_to(page, page.locator("h2", has_text="Recall per planted problem"), 80)
    elif key == "audit":
        await tab(page, "Pip's record")
        work = page.locator("h2", has_text="Work record")
        await work.wait_for()
        await scroll_to(page, work, 80)
        await point(page, page.get_by_role("button", name="Edit an old row (demo)"))
        await page.wait_for_timeout(1400)
        await point(page, page.get_by_role("button", name="Verify the record"))
        await page.locator(".banner.bad").wait_for(timeout=10000)
        await point(page, page.locator(".banner.bad"), click=False)
    elif key == "erp":
        await page.request.post(ERP + "/api/method/login", data={"usr": "Administrator", "pwd": "admin"})
        await page.goto(ERP + "/app/purchase-invoice?docstatus=1")
        await page.wait_for_timeout(3500)
        await overlay(page)


async def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    audio = []
    for i, (key, text) in enumerate(SHOTS):
        p = WORK / f"{i:02d}-{key}.aiff"
        audio.append((key, text, p, say(text, p)))
    print(f"narration: {sum(a[3] for a in audio):.0f}s over {len(audio)} shots")
    print("reset:", api("/sim/reset", {})["tier"])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(WORK), record_video_size={"width": W, "height": H},
                                        color_scheme="light")
        page = await ctx.new_page()
        t0 = time.monotonic()
        await page.goto(APP + "/#today")
        await page.locator("nav.tabs").wait_for()
        await overlay(page)
        timeline = []
        for key, text, path, dur in audio:
            start = time.monotonic() - t0
            await caption(page, text)
            await run_shot(page, key)
            if key != "erp":
                await overlay(page)
            await caption(page, text)
            remaining = start + dur + 0.7 - (time.monotonic() - t0)
            if remaining > 0:
                await page.wait_for_timeout(int(remaining * 1000))
            timeline.append((start, path))
            print(f"  {key:9} at {start:6.1f}s ({dur:.1f}s of voice, shot took {time.monotonic() - t0 - start:.1f}s)")
        await caption(page, "")
        await page.wait_for_timeout(800)
        video_path = await page.video.path()
        await ctx.close()
        await browser.close()
    api("/audit/tamper", {})  # put the edited row back
    # lay each narration line at the moment its shot started
    inputs = ["-i", str(video_path)]
    filters = []
    for n, (start, path) in enumerate(timeline, 1):
        inputs += ["-i", str(path)]
        filters.append(f"[{n}:a]aresample=48000,adelay={int(start * 1000)}|{int(start * 1000)}[a{n}]")
    mix = "".join(f"[a{n}]" for n in range(1, len(timeline) + 1))
    filters.append(f"{mix}amix=inputs={len(timeline)}:normalize=0,apad[aout]")
    raw = WORK / "raw.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", "0:v", "-map", "[aout]",
                    "-shortest", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(raw)],
                   check=True)
    subprocess.run([str(OUT / "make_video.sh"), str(raw), str(OUT / "probation-demo.mp4")], check=True, cwd=REPO,
                   env={**os.environ, "REPO_URL": os.environ.get("REPO_URL", "github.com/your-team/probation")})


if __name__ == "__main__":
    asyncio.run(main())
