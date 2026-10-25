# Demo video (YouTube, 2:45 to 3:00)

Devpost wants 3 to 5 minutes at most; judges watch the first 30 seconds closely. **Open with the moment**, not with a
title or an architecture slide. `video/make_video.sh` adds a 4.5 s title card before and a 6 s closing card after
your recording, so the recording itself should be about 2:40.

## 1. Before you record (5 minutes)
1. `make demo` (or, if it is already running, press **Reset** in the top bar). Check Today shows "3 bills need your
   call" and the Pip card says Level 2 · $5,000.
2. Browser: one window, about 1440 x 900, zoom 110%, bookmarks bar hidden, other tabs closed, notifications off
   (Control Centre → Focus → Do Not Disturb). Open a second tab on ERPNext, logged in, on the **Purchase Invoice** list.
3. Microphone: AirPods or the Mac mic in a quiet corner. Say one test sentence and play it back.
4. Record with macOS: **Cmd + Shift + 5** → *Record Selected Portion* → drag over the browser window →
   *Options* → Microphone: your mic → **Record**. Stop from the menu bar icon. The file lands on the Desktop as a `.mov`.
5. If you stumble, keep going: pause two seconds and repeat the sentence. You can cut it later in QuickTime
   (*Edit → Trim*) or just re-record; the whole take is under 3 minutes.

## 2. Shot list and narration (read it like a teleprompter)
| # | Time | On screen (you do) | Say |
|---|---|---|---|
| 1 | 0:00 | Today tab. Click **Play the planted email**. The Live card fills in. | "This is Pip, an AI accounts-payable specialist at a food distributor. An email just came in: our dairy supplier changed banks, please pay this invoice to the new account. The amount matches a real purchase order to the cent." |
| 2 | 0:20 | Point at the Live card as steps appear, then the bill lands in **Held back**. | "Pip reads it, checks the records, and holds it. The account is new, the sender's domain is one character off, and the email tells the AP system to skip the callback." |
| 3 | 0:35 | Click **See evidence**. Scroll slowly through the checks. | "Every decision comes with evidence: what the invoice says, what our records say, and what the bank history says. Pip didn't pay it, and couldn't have. Pip has no write access to anything. A separate gateway, plain code, decides. This was blocked by rule G-04: changed bank details wait for a phone call to the number already on file." |
| 4 | 0:55 | Close the drawer. Point at the header chip: **tightened**, and the Pip card note. | "Because the email tried to give Pip instructions, its spending limit drops from five thousand dollars to five hundred for a week, and the vendor is frozen. A precaution, not a penalty." |
| 5 | 1:08 | Scroll to **Paid**. Click **Report a problem**, tick the three Tamarack bills (same PO), click **One purchase, split to dodge the limit**. | "Pip isn't perfect. Yesterday it paid these three bills. Each was under its limit, but they're the same purchase order, the same week, about thirteen and a half thousand dollars together. That should have gone to the controller." |
| 6 | 1:25 | Toast appears. Click the **Pip's record** tab, scroll to **Policy Lab**. | "So I report it. Pip writes its own post-mortem: what happened, the root cause, one rule, and the exact change it wants. It replays that rule on past bills and on shuffled versions of the mistake before I see it." |
| 7 | 1:45 | Point at *Exact change* and the *Replay test* line. Click **Approve lesson**. | "Lessons can only make Pip stricter. A lesson that tried to raise a limit or skip a check would be rejected by the validator. Nothing changes until a human approves." |
| 8 | 1:58 | Top bar: **Play the split repeat**. Go to **Today**: four Northgate bills appear as *Split to dodge limit*, *needs Controller*. | "Now a different vendor tries the same trick, split four ways this time. Pip catches it before a cent moves, and the gateway routes it to the controller on the combined amount." |
| 9 | 2:15 | **Scorecard** tab. | "We measure this on three hundred labeled bills. Before the lesson, split bills got through every time. After one approved lesson: caught, and no bad bills paid, while three quarters of clean bills are still paid without anyone touching them." |
| 10 | 2:30 | **Pip's record** → *Work record*: **Edit an old row (demo)**, then **Verify the record**. Red banner. | "Every step is in a hash-chained record. If anyone edits an old row, verification points straight at it." |
| 11 | 2:40 | Switch to the **ERPNext** tab, refresh the Purchase Invoice list. | "And the payments are real entries in ERPNext, the system of record. The model proposes. Code decides." |

