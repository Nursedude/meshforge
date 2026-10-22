---
name: Test report
about: You tried something on the roadmap marked "help wanted" — tell us what happened
title: "[test report] <roadmap ref, e.g. BIC-1> — <one line>"
labels: tester-report
assignees: ''
---

Thank you — a report like this is the only way we learn how MeshForge behaves
on hardware we do not own. Failures are as useful as successes. Please do not
paste private addresses, keys or passwords.

**Roadmap item** (the `ref` from docs/roadmap.md):

**Hardware** (board, radio/HAT or USB radio, antenna if relevant):

**Firmware / versions** (MeshForge `python3 -c "from src.__version__ import __version__; print(__version__)"`, meshtasticd / radio firmware, OS):

**Offering** (one box on its own, or several boxes together):

**Steps** (what you did, in order):

**Result** (what you expected, and what actually happened — what the screen claimed vs what arrived):

**Logs** (relevant lines from `journalctl -u <service>` or the TUI's report — trimmed, addresses removed):
