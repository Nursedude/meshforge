# v0.8.0-beta — DRAFT (not tagged; REL-1 rule 3 clock restarted by the 11-24 `fix:`/`security:` commits → earliest ≈12-01)

Source: `git log v0.7.0-beta..HEAD` feat/fix/security on product paths = 221 commits
(10-15 → 11-16; extended 11-25 through `94ee48f2`, 11-16 → 11-24). Every line below names its commit so the release page can be re-derived.
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

- **"Heard by" is honest about what it cannot see.** A send that no fleet radio
  could judge says why, per send — "this box cannot see hearings" on a one-box
  install, a radio on the other preset "cannot hear" it, and hearings already
  recorded stay shown (marked "kept") when a later read goes blind. The inbox also
  says when the radio heard traffic the inbox cannot show. (87f3a192, 638e422c,
  69bd53ad, a72ebe1e, 8137020d)
- **Pair a browser from the TUI.** About → Web Login → Pair a browser shows a code
  and the clock time it expires; on a phone the code box opens in capitals.
  (1a7abd31, 98a788aa, a3bccf0f)
- **The map stops drawing what nobody measured.** A message line is drawn only for
  a packet heard direct; a relayed or unknown-hop packet says "N hops — path not
  measured" instead of a straight line. Every source dark reads BLIND, never "ok,
  0 nodes". A node heard here without GPS keeps its own record; a peer box's
  position for it is shown as borrowed. (e68094df, 044efb13, e8c82d8d, 49083cb7)
- **RNS diagnostics show filtered traffic.** Per interface: packets accepted vs
  FILTERED, and a Down interface is listed as an issue. (14e6e9ac, 6b8eb5f4)

## Reliability

- Gateway queue: during an outage, messages wait (up to 24 h) instead of being
  counted as failed. (3c10f362)
- Map: one overall collection deadline — a slow source is marked late, the rest
  still draw. (7ad440fe)
- Downlink: every injected message's outcome is counted and stalls are witnessed in
  the gateway status and the TUI. (0f53e80b)
- Bridge, MQTT, gateway, watchdog: ~100 smaller fixes (mqtt 19, gateway 16,
  watchdog 15, map 13, mesh_bridge 11, delivery 11, downlink 9, bridge 9).

- A radio's USB port being present no longer reads as "radio connected"; the
  process holding it decides. (0d7b0295)
- Opening the TUI no longer restarts a service you stopped this boot. (195f52a7)
- Bridged messages name the sender from the packet itself, never from its text. (bbae6ee8)
- rnsd config checks read the config file the way rnsd does — one reader, as
  rnsd's user; "can't tell" is reported as unknown, not as broken. (918d1412 … 5e662c30)

## Security

- Grafana removed (dashboards, installer, wildcard CORS endpoints). (fc1fbbdd)
- RNS storage handed to rnsd's own user, symlink-safe — no more 0777/0666. (1377d17c, 5503d7da, e5154e0f)
- MPTCP disabled via a sysctl drop-in installed by update.sh. (ba1ce93e)

- **Passwords are typed masked and never shown.** Every MQTT password, broker
  setup and custom channel key prompt shows stars; MeshForge keeps no stored copy
  of a secret; Radio Info and backup restore never print a password or channel
  URL. Each menu that must pass a key to a command says so once, in one line.
  (52847ae5, d0f0b490, 31016d35, 4d0cb1ec, 94ee48f2)
- Login: wrong-code limits count per code, under one lock — racing clients can't
  burn or stretch a pairing code. (ceb5bc6b, 1cd92808)

## For operators

- `rnsd_ordered_restart.py` — the safe rnsd restart order as a tested command. (e8399b1d)
- New `lab` role for a radio-less bench box. (85e712e3)

## Known limits (say them)

- "Heard by" covers broadcasts heard by YOUR fleet's radios. DMs and bridged legs
  (mesh ↔ RNS) do not yet show per-leg delivery.
- On a one-box install, "heard by" says it cannot see hearings — it needs other
  fleet boxes listening.
- "Heard by" says WHEN each box heard a send, not how well (no SNR or hops recorded).
