"""A REAL settings file for MQTT TUI tests (F8 v2).

Before the edit lease, these tests patched ``load_mqtt_config`` and
``save_mqtt_config`` — the layer the parked F8 attempt broke in, while its
tests stayed green. Now the screen opens on a real tmp file under a real
lease, and the save spy WRAPS the real write path (``save.call_args`` still
names the intent; the write still happens and can still be refused).
``save_result`` replaces the write with a canned SaveResult — for tests of
what a FAILED write shows.
"""
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from monitoring import mqtt_settings as ms
from utils.paths import atomic_write_text_preserving


def seed(d, intent):
    r = ms.acquire_lease(d, user="seed", tty="-")
    assert r.state == "held", r
    with r.lease as lease:
        res = ms.save(d, {**ms.defaults(), **intent}, atomic_write_text_preserving, lease)
        assert res.ok, res


@contextmanager
def real_settings(intent=None, raw=None, save_result=None):
    with tempfile.TemporaryDirectory(prefix="mqtt_tui_") as t:
        d = Path(t) / "meshforge"
        d.mkdir()
        if intent is not None:
            seed(d, intent)
        if raw is not None:
            (d / ms.FILENAME).write_text(raw)
        from handlers import mqtt as m
        kw = ({"return_value": save_result} if save_result is not None
              else {"wraps": m.save_mqtt_config})
        with patch("monitoring.mqtt_subscriber.mqtt_config_dir", return_value=d), \
                patch("handlers.mqtt.save_mqtt_config", **kw) as save:
            yield save, d


def write_failed(error="disk full"):
    return ms.SaveResult(False, error=error, code="io")
