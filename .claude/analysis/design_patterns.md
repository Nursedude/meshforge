# MeshForge Design Patterns & Workflow Guide

> Reference document for developers and contributors
> Date: 2026-01-05

---

## 1. Architecture Patterns

### 1.1 Panel-Based Architecture

MeshForge uses a modular panel architecture where each feature is encapsulated in its own panel class.

```python
# Pattern: Panel Class Structure
class ExamplePanel(Gtk.Box):
    """Panel template following MeshForge conventions"""

    def __init__(self, parent_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.parent_window = parent_window

        # Initialize state
        self._state_variable = None

        # Build UI
        self._build_ui()

    def _build_ui(self):
        """Build the panel UI - called once in __init__"""
        # Header section
        self._build_header()

        # Main content (scrollable)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._build_content(content)
        scrolled.set_child(content)
        self.append(scrolled)

    def _build_header(self):
        """Build header section"""
        pass

    def _build_content(self, parent):
        """Build main content"""
        pass
```

### 1.2 Action Button Pattern

Standard button creation with consistent styling:

```python
def _create_action_button(self, label: str, icon: str, callback,
                          suggested: bool = False, destructive: bool = False):
    """Create a styled action button"""
    btn = Gtk.Button()

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    if icon:
        box.append(Gtk.Image.new_from_icon_name(icon))
    box.append(Gtk.Label(label=label))
    btn.set_child(box)

    if suggested:
        btn.add_css_class("suggested-action")
    elif destructive:
        btn.add_css_class("destructive-action")

    btn.set_tooltip_text(f"{label}")
    btn.connect("clicked", callback)
    return btn
```

### 1.3 Frame Section Pattern

Consistent section framing:

```python
def _create_section_frame(self, title: str, parent: Gtk.Box) -> Gtk.Box:
    """Create a framed section"""
    frame = Gtk.Frame()
    frame.set_label(title)
    frame.set_margin_start(10)
    frame.set_margin_end(10)
    frame.set_margin_top(5)
    frame.set_margin_bottom(5)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
    content.set_margin_start(10)
    content.set_margin_end(10)
    content.set_margin_top(5)
    content.set_margin_bottom(10)

    frame.set_child(content)
    parent.append(frame)
    return content
```

---

## 2. Threading Patterns

### 2.1 Background Task with UI Update

```python
def _run_long_operation(self):
    """Run operation in background, update UI in main thread"""
    def background_work():
        try:
            # Long-running operation
            result = do_expensive_work()

            # Update UI in main thread
            GLib.idle_add(self._update_ui_with_result, result)
        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    thread = threading.Thread(target=background_work)
    thread.daemon = True
    thread.start()

def _update_ui_with_result(self, result):
    """Must be called from main thread via GLib.idle_add"""
    self.result_label.set_label(str(result))
    return False  # Don't repeat
```

### 2.2 Progress Callback Pattern

```python
def _operation_with_progress(self):
    """Operation that reports progress"""
    def background_work():
        total = 100
        for i in range(total):
            # Do work
            do_step(i)

            # Report progress (main thread)
            GLib.idle_add(self._update_progress, i / total)

        GLib.idle_add(self._operation_complete)

    thread = threading.Thread(target=background_work)
    thread.daemon = True
    thread.start()

def _update_progress(self, fraction):
    """Update progress bar from main thread"""
    self.progress_bar.set_fraction(fraction)
    return False
```

---

## 3. Settings Management

### 3.1 JSON Settings Pattern

```python
from pathlib import Path
import json

class SettingsManager:
    """Centralized settings management"""

    CONFIG_DIR = Path.home() / '.config' / 'meshforge'

    def __init__(self):
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._settings = {}
        self._load()

    def _load(self):
        """Load settings from disk"""
        settings_file = self.CONFIG_DIR / 'settings.json'
        if settings_file.exists():
            try:
                self._settings = json.loads(settings_file.read_text())
            except json.JSONDecodeError:
                self._settings = {}

    def _save(self):
        """Save settings to disk"""
        settings_file = self.CONFIG_DIR / 'settings.json'
        settings_file.write_text(json.dumps(self._settings, indent=2))

    def get(self, key: str, default=None):
        """Get a setting value"""
        return self._settings.get(key, default)

    def set(self, key: str, value):
        """Set a setting value"""
        self._settings[key] = value
        self._save()
```

