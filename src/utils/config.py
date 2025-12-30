"""Configuration management with environment variable support"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file if it exists
env_file = Path(__file__).parent.parent.parent / '.env'
if env_file.exists():
    load_dotenv(env_file)

# Meshtasticd Configuration
CONFIG_PATH = os.getenv('MESHTASTICD_CONFIG_PATH', '/etc/meshtasticd/config.yaml')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

# Hardware GPIO Configuration
LORA_CS_PIN = int(os.getenv('LORA_CS_PIN', '8'))
LORA_IRQ_PIN = int(os.getenv('LORA_IRQ_PIN', '16'))
LORA_BUSY_PIN = int(os.getenv('LORA_BUSY_PIN', '20'))
LORA_RESET_PIN = int(os.getenv('LORA_RESET_PIN', '24'))
LORA_RXEN_PIN = int(os.getenv('LORA_RXEN_PIN', '12'))
LORA_DIO2_PIN = int(os.getenv('LORA_DIO2_PIN', '6'))
LORA_DIO3_PIN = int(os.getenv('LORA_DIO3_PIN', '5'))

# GPS Configuration
GPS_SERIAL_PORT = os.getenv('GPS_SERIAL_PORT', '/dev/ttyS0')
GPS_BAUD_RATE = int(os.getenv('GPS_BAUD_RATE', '9600'))
GPS_ENABLE_PIN = int(os.getenv('GPS_ENABLE_PIN', '4'))
GPS_PPS_PIN = int(os.getenv('GPS_PPS_PIN', '17'))

# Network Configuration
WEB_INSTALLER_PORT = int(os.getenv('WEB_INSTALLER_PORT', '8080'))
MQTT_ENABLED = os.getenv('MQTT_ENABLED', 'false').lower() == 'true'
MQTT_SERVER = os.getenv('MQTT_SERVER', 'mqtt.meshtastic.org')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME', '')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD', '')
MQTT_TOPIC_PREFIX = os.getenv('MQTT_TOPIC_PREFIX', 'meshtastic')

# Radio Configuration
LORA_REGION = os.getenv('LORA_REGION', 'US')
DEFAULT_MODEM_PRESET = os.getenv('DEFAULT_MODEM_PRESET', 'MEDIUM_FAST')
DEFAULT_CHANNEL_SLOT = int(os.getenv('DEFAULT_CHANNEL_SLOT', '0'))
DEFAULT_TX_POWER = int(os.getenv('DEFAULT_TX_POWER', '22'))

# Update Configuration
UPDATE_CHECK_INTERVAL = int(os.getenv('UPDATE_CHECK_INTERVAL', '24'))
INCLUDE_BETA_UPDATES = os.getenv('INCLUDE_BETA_UPDATES', 'false').lower() == 'true'
UPDATE_URL = os.getenv('UPDATE_URL', 'https://api.github.com/repos/Nursedude/Meshtasticd_interactive_UI/releases/latest')

# Installer Configuration
DEBUG_MODE = os.getenv('DEBUG_MODE', 'false').lower() == 'true'
INSTALLER_LOG_PATH = os.getenv('INSTALLER_LOG_PATH', '/var/log/meshtasticd-installer.log')
SKIP_HARDWARE_DETECTION = os.getenv('SKIP_HARDWARE_DETECTION', 'false').lower() == 'true'

# Directory paths
TEMPLATES_DIR = Path(__file__).parent.parent.parent / 'templates'
SCRIPTS_DIR = Path(__file__).parent.parent.parent / 'scripts'
