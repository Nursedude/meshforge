"""Meshtastic module configuration"""

from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.table import Table

from utils.logger import log

console = Console()


class ModuleConfigurator:
    """Configure Meshtastic modules"""

    def __init__(self, interface=None):
        self.interface = interface
        self.module_config = {}

    def interactive_module_config(self):
        """Interactive module configuration menu"""
        console.print("\n[bold cyan]Module Configuration[/bold cyan]\n")

        while True:
            console.print("\n[cyan]Available Modules:[/cyan]")
            console.print("1.  MQTT Module")
            console.print("2.  Serial Module")
            console.print("3.  External Notification Module")
            console.print("4.  Store & Forward Module")
            console.print("5.  Range Test Module")
            console.print("6.  Telemetry Module")
            console.print("7.  Canned Message Module")
            console.print("8.  Audio Module")
            console.print("9.  Remote Hardware Module")
            console.print("10. Neighbor Info Module")
            console.print("11. Detection Sensor Module")
            console.print("12. View Current Configuration")
            console.print("13. Back to Main Menu")

            choice = Prompt.ask(
                "\nSelect module to configure",
                choices=[str(i) for i in range(1, 14)],
                default="13"
            )

            if choice == "1":
                self.configure_mqtt()
            elif choice == "2":
                self.configure_serial()
            elif choice == "3":
                self.configure_external_notification()
            elif choice == "4":
                self.configure_store_forward()
            elif choice == "5":
                self.configure_range_test()
            elif choice == "6":
                self.configure_telemetry()
            elif choice == "7":
                self.configure_canned_messages()
            elif choice == "8":
                self.configure_audio()
            elif choice == "9":
                self.configure_remote_hardware()
            elif choice == "10":
                self.configure_neighbor_info()
            elif choice == "11":
                self.configure_detection_sensor()
            elif choice == "12":
                self.show_module_config()
            elif choice == "13":
                break

        return self.module_config

    def configure_mqtt(self):
        """Configure MQTT module"""
        console.print("\n[bold cyan]MQTT Module Configuration[/bold cyan]\n")
        console.print("[yellow]MQTT enables bridging your mesh to the internet[/yellow]\n")

        mqtt_config = {}

        enabled = Confirm.ask("Enable MQTT?", default=False)
        mqtt_config['enabled'] = enabled

        if enabled:
            # Server address
            mqtt_config['address'] = Prompt.ask(
                "MQTT Server address",
                default="mqtt.meshtastic.org"
            )

            # Username
            mqtt_config['username'] = Prompt.ask(
                "MQTT Username (or press Enter for anonymous)",
                default=""
            )

            # Password with confirmation
            if mqtt_config['username']:
                while True:
                    password = Prompt.ask("MQTT Password", password=True)
                    confirm = Prompt.ask("Confirm password", password=True)
                    if password == confirm:
                        mqtt_config['password'] = password
                        break
                    console.print("[red]Passwords don't match. Try again.[/red]")

            # Encryption
            mqtt_config['encryption_enabled'] = Confirm.ask(
                "Enable encryption?",
                default=True
            )

            # JSON enabled
            mqtt_config['json_enabled'] = Confirm.ask(
                "Enable JSON output?",
                default=False
            )

            # TLS
            mqtt_config['tls_enabled'] = Confirm.ask(
                "Enable TLS?",
                default=True
            )

            # Root topic
            mqtt_config['root'] = Prompt.ask(
                "Root topic",
                default="msh/US"
            )

            console.print("\n[green]MQTT configured![/green]")

        self.module_config['mqtt'] = mqtt_config
        self._display_module_config('MQTT', mqtt_config)

    def configure_serial(self):
        """Configure Serial module"""
        console.print("\n[bold cyan]Serial Module Configuration[/bold cyan]\n")

        serial_config = {}

        enabled = Confirm.ask("Enable Serial module?", default=True)
        serial_config['enabled'] = enabled

        if enabled:
            serial_config['echo'] = Confirm.ask(
                "Enable echo?",
                default=False
            )

            serial_config['mode'] = Prompt.ask(
                "Serial mode",
                choices=["SIMPLE", "PROTO", "TEXTMSG", "NMEA"],
                default="SIMPLE"
            )

            serial_config['baud'] = IntPrompt.ask(
                "Baud rate",
                default=115200
            )

            serial_config['timeout'] = IntPrompt.ask(
                "Timeout (milliseconds)",
                default=0
            )

            console.print("\n[green]Serial module configured![/green]")

        self.module_config['serial'] = serial_config
        self._display_module_config('Serial', serial_config)

    def configure_external_notification(self):
        """Configure External Notification module"""
        console.print("\n[bold cyan]External Notification Module[/bold cyan]\n")
        console.print("[yellow]Control external LEDs, buzzers, or displays[/yellow]\n")

        ext_notif_config = {}

        enabled = Confirm.ask("Enable external notifications?", default=False)
        ext_notif_config['enabled'] = enabled

        if enabled:
            # Output settings
            ext_notif_config['output'] = IntPrompt.ask(
                "Output GPIO pin",
                default=0
            )

            ext_notif_config['output_ms'] = IntPrompt.ask(
                "Output duration (milliseconds)",
                default=1000
            )

            # Alert settings
            ext_notif_config['alert_message'] = Confirm.ask(
                "Alert on message?",
                default=True
            )

            ext_notif_config['alert_bell'] = Confirm.ask(
                "Alert on bell?",
                default=True
            )

            console.print("\n[green]External notification configured![/green]")

        self.module_config['external_notification'] = ext_notif_config
        self._display_module_config('External Notification', ext_notif_config)

    def configure_store_forward(self):
        """Configure Store & Forward module"""
        console.print("\n[bold cyan]Store & Forward Module[/bold cyan]\n")
        console.print("[yellow]Store messages and forward to nodes that were offline[/yellow]\n")

        sf_config = {}

        enabled = Confirm.ask("Enable Store & Forward?", default=False)
        sf_config['enabled'] = enabled

        if enabled:
            sf_config['is_server'] = Confirm.ask(
                "Act as Store & Forward server?",
                default=False
            )

            if sf_config['is_server']:
                sf_config['heartbeat'] = Confirm.ask(
                    "Send heartbeat?",
                    default=True
                )

                sf_config['records'] = IntPrompt.ask(
                    "Maximum records to store",
                    default=200
                )

                sf_config['history_return_max'] = IntPrompt.ask(
                    "Max messages to return",
                    default=100
                )

            console.print("\n[green]Store & Forward configured![/green]")

        self.module_config['store_forward'] = sf_config
        self._display_module_config('Store & Forward', sf_config)

    def configure_range_test(self):
        """Configure Range Test module"""
        console.print("\n[bold cyan]Range Test Module[/bold cyan]\n")

        rt_config = {}

        enabled = Confirm.ask("Enable Range Test?", default=False)
        rt_config['enabled'] = enabled

        if enabled:
            rt_config['sender'] = IntPrompt.ask(
                "Send interval (seconds, 0=disabled)",
                default=0
            )

            rt_config['save'] = Confirm.ask(
                "Save results?",
                default=False
            )

            console.print("\n[green]Range Test configured![/green]")

        self.module_config['range_test'] = rt_config
        self._display_module_config('Range Test', rt_config)

    def configure_telemetry(self):
        """Configure Telemetry module"""
        console.print("\n[bold cyan]Telemetry Module[/bold cyan]\n")

        telemetry_config = {}

        # Device metrics
        console.print("[cyan]Device Metrics:[/cyan]")
        telemetry_config['device_update_interval'] = IntPrompt.ask(
            "Device update interval (seconds)",
            default=900
        )

        # Environment metrics
        env_enabled = Confirm.ask("\nEnable environment metrics?", default=False)
        telemetry_config['environment_measurement_enabled'] = env_enabled

        if env_enabled:
            telemetry_config['environment_update_interval'] = IntPrompt.ask(
                "Environment update interval (seconds)",
                default=900
            )

            telemetry_config['environment_display_fahrenheit'] = Confirm.ask(
                "Display temperature in Fahrenheit?",
                default=False
            )

        # Air quality
        telemetry_config['air_quality_enabled'] = Confirm.ask(
            "\nEnable air quality monitoring?",
            default=False
        )

        console.print("\n[green]Telemetry configured![/green]")

        self.module_config['telemetry'] = telemetry_config
        self._display_module_config('Telemetry', telemetry_config)

    def configure_canned_messages(self):
        """Configure Canned Messages module"""
        console.print("\n[bold cyan]Canned Messages Module[/bold cyan]\n")

        cm_config = {}

        enabled = Confirm.ask("Enable Canned Messages?", default=False)
        cm_config['enabled'] = enabled

        if enabled:
            console.print("\n[cyan]Enter canned messages (empty line to finish):[/cyan]")
            messages = []

            while True:
                msg = Prompt.ask(f"Message {len(messages) + 1} (or press Enter to finish)", default="")
                if not msg:
                    break
                messages.append(msg)

            cm_config['messages'] = messages
            cm_config['allow_input_source'] = Prompt.ask(
                "Input source",
                choices=["none", "updown", "rotEnc", "cardkb"],
                default="updown"
            )

            console.print("\n[green]Canned Messages configured![/green]")

        self.module_config['canned_messages'] = cm_config
        self._display_module_config('Canned Messages', cm_config)

    def configure_audio(self):
        """Configure Audio module"""
        console.print("\n[bold cyan]Audio Module[/bold cyan]\n")

        audio_config = {}

        enabled = Confirm.ask("Enable Audio?", default=False)
        audio_config['enabled'] = enabled

        if enabled:
            audio_config['codec2_enabled'] = Confirm.ask(
                "Enable Codec2 compression?",
                default=True
            )

            audio_config['bitrate'] = Prompt.ask(
                "Bitrate",
                choices=["3200", "2400", "1600", "1400", "1300", "1200", "700"],
                default="3200"
            )

            console.print("\n[green]Audio configured![/green]")

        self.module_config['audio'] = audio_config
        self._display_module_config('Audio', audio_config)

    def configure_remote_hardware(self):
        """Configure Remote Hardware module"""
        console.print("\n[bold cyan]Remote Hardware Module[/bold cyan]\n")

        rh_config = {}

        enabled = Confirm.ask("Enable Remote Hardware?", default=False)
        rh_config['enabled'] = enabled

        if enabled:
            rh_config['allow_undefined_pin_access'] = Confirm.ask(
                "Allow undefined pin access?",
                default=False
            )

            console.print("\n[green]Remote Hardware configured![/green]")

        self.module_config['remote_hardware'] = rh_config
        self._display_module_config('Remote Hardware', rh_config)

    def configure_neighbor_info(self):
        """Configure Neighbor Info module"""
        console.print("\n[bold cyan]Neighbor Info Module[/bold cyan]\n")

        ni_config = {}

        enabled = Confirm.ask("Enable Neighbor Info?", default=True)
        ni_config['enabled'] = enabled

        if enabled:
            ni_config['update_interval'] = IntPrompt.ask(
                "Update interval (seconds)",
                default=900
            )

            console.print("\n[green]Neighbor Info configured![/green]")

        self.module_config['neighbor_info'] = ni_config
        self._display_module_config('Neighbor Info', ni_config)

    def configure_detection_sensor(self):
        """Configure Detection Sensor module"""
        console.print("\n[bold cyan]Detection Sensor Module[/bold cyan]\n")

        ds_config = {}

        enabled = Confirm.ask("Enable Detection Sensor?", default=False)
        ds_config['enabled'] = enabled

        if enabled:
            ds_config['monitor_pin'] = IntPrompt.ask(
                "Monitor GPIO pin",
                default=0
            )

            ds_config['detection_triggered_high'] = Confirm.ask(
                "Trigger on HIGH?",
                default=True
            )

            ds_config['send_bell'] = Confirm.ask(
                "Send bell on detection?",
                default=True
            )

            console.print("\n[green]Detection Sensor configured![/green]")

        self.module_config['detection_sensor'] = ds_config
        self._display_module_config('Detection Sensor', ds_config)

    def show_module_config(self):
        """Display current module configuration"""
        console.print("\n[bold cyan]Current Module Configuration[/bold cyan]\n")

        if not self.module_config:
            console.print("[yellow]No modules configured yet[/yellow]")
            return

        for module_name, config in self.module_config.items():
            self._display_module_config(module_name.replace('_', ' ').title(), config)

    def _display_module_config(self, module_name, config):
        """Display a module's configuration"""
        table = Table(title=f"{module_name} Configuration", show_header=True, header_style="bold magenta")
        table.add_column("Setting", style="cyan")
        table.add_column("Value", style="green")

        for key, value in config.items():
            display_key = key.replace('_', ' ').title()
            display_value = str(value)

            # Special handling for passwords
            if 'password' in key.lower() and value:
                display_value = "********"

            table.add_row(display_key, display_value)

        console.print("\n")
        console.print(table)
