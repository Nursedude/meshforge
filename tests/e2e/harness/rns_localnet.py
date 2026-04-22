"""Reserved for full RNS routing fidelity (Pattern A+).

Current Pattern A skeleton tests the bridge → queue → handler → HTTP
integration directly via BridgedMessage injection (see harness/pipeline.py
and test_bridge_e2e.py). That covers Issue #40-class bugs without needing
to stand up an in-process RNS network.

If we later want to exercise the RNS Identity / Destination / LXMRouter
path itself (e.g., to catch path-resolution or announce-handling bugs in
the bridge's _on_lxmf_receive entry), this module is where that lives.

Open design question for that work: RNS uses a singleton Reticulum per
process, and LocalInterface is a shared-instance client (not a peer-to-
peer interface). Realistic options:
  - subprocess-per-peer over UDPInterface(127.0.0.1)
  - single-process, multiple LXMF identities sharing one Reticulum
  - move to Pattern C (real RF on pi2w) where this is a non-issue

Until decided, this module is intentionally empty.
"""