---

## 4. Dialog Patterns

### 4.1 Confirmation Dialog

```python
def _show_confirm(self, title: str, message: str, callback):
    """Show confirmation dialog"""
    dialog = Adw.MessageDialog(
        transient_for=self.parent_window,
        heading=title,
        body=message
    )
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("confirm", "Confirm")
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.SUGGESTED)
    dialog.connect("response", lambda d, r: callback(r == "confirm"))
    dialog.present()
```

### 4.2 Progress Dialog (Non-Blocking)

```python
def _show_progress_dialog(self, title: str) -> Adw.MessageDialog:
    """Show progress dialog with spinner"""
    dialog = Adw.MessageDialog(
        transient_for=self.parent_window,
        heading=title,
    )

    # Add spinner
    spinner = Gtk.Spinner()
    spinner.start()
    dialog.set_extra_child(spinner)

    dialog.add_response("cancel", "Cancel")
    dialog.present()
    return dialog
```

---

## 5. Error Handling Patterns

### 5.1 Graceful Error Display

```python
def _safe_operation(self):
    """Pattern for operations that might fail"""
    try:
        result = risky_operation()
        self._show_success("Operation completed successfully")
    except PermissionError:
        self._show_error("Permission denied. Try running with sudo.")
    except FileNotFoundError as e:
        self._show_error(f"File not found: {e.filename}")
    except Exception as e:
        logger.exception("Unexpected error in operation")
        self._show_error(f"An error occurred: {e}")

def _show_error(self, message: str):
    """Display error to user"""
    if hasattr(self, 'parent_window') and self.parent_window:
        self.parent_window.show_info_dialog("Error", message)
    else:
        logger.error(message)
```

### 5.2 Logging Pattern

```python
import logging

logger = logging.getLogger(__name__)

class MyPanel(Gtk.Box):
    def some_method(self):
        logger.debug("Entering some_method")  # Development info
        logger.info("Processing started")      # User-relevant info
        logger.warning("Low disk space")       # Potential issues
        logger.error("Failed to connect")      # Errors
        logger.exception("Unexpected error")   # Errors with traceback
```

---

## 6. UI Component Patterns

### 6.1 Status Label with Icon

```python
def _create_status_row(self, parent: Gtk.Box):
    """Create a status indicator row"""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)

    self.status_icon = Gtk.Image.new_from_icon_name("emblem-default")
    row.append(self.status_icon)

    self.status_label = Gtk.Label(label="Status: Unknown")
    self.status_label.set_xalign(0)
    row.append(self.status_label)

    parent.append(row)

def _update_status(self, is_ok: bool, message: str):
    """Update status display"""
    icon = "emblem-ok-symbolic" if is_ok else "dialog-error-symbolic"
    self.status_icon.set_from_icon_name(icon)
    self.status_label.set_label(message)

    # Apply CSS class
    self.status_label.remove_css_class("success")
    self.status_label.remove_css_class("error")
    self.status_label.add_css_class("success" if is_ok else "error")
```

### 6.2 List Box Row Pattern

