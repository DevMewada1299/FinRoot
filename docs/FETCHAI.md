# Fetch.ai adapter (optional, do only after the core works)

Not imported into CLAUDE.md on purpose. Read this when we decide to enter the Fetch.ai track (target decision point: around midnight Saturday). It must be a thin adapter. Never change the gateway, tiers or tests for it.

## Requirements (from Fetch.ai's Cal Hacks hackpack, https://www.fetch.ai/events/hackathons/calhacks-13-0/hackpack)
Mandatory to be eligible:
- Register at least one agent on Agentverse.
- Implement the Agent Chat Protocol and be discoverable and usable through ASI:One.
- Demonstrate meaningful tool execution or multi-agent orchestration.
- The complete primary workflow runs inside an ASI:One conversation, with no custom frontend.
- Public GitHub repo with run instructions. The README lists each agent's name and address, and carries the two badges Fetch.ai specifies (innovationlab and hackathon tags; copy them exactly from the hackpack).
- Demo video, 3 to 5 minutes.
- Submit on Devpost and also register through the CalHacks ASI:One Submission Agent (link and process video are on the hackpack page).

Bonus: Payment Protocol with a credible monetisation story, multi-agent collaboration, ASI Interactive Cards (forms, review screens), reliability and error recovery, real-time data, an agent that could keep running after the event.

Judging weights: functionality and technical implementation 25%, use of Fetch.ai technology 20%, innovation 20%, real-world impact 20%, UX and presentation 15%.

Prizes: the event page lists $1,250 / $750 / $500 plus two $250 awards, with internship interviews for the top 3. The hacker guide lists $1,500 / $1,000 / $500. Ask a Fetch.ai mentor which is right.

Free-month promo: the event page shows a code that looks like two run together (CALHACKS26 and CALHACKS26AV). Try each.

## Design (thin adapter, ~2-3 hours)
- New top-level folder `fetchai/` with uAgents code. It calls the existing FastAPI (`/inbox/receive`, `/work-items/{id}/decision`, `/ask`, `/agent/state`). No business logic here.
- Preferred shape, which also gets the multi-agent bonus and mirrors segregation of duties:
  - **Clerk agent (Pip)**: receives an invoice or email in chat, runs it through `/inbox/receive`, replies with the outcome and the evidence summary.
  - **Controller agent**: handles approve, reject and phone-verified decisions via chat (Interactive Cards for the review screen if time allows).
  - **Auditor agent**: answers "why was INV-xxxx paid or blocked?" from `/ask`.
- The planted bank-change email must work end to end inside an ASI:One chat: Pip blocks it, the chat shows the tier drop, the controller releases or rejects it.
- Payment Protocol: only if everything else is done. Do not let it touch real money paths; the gateway still decides.

## Pointers
- Docs: https://innovationlab.fetch.ai/resources/docs/ (uAgent creation, ASI-compatible agents, Chat Protocol, Interactive Cards)
- Examples repo: https://github.com/fetchai/innovation-lab-examples
- Agentverse MCP server is available if useful for deploying from Claude Code.

## Checklist
- [ ] Agents registered on Agentverse, README has names, addresses and badges
- [ ] Chat Protocol implemented, agent found via ASI:One
- [ ] Full workflow (invoice in, decision out, approval, "why?") works inside ASI:One
- [ ] Demo video 3 to 5 minutes, separate from the Hyphenate cut
- [ ] Registered through the submission agent and on Devpost
