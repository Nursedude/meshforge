# GTK Threading Rules

## Golden Rule: UI updates ONLY from main thread

GTK is NOT thread-safe. All widget modifications must happen on the main thread.

---

## Pattern: Background work with UI callback

```python
import threading
from gi.repository import GLib

def _on_button_clicked(self, button):
    """Start background work when button clicked."""
    button.set_sensitive(False)
    self.status.set_text("Working...")

    def do_work():
        # This runs in background thread
        result = slow_network_operation()

        # Schedule UI update on main thread
        GLib.idle_add(self._update_ui, result)

    threading.Thread(target=do_work, daemon=True).start()

def _update_ui(self, result):
    """Called on main thread via GLib.idle_add."""
    self.status.set_text(f"Done: {result}")
    self.button.set_sensitive(True)
    return False  # Don't repeat
```

---

## Common Mistakes

### WRONG: Direct UI update from thread
```python
def worker():
    result = fetch_data()
    self.label.set_text(result)  # CRASH or corruption
```

### WRONG: Blocking main thread
```python
def _on_button_clicked(self, button):
    result = slow_operation()  # UI freezes!
    self.label.set_text(result)
```

---

## GLib.idle_add Patterns

### Simple update
```python
GLib.idle_add(self.label.set_text, "New text")
```

### With lambda (for complex updates)
```python
GLib.idle_add(lambda: self._complex_update(data))
```

### Return False to run once
```python
def _update(self):
    self.label.set_text("Updated")
    return False  # Run once only
```

---

## Opening URLs/Browsers

Browser commands can block - always run in thread:

```python
def _open_url(self, url: str):
    def do_open():
        import subprocess
        subprocess.run(["xdg-open", url], timeout=10)
    threading.Thread(target=do_open, daemon=True).start()
```

---

## Daemon Threads

Always use `daemon=True` for background threads:

```python
threading.Thread(target=worker, daemon=True).start()
```

**Why**: Daemon threads are killed when main program exits. Non-daemon threads can prevent clean shutdown.
