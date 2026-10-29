# Findings sheet — Heltec LoRa32 v3 interference avoidance holds TX (2026-10-28)

**NOT A PR DRAFT. Do not post the fork's code upstream.** Upstream (`markqvist/RNode_Firmware`)
treats machine-originated code as carrying no valid licence grant ("void grants", RNode
README → Reticulum manual). The fix in `Nursedude/RNode_Firmware` `meshforge` (`11d51d7`)
was written by an AI assistant. The operator reports these FINDINGS in his own words; the
maintainer writes any fix. Facts and measurements below, each with where its evidence lives.

## What happens
- Two Heltec LoRa32 v3 units (RNode firmware 1.86, SX1262, 850-950 MHz) each report a
  steady carrier-free "interference" of about **−71 dBm** at 903.625 MHz, against a noise floor of −100 to −107.
- It is **inside the v3**:
  - It followed unit 1 across two locations, two antennas, battery removed, and the neighbouring radio unplugged (10/10).
  - Unit 2 shows it too (10/10).
  - A Heltec v4 at unit 1's old spot is clean.
  - An Airspy SDR inches away saw the 903.625 channel **0.0% busy, no carriers, no spurs**, across 8 captures while the v3 read −71.
- With interference avoidance ON, the v3 **holds or drops its own TX**. Interleaved bench test, PLAIN broadcast probes every 15 s, receiver 10 ft:
  - **IA ON: 9/20 delivered, 11 consecutive lost while it read −71, delays up to 26.7 s.**
  - IA OFF: 20/20, max 0.41 s, including 6 probes sent under −71.

## Why (upstream code, read 2026-10-28, master `56775c5`)
- `update_modem_status()`: `interference_detected = !carrier_detected && current_rssi > noise_floor + CSMA_INFR_THRESHOLD_DB` (11). Only `BOARD_HELTEC32_V4` has its own LNA branch.
- The floor re-learns only while the level is below `CSMA_RFENV_RECAL_LIMIT_DB` (−83). A steady −71 is never re-learned.
- `medium_free()`: `if (avoid_interference && interference_detected) return false;`. IA is on when the EEPROM byte is 0x00.
- A freshly autoinstalled v3 reads IA **Disabled** (erased EEPROM), and a once-configured v3 reads **Enabled**. So affected units depend on their EEPROM history, which may explain why this has been hard to pin down.

## The idea (not code; ideas are not anyone's property)
Interference avoidance exists to dodge *transient* energy. A carrier-free level that persists
for seconds should stop blocking TX: bound the hold, keep reporting the figure to the host,
keep DCD blocking as before. Tested in our fork as a 5 s cap: unit 1 with IA ON 40/40 (max
3.76 s); unit 2 with IA ON 40/40 (worst 4.5 / 5.05 s = the cap releasing), IA OFF 20/20.

## Workaround anyone can apply today
`rnodeconf <port> --config` → if `Interference avoidance : Enabled`, run `rnodeconf <port> -X`.
LoRa carrier sense still applies.

## Evidence (VolcanoAI, `~/.local/share/meshforge/rf-swap-20261028/`)
- `moc3_v3_baseline.txt`, `kiai_sample1.txt`, `toad_off_samples.txt`, `ant_A_samples.txt`, `ant_B_samples.txt`, `v3b_samples.txt`: the −71 controls.
- `bench-v3-ia/RESULT.txt` (stock A/B), `FIX_RESULT.txt`, `kiai-unit2/RESULT.txt`, `MATRIX.txt`, `SDR_WINDOW.txt`, plus every script used.
- Provenance rows: `.claude/audits/review_provenance.md`, 2026-10-28 entries.
