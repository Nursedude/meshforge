"""The knowledge base must not teach a wrong SNR scale (2026-10-04).

It said "SNR -10 to -5 dB: Weak, may have packet loss" — on LongFast (SF11,
demod floor -17.5 dB) -9 dB is 8.5 dB of margin, the fleet's MEDIAN healthy
link. The in-app assistant and offline oracle answer from this text.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def _snr_entry():
    from utils import knowledge_content as kc
    src = open(kc.__file__).read()
    start = src.index('title="SNR (Signal-to-Noise Ratio)"')
    return src[start:start + 2500]


def test_no_absolute_weak_band_that_contradicts_the_floors():
    text = _snr_entry()
    assert "-10 to -5 dB: Weak" not in text
    assert "SNR > 0 dB: Good signal" not in text


def test_teaches_margin_over_the_sf_floor_and_the_ceiling():
    text = _snr_entry()
    assert "-17.5" in text and "-7.5" in text      # SF11 / SF7 floors
    assert "margin" in text.lower()
    assert "ceiling" in text.lower()


def _module_text():
    from utils import knowledge_content as kc
    return open(kc.__file__).read()


def test_no_gain_first_advice_anywhere_in_the_module():
    # review 1b: the SNR entry's tail still said "Use higher gain antenna"
    # beside its own new "placement or a relay hop before more gain".
    text = _module_text()
    assert "Use higher gain antenna" not in text
    assert "Higher gain antennas improve SNR" not in text


def test_guide_does_not_fail_the_median_longfast_link():
    assert "SNR > -10, RSSI > -110" not in _module_text()


def test_example_matches_its_own_bands():
    # 8.5 dB of margin is "fair" by the entry's own bands, not "healthy"
    line = next(l for l in _snr_entry().splitlines() if "-9 dB is 8.5 dB" in l)
    assert "fair" in line


def test_absolute_classification_entry_says_what_it_is():
    text = _module_text()
    start = text.index('title="Signal Quality Classification"')
    entry = text[start:start + 1500]
    # it documents rf.classify_signal's cited absolute scale; it must SAY so
    # and point at the margin-over-floor scale the rest of the app uses
    assert "absolute" in entry.lower() and "-17.5" in entry


def test_visual_guide_does_not_show_the_retired_scale():
    import pathlib
    vg = pathlib.Path(__file__).resolve().parent.parent / "docs" / "VISUAL_GUIDE.md"
    assert "SNR -10 to -5 dB: Weak" not in vg.read_text()
