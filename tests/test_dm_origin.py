"""BIC-2 DM slice 1 -- THE origin seam (operator 2026-11-26, after Fable r5:
"One origin seam, any doubt refuses").

Self-DM detection drew an important finding in r3 (#1 ``via_own``), r4 (#1
``tx_from`` alone) and r5 (#1 a judge-time registry id outranking the
header) -- the same defect KIND three times, each patched at one source.
Now ONE function, ``dm_delivery.dm_origin``, decides from EVERY source, and
any doubt refuses:

| own id stamped AT SEND | DM's uplinked header | origin   | may read delivered? | kept? |
|------------------------|----------------------|----------|---------------------|-------|
| none                   | none                 | unknown  | no                  | no    |
| X != dest              | none                 | remote   | yes                 | yes   |
| X == dest              | none                 | self     | no (own-radio hint) | yes   |
| none                   | H != dest            | remote   | yes                 | yes   |
| none                   | H == dest            | self     | no                  | yes   |
| X                      | H == X               | by X     | per the row above   | yes   |
| X                      | H != X               | conflict | no                  | no    |

The own id is the one ``utils.own_radio`` held, FRESH, when the DM was sent
(``store_outgoing(own_id=)`` -> ``dm_origin`` table) -- never read at judge
time, so a later registry change cannot re-judge an old send (r5 #1, A1/A2).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.dm_delivery import (  # noqa: E402
    ORIGIN_CONFLICT, ORIGIN_REMOTE, ORIGIN_SELF, ORIGIN_UNKNOWN, ORIGIN_STATES, dm_origin,
)

ME = 0x6201CE25
P5 = 0xB03DA894
OTHER = 0x11111111

TABLE = [
    # dest, own_at_send, header, expected
    (P5, None, None, ORIGIN_UNKNOWN),
    (P5, ME, None, ORIGIN_REMOTE),
    (ME, ME, None, ORIGIN_SELF),
    (P5, None, ME, ORIGIN_REMOTE),
    (ME, None, ME, ORIGIN_SELF),
    (P5, ME, ME, ORIGIN_REMOTE),
    (ME, ME, ME, ORIGIN_SELF),
    (P5, ME, OTHER, ORIGIN_CONFLICT),
    (ME, ME, P5, ORIGIN_CONFLICT),          # r5 A2: stale own == dest, header elsewhere
    (P5, OTHER, ME, ORIGIN_CONFLICT),       # r5 A1: own stamped wrong
    ("!b03da894", "!6201ce25", None, ORIGIN_REMOTE),
    ("junk", ME, ME, ORIGIN_UNKNOWN),
    (P5, "junk", None, ORIGIN_UNKNOWN),
    (P5, "unreadable", ME, ORIGIN_UNKNOWN),  # an unreadable stamp never defers to the header
    (P5, ME, "junk", ORIGIN_UNKNOWN),
]


@pytest.mark.parametrize("dest,own,hdr,want", TABLE)
def test_the_origin_table(dest, own, hdr, want):
    o = dm_origin(dest, own, hdr)
    assert o.state == want
    assert o.why if want in (ORIGIN_UNKNOWN, ORIGIN_CONFLICT) else True


def test_closed_vocabulary():
    assert set(ORIGIN_STATES) == {ORIGIN_REMOTE, ORIGIN_SELF, ORIGIN_UNKNOWN, ORIGIN_CONFLICT}


def test_conflict_names_both_ids():
    o = dm_origin(P5, ME, OTHER)
    assert "!6201ce25" in o.why and "!11111111" in o.why
