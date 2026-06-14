"""Tests for utils.lxmface — deterministic LXMFace avatar generator.

The parity tests are the acceptance gate: if our Python output matches the
shared upstream vectors (ratspeak/LXMFace tests/vectors.json), we match Ratspeak
and the rest of the RNS ecosystem byte-for-byte.
"""

import json
import os

import pytest

from utils.lxmface import (
    avatar_color,
    avatar_data,
    avatar_glyph,
    avatar_svg,
    seed_string_for_node,
    _GLYPH_PALETTE,
    _i32,
    _js_num,
    _seed_rand,
)

_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "lxmface_vectors.json"
)

with open(_FIXTURE, "r", encoding="utf-8") as _f:
    _VECTORS = json.load(_f)["vectors"]

_SEEDS = [v["seed"] for v in _VECTORS]


class TestParityVectors:
    """Lock byte-identical output against the upstream shared vectors."""

    @pytest.mark.parametrize("vec", _VECTORS, ids=_SEEDS)
    def test_seeded_state(self, vec):
        _rand, state = _seed_rand(vec["seed"])
        assert [_i32(v) for v in state] == vec["seeded_state"]

    @pytest.mark.parametrize("vec", _VECTORS, ids=_SEEDS)
    def test_colors(self, vec):
        d = avatar_data(vec["seed"])
        assert d.color == vec["color"]
        assert d.bgcolor == vec["bgcolor"]
        assert d.spotcolor == vec["spotcolor"]

    @pytest.mark.parametrize("vec", _VECTORS, ids=_SEEDS)
    def test_grid(self, vec):
        d = avatar_data(vec["seed"])
        assert d.grid == vec["grid"]

    @pytest.mark.parametrize("vec", _VECTORS, ids=_SEEDS)
    def test_svg_reassembles_from_vector(self, vec):
        """The SVG must place exactly the vector's grid+colours into the template."""
        svg = avatar_svg(vec["seed"], vec["size"])
        # Every cell of the known grid must be present with the right colour.
        color_for = {0: vec["bgcolor"], 1: vec["color"], 2: vec["spotcolor"]}
        for y, rowvals in enumerate(vec["grid"]):
            for x, val in enumerate(rowvals):
                fill = color_for[val] if val in color_for else vec["spotcolor"]
                assert (
                    '<rect x="%d" y="%d" width="1" height="1" fill="%s"/>'
                    % (x, y, fill)
                ) in svg


class TestSvgStructure:
    """The self-contained SVG envelope matches the upstream template exactly."""

    def test_prefix_and_envelope(self):
        svg = avatar_svg("00000000000000000000000000000000", 64)
        assert svg.startswith(
            '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"'
            ' viewBox="0 0 8 8" shape-rendering="crispEdges">'
        )
        assert (
            '<clipPath id="lxmface-clip"><circle cx="4" cy="4" r="4"/></clipPath>'
            in svg
        )
        assert '<g clip-path="url(#lxmface-clip)">' in svg
        assert svg.endswith("</g></svg>")

    def test_exactly_64_cells(self):
        svg = avatar_svg("deadbeef", 32)
        assert svg.count("<rect ") == 64
        # size is honoured in width/height but viewBox stays in grid units
        assert 'width="32" height="32"' in svg
        assert 'viewBox="0 0 8 8"' in svg

    def test_default_size(self):
        assert 'width="64" height="64"' in avatar_svg("x")


class TestDeterminism:
    def test_same_seed_same_svg(self):
        a = avatar_svg("a7b3c9d1e5f20681943ab2de77fc8e01")
        b = avatar_svg("a7b3c9d1e5f20681943ab2de77fc8e01")
        assert a == b

    def test_different_seed_different_svg(self):
        assert avatar_svg("aaaa") != avatar_svg("bbbb")

    def test_empty_seed_is_stable(self):
        # JS lxmface(seed || '', ...) — empty/None must not raise.
        assert avatar_svg("") == avatar_svg(None)
        assert isinstance(avatar_color(""), str)

    def test_color_matches_data(self):
        for seed in ("", "abcd", _SEEDS[0]):
            assert avatar_color(seed) == avatar_data(seed).color


class TestRandRange:
    def test_rand_within_zero_to_two(self):
        """Upstream quirk: rand() divides by 2**31, so the range is [0, 2)."""
        rand, _state = _seed_rand("ffffffffffffffffffffffffffffffff")
        for _ in range(2000):
            v = rand()
            assert 0.0 <= v < 2.0


