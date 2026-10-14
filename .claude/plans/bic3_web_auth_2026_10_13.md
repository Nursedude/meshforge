# BIC-3 — operator login before any web write (grilled 2026-10-13, Q1–Q21)

**Why now:** a trusted-LAN `curl` with JSON and no Origin header keys the radio
today (`POST /api/radio/message`); `/api/settings` has no IP gate at all
(`_map_node_endpoints.py:465`); the dormant toradio/blink/restart proxy routes
(`api_proxy` never assigned, `map_http_handler.py:245`) would reopen ungated.

## Decisions
| # | Decision |
|---|---|
| Q1 | Defend: LAN guest acting (fully); cross-site page (kept). Accepted + documented: LAN sniffing over plain HTTP until BIC-13b. Internet: out of scope (never forwarded). |
| Q2 | Login is for OPERATORS only. Viewing stays as today (LAN trust, no login). |
| Q3 | Default-DENY: every state-changing request needs operator; read-only routes are an explicit allowlist. |
| Q4 | First credential = one-time pairing code printed by a CLI on the box (shell = operator proof). TUI stays a surface. |
| Q5 | `revoke-all` + 30-day sliding expiry. No per-session list. |
| Q6 | Browser session cookie only; no bearer tokens until a real script writer exists. |
| Q7 | Cookie `HttpOnly; SameSite=Strict` (no `Secure` over http); Origin gate kept; cookie + absent Origin still refused unless same-site. |
| Q8 | Region picker (`POST /api/settings`) → operator-only; disabled for viewers with "log in to change". |
| Q9 | `/fleet/logs` → operator-only. Coverage/LOS/fleet/federation reads stay viewer. |
| Q10 | `~/.config/meshforge/web_sessions.json`, 0600, SHA-256 hashes only, survives restart, atomic rename under flock. Unreadable → all refused with reason; corrupt → moved aside + logged, never silently re-created. JSON, not SQLite (no DBSpec). |
| Q11 | Per-box sessions. Federation is GET-only, unaffected. |
| Q12 | Field lockout accepted (phone SSH client; pair before leaving; sliding expiry; BIC-15 for reads). |
| Q13 | Refusal = 401 JSON `{"error":"operator login required","how":"on the box: python3 scripts/meshforge_web.py pair"}`; logged once/client/min; `auth_refusals` counter on `/api/status`. |
| Q14 | MeshForge first, then port to MeshAnchor's twin gate with the same contract (verify MA's gap there, don't assume). |
| Q15 | CLI `python3 scripts/meshforge_web.py pair \| revoke-all \| status`, run as the operator; no installed binary. |
| Q16 | Code: 8 chars, no look-alike letters (~40 bits), 5-min expiry, single use, burns after 5 wrong tries. |
| Q17 | CLI writes the pending code's HASH into the same sessions file under the same lock; no IPC. |
| Q18 | `/login` page (one field) → cookie → back to origin page; header shows `viewer · log in` / `operator · log out`; logout ends only that browser's session. |
| Q21 | Canary on VolcanoAI + the Q20 drill, then fleet deploy the same session. |

## Tests — each RED on today's code first (Q19)
1. Trusted-LAN POST `/api/radio/message`, no cookie, no Origin → 401, nothing sent.
2. `/api/settings` without a session, from any client → 401.
3. `/fleet/logs` without a session → 401.
4. Route census: every state-changing route is operator-gated unless on the read-only allowlist; a new undecided route fails (covers toradio/blink/restart).
5. Pairing: good code → cookie; 5 wrong → burned; expired / reused → refused.
6. Unreadable / corrupt sessions file → every write refused with reason; corpse moved aside, not overwritten.
7. `revoke-all` kills live cookies; 30-day idle session refused.
8. Every in-repo GET reader still answers without a cookie (honest_status, fleet tooling, federation, `/metrics`).

## Live acceptance on VolcanoAI (Q20) — no RF keyed
1. From another LAN host: curl a transmit → 401, `auth_refusals` +1, no `Send` in the meshtasticd journal.
2. Pair from the phone → log in → change region → succeeds.
3. `revoke-all` → the phone is refused again.
4. Corrupt the sessions file → every write refused with reason; corpse moved aside.
