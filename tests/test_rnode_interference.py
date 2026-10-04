"""RNode Interference — the radio's own interference flag, shown WITH what it is of.

Born 2026-10-03. A fleet RNode reported "interference -71 dBm" 65-96 % of the
time; two independent SDRs beside it measured NO matching energy in the air
(n=84 paired polls). The number is real — it is just not proof of RF.

Premises were read at source BEFORE design (RNS RNodeInterface/Reticulum,
upstream RNode firmware 56775c51); two contextless reviewers then re-checked
them and found what the first draft still got wrong — each confirmed finding
has a test below (cost stated as fact though avoid_interference lives in
EEPROM and reads OFF when erased; no-stats firmware read as clean; one DOWN
RNode hid another's caveat; malformed JSON read as "no RNode"; stale values
from down/frozen frames counted as hits; a part-way failure dropped its
reason; Multi interfaces reported as absent).
Fixture: one live `rnstatus -j` RNode entry, network name and IFAC removed (MF014).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "launcher_tui"))
sys.path.insert(0, os.path.dirname(__file__))

from utils import rnode_interference as ri  # noqa: E402
from utils.observation import Failed, Seen, Unobservable  # noqa: E402

T0 = 1791072770.0
RNODE = {"name": "RNodeInterface[RNode LoRa]", "type": "RNodeInterface", "status": True,
         "bitrate": 10937.5, "noise_floor": -95, "interference": -71,
         "interference_last_dbm": -71, "interference_last_ts": T0 - 1.4,
         "channel_load_short": 0.0, "channel_load_long": 0.68, "airtime_long": 0.37}
TCP = {"name": "TCPServerInterface[gateway-tcp-server]", "type": "TCPServerInterface", "status": True,
       "interference": None, "noise_floor": None}


def _e(**over):
    d = dict(RNODE)
    d.update(over)
    return d


def _snap(*entries, t=T0):
    return ri.parse_snapshot({"interfaces": [TCP, *entries]}, t)


def _series(entries_by_sample):
    """entries_by_sample: list of lists of entry dicts; sample k taken at T0+5k,
    asserted entries re-stamped as a live radio does."""
    snaps = []
    for k, ents in enumerate(entries_by_sample):
        t = T0 + 5 * k
        fixed = []
        for e in ents:
            e = dict(e)
            if e.get("interference") is not None and e.get("interference_last_ts") == RNODE["interference_last_ts"]:
                e["interference_last_ts"] = t - 0.8
            fixed.append(e)
        snaps.append(_snap(*fixed, t=t))
    return Seen(ri.Sampling(snaps=snaps))


def _v(entries_by_sample, now=T0 + 30):
    return ri.verdict(_series(entries_by_sample), now=now)


class TestParse:
    def test_only_rnode_family(self):
        rs = _snap(_e())
        assert [r.name for r in rs] == ["RNodeInterface[RNode LoRa]"]
        assert (rs[0].interference, rs[0].noise_floor, rs[0].up) == (-71, -95, True)

    def test_bad_shape_raises_never_empty(self):
        for doc in (None, [], {"x": 1}, {"interfaces": None}):
            try:
                ri.parse_snapshot(doc, T0)
            except ri._BadShape:
                continue
            raise AssertionError(f"{doc!r} parsed as data")


class TestVerdict:
    def test_mostly_asserted(self):
        v = _v([[_e()]] * 5)
        assert v.status == "MOSTLY ASSERTED"
        assert any("5 of 5 live samples" in line for line in v.lines)

    def test_asserted_some(self):
        v = _v([[_e()]] + [[_e(interference=None)]] * 4)
        assert v.status == "ASSERTED"

    def test_mostly_needs_three_samples(self):
        assert _v([[_e()]]).status == "ASSERTED"

    def test_earlier_and_none(self):
        assert _v([[_e(interference=None)]] * 3).status == "EARLIER"
        e = _e(interference=None)
        e.pop("interference_last_ts"); e.pop("interference_last_dbm")
        assert _v([[e]] * 3).status == "NONE"

    def test_cost_is_conditional_never_fact(self):
        text = "\n".join(_v([[_e()]] * 5).lines)
        assert "IF interference avoidance is enabled" in text
        assert "autoinstalled RNode reads Disabled" in text
        assert "on by default" not in text
        assert "not proof of RF in the air" in text.replace("\n  ", " ") or "Not proof of RF in the air" in text

    def test_no_channel_stats_is_unobservable_not_clean(self):
        e = _e(interference=None, noise_floor=None)
        e.pop("interference_last_ts"); e.pop("interference_last_dbm")
        v = _v([[e]] * 3)
        assert v.status == "UNOBSERVABLE" and any("NOT clean" in line for line in v.lines)

    def test_down_sibling_does_not_hide_the_caveat(self):
        b = _e(name="RNodeInterface[B]")
        a = _e(name="RNodeInterface[A]", status=False)
        v = _v([[a, b]] * 5)
        assert v.status == "DOWN"
        assert any("IF interference avoidance" in line for line in v.lines)

    def test_flapping_counts_live_samples_only(self):
        v = _v([[_e()], [_e(status=False)], [_e(status=False)], [_e(interference=None)], [_e(interference=None)]])
        assert any("1 of 3 live samples" in line for line in v.lines)
        assert any("DOWN in 2 of 5" in line for line in v.lines)
        assert v.status == "ASSERTED"

    def test_frozen_frames_are_not_hits(self):
        old = _e(interference_last_ts=T0 - 600)          # stats stopped arriving 10 min ago
        snaps = [_snap(old, t=T0 + 5 * k) for k in range(5)]
        v = ri.verdict(Seen(ri.Sampling(snaps=snaps)), now=T0 + 30)
        assert v.status == "UNOBSERVABLE"
        assert any("frozen" in line for line in v.lines)

    def test_multi_interface_is_present_not_absent(self):
        sub = {"name": "RNodeMultiInterface[x][y]", "type": "RNodeSubInterface", "status": True}
        v = ri.verdict(Seen(ri.Sampling(snaps=[ri.parse_snapshot({"interfaces": [sub]}, T0)])), now=T0)
        assert v.status == "UNOBSERVABLE" and any("does not export" in line for line in v.lines)

    def test_absent(self):
        v = ri.verdict(Seen(ri.Sampling(snaps=[ri.parse_snapshot({"interfaces": [TCP]}, T0)])), now=T0)
        assert v.status == "ABSENT"

    def test_partial_failure_keeps_its_reason(self):
        s = _series([[_e()]])
        s.value.stopped_why = "sample 2/5: rnstatus timed out after 20 s"
        v = ri.verdict(s, now=T0 + 30)
        assert any("STOPPED early" in line and "timed out" in line for line in v.lines)
        assert v.status == "ASSERTED"                     # 1 of 1 is never "MOSTLY"

    def test_floor_is_labelled_now(self):
        assert any("floor" in line and "now" in line for line in _v([[_e()]] * 3).lines)

    def test_future_last_ts_is_a_clock_step(self):
        v = _v([[_e(interference=None, interference_last_ts=T0 + 3600)]])
        assert any("clock stepped" in line for line in v.lines)

    def test_values_shown_as_a_range(self):
        v = _v([[_e(interference=-75)], [_e(interference=-71)], [_e(interference=-73)]])
        assert any("-75..-71 dBm" in line for line in v.lines)


class TestSample:
    def _clock(self):
        t = [0.0]

        def clock():
            return t[0]

        def sleep(s):
            t[0] += s
        return clock, sleep

    def test_five_samples(self):
        clock, sleep = self._clock()
        obs = ri.sample(5, 5.0, reader=lambda: Seen(_snap(_e())), sleep=sleep, clock=clock)
        assert isinstance(obs, Seen) and len(obs.value.snaps) == 5 and obs.value.stopped_why is None

    def test_first_failure_is_the_answer(self):
        clock, sleep = self._clock()
        obs = ri.sample(5, 5.0, reader=lambda: Unobservable("rnstatus rc=1: No shared RNS instance"),
                        sleep=sleep, clock=clock)
        assert isinstance(obs, Unobservable) and "shared RNS" in obs.why

    def test_part_way_failure_is_kept(self):
        clock, sleep = self._clock()
        seq = iter([Seen(_snap(_e())), Unobservable("rnstatus timed out after 20 s")])
        obs = ri.sample(5, 5.0, reader=lambda: next(seq), sleep=sleep, clock=clock)
        assert isinstance(obs, Seen) and len(obs.value.snaps) == 1
        assert "sample 2/5" in obs.value.stopped_why

    def test_total_deadline_bounds_the_wait(self):
        t = [0.0]

        def slow_reader():
            t[0] += 15.0                                  # a CPU-starved rnstatus
            return Seen(_snap(_e()))
        obs = ri.sample(5, 5.0, reader=slow_reader, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
        assert isinstance(obs, Seen) and len(obs.value.snaps) < 5
        assert "time budget" in obs.value.stopped_why


def _fake_run(rc, out, err="", timeout=False):
    def run(argv, **kw):
        assert argv[-1] == "-j" and "rnstatus" in argv[0]
        if timeout:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout", 0))
        return subprocess.CompletedProcess(argv, rc, out, err)
    return run


class TestReadSnapshot:
    def _use(self, monkeypatch, run):
        monkeypatch.setattr(ri, "_rnstatus_bin", lambda: "/usr/bin/rnstatus")
        monkeypatch.setattr(subprocess, "run", run)

    def test_ok(self, monkeypatch):
        self._use(monkeypatch, _fake_run(0, json.dumps({"interfaces": [TCP, RNODE]})))
        assert isinstance(ri.read_snapshot(), Seen)

    def test_timeout_names_the_wedge(self, monkeypatch):
        self._use(monkeypatch, _fake_run(0, "", timeout=True))
        obs = ri.read_snapshot()
        assert isinstance(obs, Unobservable) and "wedged" in obs.why

    def test_bad_json_is_failed(self, monkeypatch):
        self._use(monkeypatch, _fake_run(0, "not json"))
        assert isinstance(ri.read_snapshot(), Failed)

    def test_wrong_shape_is_failed_not_absent(self, monkeypatch):
        self._use(monkeypatch, _fake_run(0, json.dumps({"no": "interfaces"})))
        assert isinstance(ri.read_snapshot(), Failed)

    def test_rc_nonzero_is_unobservable(self, monkeypatch):
        self._use(monkeypatch, _fake_run(1, "", "No shared RNS instance available"))
        obs = ri.read_snapshot()
        assert isinstance(obs, Unobservable) and "shared RNS instance" in obs.why

    def test_no_binary(self, monkeypatch):
        monkeypatch.setattr(ri, "_rnstatus_bin", lambda: None)
        assert isinstance(ri.read_snapshot(), Unobservable)


class TestHandler:
    def _handler(self):
        from handler_test_utils import make_handler_context
        from handlers.rns_rnode_interference import RNodeInterferenceHandler
        h = RNodeInterferenceHandler()
        h.set_context(make_handler_context())
        h.ctx.safe_call = lambda title, fn, *a, **k: fn(*a, **k)
        return h

    def test_menu(self):
        from handlers.rns_rnode_interference import RNodeInterferenceHandler
        assert RNodeInterferenceHandler.menu_section == "rns"
        assert [t for t, _, _ in RNodeInterferenceHandler().menu_items()] == ["rnode_interference"]

    def test_every_status_explained(self):
        from handlers.rns_rnode_interference import _EXPLAIN
        assert set(_EXPLAIN) == {"NONE", "EARLIER", "ASSERTED", "MOSTLY ASSERTED", "DOWN", "UNOBSERVABLE", "ABSENT"}

    def test_renders_in_a_scrolling_textbox_with_the_caveat(self, monkeypatch):
        h = self._handler()
        monkeypatch.setattr(ri, "read_all", lambda samples=5, gap_s=5.0: _v([[_e()]] * 5))
        h.execute("rnode_interference")
        kinds = [c[0] for c in h.ctx.dialog.calls]
        assert "textbox" in kinds
        text = next(c[1][1] for c in h.ctx.dialog.calls if c[0] == "textbox")
        assert text.startswith("Status: MOSTLY ASSERTED") and "IF interference avoidance" in text

    def test_never_writes(self):
        import ast
        import inspect
        from handlers import rns_rnode_interference
        for mod in (rns_rnode_interference, ri):
            for node in ast.walk(ast.parse(inspect.getsource(mod))):
                if isinstance(node, ast.Attribute):
                    assert node.attr not in ("write_bytes", "write_text", "unlink", "Popen"), node.attr
                if isinstance(node, ast.Name):
                    assert node.id not in ("open",), node.id
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                    argv = node.args[0]
                    assert isinstance(argv, ast.List)
                    assert [e.value for e in argv.elts if isinstance(e, ast.Constant)] == ["-j"]


def test_last_ts_slightly_after_the_sample_stamp_is_still_fresh():
    # Measured live: stamping before rnstatus (~1.2 s on a Pi 3) puts the
    # radio's last_ts 0.3-1.0 s AFTER the sample time. That is fresh.
    snaps = [_snap(_e(interference_last_ts=T0 + 5 * k + 0.9), t=T0 + 5 * k) for k in range(5)]
    v = ri.verdict(Seen(ri.Sampling(snaps=snaps)), now=T0 + 30)
    assert v.status == "MOSTLY ASSERTED"
