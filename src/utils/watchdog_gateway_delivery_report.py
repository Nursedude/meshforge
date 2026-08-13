"""Gateway delivery self-report — journal parsing + windowed gap judgement.

Split out of ``watchdog_probes_gateway_flow`` on 2026-08-12 (MF025: that file
reached the 1,500-line cap while the dual-path-dedup exclusion below was being
added — the rule says split, never raise the cap).

The seam is deliberate: everything here turns the gateway's OWN journal
self-report into numbers. Deciding what those numbers MEAN — severity,
debounce, disposition — stays in ``probe_gateway_delivery_degraded``. Both the
probe module and ``watchdog_probes_gateway`` re-export these names, so external
importers (tests, the runner) are unaffected by the move.
"""

import re
import subprocess
from typing import List, Optional, Tuple

from utils.watchdog_probe_core import _journal_match_lines, _short_unix_ts

# Matches the att/del/drop line in BOTH bridge_cli formats — single-bridge
# ("  attempted/delivered/dropped — M->R: a/d/x  R->M: a/d/x") and
# multi-bridge ("      att/del/drop — M->R: a/d/x  R->M: a/d/x"). The
# slash triplet after "M->R:" is the discriminator: the "Messages bridged:
# N (M->R: a, R->M: b)" line uses a COMMA, so it never matches. ERE for
# journalctl -g.
GATEWAY_DELIVERY_BLOCK_GREP = r"M->R: [0-9]+/[0-9]+/[0-9]+"
_GATEWAY_DELIVERY_BLOCK_RE = re.compile(
    r"M->R:\s*(\d+)/(\d+)/(\d+)\s+R->M:\s*(\d+)/(\d+)/(\d+)")

# The RNS error-channel witnesses (ERE for journalctl -g). EROFS is the
# 2026-06-20 wx class; the other two are the adjacent resource/forward
# failure shapes. Deliberately concrete strings — NOT a bare "Resource"
# match, which would false-fire on benign "Resource" log lines.
GATEWAY_RNS_ERROR_GREP = (
    r"EROFS|Error while assembling received resource|"
    r"Failed to forward to secondary")

# Dual-path dedup witness (2026-08-12). On a box running BOTH bridges (moc),
# an RNS→Mesh broadcast the local mesh_bridge already put on RF is suppressed
# here — correct behaviour, and rns_bridge.stats documents it as "attempted
# counts them; delivered/dropped do not — this counter explains the gap". The
# counter had NO reader: the att/del block this module parses omits it, so
# every correct suppression read as an undelivered message and drove the ratio
# down without bound. Live 2026-08-12 21:32 on moc: R→M 8/22 (36%) paged while
# all 14 missing were suppressions — true delivery 8/8, nothing lost. ASCII
# tail keyed on purpose; mesh_bridge's mirror line says "via rns_bridge" and
# is a DIFFERENT counter that never touches rns_to_mesh_attempted.
GATEWAY_R2M_SUPPRESSED_GREP = r"already on RF via mesh_bridge"


def _parse_delivery_block(
    line: str,
) -> Optional[Tuple[float, int, int, int, int, int, int]]:
    """Parse one ``-o short-unix`` att/del/drop journal line.

    Returns ``(ts, m2r_att, m2r_del, m2r_drop, r2m_att, r2m_del, r2m_drop)``
    or None when the epoch or the six counters don't parse (a torn line, a
    format that doesn't match) — None is dropped by the caller, never read as
    a zeroed block.
    """
    ts = _short_unix_ts(line)
    if ts is None:
        return None
    m = _GATEWAY_DELIVERY_BLOCK_RE.search(line)
    if m is None:
        return None
    try:
        n = [int(x) for x in m.groups()]
    except (ValueError, TypeError):
        return None
    return (ts, n[0], n[1], n[2], n[3], n[4], n[5])


