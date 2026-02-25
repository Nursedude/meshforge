# Installation Options Guide

This guide compares all available installation methods for the Meshtasticd Interactive Installer.

## 📊 Comparison Table

| Method | Difficulty | Time | Best For | Internet Required |
|--------|-----------|------|----------|-------------------|
| **Quick Install** | ⭐ Easy | ~2 min | Most users | Yes |
| **Web-Based** | ⭐ Easy | ~3 min | Beginners, Remote access | Yes |
| **Docker** | ⭐⭐ Moderate | ~5 min | Containers, Isolation | Yes |
| **Manual** | ⭐⭐⭐ Advanced | ~5 min | Advanced users, Custom setups | Yes |

---

## 🚀 Method 1: Quick Install (Recommended)

### Overview
Single command that does everything automatically.

### Advantages
- ✅ Fastest installation
- ✅ Automatically installs all dependencies
- ✅ Creates system command `meshtasticd-installer`
- ✅ No manual steps required
- ✅ Auto-updates on re-run

### Disadvantages
- ❌ Requires internet connection
- ❌ Less control over installation location
- ❌ Requires trust in remote script execution

### Installation Command
```bash
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
```

### What It Does
1. Detects your system architecture (32-bit/64-bit)
2. Installs system dependencies (Python, Git, etc.)
3. Clones repository to `/opt/meshtasticd-installer`
4. Installs Python dependencies
5. Creates `/usr/local/bin/meshtasticd-installer` command
6. Offers to start installer immediately

### Usage After Installation
```bash
# Interactive mode
sudo meshtasticd-installer

# Install stable version
sudo meshtasticd-installer --install stable

# Configure device
sudo meshtasticd-installer --configure

# Check dependencies
sudo meshtasticd-installer --check
```

---

## 🌐 Method 2: Web-Based Installer

### Overview
Browser-based interface for installation with real-time progress monitoring.

### Advantages
- ✅ User-friendly web interface
- ✅ Visual progress indicators
- ✅ No terminal knowledge required
- ✅ Access from any device on your network
- ✅ Mobile-friendly design
- ✅ Great for remote installations

### Disadvantages
- ❌ Requires cloning repository first
- ❌ Must keep browser tab open during installation
- ❌ Requires port 8080 to be available

### Installation Steps

1. **Clone the repository:**
```bash
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
```

2. **Start the web installer:**
```bash
sudo python3 web_installer.py
```

3. **Access the web interface:**
- Local: `http://localhost:8080`
- Remote: `http://<raspberry-pi-ip>:8080`

### Features
- 📊 System information display
- 🎯 One-click stable/beta installation
- 📝 Manual installation instructions
- 🔄 Real-time installation progress
- 📱 Responsive design for all devices

### Security Note
The web installer runs on port 8080 and is accessible to anyone on your network. Stop the server (Ctrl+C) when not in use.

---

## 🐳 Method 3: Docker Installation

### Overview
Containerized installation for isolated environments.

### Advantages
- ✅ Complete isolation from host system
- ✅ Reproducible environment
- ✅ Easy to remove (just delete container)
- ✅ Can run multiple versions
- ✅ Includes web installer option
- ✅ Pre-configured with all dependencies

### Disadvantages
- ❌ Requires Docker installation
- ❌ Requires --privileged flag for hardware access
- ❌ Larger download size
- ❌ More complex setup

### Prerequisites
Install Docker:
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER
```

### Option A: Docker Compose (Recommended)

```bash
# Clone repository
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI

# Start interactive installer
docker-compose run meshtasticd-installer

# Or start web installer
docker-compose up web-installer
# Visit http://localhost:8080
```

### Option B: Manual Docker Build

```bash
# Build image
docker build -t meshtasticd-installer .

# Run interactively
docker run -it --privileged -v /dev:/dev meshtasticd-installer

# Run web installer
docker run -d -p 8080:8080 --privileged -v /dev:/dev meshtasticd-installer web

