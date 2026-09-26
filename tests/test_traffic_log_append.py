"""TrafficLogger must never erase the traffic log (2026-09-26).

It opened the log with 'w' on every construction: each gateway restart, and
merely OPENING Traffic Inspector in the TUI, wiped the gateway's live log on
the gateway boxes (measured: moc's 80-line log; a TUI render left 8 lines).
"""
import os

from monitoring.traffic_storage import TrafficLogger


def test_second_logger_keeps_the_first_ones_lines(tmp_path):
    log = tmp_path / "traffic.log"
    TrafficLogger(str(log))
    with open(log, "a") as f:
        f.write("09:37:16.269 <-   rns        local          4b21083c21f84e\n")
    TrafficLogger(str(log))               # the TUI opening the screen
    text = log.read_text()
    assert "4b21083c21f84e" in text
    assert text.count("MESHFORGE TRAFFIC LOG") == 2


def test_each_header_names_its_process(tmp_path):
    log = tmp_path / "traffic.log"
    TrafficLogger(str(log))
    assert f"pid={os.getpid()}" in log.read_text()
