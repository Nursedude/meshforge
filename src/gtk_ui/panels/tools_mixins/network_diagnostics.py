"""
Network Diagnostics Mixin - Advanced network analysis

Provides /proc/net parsing, port listener enumeration, and service diagnostics.
Extracted from tools.py for maintainability.
"""

import os
import re
import struct
import subprocess
import threading
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import GLib


class NetworkDiagnosticsMixin:
    """Mixin providing network diagnostics functionality for ToolsPanel"""

    # TCP state mapping
    TCP_STATES = {
        '01': 'ESTABLISHED',
        '02': 'SYN_SENT',
        '03': 'SYN_RECV',
        '04': 'FIN_WAIT1',
        '05': 'FIN_WAIT2',
        '06': 'TIME_WAIT',
        '07': 'CLOSE',
        '08': 'CLOSE_WAIT',
        '09': 'LAST_ACK',
        '0A': 'LISTEN',
        '0B': 'CLOSING',
    }

    def _parse_proc_net(self, protocol: str) -> list:
        """Parse /proc/net/{tcp,udp} for connection info.

        Args:
            protocol: 'tcp' or 'udp'

        Returns:
            List of connection dicts with port, state, inode info
        """
        connections = []
        proc_file = f'/proc/net/{protocol}'

        if not os.path.exists(proc_file):
            return connections

        try:
            with open(proc_file, 'r') as f:
                lines = f.readlines()[1:]  # Skip header

            for line in lines:
                parts = line.split()
                if len(parts) < 10:
                    continue

                local_addr = parts[1]
                remote_addr = parts[2]
                state = parts[3]
                inode = parts[9] if len(parts) > 9 else '0'

                # Parse hex addresses
                local_ip_hex, local_port_hex = local_addr.split(':')
                local_port = int(local_port_hex, 16)

                remote_ip_hex, remote_port_hex = remote_addr.split(':')
                remote_port = int(remote_port_hex, 16)

                connections.append({
                    'local_port': local_port,
                    'remote_port': remote_port,
                    'state': self.TCP_STATES.get(state, state),
                    'state_hex': state,
                    'inode': inode,
                })

        except Exception:
            pass

        return connections

    def _get_inode_to_process(self) -> dict:
        """Map socket inodes to process info by scanning /proc/*/fd.

        Returns:
            Dict mapping inode strings to (pid, process_name) tuples
        """
        inode_map = {}

        try:
            for pid_dir in Path('/proc').iterdir():
                if not pid_dir.name.isdigit():
                    continue

                pid = pid_dir.name
                fd_dir = pid_dir / 'fd'

                try:
                    # Get process name
                    comm_file = pid_dir / 'comm'
                    if comm_file.exists():
                        proc_name = comm_file.read_text().strip()
                    else:
                        proc_name = 'unknown'

                    # Scan file descriptors
                    for fd in fd_dir.iterdir():
                        try:
                            link = fd.resolve()
                            link_str = str(link)
                            if link_str.startswith('socket:['):
                                inode = link_str[8:-1]  # Extract inode
                                inode_map[inode] = (pid, proc_name)
                        except (PermissionError, FileNotFoundError):
                            pass
                except (PermissionError, FileNotFoundError):
                    pass
        except Exception:
            pass

        return inode_map

    def _on_show_udp_listeners(self, button=None):
        """Show UDP port listeners"""
        threading.Thread(target=self._fetch_udp_listeners, daemon=True).start()

    def _fetch_udp_listeners(self):
        """Fetch UDP listener info"""
        GLib.idle_add(self._log, "\n=== UDP Listeners ===")

        connections = self._parse_proc_net('udp')
        inode_map = self._get_inode_to_process()

        for conn in connections:
            if conn['local_port'] > 0:
                pid, proc = inode_map.get(conn['inode'], ('?', 'unknown'))
                GLib.idle_add(
                    self._log,
                    f"  UDP :{conn['local_port']} - {proc} (PID: {pid})"
                )

        if not connections:
            GLib.idle_add(self._log, "  No UDP listeners found")

    def _on_show_tcp_listeners(self, button=None):
        """Show TCP port listeners"""
        threading.Thread(target=self._fetch_tcp_listeners, daemon=True).start()

    def _fetch_tcp_listeners(self):
        """Fetch TCP listener info"""
        GLib.idle_add(self._log, "\n=== TCP Listeners ===")

        connections = self._parse_proc_net('tcp')
        inode_map = self._get_inode_to_process()

        listeners = [c for c in connections if c['state'] == 'LISTEN']

        for conn in listeners:
            pid, proc = inode_map.get(conn['inode'], ('?', 'unknown'))
            GLib.idle_add(
                self._log,
                f"  TCP :{conn['local_port']} - {proc} (PID: {pid})"
            )

        if not listeners:
            GLib.idle_add(self._log, "  No TCP listeners found")

    def _on_check_rns_ports(self, button=None):
        """Check RNS-related ports"""
        threading.Thread(target=self._check_rns_ports_thread, daemon=True).start()

    def _check_rns_ports_thread(self):
        """Check RNS ports in background"""
        GLib.idle_add(self._log, "\n=== RNS Port Check ===")

        rns_ports = [
            (37428, 'UDP', 'RNS AutoInterface'),
            (4242, 'TCP', 'RNS TCP Server'),
        ]

        import socket
        for port, proto, name in rns_ports:
            try:
                sock_type = socket.SOCK_DGRAM if proto == 'UDP' else socket.SOCK_STREAM
                sock = socket.socket(socket.AF_INET, sock_type)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))
                sock.close()

                status = "OPEN" if result == 0 else "CLOSED"
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): {status}")
            except Exception as e:
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): Error - {e}")

    def _on_check_mesh_ports(self, button=None):
        """Check Meshtastic-related ports"""
        threading.Thread(target=self._check_mesh_ports_thread, daemon=True).start()

    def _check_mesh_ports_thread(self):
        """Check Meshtastic ports in background"""
        GLib.idle_add(self._log, "\n=== Meshtastic Port Check ===")

        mesh_ports = [
            (4403, 'TCP', 'meshtasticd API'),
            (9443, 'TCP', 'meshtasticd gRPC'),
        ]

        import socket
        for port, proto, name in mesh_ports:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('localhost', port))
                sock.close()

                status = "OPEN" if result == 0 else "CLOSED"
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): {status}")
            except Exception as e:
                GLib.idle_add(self._log, f"  {name} ({proto} {port}): Error - {e}")

    def _on_full_network_diagnostics(self, button=None):
        """Run comprehensive network diagnostics"""
        threading.Thread(target=self._run_full_diagnostics, daemon=True).start()

    def _run_full_diagnostics(self):
        """Run full network diagnostics in background"""
        GLib.idle_add(self._log, "\n" + "=" * 50)
        GLib.idle_add(self._log, "FULL NETWORK DIAGNOSTICS")
        GLib.idle_add(self._log, "=" * 50)

        # TCP listeners
        self._fetch_tcp_listeners()

        # UDP listeners
        self._fetch_udp_listeners()

        # RNS ports
        self._check_rns_ports_thread()

        # Meshtastic ports
        self._check_mesh_ports_thread()

        GLib.idle_add(self._log, "\n" + "=" * 50)
        GLib.idle_add(self._log, "Diagnostics complete")
