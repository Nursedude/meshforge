# Offer-back draft — RNode_Firmware: bound the interference-avoidance TX hold

**Status:** DRAFT for the operator to post upstream (`markqvist/RNode_Firmware`).
Not posted by a session. Fix: `Nursedude/RNode_Firmware` branch `meshforge` @ `11d51d7`.

## Suggested PR title
Bound the interference-avoidance TX hold so a persistent carrier-free level cannot silence a node

## Suggested PR body
`medium_free()` refuses to transmit while `interference_detected` is true. With
interference avoidance on (the default), a carrier-free level above
`noise_floor + CSMA_INFR_THRESHOLD_DB` blocks TX. The floor only re-learns levels
below `CSMA_RFENV_RECAL_LIMIT_DB` (-83 dBm), so a *steady* level above that
blocks TX indefinitely.

We hit this on Heltec LoRa32 v3 (two separate units, firmware 1.86). Both report a
steady ~-71 dBm "interference" at 903.625 MHz. Neither a Heltec v4 at the same
spot nor an Airspy SDR inches away sees it: 0.0% channel occupancy, no carriers.
It appears to be generated inside the v3. With IA on, in an interleaved bench test
(PLAIN broadcast probes every 15 s to a receiver 10 ft away), 9/20 arrived. 11 in a
row were lost while the v3 reported -71, and others took up to 26.7 s. With IA off:
20/20, max 0.41 s.

This change keeps IA's purpose (dodging *transient* energy) and bounds the hold.
Carrier-free interference that has persisted continuously for
`CSMA_INFR_MAX_HOLD_MS` (5 s, debounced over `CSMA_INFR_GAP_MS` = 500 ms gaps) no
longer blocks TX. It is still reported to the host, DCD still blocks, and the V4
LNA path is untouched. Patched v3 with IA on: 40/40 delivered, including all 11
probes sent under -71, max 3.76 s. Builds for heltec32_v3 and heltec32_v4 on ESP32
core 2.0.17.

Open questions for review:
- Should the cap be per-board or a runtime setting?
- Should a v3-specific front-end treatment (like the V4 LNA branch) also exist?

## Evidence on file
`~/.local/share/meshforge/rf-swap-20261028/bench-v3-ia/` on VolcanoAI: RESULT.txt
(stock A/B), FIX_RESULT.txt (patched), MATRIX.txt, SDR_WINDOW.txt, and the scripts.
