# Emoji Support Guide

The Meshtasticd Interactive UI uses emojis to make the interface more visually appealing. However, emoji support depends on your terminal and system configuration.

## Quick Fix: Enable Emojis

If you want to see emojis instead of ASCII fallbacks like `[PKG]`, `[CFG]`, etc., you can force enable them:

### Option 1: Environment Variable (Temporary)
```bash
export ENABLE_EMOJI=true
sudo -E python3 src/main.py
```

### Option 2: Create .env File (Permanent)
```bash
# Copy the example configuration
cp .env.example .env

# Edit the file and set ENABLE_EMOJI=true
nano .env

# Or use sed to set it automatically
sed -i 's/ENABLE_EMOJI=false/ENABLE_EMOJI=true/' .env
```

### Option 3: One-Line Enable
```bash
echo "ENABLE_EMOJI=true" > .env
```

## Current Emoji Detection Logic

The system automatically detects emoji support based on:

1. **ENABLE_EMOJI** environment variable (forces enable)
2. **DISABLE_EMOJI** environment variable (forces disable)
3. **SSH connections** (disables emojis over SSH)
4. **Raspberry Pi OS** (disabled by default for compatibility)
5. **Terminal type** (checks for known good terminals)
6. **UTF-8 support** (checks locale settings)

## Why Are Emojis Disabled by Default?

On Raspberry Pi OS, especially over SSH connections, many terminals don't render emojis correctly, showing boxes (□) or question marks (?) instead. The ASCII fallbacks ensure a consistent experience across all terminals.

## ASCII Fallbacks

When emojis are disabled, you'll see these ASCII alternatives:

| Emoji | ASCII | Meaning |
|-------|-------|---------|
| 🔴 | [ ] | Stopped/Error |
| 🟢 | [*] | Running/Success |
| 🟡 | [~] | Warning |
| 📊 | [DASH] | Dashboard |
| 📦 | [PKG] | Package/Install |
| ⬆️ | [UP] | Update |
| ⚙️ | [CFG] | Configuration |
| 🐛 | [DEBUG] | Debug |
| ✓ | [OK] | Success |
| ✗ | [X] | Fail |

## Testing Emoji Support

To test if your terminal supports emojis:

```bash
echo "🔴 🟢 🟡 🔵 📊 📦 ⚙️ 🐛"
```

If you see colored circles and icons, your terminal supports emojis!

## Recommended Terminals for Emoji Support

These terminals have excellent emoji support:
- **GNOME Terminal** (Linux desktops)
- **Konsole** (KDE)
- **iTerm2** (macOS)
- **Windows Terminal** (Windows 10/11)
- **Alacritty** (cross-platform)
- **Kitty** (cross-platform)

## Troubleshooting

**Q: I set ENABLE_EMOJI=true but still see ASCII**
- Make sure you're using `sudo -E` to preserve environment variables
- Try exporting the variable before running: `export ENABLE_EMOJI=true`
- Check if .env file is in the project root directory

**Q: Emojis show as boxes (□)**
- Your terminal font doesn't include emoji characters
- Install a font with emoji support like "Noto Color Emoji"
- Use the ASCII fallback mode (default)

**Q: Some emojis work but others don't**
- This is font-dependent
- Consider using ASCII mode for consistency
