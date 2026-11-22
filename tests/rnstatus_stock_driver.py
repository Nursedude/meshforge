"""Drive the REAL stock ``rnstatus`` print code with random interface stats.

Why (WITNESS-TUI r6, 2026-11-22): five review rounds found five parser holes
from one cause — the test inputs were rnstatus output *I imagined*. This
driver makes upstream's own ``program_setup`` print the text: a fake
Reticulum instance hands it random ``get_interface_stats()`` dicts, and the
truth (which interfaces are shown, their byte counters as ``prettysize``
prints them) is computed from the same dicts.

Read-only and offline: ``rns_instance=`` skips ``RNS.Reticulum()``; nothing
connects to rnsd. Not a test module (no ``test_`` prefix) — imported by
``test_rns_every_line.py`` and run as a script to (re)generate the committed
corpus:

    python3 tests/rnstatus_stock_driver.py OUT.json [N] [SEED]

1.6.1 is generated on meshforge-lab in its stock-witness venv (the fleet runs
1.3.9; see the fixture's ``source`` field).
"""

import contextlib
import inspect
import io
import json
import random
import sys

# Interface names: stock __str__ shapes (RNS/Interfaces/*.py) around config
# section names, which are free text. Includes the two shapes r5 found the
# old header regex could not read.
_SECTION_NAMES = [
    "Default Interface", "Regional RNode RF", "RNode-Multi", "lab_1",
    "Regional RNS", "box-gateway", "v6 peer", "a.b/c", "Ünïcode ☃",
    "x : y", "Status", "Traffic", "100%", "[nested]", "dash-and.dot",
]


def _iface_name(rng):
    sec = rng.choice(_SECTION_NAMES)
    shape = rng.randrange(12)
    if shape == 0:
        return f"TCPInterface[{sec}/192.0.2.{rng.randrange(255)}:4242]"
    if shape == 1:
        return f"TCPInterface[{sec}/[fe80::1]:4242]"
    if shape == 2:
        return f"RNodeInterface[{sec}]"
    if shape == 3:
        return f"{sec}[High Datarate]"          # RNodeSubInterface
    if shape == 4:
        return f"WDCL over /dev/ttyUSB{rng.randrange(3)}"   # WeaveInterface as_interface
    if shape == 5:
        return f"AutoInterface[{sec}]"
    if shape == 6:
        return f"UDPInterface[{sec}/0.0.0.0:4242]"
    if shape == 7:
        return f"BackboneInterface[{sec}]"
    if shape == 8:
        return f"LocalInterface[rns/{sec}]"     # hidden by stock (not -a)
    if shape == 9:
        return f"TCPInterface[Client on {sec}]"  # hidden by stock
    if shape == 10:
        return f"KISSInterface[{sec}]"
    return f"I2PInterface[{sec}]"


def _size(rng):
    return rng.choice([0, 0, rng.randrange(1, 1000), rng.randrange(1000, 10**6),
                       rng.randrange(10**6, 10**10)])