# Run with specific command
docker run -it --privileged -v /dev:/dev meshtasticd-installer --install stable
```

### Important Docker Flags
- `--privileged`: Required for hardware access (USB, SPI)
- `-v /dev:/dev`: Mounts device directory for LoRa modules
- `-v /etc/meshtasticd:/etc/meshtasticd`: Persists configuration
- `-p 8080:8080`: Exposes web installer port

---

## 📦 Method 4: Manual Installation

### Overview
Traditional manual installation with full control over each step.

### Advantages
- ✅ Full control over installation process
- ✅ Can customize installation location
- ✅ Easy to troubleshoot
- ✅ No automated script execution
- ✅ Can skip unnecessary dependencies

### Disadvantages
- ❌ More steps required
- ❌ Must manage dependencies manually
- ❌ No automatic updates
- ❌ Requires more technical knowledge

### Step-by-Step Installation

**Step 1: Update system**
```bash
sudo apt-get update
sudo apt-get upgrade -y
```

**Step 2: Install system dependencies**
```bash
sudo apt-get install -y python3 python3-pip python3-venv git wget curl
```

**Step 3: Clone repository**
```bash
cd ~
git clone https://github.com/Nursedude/Meshtasticd_interactive_UI.git
cd Meshtasticd_interactive_UI
```

**Step 4: (Optional) Create virtual environment**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Step 5: Install Python dependencies**
```bash
sudo python3 -m pip install --upgrade pip
sudo python3 -m pip install -r requirements.txt
```

**Step 6: Run installer**
```bash
sudo python3 src/main.py
```

### Creating a System Command (Optional)
```bash
sudo tee /usr/local/bin/meshtasticd-installer > /dev/null << 'EOF'
#!/bin/bash
cd ~/Meshtasticd_interactive_UI
exec sudo python3 src/main.py "$@"
EOF

sudo chmod +x /usr/local/bin/meshtasticd-installer
```

---

## 🔍 Verification

After installation with any method, verify it works:

```bash
# Check system info
sudo meshtasticd-installer --check

# View help
sudo meshtasticd-installer --help

# Test interactive mode
sudo meshtasticd-installer
```

---

## 🆘 Troubleshooting

### Quick Install Issues

**Problem:** "Permission denied"
```bash
# Make sure you're using sudo
curl -sSL https://raw.githubusercontent.com/Nursedude/Meshtasticd_interactive_UI/main/install.sh | sudo bash
```

**Problem:** "Command not found: curl"
```bash
sudo apt-get install -y curl
```

### Web Installer Issues

**Problem:** "Address already in use"
```bash
# Port 8080 is in use, check what's using it:
sudo lsof -i :8080

# Or use a different port by editing web_installer.py
```

**Problem:** "Cannot connect to web interface"
```bash
# Check firewall
sudo ufw allow 8080/tcp

# Or disable firewall temporarily
sudo ufw disable
```

### Docker Issues

**Problem:** "permission denied while trying to connect to Docker daemon"
```bash
sudo usermod -aG docker $USER
# Log out and back in
```

**Problem:** "Cannot detect USB device in Docker"
```bash
# Make sure you're using --privileged flag and mounting /dev
docker run -it --privileged -v /dev:/dev meshtasticd-installer
```

### Manual Installation Issues

**Problem:** "ModuleNotFoundError"
```bash
# Reinstall dependencies
sudo python3 -m pip install -r requirements.txt --force-reinstall
```

**Problem:** "This tool requires root/sudo privileges"
```bash
# Always use sudo
sudo python3 src/main.py
```

---

## 🎯 Recommendation Guide

**Choose Quick Install if:**
- You want the fastest setup
- You're comfortable with one-liner installations
- You want automatic dependency management

**Choose Web Installer if:**
- You prefer graphical interfaces
- You're installing remotely
- You want to monitor progress in browser
- You're less comfortable with command line

**Choose Docker if:**
- You want complete isolation
- You're familiar with containers
- You need reproducible environments
- You want to run multiple instances

**Choose Manual if:**
- You want full control
- You need custom installation paths
- You're troubleshooting issues
- You prefer step-by-step installation

---

## 📚 Additional Resources

- [Main README](README.md) - Full project documentation
- [Meshtastic Docs](https://meshtastic.org/docs/) - Official Meshtastic documentation

---

## 🤝 Contributing

Found an issue with one of the installation methods? Please [open an issue](https://github.com/Nursedude/meshforge/issues) or submit a pull request.
