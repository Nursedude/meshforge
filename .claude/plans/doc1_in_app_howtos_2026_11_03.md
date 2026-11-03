# DOC-1 — in-app how-tos (operator 2026-11-03)

Operator: "make sure all this work is not lying in the darkness of code and
half done". Audit 2026-11-03 (read-only, all 88 handlers): ONE TUI flow had
step-by-step docs (MQTT Configure, written that day); the rest were named in
a list or absent; `docs/USAGE.md` describes `src/main.py`, `VISUAL_GUIDE.md`
the v0.4.6 menu; About → Help was keyboard shortcuts only.

## Shape
- One copy per guide: `docs/howto/<flow>.md`, read on GitHub AND shown
  offline in About → Help → How-to guides (the standalone-user bar).
- Every guide is checked against the code before it lands; a guard fails
  when a guide's `**Where:**` names a menu row the TUI no longer has.
- A guide that finds a defect gets the defect FIXED red-first, not
  documented (2026-11-03: LoRa DIO Esc wrote; blank Busy kept the old pin).

## Order — who meets it WITHOUT Claude (not raw blast radius)
1. Node DB reset  2. Channel/PSK edit  3. LoRa pin overlay  (11-03)
4. on-device MQTT  5. RNS interfaces  6. RNS repair  7. mosquitto setup
8. web-client SSL  9. radio region/power  10. first-run wizard
Then rewrite `USAGE.md` and `VISUAL_GUIDE.md`. Manager-only flows (fleet
posture, NOC grants) last: one operator, strong in-app dry-run + typed confirm.
