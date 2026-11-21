"""MAP-H H4a follow-up (finding 2026-11-21): the popup "Last heard here" row.

Every federated marker printed the PEER's raw ``last_seen`` — its collector
WRITE time (H14's wrong quantity) as an unformatted epoch, e.g.
``1795258905.68`` under a status line saying "last 78 d ago". The row's label
is "here": it may only say THIS radio's hearing (``heard_here*``, aged on the
SERVER's clock, r1 F6) or say plainly that this radio did not hear it. Never a
peer write time, never a raw epoch.
"""
import json

import pytest

from tests.test_peer_radio_heard import _js_engine, _js_fn, _run_page_js  # noqa: E402

PEER_WRITE_TIME = 1795258905.68


def _run(engine, tmp_path, cases):
    funcs = "function %s\nfunction %s\n" % (_js_fn("agoText"), _js_fn("lastHeardHereText"))
    return _run_page_js(engine, funcs, "JSON.stringify(%s.map(p => lastHeardHereText(p, %d)))"
                        % (json.dumps(cases), int(PEER_WRITE_TIME) + 10), tmp_path)


@pytest.mark.parametrize("engine", [_js_engine() or "none"])
class TestLastHeardHereRow:
    def test_federated_rows_say_this_radio_never_the_peer_write_time(self, tmp_path, engine):
        if engine == "none":
            pytest.skip("no node and no headless chromium on this box — JS not executed")
        base = {"federated": True, "network": "meshtastic", "last_seen": PEER_WRITE_TIME}
        cases = [
            dict(base),                                                    # not ours
            dict(base, heard_here=True, heard_here_age_s=60),
            dict(base, heard_here=False, heard_here_age_s=78 * 86400),
            dict(base, heard_here=None),                                   # unobservable
            dict(base, heard_here=True),                                   # no age
            dict(base, heard_here=False),
        ]
        out = _run(engine, tmp_path, cases)
        for s in out:
            assert "1795258" not in s, out
        assert out[0] == "Not heard by this radio", out
        assert out[1] == "1 min ago", out
        assert out[2] == "78 d ago", out
        assert out[3] == "Radio hearing unknown", out
        assert out[4] == "Heard (time unknown)", out
        assert out[5] == "Not heard by this radio", out

    def test_local_rows_keep_their_age_and_never_print_a_raw_epoch(self, tmp_path, engine):
        if engine == "none":
            pytest.skip("no node and no headless chromium on this box — JS not executed")
        now = int(PEER_WRITE_TIME) + 10
        cases = [
            {"network": "meshtastic", "last_heard": now - 120},
            {"network": "meshtastic", "last_seen": PEER_WRITE_TIME},      # epoch, no last_heard
            {"network": "rns", "last_seen": "2026-11-21T01:00:00"},       # text passes through
            {"network": "meshtastic"},
            {"network": "meshtastic", "last_seen": str(PEER_WRITE_TIME)},  # epoch as TEXT
        ]
        out = _run(engine, tmp_path, cases)
        assert out[0] == "2m ago", out
        assert out[1] == "9s ago", out          # a local epoch IS a hearing (collector :1418)
        assert out[2] == "2026-11-21T01:00:00", out
        assert out[3] == "Unknown", out
        assert out[4] == "9s ago", out
