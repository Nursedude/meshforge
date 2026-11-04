# F8 v2 — V11 live acceptance drill (manager box, operator at the keyboard)

Deployed fleet-wide 2026-11-03 13:12 HST (MF `4bc60490`). This drill is the
acceptance V11 pre-registered BEFORE code (plan § F8 v2). Every step says
what you should SEE; a mismatch is a finding — stop and note it.

Two terminals on VolcanoAI, both: `scripts/meshforge-launcher.sh`.
Path in both: `2. Mesh Networks → MQTT Monitor → Configure`.

## 0. Baseline (terminal B shell, before opening any TUI)
```
date; cp -p ~/.config/meshforge/mqtt_nodeless.json /tmp/mqtt_before.json
grep -E '"auto_start' ~/.config/meshforge/mqtt_nodeless.json
cat ~/.config/.meshforge.mqtt-edit.lease; echo "[lease end]"
```
SEE: two auto_start lines; the lease file empty (or absent).

## 1. One editor at a time
- A: open Configure → the list appears.
- B: open Configure → **"MQTT Configuration — View Only"** naming A's user,
  `pid N`, tty, since-time; password shown only as `(saved)` / `(none)`.

## 2. First save
- A: toggle **Auto Telemetry** → Save & Exit → "Saved" box. Leave the menu.

## 3. Second session sees it, saves its own
- B: Esc out, open Configure again → now EDITABLE; the Auto Telemetry row
  shows A's change. Toggle **Auto-Start** → Save & Exit.
- Shell: `grep -E '"auto_start' ~/.config/meshforge/mqtt_nodeless.json`
  SEE: BOTH lines changed vs step 0.
- Shell: `diff <(grep -v auto_start /tmp/mqtt_before.json) <(grep -v auto_start ~/.config/meshforge/mqtt_nodeless.json)`
  SEE: no output (nothing else moved — the password included).

## 4. A write without the lease is refused, writes nothing
- A: open Configure, change **Channel** to `DrillX` (do NOT save yet).
- Shell: `echo >> ~/.config/meshforge/mqtt_nodeless.json`
  (one extra blank line = a writer that skipped the lease; the file still
  loads; step 7's save rewrites it clean)
- A: Save & Exit → **"Not Saved — Changed Elsewhere"**; the screen closes.
- Shell: `grep -c DrillX ~/.config/meshforge/mqtt_nodeless.json` → SEE `0`.

## 5. The password never follows a broker change
- A: open Configure → **Broker** → `mqtt.example.invalid` → Save & Exit.
  SEE: a **Password** prompt naming `<user>@mqtt.example.invalid` and
  never showing the saved password. Press **Esc**.
- SEE: back at the list, nothing written. Choose **Cancel**.
- Shell: `grep -c example.invalid ~/.config/meshforge/mqtt_nodeless.json` → `0`.

## 6. Esc never writes / lease released
- Shell: `cat ~/.config/.meshforge.mqtt-edit.lease; echo "[lease end]"` → empty.

## 7. Restore
- One TUI: Configure → toggle Auto Telemetry and Auto-Start back to step 0
  → Save & Exit. Close every TUI.
- Shell: `diff /tmp/mqtt_before.json ~/.config/meshforge/mqtt_nodeless.json && echo RESTORED`
- Restart the subscriber only if it was running before (MQTT Monitor → Stop → Start).

Report: PASS/FAIL per step (1–7) and anything that surprised you.

## RESULT — 2026-11-03 14:12–14:31 HST — **PASS** (operator at the keyboard; Opus 5.5 watching)
Independent witness: a 1 s file watch (settings sha256 + lease record) the model ran
read-only alongside the drill; holder confirmed by `ps` + `/proc/locks` (FLOCK WRITE).

| Step | Result | Evidence |
|---|---|---|
| 0 | PASS | auto_start false / telemetry true; lease absent; watch baseline `f268aec60696` |
| 1 | PASS | B: View Only naming pid 3692822 /dev/pts/4 since 14:18:41 = kernel lock holder |
| 2 | PASS | A saved 14:21:55 (`296b20f97549`) under its lease; released 14:22:10 on OK |
| 3 | PASS | B (pid 3698974 pts/5) saved 14:22:57; both fields changed; `diff` (minus auto_start) empty |
| 4 | PASS | lease-less `echo >>` at 14:25:32 → A "Not Saved — Changed Elsewhere"; hash held; DrillX 0 |
| 5 | N/A here | no saved MQTT password on this box → nothing to protect; tests cover it; live owed on a box WITH one |
| 6 | PASS | lease file empty |
| 7 | PASS | 2 restore passes (first missed one toggle — the watch's hash named which); final hash `f268aec60696`, `diff` RESTORED |

Legibility finding (LOW, next build): the Changed Elsewhere box says "nothing written" twice.
Ledger: claim `d204056393e2` (verified, manual).
