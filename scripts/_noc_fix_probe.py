"""On-box validation for the NOC fix-routing primitive (service_remediation).

Non-interactive: for each NOC-core service, read its REAL systemd state and print
what the Dashboard's "Fix a Degraded Service" chooser would offer. Proves the
detection->routing logic against live hardware (the whiptail UX still needs a
human, but the routing decision is what this validates).

Run from anywhere:  python3 /opt/meshforge/scripts/_noc_fix_probe.py
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_here, "..", "src"),
                os.path.join(_here, "..", "src", "launcher_tui")]

from utils.service_check import check_service  # noqa: E402
from service_remediation import service_fix_actions  # noqa: E402

NOC = ["meshtasticd", "rnsd", "mosquitto", "meshforge-gateway",
       "meshforge", "meshforge-map", "meshforge-maps"]

print(f"NOC fix-routing probe @ {os.uname().nodename}")
print("=" * 64)
for svc in NOC:
    try:
        st = check_service(svc)
    except Exception as e:  # noqa: BLE001
        print(f"  {svc:<20} check_service errored: {e}")
        continue
    state = getattr(st.state, "value", str(st.state))
    if st.available:
        # Healthy: dashboard shows "running", offers no fix.
        print(f"  {svc:<20} {state:<14} healthy — no fix offered")
    else:
        offered = [a.label for a in service_fix_actions(svc, running=False)]
        verdict = f"WOULD OFFER: {offered}" if offered else "no route (not a known-fixable service)"
        print(f"  {svc:<20} {state:<14} DEGRADED — {verdict}")
print("=" * 64)
print("Note: the Dashboard only lists services this box's startup-checks expect")
print("(env_state.services); a service intentionally-off on this box's profile")
print("should NOT appear there. This probe checks ALL NOC services directly.")