Total about 2:45. If you are long, cut shot 10 first, then shot 9's last sentence.

## 3. Make the final video
```
./video/make_video.sh ~/Desktop/"Screen Recording 2026-10-25 at 7.12.03 AM.mov"
# -> video/probation-demo.mp4 (1080p, 30 fps, loudness-normalised, intro + outro cards)
REPO_URL=github.com/<you>/probation ./video/make_video.sh <raw.mov>   # put your real repo on the closing card
```
Watch it once end to end before uploading. The output is in `video/` and is gitignored (`video/*.mp4`).

## 4. Upload to YouTube
YouTube → **Create → Upload video** → pick `video/probation-demo.mp4`. Visibility: **Unlisted** is enough for Devpost
(anyone with the link can watch); choose Public if you want it findable. Not made for kids. Paste:

**Title**
Probation: hiring an AI accounts-payable agent the way you hire a person (Cal Hacks 13.0)

**Description**
```
Pip is the AP specialist at Harborline Provisions. It reads vendor bills, matches them to purchase orders and goods
receipts, pays what is safe, and asks the right human for the rest.

Probation is the risk layer that lets a finance team hire it:
- The model proposes, plain code decides. Pip has no write access; a gateway enforces tiers, the approval matrix and
  hard limits (bank changes need a phone check; text inside emails is data, never instructions).
- Trust is earned and lost by rules: Level 0 to 3, promotions after correct decisions, instant demotion after a mistake.
- Every step is in a hash-chained, tamper-evident work record you can ask "why?".
- Pip learns from its own mistakes: incident, post-mortem, human-approved lesson, regression test. Lessons can only
  make it stricter.
- ERPNext is the system of record. Company, vendors, bills and fraud scenarios are synthetic; the spend mix is
  calibrated to a public food distributor's 10-K. Every number shown comes from our labeled evaluation.

Chapters
0:00 Intro
0:05 A fake "we changed banks" email
0:40 Evidence and the gateway's decision
1:13 Pip's mistake: one bill split three ways
1:30 Post-mortem and the Policy Lab
2:03 The same trick, caught
2:20 Scorecard
2:35 Tamper-evident record and ERPNext

Built at Cal Hacks 13.0.
```
The chapter times assume the 4.5 s intro card; adjust after you watch the final file. Chapters need the first one to be
0:00 and at least three of them.

## Optional: rehearse timing with a scratch voice
`sed -n '/^| 1 /,/^| 11 /p' docs/VIDEO.md | awk -F'|' '{print $5}' | tr -d '"' > /tmp/narration.txt && say -r 170 -f /tmp/narration.txt -o /tmp/narration.aiff`
plays the 376-word script in a synthetic voice (about 2:05; your natural pace with pauses is about 2:30 to 2:45) so you can pace the clicks. Use your own voice
for the real video; it is far more convincing.

## Automated version (already made: `video/probation-demo.mp4`)
`uv run --with playwright --with edge-tts python video/record_demo.py` drives the running app in a headless browser,
narrates each shot with a Microsoft neural voice through the edge-tts library (default `en-US-AndrewMultilingualNeural`;
samples of Andrew, Ava and Brian in `video/voice-samples/`), burns in captions, and runs `make_video.sh`. Reset is
automatic. It takes about 3 minutes and produces a 2:35 video. Other voices: `VOICE=en-US-AvaMultilingualNeural`;
offline macOS voice: `TTS=say VOICE=Samantha`. For the most natural voice, ElevenLabs or Azure AI Speech (both need a
key) can replace the `say()` function in `video/record_demo.py`. Chapters for the current file:
```
0:00 Intro
0:05 A fake "we changed banks" email
0:34 Evidence and the gateway's decision
1:06 Pip's mistake: one bill split three ways
1:21 Post-mortem and the Policy Lab
1:45 The same trick, caught
1:56 Scorecard
2:12 Tamper-evident record
2:20 ERPNext
```
Re-run it after any UI change.
