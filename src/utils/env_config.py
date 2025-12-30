"""Environment configuration loader and validator"""

import os
from pathlib import Path
from typing import Dict, Optional, Any
from rich.console import Console
from rich.table import Table

console = Console()

# Default configuration values
DEFAULTS = {
    # Meshtasticd paths
    'MESHTASTICD_CONFIG_PATH': '/etc/meshtasticd/config.yaml',

    # Logging
    'LOG_LEVEL': 'INFO',
    'INSTALLER_LOG_PATH': '/var/log/meshtasticd-installer.log',
    'ERROR_LOG_PATH': '/var/log/meshtasticd-installer-error.log',

    # LoRa defaults
    'LORA_REGION': 'US',
    'DEFAULT_MODEM_PRESET': 'MEDIUM_FAST',
    'DEFAULT_CHANNEL_SLOT': '0',

    # UI settings
    'ENABLE_EMOJI': 'false',
    'DISABLE_EMOJI': 'false',

    # Update settings
    'UPDATE_CHECK_INTERVAL': '24',
    'INCLUDE_BETA_UPDATES': 'false',

    # Debug settings
    'DEBUG_MODE': 'false',
    'SKIP_HARDWARE_DETECTION': 'false',

    # Web installer
    'WEB_INSTALLER_PORT': '8080',
}


def find_env_file() -> Optional[Path]:
    """Find the .env file in standard locations"""
    # Check locations in order of priority
    search_paths = [
        Path.cwd() / '.env',
        Path('/opt/meshtasticd-installer/.env'),
        Path.home() / '.meshtasticd-installer.env',
        Path(__file__).parent.parent.parent / '.env',
    ]

    for path in search_paths:
        if path.exists():
            return path

    return None


def load_env_file(env_path: Optional[Path] = None) -> Dict[str, str]:
    """Load environment variables from .env file

    Args:
        env_path: Optional path to .env file. If None, auto-discovers.

    Returns:
        Dictionary of loaded environment variables
    """
    loaded_vars = {}

    if env_path is None:
        env_path = find_env_file()

    if env_path is None or not env_path.exists():
        return loaded_vars

    try:
        with open(env_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()

                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue

                # Parse KEY=VALUE
                if '=' not in line:
                    continue

                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip()

                # Remove quotes if present
                if value and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]

                if key:
                    loaded_vars[key] = value
                    os.environ[key] = value

    except Exception as e:
        console.print(f"[yellow]Warning: Could not load .env file: {e}[/yellow]")

    return loaded_vars


def get_config(key: str, default: Optional[str] = None) -> str:
    """Get configuration value from environment or defaults

    Priority:
    1. Environment variable
    2. Provided default
    3. Built-in default
    """
    return os.environ.get(key, default or DEFAULTS.get(key, ''))


def get_config_bool(key: str, default: bool = False) -> bool:
    """Get boolean configuration value"""
    value = get_config(key, str(default).lower())
    return value.lower() in ('true', 'yes', '1', 'on')


def get_config_int(key: str, default: int = 0) -> int:
    """Get integer configuration value"""
    try:
        return int(get_config(key, str(default)))
    except ValueError:
        return default


def validate_config() -> Dict[str, Any]:
    """Validate current configuration and return status

    Returns:
        Dictionary with validation results
    """
    results = {
        'valid': True,
        'warnings': [],
        'errors': [],
        'config': {}
    }

    # Check meshtasticd config path
    config_path = Path(get_config('MESHTASTICD_CONFIG_PATH'))
    if not config_path.parent.exists():
        results['warnings'].append(f"Config directory does not exist: {config_path.parent}")
    results['config']['meshtasticd_config'] = str(config_path)

    # Check log level
    log_level = get_config('LOG_LEVEL').upper()
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if log_level not in valid_levels:
        results['errors'].append(f"Invalid LOG_LEVEL: {log_level}")
        results['valid'] = False
    results['config']['log_level'] = log_level

    # Check region
    region = get_config('LORA_REGION').upper()
    valid_regions = ['US', 'EU_868', 'EU_433', 'CN', 'JP', 'ANZ', 'KR', 'TW', 'RU', 'IN', 'NZ_865', 'TH', 'UA_868', 'UA_433', 'LORA_24', 'UNSET']
    if region not in valid_regions:
        results['warnings'].append(f"Non-standard LORA_REGION: {region}")
    results['config']['lora_region'] = region

    # Check modem preset
    preset = get_config('DEFAULT_MODEM_PRESET').upper()
    valid_presets = ['LONG_FAST', 'LONG_SLOW', 'VERY_LONG_SLOW', 'MEDIUM_SLOW', 'MEDIUM_FAST', 'SHORT_SLOW', 'SHORT_FAST', 'LONG_MODERATE', 'SHORT_TURBO']
    if preset not in valid_presets:
        results['warnings'].append(f"Non-standard modem preset: {preset}")
    results['config']['modem_preset'] = preset

    # Check debug mode
    results['config']['debug_mode'] = get_config_bool('DEBUG_MODE')

    # Check emoji settings
    results['config']['emoji_enabled'] = get_config_bool('ENABLE_EMOJI')
    results['config']['emoji_disabled'] = get_config_bool('DISABLE_EMOJI')

    return results


def show_config_summary():
    """Display current configuration summary"""
    table = Table(title="Current Configuration", show_header=True, header_style="bold cyan")
    table.add_column("Setting", style="cyan")
    table.add_column("Value", style="green")
    table.add_column("Source", style="yellow")

    env_file = find_env_file()

    for key in sorted(DEFAULTS.keys()):
        env_value = os.environ.get(key)
        default_value = DEFAULTS[key]

        if env_value is not None:
            value = env_value
            source = ".env" if env_file else "env var"
        else:
            value = default_value
            source = "default"

        table.add_row(key, value, source)

    console.print(table)

    if env_file:
        console.print(f"\n[dim]Loaded from: {env_file}[/dim]")
    else:
        console.print("\n[dim]No .env file found, using defaults[/dim]")


def initialize_config():
    """Initialize configuration by loading .env file

    Call this at application startup
    """
    env_file = find_env_file()
    loaded = load_env_file(env_file)

    if loaded:
        from utils.logger import log
        log(f"Loaded {len(loaded)} settings from {env_file}")

    return validate_config()
