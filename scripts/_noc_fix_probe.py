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
from service_remediation import service_fix_actions, service_enabled_here  # noqa: E402

NOC = ["meshtasticd", "rnsd", "mosquitto", "meshforge-gateway",
       "meshforge", "meshforge-map", "meshforge-maps"]

print(f"NOC fix-routing probe @ {os.uname().nodename}")
print("=" * 72)
for svc in NOC:
    try:
        st = check_service(svc)
    except Exception as e:  # noqa: BLE001
        print(f"  {svc:<20} check_service errored: {e}")
        continue
    state = getattr(st.state, "value", str(st.state))
    if st.available:
        print(f"  {svc:<20} {state:<14} healthy — no fix offered")
        continue
    # The chooser is gated by enabled-at-boot: a disabled/absent unit is
    # intentionally off here and must NOT be offered (the moc3 map case).
    enabled = service_enabled_here(svc)
    has_route = bool(service_fix_actions(svc, running=False))
    if not has_route:
        print(f"  {svc:<20} {state:<14} DEGRADED — no route (unknown service)")
    elif not enabled:
        print(f"  {svc:<20} {state:<14} down but DISABLED/absent — GATED (no nag)")
    else:
        offered = [a.label for a in service_fix_actions(svc, running=False)]
        print(f"  {svc:<20} {state:<14} DEGRADED + enabled — WOULD OFFER: {offered}")
print("=" * 72)
print("GATED = unit is disabled or absent on this box (intentionally off) → the")
print("fix chooser correctly skips it. WOULD OFFER = enabled-but-down → real fix.")
