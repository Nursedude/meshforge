# MQTT-CFG — plan r2 (2026-10-31, Opus 5.5, from the grill + one contextless review)

> Decisions: `mqtt_cfg_grill_2026_10_31.md` (Q1–Q15). This file is HOW, not WHY.
> **Status: r2 — revised after the Q10 contextless plan review (Fable 5.1, 15
> findings: 3 blockers, 10 should-fix). The author re-verified the blockers
> against the code before adopting them (see "Review delta" at the end). No code yet.**
> Roadmap row: `MQTT-CFG` [MF][MA]. MF lands first, then the byte-identical port to MA.

## Verdict in one line
Replace "two schemas + a translator in every reader" with ONE stdlib-only module
that owns the file. save() validates and writes intent through the repo's
ownership-preserving writer. load() has three states. subscriptions() derives
the topics from the subscriber dict, the one thing both the subscriber and the
TUI status read.

## Facts this plan depends on (measured 10-31, author + reviewer, file:line)
- **Who reads the saved file LIVE**: only the sudo TUI (`mqtt.py:139-152`
  auto-start, `:444-453` Start) and kilo ingest (`ingest.py:376`, bare
  constructor + `_config.update` overrides). `meshforge-daemon` is
  `LoadState=not-found` on VolcanoAI and is in no box's `docs/fleet_roles.yaml`;
  its only unit is `User=root` (Q8 territory). The map's subscriber
  (`create_local_subscriber`/`get_local_subscriber`) NEVER reads the file.
- **The TUI writes as root** (`meshforge-launcher.sh:48` `exec sudo`) into
  `~/.config/meshforge` (`<operator> 700`). Today's `open(path,'w')` keeps the
  operator's ownership by truncating in place. A fresh inode made by root would
  not.
- `utils.paths.atomic_write_text_preserving` (MF `:851`, MA `:843`, identical
  except a comment) keeps the existing owner AND MODE, and hands a NEW file to
  the operator. Keeping the mode means a legacy 0644 file stays 0644, so 0600
  (Q12) must be forced after it.
- Subscriptions come ONLY from `root_topic` + `channel` + `json_channel`
  (`mqtt_subscriber.py:430-451`). The json leg is
  `root.rsplit('/',1)[0] + "/json/"`, which is wrong for any root not ending in
  `/e`. The private form's help offers `msh`, `msh/US`, `msh/HI`
  (`mqtt.py:1057-1063`). `regions` has no consumer (only `:201,:417
  comment,:985,:1017,:1037`; MA the same).
- **Writers (all write `use_tls`; presets write `username/password: None`)**:
  local preset (`mqtt.py:~560`, `topic=msh/2/json/<ch>/#`, effective root
  `msh/2/e`); public preset (`~590`); Configure "Save & Exit" (`~682`, edits
  broker/port/auth/topic/auto_start/auto_telem and rewrites a loaded
  root_topic/channel UNCHANGED, which is the stale-pair source); private form
  (`~1115`); broker profiles (`broker.py:749` via `to_tui_config`
  `broker_profiles.py:117-129`; the profile already holds root_topic, channel,
  encryption_key, use_tls at `:66-69`); `MQTTNodelessSubscriber.save_config`
  (`:207`, 0 callers).
- Daemon overlay (`daemon.py:228-238`) pops `use_tls` when the port is overlaid
  and the delays when the broker is overlaid (b8bcb7b5 F3/F4, pinned by 3 tests
  in `test_mqtt_saved_config.py`).
- Bare `MQTTNodelessSubscriber()` returns public defaults on absent AND
  unreadable (`:178-205`). MA's daemon builds a bare subscriber and overlays
  `_config` broker/port/use_tls (`/opt/meshanchor/src/daemon.py:206-227`).
- `telemetry_poll_minutes` is read at `mqtt.py:176` and written nowhere.
- Gates the plan touches: MF025 (`handlers/mqtt.py` is 1389 lines),
  MF026 baseline `'src/launcher_tui/handlers/mqtt.py': 1` (`lint.py:1224`, the
  `open(...,'w')` at `mqtt.py:97`, which must shrink to 0),
  `tests/conftest.py:441-448 _OPERATOR_STORE_MODULES` (the 10-31 guard).
  `scripts/parity_check.py` exists in MF only.

