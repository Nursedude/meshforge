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
