# v0.8.0-beta — DRAFT (not tagged; earliest 11-21 under REL-1 rule 3)

Source: `git log v0.7.0-beta..HEAD` feat/fix/security on product paths = 221 commits
(10-15 → 11-16). Every line below names its commit so the release page can be re-derived.
⚠️ Draft for the operator's edit — wording is mine, facts are from commit subjects only
(BELIEVED until each is re-checked against the shipped behaviour).

## What you'll notice

- **The inbox tells you who heard your message.** Send a broadcast from the web
  inbox (phone or laptop) and each send shows which of your own fleet radios
  decoded it — "heard by moc, kiai, lehua …" — and which ones MeshForge cannot see
  right now. It never says "delivered": a broadcast has no acknowledgement, so it
  only reports what radios actually heard. (a568fbd5, 0f83ead6, f97e053f, 2da1bfe9)
- **No more duplicate inbox rows.** A message your own radio echoes back now shows
  once, matched on the radio's packet id, never on matching text. (c9c00b61, 926df069)
- **Phones stay logged in.** Using the web pages from a phone no longer logs you out
  30 days after pairing; the login renews while you use it. (64d76031)
- **"Blind" is shown, not hidden.** A channel this box can't hear (no MQTT uplink)
  says so instead of looking like a quiet channel. (e09a8d87)
- **Delivery legs.** The TUI has a "Delivery legs" pane: per-route message counts
  over 15 min / 1 h / 24 h, including expired queue entries. (c808b2df, 3d835e90, 5575b41d)
- **How-to guides, offline.** About → How-to Guides: four guides that work with no
  internet. (73b2cb3f)
- **MQTT settings have one owner.** The TUI and broker profiles edit one settings
  file instead of overwriting each other. (3248408f, 0cfef4d7)

## Reliability

- Gateway queue: during an outage, messages wait (up to 24 h) instead of being
  counted as failed. (3c10f362)
- Map: one overall collection deadline — a slow source is marked late, the rest
  still draw. (7ad440fe)
- Downlink: every injected message's outcome is counted and stalls are witnessed in
  the gateway status and the TUI. (0f53e80b)
- Bridge, MQTT, gateway, watchdog: ~100 smaller fixes (mqtt 19, gateway 16,
  watchdog 15, map 13, mesh_bridge 11, delivery 11, downlink 9, bridge 9).

## Security

- Grafana removed (dashboards, installer, wildcard CORS endpoints). (fc1fbbdd)
- RNS storage handed to rnsd's own user, symlink-safe — no more 0777/0666. (1377d17c, 5503d7da, e5154e0f)
- MPTCP disabled via a sysctl drop-in installed by update.sh. (ba1ce93e)

## For operators

- `rnsd_ordered_restart.py` — the safe rnsd restart order as a tested command. (e8399b1d)
- New `lab` role for a radio-less bench box. (85e712e3)

## Known limits (say them)

- "Heard by" covers broadcasts heard by YOUR fleet's radios. DMs and bridged legs
  (mesh ↔ RNS) do not yet show per-leg delivery.
- On a one-box install, "heard by" can only show that box — confirm before promising more.
- If your phone's login expires, the page still tells you to run a command on the box;
  in-app re-pairing is the next BIC-2 slice.