class TestJsNumberFormatting:
    def test_integer_valued_has_no_decimal(self):
        assert _js_num(50.0) == "50"
        assert _js_num(0.0) == "0"

    def test_fractional_shortest_roundtrip(self):
        assert _js_num(72.79951338656247) == "72.79951338656247"
        assert _js_num(49.99999999999999) == "49.99999999999999"

    def test_roundtrips_to_same_double(self):
        import struct

        for x in (72.79951338656247, 49.996234150603414, 29.751886369194835):
            s = _js_num(x)
            assert struct.pack("d", float(s)) == struct.pack("d", x)


class TestSeedCanonicalizer:
    def test_rns_hash_passthrough_lowercased(self):
        assert (
            seed_string_for_node("anything", rns_hash_hex="A7B3C9D1E5F2")
            == "a7b3c9d1e5f2"
        )

    def test_rns_hash_strips_0x(self):
        assert seed_string_for_node("", rns_hash_hex="0xDEADBEEF") == "deadbeef"

    def test_meshtastic_strips_bang_and_lowercases(self):
        assert seed_string_for_node("!A2E95BA4") == "a2e95ba4"

    def test_meshtastic_without_bang(self):
        assert seed_string_for_node("abcd1234") == "abcd1234"

    def test_rns_hash_preferred_over_node_id(self):
        # A bridged node carries both; the RNS hash wins for cross-app parity.
        assert seed_string_for_node("!a2e95ba4", rns_hash_hex="ff00") == "ff00"

    def test_empty(self):
        assert seed_string_for_node("") == ""
        assert seed_string_for_node(None) == ""


class TestGlyph:
    def test_two_chars_from_palette(self):
        g = avatar_glyph("a7b3c9d1e5f20681943ab2de77fc8e01")
        assert len(g) == 2
        assert all(ch in _GLYPH_PALETTE for ch in g)

    def test_deterministic(self):
        assert avatar_glyph("moc-gateway") == avatar_glyph("moc-gateway")

    def test_empty_seed_does_not_raise(self):
        assert len(avatar_glyph("")) == 2
        assert len(avatar_glyph(None)) == 2


class TestFoliumPopupWiring:
    """End-to-end: the Folium coverage-map popup embeds the avatar SVG."""

    def _popup(self, **kw):
        from utils.coverage_map import CoverageMapGenerator, MapNode

        gen = CoverageMapGenerator()
        defaults = dict(latitude=21.3, longitude=-157.8, network="rns")
        defaults.update(kw)
        return gen._create_popup(MapNode(**defaults))

    def test_popup_embeds_exact_avatar(self):
        seed = "a7b3c9d1e5f20681943ab2de77fc8e01"
        html = self._popup(id=seed, name="Test RNS Node")
        assert "LXMFace identity" in html
        # the byte-exact upstream face for this address is present in the popup
        assert avatar_svg(seed_string_for_node(seed), 36) in html

    def test_popup_without_id_omits_avatar(self):
        # No id => no avatar (honest: nothing to seed from), no stray <svg>.
        html = self._popup(id="", name="No Id")
        assert "<svg" not in html


class TestWebMapWiring:
    """The web map vendors the JS and renders the avatar client-side."""

    def _web(self, *parts):
        return os.path.join(os.path.dirname(__file__), "..", "web", *parts)

    def test_node_map_includes_script_and_popup_call(self):
        with open(self._web("node_map.html"), "r", encoding="utf-8") as f:
            html = f.read()
        assert 'src="js/lxmface.js"' in html
        assert "lxmface(seedForId(props.id)" in html

    def test_vendored_js_present_and_attributed(self):
        with open(self._web("js", "lxmface.js"), "r", encoding="utf-8") as f:
            src = f.read()
        assert "MIT License" in src
        assert "function lxmface" in src
        assert "function seedForId" in src


class TestGatewayAttributionWiring:
    """The Mesh->RNS transform emits the avatar seed as LXMF metadata."""

    def test_xform_emits_avatar_seed_field(self):
        path = os.path.join(
            os.path.dirname(__file__), "..", "src", "gateway", "_rns_bridge_xform.py"
        )
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
        assert "meshforge_from_avatar_seed" in code
        assert "seed_string_for_node" in code
