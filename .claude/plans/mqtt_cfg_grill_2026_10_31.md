# MQTT-CFG — grill decisions (2026-10-31, operator WH6GXZ + Opus 5.5)

> DECISIONS ONLY — the plan is written AFTER the 10-31 BIC-2 kill verdict, and
> a contextless reviewer attacks the PLAN before any code (Q10).
> ROADMAP item: `MQTT-CFG`. Why: three non-author review rounds on reader-side
> fixes (a30390ae → b8bcb7b5 → d583e37e) each found ~10 real cases; the
> root defect is several writers / two schemas in one file. Cure at the WRITE
> boundary. Evidence + residuals: `.claude/audits/review_provenance.md` 10-31.

## Facts (measured 10-31, read-only fact-finder)
- Writers (MF): TUI `save_mqtt_config` via local preset, public preset,
  Configure "Save & Exit" (never updates root_topic/channel), private-broker
  form (writes topic + root_topic + channel), `broker.py:749` broker profiles;
  `MQTTNodelessSubscriber.save_config` (0 callers). **No TUI writer sets 0600.**
- MA uses a SEPARATE path `~/.config/meshanchor/mqtt_nodeless.json`; MA has
  none of today's MF shared reader; still duplicates the topic translation;
  MA `save_mqtt_config` returns None (callers can't see a failed save).
- Fleet: the file exists on NO box (11 checked, operator + root homes).

## Decisions
| # | Decision |
|---|---|
| Q1 | Store INTENT only: broker, port, tls, username, password, root_topic, channel, key, auto_start, auto_start_telemetry. `topic` and reconnect delays are DERIVED, never stored. |
| Q2 | `tls`: `"auto"` (= port 8883) \| `true` \| `false`; default auto. |
| Q3 | One owner module (`monitoring/mqtt_settings.py`): load()/save(); every writer goes through save(); a regression guard fails on any other writer of the filename. |
| Q4 | Validate at SAVE (port 1–65535, non-empty strings, known tls) and refuse in the TUI; a hand-edited bad file loads as "unreadable" (three-state), never defaults. |
| Q5 | Daemon precedence unchanged: saved → explicit daemon.yaml/global.ini broker/port → local mosquitto. |
| Q6 | `schema_version: 1`; newer version refused loudly; first save over a legacy file keeps `.pre-v1` (1.0 gate 2 pattern, as `gateway.json`). |
| Q7 | TUI Configure edits Root topic + Channel (not a free Topic); shows "TLS: auto → on"; status screen shows the EFFECTIVE subscription from the same function the subscriber uses. |
| Q8 | Root-unit operator identity: an explicit `MESHFORGE_OPERATOR=<user>` honoured first by the home resolver — a SEPARATE small item (touches the shared resolver); all of the daemon's home lookups move with it, stated in the unit comment. |
| Q9 | The module is BYTE-IDENTICAL in MF and MA, tracked by `scripts/parity_check.py`. |
| Q10 | A contextless reviewer attacks the written PLAN before code. |
| Q11 | The module takes the app config dir as a parameter (one tiny per-repo constant) — MA's path differs. |
| Q12 | save() writes 0600, atomically (tmp + rename) — credentials are world-readable today. |
| Q13 | Migration: convert a legacy file once only when unambiguous (≥4 segments, no `/json/`, or a consistent topic/root pair), keep `.pre-v1`; otherwise "unreadable" naming the field — never guess. |
| Q14 | `regions` / `json_channel` not stored (only the uncalled save_config wrote them); `json_channel` derived: `+` for a local broker, the channel for a remote one. |
| Q15 | VolcanoAI's pre-15:26 file had nothing custom (operator) — defaults in effect are correct; the 15:26 overwrite was a test artifact (quarantined `*.test-artifact-20261031T1526`, guard `tests/conftest.py` `_OPERATOR_STORE_MODULES`). |

## Purpose test
END: the truth told in-app (status shows the running subscription; a bad
save is refused where it is made). Gates: 1.0 gate 2 (config upgrades) +
supports gate 7. Row: the MQTT row in `docs/capabilities.md`.