```python
def _create_list_row(self, title: str, subtitle: str, action_callback) -> Gtk.ListBoxRow:
    """Create a consistent list row"""
    row = Gtk.ListBoxRow()
    row.set_activatable(False)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    box.set_margin_start(10)
    box.set_margin_end(10)
    box.set_margin_top(8)
    box.set_margin_bottom(8)

    # Info section
    info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    info.set_hexpand(True)

    title_label = Gtk.Label(label=title)
    title_label.set_xalign(0)
    title_label.add_css_class("heading")
    info.append(title_label)

    subtitle_label = Gtk.Label(label=subtitle)
    subtitle_label.set_xalign(0)
    subtitle_label.add_css_class("dim-label")
    subtitle_label.add_css_class("caption")
    info.append(subtitle_label)

    box.append(info)

    # Action button
    btn = Gtk.Button(label="Open")
    btn.connect("clicked", action_callback)
    box.append(btn)

    row.set_child(box)
    return row
```

---

## 7. Keyboard Shortcuts

### 7.1 Panel-Level Shortcuts

```python
def _setup_shortcuts(self):
    """Set up panel-specific keyboard shortcuts"""
    controller = Gtk.EventControllerKey()
    controller.connect("key-pressed", self._on_key_pressed)
    self.add_controller(controller)

def _on_key_pressed(self, controller, keyval, keycode, state):
    """Handle keyboard events"""
    from gi.repository import Gdk

    # Ctrl+R = Refresh
    if keyval == Gdk.KEY_r and (state & Gdk.ModifierType.CONTROL_MASK):
        self._refresh_data()
        return True

    # Ctrl+S = Save
    if keyval == Gdk.KEY_s and (state & Gdk.ModifierType.CONTROL_MASK):
        self._save_settings()
        return True

    return False  # Let other handlers process
```

### 7.2 Standard Shortcuts Reference

| Shortcut | Action | Scope |
|----------|--------|-------|
| Ctrl+1-9 | Navigate to panel | Global |
| F9 | Toggle sidebar | Global |
| F11 | Toggle fullscreen | Global |
| Ctrl+Q | Quit | Global |
| Escape | Exit fullscreen | Global |
| Ctrl+R | Refresh | Panel |
| Ctrl+S | Save | Panel |

---

## 8. Workflow: Adding a New Panel

1. **Create the panel file**
   ```
   src/gtk_ui/panels/my_panel.py
   ```

2. **Implement the panel class**
   - Inherit from `Gtk.Box`
   - Accept `parent_window` in `__init__`
   - Implement `_build_ui()`

3. **Register in app.py**
   ```python
   # Add page loader
   def _add_my_panel_page(self):
       from .panels.my_panel import MyPanel
       panel = MyPanel(self)
       self.content_stack.add_named(panel, "my_panel")
       self.my_panel = panel

   # Call in _build_ui()
   self._add_my_panel_page()

   # Add to nav_items
   ("my_panel", "My Panel", "my-icon-symbolic"),
   ```

4. **Test the panel**
   - Check navigation works
   - Verify responsive layout
   - Test keyboard shortcuts

---

## 9. CSS Classes Reference

| Class | Usage |
|-------|-------|
| `title-1` | Main page title |
| `title-2` | Section title |
| `title-3` | Subsection title |
| `heading` | Row/item heading |
| `caption` | Small metadata text |
| `dim-label` | De-emphasized text |
| `card` | Card-style container |
| `boxed-list` | Bordered list box |
| `suggested-action` | Primary action button |
| `destructive-action` | Dangerous action button |
| `success` | Green/positive styling |
| `warning` | Yellow/caution styling |
| `error` | Red/negative styling |

---

## 10. File Organization

```
src/gtk_ui/
├── app.py              # Main window, navigation
├── panels/             # Feature panels
│   ├── __init__.py
│   ├── dashboard.py    # Home/overview
│   ├── service.py      # Service management
│   ├── config.py       # Config editing
│   ├── radio_config.py # Radio settings
│   ├── rns.py          # Reticulum
│   ├── map.py          # Node map
│   ├── hamclock.py     # Space weather
│   ├── tools.py        # RF tools
│   ├── aredn.py        # AREDN mesh
│   ├── university.py   # Learning system
│   └── settings.py     # App settings
└── dialogs/            # Reusable dialogs
    └── __init__.py
```

---

*Document maintained as part of MeshForge development guidelines*