def random_stats(rng, n_if=None):
    """A random ``get_interface_stats()`` dict (keys stock reads, both versions)."""
    import RNS
    modes = [getattr(RNS.Interfaces.Interface.Interface, m) for m in (
        "MODE_FULL", "MODE_ACCESS_POINT", "MODE_POINT_TO_POINT", "MODE_ROAMING",
        "MODE_BOUNDARY", "MODE_GATEWAY") if hasattr(RNS.Interfaces.Interface.Interface, m)]
    ifs = []
    if rng.random() < 0.8:
        ifs.append({"name": "Shared Instance[rns/default]", "clients": rng.randrange(0, 5)})
    for _ in range(rng.randrange(0, 6) if n_if is None else n_if):
        ifs.append({"name": _iface_name(rng), "clients": rng.choice([None, None, rng.randrange(0, 9)])})
    for i in ifs:
        i.update({
            "status": rng.random() < 0.85, "mode": rng.choice(modes),
            "rxb": _size(rng), "txb": _size(rng),
            "txdrp": rng.choice([0, 0, rng.randrange(1, 50)]), "txdrb": _size(rng),
            "txbuffered": rng.choice([0, 0, _size(rng)]), "txstalled": rng.random() < 0.2,
            "frxb": _size(rng), "ftxb": _size(rng),
        })
        if rng.random() < 0.7:
            i["rxs"], i["txs"] = rng.randrange(0, 10**6), rng.randrange(0, 10**6)
            i["frxs"], i["ftxs"] = rng.randrange(0, 10**4), rng.randrange(0, 10**4)
        if rng.random() < 0.8:
            i["bitrate"], i["mtu"] = rng.choice([1200, 10940, 10**7, 10**9]), 1064
        opt = {
            "autoconnect_source": "abcd", "ifac_netname": "regional rns",
            "noise_floor": rng.choice([None, -106]), "cpu_load": rng.choice([None, 12]),
            "cpu_temp": rng.choice([None, 43]), "mem_load": 40,
            "battery_percent": 100, "battery_state": "discharging",
            "airtime_short": 0.0, "airtime_long": 0.25,
            "channel_load_short": 9.84, "channel_load_long": 3.79,
            "switch_id": rng.choice([None, "ab12"]), "endpoint_id": rng.choice([None, "cd34"]),
            "via_switch_id": rng.choice([None, "ef56"]), "peers": rng.randrange(0, 9),
            "tunnelstate": "Tunnel ready", "ifac_signature": bytes(range(10)), "ifac_size": 8,
            "i2p_b32": "xyz.b32.i2p", "gravity": rng.choice([0, 3]),
            "blocked_ips": rng.randrange(0, 3), "i2p_connectable": rng.random() < 0.7,
            "protocol_violations": rng.randrange(0, 3), "ifac_violations": rng.randrange(0, 2),
            "packet_filter_hits": rng.randrange(0, 4),
        }
        for k, v in opt.items():
            if rng.random() < 0.25:
                i[k] = v
        if "noise_floor" in i and rng.random() < 0.5:
            i["interference"] = rng.choice([None, -88])
            if rng.random() < 0.5:
                i["interference_last_ts"], i["interference_last_dbm"] = 0, -90
        if "mem_load" in i:             # stock reads cpu_load under mem_load
            i.setdefault("cpu_load", 12)  # (a real RNode reports both)
        if "ifac_signature" in i:
            i.setdefault("ifac_size", 8)
        if "battery_percent" in i:
            i.setdefault("battery_state", "charging")
    stats = {"interfaces": ifs, "rxb": _size(rng), "txb": _size(rng),
             "rxs": 0, "txs": 0, "frxb": _size(rng), "ftxb": _size(rng)}
    if rng.random() < 0.9:
        stats["transport_id"] = bytes(rng.randrange(256) for _ in range(16))
        stats["transport_uptime"] = rng.randrange(1, 10**6)
        if rng.random() < 0.3:
            stats["network_id"] = bytes(16)
        if rng.random() < 0.3:
            stats["probe_responder"] = bytes(16)
    return stats


class _FakeReticulum:
    def __init__(self, stats):
        self._stats = stats

    def get_interface_stats(self):
        return _copy(self._stats)

    def get_link_count(self):
        return 0


def _copy(stats):
    out = dict(stats)
    out["interfaces"] = [dict(i) for i in stats["interfaces"]]
    return out


def _rnstatus():
    from RNS.Utilities import rnstatus
    return rnstatus


def has_fstats():
    return "fstats" in inspect.signature(_rnstatus().program_setup).parameters


def render(stats, *, fstats=False):
    """stdout of stock ``program_setup`` for ``stats`` (fstats = ``-F``)."""
    rnstatus = _rnstatus()
    kw = {"configdir": None, "rns_instance": _FakeReticulum(stats)}
    if fstats:
        kw["fstats"] = True
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rnstatus.program_setup(**kw)
    return buf.getvalue()


def _shown(name, ifstat):
    """Stock's own visibility rule (dispall False, no name/burst filter)."""
    hidden = ("LocalInterface[", "TCPInterface[Client", "BackboneInterface[Client on",
              "AutoInterfacePeer[", "WeaveInterfacePeer[", "I2PInterfacePeer[Connected peer")
    if name.startswith(hidden):
        return False
    if name.startswith("I2PInterface[") and ifstat.get("i2p_connectable") is False:
        return False
    return True


def truth(stats, *, fstats=False):
    """[{name, tx, rx, rx_filtered|None, up}] for every interface stock shows."""
    import RNS
    out = []
    for i in stats["interfaces"]:
        if not _shown(i["name"], i):
            continue
        out.append({
            "name": i["name"], "up": bool(i["status"]),
            "tx": RNS.prettysize(i["txb"]), "rx": RNS.prettysize(i["rxb"]),
            "rx_filtered": RNS.prettysize(i["frxb"]) if fstats else None,
        })
    return out


def corpus(n, seed):
    import RNS
    rng = random.Random(seed)
    fs = has_fstats()
    cases = []
    for k in range(n):
        stats = random_stats(rng)
        for flag in ([False, True] if fs else [False]):
            cases.append({"case": k, "fstats": flag, "output": render(stats, fstats=flag),
                          "truth": truth(stats, fstats=flag)})
    return {"source": f"stock RNS {RNS.__version__} RNS/Utilities/rnstatus.py program_setup "
                      f"(tests/rnstatus_stock_driver.py, seed {seed}, n {n})",
            "cases": cases}


if __name__ == "__main__":
    out_path = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20261122
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(corpus(n, seed), fh, ensure_ascii=False, indent=0)
    print(f"wrote {out_path}")