def _gateway_delivery_blocks(
    unit: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[List[Tuple[float, int, int, int, int, int, int]]]:
    """All att/del/drop counter blocks for ``unit`` within ``lookback``.

    Returns the parsed block list (``[]`` = the gateway printed no att/del
    block in the window — idle / just-started, a genuine *observed* state),
    or **None** on journalctl unavailable / timeout / rc∉(0,1) — the honest
    *unobservable* answer. The caller must never read None as ``[]`` (empty ≠
    error — honest_failure_modes #1), or a journalctl wedge would mask the
    very delivery collapse this measures.
    """
    try:
        proc = subprocess.run(
            [
                journalctl_path, "-u", unit, "--since", f"-{lookback}",
                "-g", GATEWAY_DELIVERY_BLOCK_GREP, "-o", "short-unix",
                "-q", "--no-pager",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    out = proc.stdout
    if not out:
        return []
    blocks: List[Tuple[float, int, int, int, int, int, int]] = []
    for ln in out.splitlines():
        if not ln:
            continue
        parsed = _parse_delivery_block(ln)
        if parsed is not None:
            blocks.append(parsed)
    return blocks


def _gateway_r2m_suppressed(
    unit: str,
    lookback: str,
    journalctl_path: str = "journalctl",
) -> Optional[Tuple[int, int]]:
    """``(total, cid_only)`` RNS→Mesh dual-path suppressions in the window,
    or **None** unobservable. ``cid_only`` is the loss-exposure subset (the
    suppression's only evidence was a content-id registration, not observed
    text) — surfaced separately so excluding it from the ratio never averages
    that exposure away (calibrated_claims #5).
    """
    lines = _journal_match_lines(
        unit, GATEWAY_R2M_SUPPRESSED_GREP, lookback,
        journalctl_path=journalctl_path)
    if lines is None:
        return None
    return (len(lines), sum(1 for ln in lines if "dedup [cid]" in ln))


def _window_delivery_gap(
    blocks: List[Tuple[float, int, int, int, int, int, int]],
    *,
    min_volume: int,
    ratio_floor: float,
    r2m_suppressed: Optional[int] = None,
) -> Tuple[List[Tuple[str, int, int, float, int]], List[str]]:
    """Per-direction windowed delivered/attempted gap.

    Returns ``(findings, unjudged)``:

    * ``findings`` = ``[(label, d_att, d_del, ratio, suppressed_excluded), …]``
      for each direction whose WINDOWED delivery (newest counter minus oldest
      in the window) fell below ``ratio_floor`` with at least ``min_volume``
      attempts — the recent-drop lens, not the lifetime-cumulative one (which
      would mask a fresh collapse on a long-uptime box). A counter going
      BACKWARD across the window means the gateway restarted mid-window
      (counters are in-memory); the earliest baseline is then taken as zero so
      we measure since-the-restart rather than reading a bogus negative delta.
    * ``unjudged`` = direction labels carrying a real gap that could NOT be
      judged because ``r2m_suppressed`` was unobservable. The caller must treat
      these as indeterminate, never as clean.

    Needs ≥2 blocks to form a delta; fewer → both lists empty (can't judge —
    the caller treats that as *no finding*, not *healthy*, and the volume gate
    keeps a quiet box silent regardless).

    NOTE (calibrated): the journal exposes only the TOTAL dropped count, which
    on the Mesh→RNS direction folds in benign best-effort broadcast-to-no-peer
    misses alongside real failures (RNS→Mesh dropped is clean — failures only).
    That is why the floor is conservative (a true majority-failure collapse,
    far below any benign-broadcast steady state) and why the precise,
    reason-split moderate-gap detection is delivery_confirmation_stall's job,
    not this leg's. Leg 1 is the gross-collapse backstop.
    """
    if len(blocks) < 2:
        return [], []
    ordered = sorted(blocks, key=lambda b: b[0])
    earliest, latest = ordered[0], ordered[-1]
    findings: List[Tuple[str, int, int, float, int]] = []
    unjudged: List[str] = []
    # tuple indices: ts=0; M->R att/del/drop = 1/2/3; R->M att/del/drop = 4/5/6
    for label, att_i, del_i in (("Mesh->RNS", 1, 2), ("RNS->Mesh", 4, 5)):
        att_l, del_l = latest[att_i], latest[del_i]
        att_e, del_e = earliest[att_i], earliest[del_i]
        counter_reset = att_l < att_e
        if counter_reset:                 # counter reset → measure since reset
            base_att, base_del = 0, 0
        else:
            base_att, base_del = att_e, del_e
        d_att = att_l - base_att
        d_del = del_l - base_del
        excluded = 0
        if label == "RNS->Mesh" and d_att - d_del > 0:
            if r2m_suppressed is None:
                # Unobservable exclusion + a real gap = we cannot tell a
                # collapse from correct dedup. Judging it as 0 suppressed is
                # exactly the false page this exclusion exists to kill.
                unjudged.append(label)
                continue
            if counter_reset:
                # The two observation windows no longer align: the counter
                # measures since-the-restart, the suppression line count spans
                # the WHOLE window. Subtracting across that seam could either
                # invent delivery or mask a collapse, so refuse to judge this
                # direction for one tick rather than guess (the next tick's
                # window is clean). Rare — gateway restarts only.
                unjudged.append(label)
                continue
            # Clamp: suppressions can never exceed the unexplained gap.
            excluded = max(0, min(r2m_suppressed, d_att - d_del))
            d_att -= excluded
        # Volume gate on the ADJUSTED count — a window that is mostly correct
        # dedup has not done enough real delivery work to judge.
        if d_att <= 0 or d_att < min_volume:
            continue
        ratio = max(0.0, min(1.0, d_del / d_att))
        if ratio < ratio_floor:
            findings.append((label, d_att, d_del, ratio, excluded))
    return findings, unjudged
