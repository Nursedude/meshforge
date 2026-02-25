# Testing Rules

## Running Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_rf.py -v             # RF calculations
python3 -m pytest tests/test_rns_bridge.py -v     # Gateway bridge

# Run with coverage
python3 -m pytest tests/ --cov=src --cov-report=term-missing

# Quick syntax check
python3 -m py_compile src/**/*.py
```

---

## Test Locations

```
tests/                             # 1,986 tests across 60 files
├── conftest.py                    # Shared fixtures (mock_meshtastic, mock_rns, no_network)
├── test_rns_bridge.py             # Gateway bridge (140 tests)
├── test_rf.py                     # RF calculations (107 tests)
├── test_rns_transport.py          # RNS transport (97 tests)
├── test_meshtastic_protobuf.py    # Protobuf client (74 tests)
├── test_message_queue.py          # SQLite queue (72 tests)
├── test_status_bar.py             # TUI status bar (70 tests)
├── test_node_tracker.py           # Node tracking (68 tests)
├── test_mqtt_robustness.py        # MQTT reliability (66 tests)
├── test_commands.py               # CLI commands (61 tests)
├── ... (50 more test files)
└── test_regression_guards.py      # Architectural regression prevention (9 tests)
```

---

## Writing Tests

### Test pure functions first
```python
def test_haversine_distance():
    """Test distance calculation between two points."""
    # Known distance: SF to LA ~559 km
    result = haversine_distance(37.7749, -122.4194, 34.0522, -118.2437)
    assert 550 < result < 570
```

### Use fixtures for setup
```python
@pytest.fixture
def sample_node():
    return {"id": "!abc123", "lat": 37.7749, "lon": -122.4194}

def test_node_processing(sample_node):
    result = process_node(sample_node)
    assert result.id == "!abc123"
```

### Test error conditions
```python
def test_invalid_coordinates():
    with pytest.raises(ValueError):
        haversine_distance(999, 999, 0, 0)
```

---

## Test Patterns for MeshForge

### Mocking external services
```python
from unittest.mock import patch, MagicMock

@patch('requests.get')
def test_hamclock_fetch(mock_get):
    mock_get.return_value.json.return_value = {"sfi": 150}
    result = fetch_space_weather()
    assert result["sfi"] == 150
```

### Testing path utilities
```python
@patch.dict(os.environ, {"SUDO_USER": "testuser"})
def test_get_real_user_home_with_sudo():
    result = get_real_user_home()
    assert result == Path("/home/testuser")
```

### Testing GTK (avoid where possible)
```python
# Prefer testing logic separately from UI
def test_parse_node_data():
    raw = '{"id": "!abc", "name": "Node1"}'
    result = parse_node_data(raw)
    assert result.name == "Node1"
```

---

## When to Write Tests

1. **RF calculations** - Always test (critical for HAMs)
2. **Data parsing** - Test edge cases
3. **Path utilities** - Test sudo compatibility
4. **Business logic** - Test before GTK integration

---

## Linter as Pre-Test

Run linter before tests:
```bash
python3 scripts/lint.py --all && python3 -m pytest tests/ -v
```
