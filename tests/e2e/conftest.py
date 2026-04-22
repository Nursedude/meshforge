"""E2E harness fixtures: mock meshtasticd HTTP capture + real-pipeline bridge."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from tests.e2e.harness.mock_meshtasticd import MockMeshtasticDaemon


@pytest.fixture
def mock_meshtasticd():
    daemon = MockMeshtasticDaemon()
    daemon.start()
    yield daemon
    daemon.stop()


@pytest.fixture
def gateway_identity_dir(tmp_path):
    d = tmp_path / "gateway_identity"
    d.mkdir()
    return d


@pytest.fixture
def queue_db_path(tmp_path):
    return tmp_path / "queue.db"
