"""The map rebuilds a marker's icon only when markerIconKey() changes (2026-09-25:
rebuilding every marker each 60 s refresh made the map flash — "it rolls").
That is only correct while the key covers EVERY prop the icon reads; a missed
field would leave a stale icon on screen. This pins the two together."""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
HTML = open(os.path.join(HERE, "..", "web", "node_map.html"), encoding="utf-8").read()


def _body(name: str) -> str:
    m = re.search(r"function %s\(([^)]*)\)\s*\{" % name, HTML)
    assert m, f"{name} not found in node_map.html"
    depth, i = 1, m.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(HTML[i], 0)
        i += 1
    return HTML[m.end():i]


def _fields(body: str, var: str) -> set:
    return set(re.findall(r"\b%s\.([a-zA-Z_]+)" % var, body))


def test_icon_key_covers_every_prop_the_icon_reads():
    read = set()
    for fn in ("createMarkerIcon", "getNodeColor", "getNodeShape"):
        read |= _fields(_body(fn), "props")
    keyed = _fields(_body("markerIconKey"), "p")
    missing = read - keyed
    assert not missing, f"markerIconKey() misses icon inputs {sorted(missing)} — stale icons"


def test_setIcon_on_update_is_guarded_by_the_key():
    upd = HTML[HTML.index("const existing = state.markers.get(id);"):]
    upd = upd[:upd.index("existing.feature = feature;")]
    assert "markerIconKey(oldProps) !== markerIconKey(props)" in upd
    assert upd.count("setIcon(") == 1


def test_heatmap_only_uses_displayed_features():
    """2026-09-25: the heatmap was fed every feature, so a Hawaii view heated
    the US mainland. It must get the same activeIds the markers use."""
    fin = _body("_finalizeUpdate")
    call = fin[fin.index("updateSignalHeatmap("):]
    call = call[:call.index("}));") + 4]
    assert "ctx.activeIds.has(" in call
    assert "updateSignalHeatmap(state.currentGeoJSON.features)" not in fin
