# Contributing to MeshForge

Thank you for your interest in contributing to MeshForge! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Architecture](#project-architecture)
- [Making Contributions](#making-contributions)
- [Testing](#testing)
- [Security](#security)
- [Pull Request Process](#pull-request-process)
- [Style Guide](#style-guide)
- [Community](#community)

## Code of Conduct

By participating in this project, you agree to maintain a welcoming, inclusive environment. Be respectful, constructive, and collaborative.

**Expected behavior:**
- Be respectful and inclusive
- Accept constructive criticism gracefully
- Focus on what's best for the community
- Show empathy towards others

**Unacceptable behavior:**
- Harassment, trolling, or personal attacks
- Publishing others' private information
- Inappropriate or unwelcome conduct

## Getting Started

### Prerequisites

- Raspberry Pi (3, 4, 5, Zero 2 W) or Linux system
- Python 3.9+
- Basic understanding of:
  - Meshtastic mesh networking
  - LoRa radio concepts
  - GTK4/libadwaita (for UI contributions)
  - Flask (for Web UI contributions)

### Quick Start

```bash
# Fork and clone
git clone https://github.com/YOUR_USERNAME/meshforge.git
cd meshforge

# Create feature branch
git checkout -b feature/your-feature-name

# Install dependencies
sudo apt install -y python3-pip python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 libadwaita-1-0 gir1.2-adw-1
pip3 install rich textual flask meshtastic

# Verify installation
python3 -c "from src.__version__ import __version__; print(f'MeshForge v{__version__}')"
```

## Development Setup

### Virtual Environment (Recommended)

```bash
python3 -m venv venv
source venv/bin/activate
pip install rich textual flask meshtastic
```

### Running Interfaces

```bash
# GTK Desktop UI (requires display)
sudo python3 src/main_gtk.py

# Web UI (headless/SSH)
sudo python3 src/main_web.py

# Terminal TUI
sudo python3 src/main_tui.py

# Rich CLI
sudo python3 src/main.py

# Monitor (no sudo required)
python3 -m src.monitor
```

### Development Tips

1. **Test on actual hardware** - Many features require a LoRa device
2. **Use the Monitor** - `python3 -m src.monitor` doesn't need sudo
3. **Check logs** - `journalctl -u meshtasticd -f` for service logs
4. **Web UI hot reload** - Flask auto-reloads on file changes

## Project Architecture

```
meshforge/
├── src/
│   ├── launcher.py           # Auto-detect best interface
│   ├── main_gtk.py           # GTK4/libadwaita desktop
│   ├── main_web.py           # Flask web server
│   ├── main_tui.py           # Textual terminal UI
│   ├── main.py               # Rich CLI
│   ├── monitor.py            # Lightweight node monitor
│   │
│   ├── gtk_ui/               # GTK desktop components
│   │   ├── app.py            # Main application window
│   │   ├── panels/           # Feature panels (tabs)
│   │   └── dialogs/          # Modal dialogs
│   │
│   ├── tui/                  # Terminal UI components
│   │   └── app.py            # Textual application
│   │
│   ├── gateway/              # RNS-Meshtastic bridge
│   │   ├── rns_bridge.py     # Gateway logic
│   │   ├── node_tracker.py   # Unified node tracking
│   │   └── config.py         # Gateway configuration
│   │
│   ├── config/               # Configuration management
│   ├── monitoring/           # Node tracking
│   ├── services/             # Systemd integration
│   ├── tools/                # RF calculations, network tools
│   └── utils/                # Shared utilities
│
├── web/                      # Static web assets
├── templates/                # Config file templates
├── tests/                    # Test suite
└── .claude/                  # Development notes
```

### Key Patterns

1. **Multi-interface architecture** - One codebase, four UIs (GTK, Web, TUI, CLI)
2. **Panel-based design** - GTK UI uses modular panels in `gtk_ui/panels/`
3. **Subprocess for commands** - Never use `os.system()` or `shell=True`
4. **Defensive programming** - Use `hasattr()` checks, validate all inputs
5. **Security first** - Escape HTML, validate user input, use safe defaults

## Making Contributions

### Types of Contributions

| Type | Description | Good First Issue? |
|------|-------------|-------------------|
| **Bug fixes** | Fix reported issues | Yes |
| **Documentation** | Improve README, add examples | Yes |
| **UI improvements** | Enhance GTK/Web/TUI interfaces | Maybe |
| **New features** | Add functionality | No |
| **RF tools** | Add calculations, visualizations | No |
| **RNS integration** | Improve gateway bridge | No |
| **Testing** | Add test cases | Yes |

### Good First Issues

Look for issues labeled `good first issue` or consider:

- Fixing typos or documentation
- Adding error messages
- Improving UI text/labels
- Adding input validation
- Writing tests for existing features

### Feature Proposals

For new features:

1. Open an issue describing the feature
2. Discuss approach with maintainers
3. Wait for approval before major work
4. Reference the issue in your PR

## Testing

### Manual Testing Checklist

Before submitting a PR, test your changes:

- [ ] GTK UI launches without errors
- [ ] Web UI loads in browser
- [ ] TUI runs in terminal
- [ ] No Python syntax errors (`python3 -m py_compile src/**/*.py`)
- [ ] Feature works with/without LoRa hardware connected
- [ ] No regressions in related features

### Running Syntax Check

```bash
# Check all Python files for syntax errors
find src -name "*.py" -exec python3 -m py_compile {} \;

# Or individually
python3 -m py_compile src/main_gtk.py
```

### Testing Without Hardware

Many features can be tested without a LoRa device:

- UI layout and navigation
- Settings panels
- RF calculators (frequency slot, LOS)
- Configuration file editing
- RNS config editor

## Security

**Security is critical for this project.** Please review [SECURITY.md](SECURITY.md) for our security policy.

### Security Checklist for PRs

- [ ] No `os.system()` calls - use `subprocess.run()`
- [ ] No `shell=True` in subprocess calls
- [ ] HTML output is escaped (use `escapeHtml()` in JavaScript)
- [ ] User input is validated before use
- [ ] No hardcoded credentials or secrets
- [ ] File paths are validated (no path traversal)
- [ ] Network binding defaults to `127.0.0.1`, not `0.0.0.0`

### Reporting Security Issues

Do NOT open public issues for security vulnerabilities. Email the maintainers directly.

## Pull Request Process

### 1. Before Creating a PR

- [ ] Fork and clone the repository
- [ ] Create a feature branch from `main`
- [ ] Make your changes with clear commits
- [ ] Test on actual hardware if possible
- [ ] Run syntax checks

### 2. Creating the PR

```bash
# Push your branch
git push -u origin feature/your-feature-name
```

Then create a PR on GitHub with:

- **Clear title** describing the change
- **Summary** of what changed and why
- **Testing done** - how you verified it works
- **Screenshots** for UI changes

### 3. PR Template

```markdown
## Summary
Brief description of changes

## Changes
- Change 1
- Change 2

## Testing
- [ ] Tested on Pi 4 with Heltec V3
- [ ] GTK UI works
- [ ] Web UI works
- [ ] No console errors

## Screenshots (if UI changes)
[Add screenshots here]
```

### 4. Review Process

1. Maintainers will review within a few days
2. Address any feedback
3. Once approved, maintainers will merge

## Style Guide

### Python

- Follow PEP 8
- Use descriptive variable names
- Add docstrings to functions
- Maximum line length: 100 characters
- Use type hints for function signatures

```python
def calculate_frequency(channel_name: str, region: str) -> float:
    """Calculate the center frequency for a Meshtastic channel.

    Args:
        channel_name: The channel name (e.g., "LongFast")
        region: The region code (e.g., "US")

    Returns:
        The center frequency in MHz
    """
    pass
```

### JavaScript (Web UI)

- Use `const` and `let`, not `var`
- Always escape HTML output
- Use meaningful function names

### Commit Messages

Use conventional commit format:

```
type: short description

Longer description if needed

Fixes #123
```

Types:
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation
- `style:` - Formatting (no code change)
- `refactor:` - Code change that doesn't fix bug or add feature
- `test:` - Adding tests
- `security:` - Security improvements

Examples:
```
feat: Add node battery monitoring to dashboard
fix: Correct frequency calculation for EU_868 region
docs: Add RNS setup instructions to README
security: Add input validation for journalctl parameters
```

## Community

### Getting Help

- **GitHub Issues** - For bugs and feature requests
- **GitHub Discussions** - For questions and ideas

### Resources

- [Meshtastic Docs](https://meshtastic.org/docs/)
- [Reticulum Network](https://reticulum.network/)
- [GTK4 Documentation](https://docs.gtk.org/gtk4/)
- [Flask Documentation](https://flask.palletsprojects.com/)

### Acknowledgments

Thanks to all contributors who help make MeshForge better!

---

**Happy Contributing!**
