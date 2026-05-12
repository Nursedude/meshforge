"""MeshForge lab — synthetic-traffic soak + reliability measurement.

L1 of the cmd/diag/analyzer triad. Sister to scripts/lab_load_traffic.sh
(one-way LXMF injection); this package adds the **return path** —
echo responder + tracer + aggregator producing per-pair RTT and
reliability over time.

Modules
-------
- ``lxmf_echo``   — long-running daemon. Replies ACK to PINGs.
- ``lxmf_tracer`` — one-shot. Sends PING to each peer, measures RTT.

See ``project_lab_traffic_soak_l1_plan.md`` for the operator-aligned
design rationale.
"""
