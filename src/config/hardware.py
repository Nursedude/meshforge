"""Hardware detection for LoRa modules and devices"""

import os
import glob
from pathlib import Path
from typing import Optional
from rich.console import Console

from utils.system import run_command
from utils.logger import log

console = Console()


class HardwareDetector:
    """Detect LoRa hardware modules"""

    # Known LoRa module USB vendor/product IDs
    KNOWN_USB_MODULES = {
        '1a86:7523': {
            'name': 'CH340/CH341 USB-Serial',
            'common_devices': ['MeshToad', 'MeshTadpole', 'Generic CH340 LoRa'],
            'meshtastic_compatible': True,
            'power_requirement': '900mA (peak)',
            'notes': 'Common in MtnMesh devices'
        },
        '1a86:55d4': {
            'name': 'CH341 USB-Serial (alternate)',
            'common_devices': ['MeshToad v2'],
            'meshtastic_compatible': True,
            'power_requirement': '900mA (peak)',
            'notes': 'MeshToad variant'
        },
        '1a86:5512': {
            'name': 'CH341 USB-to-SPI/I2C',
            'common_devices': ['MeshToad E22', 'Pinedio USB', 'MeshStick 1262', 'PiggyStick'],
            'meshtastic_compatible': True,
            'power_requirement': '900mA (peak)',
            'notes': 'CH341 in SPI/I2C bridge mode (not serial). Creates virtual spidev/i2c buses.',
            'connection_type': 'spi'
        },
        '10c4:ea60': {
            'name': 'CP2102 USB-Serial',
            'common_devices': ['Station G2', 'Various LoRa modules'],
            'meshtastic_compatible': True,
            'power_requirement': 'Standard USB (5V DC or PoE for Station G2)',
            'notes': 'Silicon Labs chipset. Station G2 variant has Ethernet.',
            'gateway_capable': True,
            'flash_method': 'esptool'
        },
        '0403:6001': {
            'name': 'FT232 USB-Serial',
            'common_devices': ['FTDI-based LoRa modules'],
            'meshtastic_compatible': True,
            'power_requirement': 'Standard USB',
            'notes': 'FTDI chipset'
        },
        '1209:0000': {
            'name': 'MeshStick',
            'common_devices': ['MeshStick'],
            'meshtastic_compatible': True,
            'power_requirement': 'Standard USB',
            'notes': 'Official Meshtastic USB device'
        },
        # Heltec devices (ESP32-S3 based)
        '303a:1001': {
            'name': 'Heltec ESP32-S3 (CDC)',
            'common_devices': ['Heltec V3', 'Heltec V4', 'Station G2'],
            'meshtastic_compatible': True,
            'power_requirement': '500mA typical, 1A peak (V4 at max TX)',
            'notes': 'ESP32-S3 native USB CDC. V4 supports 28dBm TX.',
            'gateway_capable': True,
            'flash_method': 'esptool'
        },
        '303a:4001': {
            'name': 'Heltec ESP32-S3 (JTAG)',
            'common_devices': ['Heltec V3', 'Heltec V4'],
            'meshtastic_compatible': True,
            'power_requirement': '500mA typical',
            'notes': 'ESP32-S3 JTAG interface for debugging',
            'gateway_capable': True,
            'flash_method': 'esptool'
        },
        # RAK WisBlock devices (nRF52840 based)
        '239a:8029': {
            'name': 'RAK4631 (Adafruit nRF52840)',
            'common_devices': ['RAK4631', 'RAK WisBlock Meshtastic Kit'],
            'meshtastic_compatible': True,
            'power_requirement': '100mA typical',
            'notes': 'nRF52840 + SX1262. Low power, excellent for solar nodes.',
            'gateway_capable': True,
            'flash_method': 'uf2'
        },
        '239a:0029': {
            'name': 'RAK4631 Bootloader',
            'common_devices': ['RAK4631 in bootloader mode'],
            'meshtastic_compatible': True,
            'power_requirement': '100mA',
            'notes': 'RAK4631 in UF2 bootloader mode - ready for flashing',
            'gateway_capable': True,
            'flash_method': 'uf2'
        },
        # LILYGO T-Beam
        '1a86:55d3': {
            'name': 'CH9102 USB-Serial',
            'common_devices': ['LILYGO T-Beam S3', 'T-Beam Supreme'],
            'meshtastic_compatible': True,
            'power_requirement': '500mA typical',
            'notes': 'ESP32-S3 based T-Beam with GPS',
            'gateway_capable': True,
            'flash_method': 'esptool'
        }
    }

    # USB vendor:product ID → meshtasticd template mapping
    # Maps detected USB chipsets to the correct template in available.d/
    USB_ID_TO_TEMPLATE = {
        # Heltec ESP32-S3 variants
        '303a:1001': 'heltec-usb.yaml',      # ESP32-S3 CDC (Heltec V3/V4)
        '303a:4001': 'heltec-usb.yaml',      # ESP32-S3 JTAG
        '303a:1002': 'heltec-usb.yaml',      # ESP32-S3 Native USB
        # MeshStick
        '1209:0000': 'meshstick-usb.yaml',   # Official Meshtastic USB device
        # MeshToad / CH340 family
        '1a86:7523': 'meshtoad-usb.yaml',    # CH340 (MeshToad, MeshTadpole)
        '1a86:55d4': 'meshtoad-usb.yaml',    # CH341 alternate
        '1a86:5512': 'lora-usb-meshtoad-e22.yaml',  # CH341 SPI/I2C bridge
        '1a86:7522': 'meshtoad-usb.yaml',    # CH340K variant
        # RAK4631 / nRF52840
        '239a:8029': 'rak4631-usb.yaml',     # Adafruit nRF52840 (RAK4631)
        '239a:0029': 'rak4631-usb.yaml',     # RAK4631 bootloader mode
        '19d2:0016': 'rak4631-usb.yaml',     # RAK WisBlock USB
        # Station G2 / CP2102
        '10c4:ea60': 'station-g2-usb.yaml',  # CP2102 (Station G2)
        # T-Beam S3 / CH9102
        '1a86:55d3': 'tbeam-usb.yaml',       # CH9102 (T-Beam S3)
        # Generic USB-serial fallback
        '0403:6001': 'usb-serial-generic.yaml',  # FT232R
        '0403:6015': 'usb-serial-generic.yaml',  # FT231X
    }

    @classmethod
    def match_usb_to_template(cls, vendor_product_id: str) -> 'Optional[str]':
        """Match a USB vendor:product ID to a meshtasticd template filename.

        Args:
            vendor_product_id: USB ID in 'vendor:product' format (e.g. '303a:1001')

        Returns:
            Template filename (e.g. 'heltec-usb.yaml') or None if no match.
        """
        return cls.USB_ID_TO_TEMPLATE.get(vendor_product_id.lower())

    @classmethod
    def get_device_name_for_usb_id(cls, vendor_product_id: str) -> 'Optional[str]':
        """Get human-readable device name for a USB vendor:product ID.

        Args:
            vendor_product_id: USB ID in 'vendor:product' format (e.g. '303a:1001')

        Returns:
            Device name string (e.g. 'Heltec V3/V4') or None.
        """
        info = cls.KNOWN_USB_MODULES.get(vendor_product_id.lower())
        if info:
            return ', '.join(info.get('common_devices', [info['name']]))
        return None

    # Known SPI LoRa HATs with detailed configuration
    KNOWN_SPI_HATS = {
        'MeshAdv-Mini': {
            'name': 'MeshAdv-Mini',
            'manufacturer': 'chrismyers2000',
            'description': 'LoRa/GPS Raspberry Pi HAT with SX1262/SX1268',
            'radio_module': 'SX1262',  # or SX1268 for 400MHz version
            'power_output': '+22dBm',
            'features': ['GPS', 'Temperature Sensor', 'PWM Fan', 'I2C/Qwiic'],
            'compatible_boards': ['Pi 2', 'Pi 3', 'Pi 4', 'Pi 5', 'Pi Zero', 'Zero W', 'Zero 2 W'],
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 8,      # SPI Chip Select
                'IRQ': 16,    # Interrupt Request (DIO1)
                'Busy': 20,   # Busy signal
                'Reset': 24,  # Reset pin
                'RXen': 12,   # RX Enable
            },
            'spi_config': {
                'MOSI': 10,   # GPIO 10, Pin 19
                'MISO': 9,    # GPIO 9, Pin 21
                'CLK': 11,    # GPIO 11, Pin 23
            },
            'gps_config': {
                'module': 'ATGM336H-5NR32',
                'serial_path': '/dev/ttyS0',
                'serial_path_alt': '/dev/ttyAMA0',
                'enable_gpio': 4,
                'pps_gpio': 17,
            },
            'i2c_config': {
                'SDA': 2,     # GPIO 2, Pin 3 (I2C1)
                'SCL': 3,     # GPIO 3, Pin 5 (I2C1)
                'temp_sensor_addr': '0x48',  # TMP102
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
                'DIO3_TCXO_VOLTAGE': True,
            },
            'notes': 'HAT+ EEPROM enabled, supports 5V PWM fans on GPIO 18'
        },
        'MeshAdv-Pi v1.1': {
            'name': 'MeshAdv-Pi v1.1',
            'manufacturer': 'MeshAdv',
            'description': 'MeshAdv Pi HAT for Meshtastic',
            'radio_module': 'SX1262',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 8,
                'IRQ': 22,
                'Busy': 23,
                'Reset': 24,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
            },
            'notes': 'Standard MeshAdv Pi HAT'
        },
        'MeshAdv-Pi-Hat': {
            'name': 'MeshAdv-Pi-Hat',
            'manufacturer': 'chrismyers2000',
            'description': '1W High-Power LoRa/GPS Raspberry Pi HAT with SX1262/SX1268',
            'radio_module': 'SX1262',  # or SX1268 for 400MHz version
            'power_output': '+33dBm (1W)',
            'features': ['GPS', 'High Power', 'I2C/Qwiic', 'PPS'],
            'compatible_boards': ['Pi 2', 'Pi 3', 'Pi 4', 'Pi 5', 'Pi Zero', 'Zero W', 'Zero 2 W', 'Pi 400'],
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 21,     # SPI Chip Select (NSS)
                'IRQ': 16,    # Interrupt Request (DIO1)
                'Busy': 20,   # Busy signal
                'Reset': 18,  # Reset pin
                'RXen': 12,   # RX Enable
                'TXen': 13,   # TX Enable
            },
            'spi_config': {
                'MOSI': 10,   # GPIO 10, Pin 19
                'MISO': 9,    # GPIO 9, Pin 21
                'CLK': 11,    # GPIO 11, Pin 23
            },
            'gps_config': {
                'module': 'ATGM336H',
                'serial_path': '/dev/ttyS0',
                'serial_path_alt': '/dev/ttyAMA0',
                'uart_tx': 14,
                'uart_rx': 15,
                'pps_gpio': 23,
            },
            'i2c_config': {
                'SDA': 2,     # GPIO 2, Pin 3 (I2C1)
                'SCL': 3,     # GPIO 3, Pin 5 (I2C1)
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
                'DIO3_TCXO_VOLTAGE': True,
            },
            'notes': 'High-power 1W LoRa HAT, supports E22-900M30S/33S (900MHz) and E22-400M30S/33S (400MHz)'
        },
        'Adafruit RFM9x': {
            'name': 'Adafruit RFM9x',
            'manufacturer': 'Adafruit',
            'description': 'Adafruit RFM9x LoRa Radio Bonnet',
            'radio_module': 'SX1276',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 7,
                'IRQ': 25,
                'Reset': 17,
            },
            'notes': 'Adafruit LoRa Bonnet for Raspberry Pi'
        },
        'Waveshare SX126X': {
            'name': 'Waveshare SX126X',
            'manufacturer': 'Waveshare',
            'description': 'Waveshare SX1262 LoRa HAT',
            'radio_module': 'SX1262',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 21,
                'IRQ': 16,
                'Busy': 20,
                'Reset': 18,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
            },
            'notes': 'Waveshare LoRa HAT'
        },
        'Elecrow LoRa RFM95': {
            'name': 'Elecrow LoRa RFM95',
            'manufacturer': 'Elecrow',
            'description': 'Elecrow RFM95 LoRa HAT',
            'radio_module': 'SX1276',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 25,
                'IRQ': 5,
                'Reset': 17,
            },
            'notes': 'Elecrow RFM95 HAT'
        },
        'PiTx LoRa': {
            'name': 'PiTx LoRa',
            'manufacturer': 'PiTx',
            'description': 'PiTx LoRa HAT',
            'radio_module': 'SX1276',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 8,
                'IRQ': 22,
                'Reset': 27,
            },
            'notes': 'PiTx LoRa HAT for Raspberry Pi'
        },
        'FemtoFox': {
            'name': 'FemtoFox',
            'manufacturer': 'FemtoFox',
            'description': 'FemtoFox LoRa board with SX1262',
            'radio_module': 'SX1262',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 8,
                'IRQ': 16,
                'Busy': 20,
                'Reset': 24,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
                'DIO3_TCXO_VOLTAGE': True,
            },
            'notes': 'FemtoFox LoRa board - compact SX1262 module'
        },
        'Ebyte E22-900M30S': {
            'name': 'Ebyte E22-900M30S',
            'manufacturer': 'Ebyte',
            'description': '1W High-Power SX1262 Module (915MHz)',
            'radio_module': 'SX1262',
            'power_output': '+30dBm (1W)',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 21,
                'IRQ': 16,
                'Busy': 20,
                'Reset': 18,
                'RXen': 12,
                'TXen': 13,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
                'DIO3_TCXO_VOLTAGE': True,
            },
            'notes': 'High-power 1W module - requires adequate power supply'
        },
        'Ebyte E22-400M30S': {
            'name': 'Ebyte E22-400M30S',
            'manufacturer': 'Ebyte',
            'description': '1W High-Power SX1268 Module (433MHz)',
            'radio_module': 'SX1268',
            'power_output': '+30dBm (1W)',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 21,
                'IRQ': 16,
                'Busy': 20,
                'Reset': 18,
                'RXen': 12,
                'TXen': 13,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
                'DIO3_TCXO_VOLTAGE': True,
            },
            'notes': '433MHz variant for EU/Asia - High-power 1W module'
        },
        'Seeed SenseCAP E5': {
            'name': 'Seeed SenseCAP E5',
            'manufacturer': 'Seeed Studio',
            'description': 'SenseCAP LoRa HAT with SX1262',
            'radio_module': 'SX1262',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 8,
                'IRQ': 25,
                'Reset': 22,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
            },
            'notes': 'Seeed SenseCAP LoRa module'
        },
        'RAKwireless RAK2287': {
            'name': 'RAKwireless RAK2287',
            'manufacturer': 'RAKwireless',
            'description': 'RAK2287 Pi HAT with SX1262',
            'radio_module': 'SX1262',
            'meshtastic_compatible': True,
            'gpio_config': {
                'CS': 8,
                'IRQ': 25,
                'Busy': 24,
                'Reset': 17,
            },
            'lora_options': {
                'DIO2_AS_RF_SWITCH': True,
            },
            'notes': 'RAKwireless Pi HAT module'
        },
    }

    # Mapping from KNOWN_SPI_HATS key → template filename in available.d/
    HAT_KEY_TO_TEMPLATE = {
        'MeshAdv-Mini': 'meshadv-mini.yaml',
        'MeshAdv-Pi v1.1': 'meshadv-pi-v1.1.yaml',
        'MeshAdv-Pi-Hat': 'meshadv-pi-hat.yaml',
        'Adafruit RFM9x': 'adafruit-rfm9x.yaml',
        'Waveshare SX126X': 'waveshare-sx1262.yaml',
        'Elecrow LoRa RFM95': 'elecrow-rfm95.yaml',
        'FemtoFox': 'femtofox.yaml',
        'Ebyte E22-900M30S': 'ebyte-e22-900m30s.yaml',
        'Ebyte E22-400M30S': 'ebyte-e22-400m30s.yaml',
        'Seeed SenseCAP E5': 'seeed-sensecap.yaml',
        'RAKwireless RAK2287': 'rak-hat-spi.yaml',
    }

    @classmethod
    def match_eeprom_to_template(cls) -> Optional[str]:
        """Match HAT EEPROM product string to a template filename.

        Reads /proc/device-tree/hat/product (populated by RPi kernel
        from HAT EEPROM at I2C address 0x50) and matches against
        KNOWN_SPI_HATS keys.

        Returns:
            Template filename (e.g. 'meshadv-mini.yaml') or None.
        """
        try:
            product_path = Path('/proc/device-tree/hat/product')
            if not product_path.exists():
                return None
            product = product_path.read_text().strip('\x00').strip()
            if not product:
                return None

            for hat_key in cls.KNOWN_SPI_HATS:
                if hat_key.lower() in product.lower():
                    template = cls.HAT_KEY_TO_TEMPLATE.get(hat_key)
                    if template:
                        log(
                            f"EEPROM product '{product}' matched "
                            f"HAT '{hat_key}' → template '{template}'"
                        )
                        return template
            return None
        except (OSError, PermissionError):
            return None

    def __init__(self):
        self.detected_hardware = {}

    def detect_all(self):
        """Detect all hardware"""
        self.detected_hardware = {}

        self.detect_usb_modules()
        self.detect_spi_modules()
        self.detect_raspberry_pi_model()

        return self.detected_hardware

    def detect_usb_modules(self):
        """Detect USB LoRa modules"""
        log("Detecting USB LoRa modules")

        result = run_command('lsusb')

        if result['success']:
            usb_devices = []

            for line in result['stdout'].split('\n'):
                for vendor_product, device_info in self.KNOWN_USB_MODULES.items():
                    vendor, product = vendor_product.split(':')
                    if vendor.lower() in line.lower() and product.lower() in line.lower():
                        # Detected a known device
                        device_entry = {
                            'type': 'USB LoRa Module',
                            'chipset': device_info['name'],
                            'possible_devices': ', '.join(device_info['common_devices']),
                            'meshtastic_compatible': device_info['meshtastic_compatible'],
                            'power_requirement': device_info['power_requirement'],
                            'notes': device_info['notes'],
                            'usb_id': vendor_product,
                            'raw': line.strip()
                        }

                        # Check if it's likely a MeshToad
                        if vendor_product in ('1a86:7523', '1a86:55d4', '1a86:5512'):
                            device_entry['likely_meshtoad'] = True
                            device_entry['recommended_config'] = 'MediumFast preset recommended for MtnMesh compatibility'

                        usb_devices.append(device_entry)

            if usb_devices:
                self.detected_hardware['usb_modules'] = usb_devices

        # Also check /dev for ttyUSB or ttyACM devices
        usb_serial_devices = []
        for device_pattern in ['/dev/ttyUSB*', '/dev/ttyACM*']:
            devices = glob.glob(device_pattern)
            usb_serial_devices.extend(devices)

        if usb_serial_devices:
            self.detected_hardware['usb_serial_ports'] = usb_serial_devices

    def detect_spi_modules(self):
        """Detect SPI LoRa modules"""
        log("Detecting SPI LoRa modules")

        spi_devices = []

        # Check if SPI is enabled
        spi_enabled = self._is_spi_enabled()

        if spi_enabled:
            # Check for SPI devices
            spi_dev_pattern = '/dev/spidev*'
            spi_devs = glob.glob(spi_dev_pattern)

            if spi_devs:
                for spi_dev in spi_devs:
                    spi_devices.append({
                        'device': spi_dev,
                        'type': 'SPI Device',
                        'status': 'Available'
                    })

                self.detected_hardware['spi_devices'] = spi_devices

            # Try to detect which HAT is installed (if possible)
            # This is challenging without specific identifiers
            # We can check I2C EEPROM for HAT information
            hat_info = self._detect_hat_eeprom()
            if hat_info:
                self.detected_hardware['hat_info'] = hat_info
        else:
            self.detected_hardware['spi_status'] = 'SPI not enabled'

    def _is_spi_enabled(self):
        """Check if SPI is enabled"""
        config_files = ['/boot/config.txt', '/boot/firmware/config.txt']

        for config_file in config_files:
            if os.path.exists(config_file):
                try:
                    with open(config_file, 'r') as f:
                        content = f.read()
                        if 'dtparam=spi=on' in content:
                            return True
                except Exception as e:
                    from utils.logger import get_logger
                    logger = get_logger()
                    logger.debug(f"Could not read config file {config_file}: {e}")

        return False

    def _detect_hat_eeprom(self):
        """Try to detect HAT information from EEPROM"""
        # Raspberry Pi HATs have EEPROM at I2C address 0x50
        # This requires I2C tools
        result = run_command('i2cdetect -y 0')

        if not result['success']:
            result = run_command('i2cdetect -y 1')

        if result['success'] and '50' in result['stdout']:
            # HAT EEPROM detected, try to read it
            # Read HAT EEPROM product info (using list args for security)
            eeprom_result = run_command(['cat', '/proc/device-tree/hat/product'], stderr_to_null=True)

            if eeprom_result['success'] and eeprom_result['stdout'].strip():
                return {
                    'product': eeprom_result['stdout'].strip(),
                    'detected_via': 'EEPROM'
                }

        return None

    def detect_raspberry_pi_model(self):
        """Detect Raspberry Pi model"""
        log("Detecting Raspberry Pi model")

        try:
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().strip('\x00').strip()
                self.detected_hardware['raspberry_pi_model'] = model
        except FileNotFoundError:
            from utils.logger import get_logger
            logger = get_logger()
            logger.debug("Device tree model file not found - not running on Raspberry Pi?")

        # Also get CPU info
        try:
            with open('/proc/cpuinfo', 'r') as f:
                cpuinfo = f.read()

                # Extract relevant information
                for line in cpuinfo.split('\n'):
                    if 'Hardware' in line:
                        self.detected_hardware['cpu_hardware'] = line.split(':')[1].strip()
                    elif 'Revision' in line:
                        self.detected_hardware['cpu_revision'] = line.split(':')[1].strip()
        except FileNotFoundError:
            from utils.logger import get_logger
            logger = get_logger()
            logger.debug("CPU info file not found")

    def get_recommended_configuration(self):
        """Get recommended configuration based on detected hardware"""
        recommendations = []

        if 'usb_serial_ports' in self.detected_hardware:
            ports = self.detected_hardware['usb_serial_ports']
            if ports:
                recommendations.append({
                    'type': 'connection',
                    'value': f"--port {ports[0]}",
                    'description': f'Use USB serial port {ports[0]}'
                })

        if 'spi_devices' in self.detected_hardware:
            recommendations.append({
                'type': 'connection',
                'value': '--spi',
                'description': 'Use SPI connection'
            })

        return recommendations

    def show_hardware_info(self):
        """Display detected hardware information"""
        from rich.table import Table

        if not self.detected_hardware:
            console.print("[yellow]No hardware detected[/yellow]")
            return

        table = Table(title="Detected Hardware", show_header=True, header_style="bold magenta")
        table.add_column("Type", style="cyan")
        table.add_column("Details", style="green")

        for hw_type, details in self.detected_hardware.items():
            if isinstance(details, list):
                details_str = '\n'.join([str(d) for d in details])
            elif isinstance(details, dict):
                details_str = '\n'.join([f"{k}: {v}" for k, v in details.items()])
            else:
                details_str = str(details)

            table.add_row(hw_type, details_str)

        console.print(table)