## The module — `src/monitoring/mqtt_settings.py` (byte-identical MF/MA, Q9)
Imports stdlib only. Each repo passes in its config dir and its writer, so
nothing app-specific lives in the module. Messages are app-neutral ("written by
a newer version").

| Name | Contract |
|---|---|
| `SCHEMA_VERSION = 1`, `FILENAME = "mqtt_nodeless.json"` | the module docstring: **any new field = a `schema_version` bump** |
| `INTENT_FIELDS` | broker, port, tls, username, password, root_topic, channel, key, auto_start, auto_start_telemetry (Q1). `telemetry_poll_minutes` is NOT a field; its dead read is deleted in S3 |
| `validate(intent) -> list[str]` | one message per bad field. port is an int 1–65535 and not a bool. broker/channel/key are non-empty str. **root_topic is non-empty and ends in `/e`** (review F9; the private form's help text is fixed with it). tls ∈ {"auto", True, False}. username/password are str; absent == "" (F10). The two auto_* fields are bool. An unknown key is an error (hfm #3) |
| `load(config_dir) -> Loaded` | `state ∈ {ok, absent, unreadable}` plus `intent`, `error` (names the field), `legacy` (bool: re-parsed via `from_legacy`, NOTHING written). Newer `schema_version` → unreadable. An unknown key → unreadable (open choice 1, kept) |
| `save(config_dir, intent, write) -> SaveResult` | `write` = the repo's `atomic_write_text_preserving`, injected (review F1; no duplicated writer). It validates and refuses with the errors, writing nothing. Otherwise: the first save over a non-v1 file copies it to `.pre-v1` once (never overwritten, same writer, then 0600); `write(path, json)`; then `os.chmod(path, 0o600)` (Q12; the owner is preserved by `write`). Returns ok/error and never raises on OSError |
| `effective_tls(intent) -> bool` | `"auto"` → `port == 8883` (Q2) |
| `subscriber_config(intent) -> dict` | what `MQTTNodelessSubscriber(config=…)` takes. Derives `use_tls`, `json_channel` (`"+"` for a localhost/127.0.0.1 broker, else the channel, Q14) and the reconnect delays (local 2/30, remote 5/60) |
| `subscriptions(cfg) -> list[str]` | takes the **subscriber dict**, not the intent (F5), so kilo `--root-topic/--channel` and MA's `_config` overlays still flow. `_subscribe_topics` calls `subscriptions(self._config)`. The TUI status calls `subscriptions(subscriber_config(intent))` |
| `from_legacy(raw) -> (intent, None) \| (None, error)` | Q13, capped at 6 shapes with one fixture each: (a) local preset → `root_topic="msh/2/e"` explicitly + the detected channel; (b) public preset; (c) Configure topic-only, with or without a stale pair. **A ≥4-segment `topic` converts from `topic` (it is the newer edit: d583e37e F2 kept)**; (d) private pair (pair agrees with topic, or no topic); (e) subscriber schema; (f) broker-profile `e`/`json` topics. `use_tls` → `tls`: explicit when it disagrees with `port==8883`, else `"auto"` (F2). `None` creds → "". Anything else → unreadable, naming the field |
| `defaults()` | the public-broker intent |

**Absent vs unreadable at the consumers (F6, stated):**
- **absent**: the TUI Start and the bare constructor use `defaults()`. That is
  today's behaviour; "never configured" is a normal state.
- **unreadable**: the bare constructor keeps `config_error` and `start()`
  returns False, logging the field. kilo's `summary["error"]` carries it
  (`ingest.py:420-425`), so a cron sees a witness, not a traceback. The TUI
  status shows `unreadable: <field>`, and Start refuses.
- **legacy**: works as converted. The TUI status shows "legacy file — Save
  once to convert".

**Daemon overlay (F2, stated):** overlay broker/port onto the INTENT. When
`port` is overlaid, force `tls="auto"`. Then call `subscriber_config()`. The
delays re-derive from the final broker automatically. The 3 pinned F3/F4 tests
must stay green unchanged.

## Slices (one commit each, test-first, main green between every commit)
**S1 — the module + its tests** (`tests/test_mqtt_settings.py`). Red first:
- three-state load; every validate() rule, including `/e`, None creds and an unknown key;
- ownership: `os.geteuid`/`os.fchown` monkeypatched as root, recording the
  call; a pre-existing 0644 file ends 0600 with the owner kept;
- atomic: a failure between write and replace leaves the old file intact;
- `.pre-v1` once; a newer schema refused; the 6 legacy fixtures;
- a `subscriptions()` table (public, private, local, kilo-overridden dict).

No conftest entry needed for S1: the module resolves no home (every test
passes `tmp_path`). **S2 owes the entry**: whichever module gains the
per-repo config-dir helper goes into `conftest._OPERATOR_STORE_MODULES` in
the same commit. No caller changes.
_S1 landed 10-31: 92 tests; mutation check (no-0600, no-`/e`, pair-wins,
`.pre-v1` overwritten) → 1/5/1/1 failures, restored → 92 pass._

**S2 — MF readers + the TUI wrappers together** (F7: no broken intermediate).
- The subscriber's `_load_config` uses `load()`; `_subscribe_topics` uses `subscriptions(self._config)`.
- The daemon uses the intent overlay above (`config_source` and Q5 unchanged).
- kilo's error string references `mqtt_settings.FILENAME`.
- `load_mqtt_config`/`save_mqtt_config` become thin wrappers over load/save in
  the SAME commit, and `saved_mqtt_config_path` stays as a one-line shim until
  S3. Delete `subscriber_config_from_saved`, `_SUBSCRIBER_PASSTHROUGH` and the
  dead `save_config`, together with its test `test_save_config_writes_0600`
  (replaced by S1's).

**Characterization tests DELIBERATELY flipped by Q13** (each named in the
commit, F4):
- `test_short_topic_falls_back_like_the_tui` (`msh/#`: defaults → unreadable);
- `test_empty_saved_uses_the_tui_defaults` (`{}`: defaults → unreadable,
  because an empty object is not "absent").

`test_topic_edited_after_a_private_save_wins_over_the_stale_pair` STAYS green
under shape (c). Every other pinned output is unchanged.

**S3 — MF writers onto it.**
- Every TUI writer builds an intent and calls `save()`. A refusal shows the
  field errors and leaves the file unchanged.
- The local preset sets `root_topic="msh/2/e"`.
- Configure edits Root topic + Channel (no free Topic) and shows `TLS: auto → on`.
- The status screen prints `subscriptions(...)` plus `load().state`.
- `BrokerProfile.to_tui_config` → `to_intent()`, a direct field map
  (`tls=use_tls`, `key=encryption_key`, root_topic, channel) with no topic
  round-trip, so an "unparseable profile" cannot exist (F8). `broker.py`
  still carries auto_start/auto_start_telemetry over.
- Delete the dead `telemetry_poll_minutes` read and the shim; fix the private
  form's root-topic help (`/e` endings).
- Gates: net lines in `handlers/mqtt.py` ≤ 0 (MF025), and remove the MF026
  baseline entry (`test_baseline_only_shrinks`).

**S4 — guard.** In `tests/test_regression_guards.py`, a write
(`open(...,'w'|'a')`, `write_text`, `json.dump` to a path) whose target
resolves through `FILENAME` or the literal, outside `mqtt_settings.py`, fails.
Docstrings and comments are exempt (`broker_profiles.py:118`). **Drill it**:
plant a writer in a scratch copy and watch it FAIL (`scripts/guard_drill.py`),
and record the planted failure.

**S5 — MA port.**
- Copy the module byte-identical and set MA's config dir (`~/.config/meshanchor`).
- MA `save_mqtt_config` returns a bool via `SaveResult`, and MA's duplicate
  topic translation is deleted.
- **MA's daemon** keeps its bare constructor + `_config` overlay (F13), which
  the subscriber-dict `subscriptions()` honours. Absent → defaults, unreadable
  → `start()` False. No Q5/`config_source` port in this item; that is
  documented as a residual.
- Add the file to **MF's** `scripts/parity_check.py` byte-identical tier (MA
  has none, F12).

**S5 MA inventory (read-only survey, 10-31 23:47 — the next session starts here):**
the subscriber reads the RAW file (`/opt/meshanchor/src/monitoring/mqtt_subscriber.py:239`,
so a TUI-shaped file with `topic` and no `root_topic` silently subscribes the
defaults — the original two-schema bug, live in MA); a dead `save_config`
(`:263`); TUI `load_mqtt_config`/`save_mqtt_config` (`handlers/mqtt.py:64,83`)
where save returns None; the daemon builds a bare subscriber (`daemon.py:214`);
no `mqtt_settings.py`. File absent on every box (Q15), so latent, not live.
**Port only after the MF S1–S4 review** (the module must be final before a
byte-identical copy — S2 already changed it once).

**S6 — surface + records.** Update the MQTT row in `docs/capabilities.md` and
the ROADMAP row. Write a provenance row per slice.

**Not in this item:** Q8 (`MESHFORGE_OPERATOR`) is its own small item. Until
it lands, a `User=root` daemon reports "operator home unknown → UNOBSERVED".
No fleet box runs one, so the fleet loses nothing today.

## Acceptance — pre-registered, END = what the RUNNING subscriber did
Run on VolcanoAI (MF) and then meshanchor-server (MA, operator consent). Each
end-to-end line has a baseline run on the PRE-change code first.
- **A1** Private-broker save in the sudo TUI → `stat -c '%U %a'` =
  `<operator> 600`, the file has `schema_version: 1` and no `topic` key. TUI
  **Stop → Start Subscriber**; the TUI status lines are string-equal to the
  subscriber's `Subscribed to:` lines in the TUI log (path named in the run
  record). The same file is then loaded by a NON-root process
  (`python3 -m kilo … --transport mqtt` as the operator), which reaches `ok`.
  *Baseline: pre-change code, same save as root over a root-created file →
  record what it produced.*
- **A2** Configure: change Channel → Save → Stop/Start → both `Subscribed to:`
  lines change (the d583e37e F2 regression as a live check).
- **A3** Port `70000` → refused with the field named; the file's `sha256sum`
  is the same before and after. (The control: the old form accepted
  out-of-range values only where `validate_port` was skipped. Baseline first.)
- **A4** Hand-edit `"port": "abc"` → TUI status reads `unreadable: port`, and
  Start refuses. The kilo run's summary `error` names `port`. No subscriber
  socket to `mqtt.meshtastic.org`, checked with `ss -tnp` **filtered to the
  subscriber's PID** (the mqtt_bridge plugin may hold its own). *Baseline:
  the same edit on the pre-change code connects to the default.*
- **A5 (unit, not END)**: the 6 legacy shapes → S1 fixtures. Live: plant ONE
  shape (the local preset) → status reads "legacy" → Save → `.pre-v1` exists,
  and its sha is unchanged after a second save.
- **A6** `parity_check.py` reports `mqtt_settings.py` identical; the S4 guard
  drill shows a planted writer FAILING.
- **What would still pass if this were dead?** A correct file that no running
  subscriber honours. That is why A1/A2/A4 end at the subscriber's own
  `Subscribed to:` lines and its socket, and why A1 includes a non-root reader.

## Review and deploy gates
r2 plan → operator go → code S1–S4 → `code-review medium` over the MF range
(non-author) → deploy MF (CI green for the SHA → sync → pull → honest_status)
→ A1–A6 → S5 → MA review → MA deploy → A1–A5 on MA.

## Review delta (r1 → r2, Q10 pass 10-31)
The author re-verified these against the code: F1 (launcher `exec sudo`, dir
`700` operator-owned, helper exists in both repos), F2 (`daemon.py:228-238`
pops), F3 (`meshforge-daemon` not-found), F11 (MF026 baseline `:1224`), F12
(MA has no parity_check). The rest were adopted as the reviewer reported them
(CONFIRMED by the reviewer, not re-run by the author). One addition by the
author: `atomic_write_text_preserving` keeps the old MODE as well as the owner,
so `save()` forces 0600 after it. Open choices: 1 kept (unknown key →
unreadable); 2 accepted with the explicit `msh/2/e` local root; 3 kept, capped
at 6 named shapes.

## Run record — 2026-11-01, VolcanoAI, `ada1fb67`
A1 ✅ · A2 PARTIAL (json leg is `+` on localhost by Q14 — needs a non-local
broker) · A3 ✅ (but baseline refuses identically — not discriminating) ·
A4 ✅ (refusal predates S1; the new evidence is the named status + kilo) ·
A5 ✅ · A6 guard ✅ / parity UNKNOWN until S5. Full row + baselines:
`.claude/audits/review_provenance.md` 2026-11-01 "DEPLOY + MQTT-CFG acceptance".

## Run record — 2026-11-01, meshanchor-server, MA `b45e8a58` (13:13–13:54 HST)
Baselines on MA `105d6052` (`git archive` → `/tmp/ma-accept/old`, scratch HOME,
wrapper refused to launch unless the old resolver landed in scratch). A1 ✅ ·
A2 PARTIAL (json `+` on localhost, Q14) · A3 ✅ **discriminating on MA** (the
baseline WROTE 70000) · A4 ✅ (baseline: crashed in connect, said "pending" —
NOT "connects to the default" as predicted) · A5 ✅ · A6 parity ✅ (sha
`5cfb4f7e` both twins). End: the daemon, restarted on the saved file, moved its
json leg LongFast→`+`. Row: `.claude/audits/review_provenance.md` 2026-11-01
"MA MQTT-CFG acceptance".
