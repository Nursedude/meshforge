# MIT License - Copyright (c) 2024 Mark Qvist / unsigned.io
# Maintained by: Nursedude / MeshForge (github.com/Nursedude/meshforge)
# Origin: github.com/landandair/RNS_Over_Meshtastic
#
# RNS external interface plugin — bridges Reticulum over Meshtastic LoRa.
# Place in /etc/reticulum/interfaces/ and add to your reticulum config:
#
# [[Meshtastic Interface]]
#   type = Meshtastic_Interface
#   enabled = true
#   mode = gateway
#   port = /dev/ttyUSB0
#   data_speed = 8

from RNS.Interfaces.Interface import Interface
import struct
import threading
import time
import re


class MeshtasticInterface(Interface):
    DEFAULT_IFAC_SIZE = 8

    # Modem preset -> inter-packet delay (seconds).
    # Higher delay = slower but more reliable on that preset.
    speed_to_delay = {
        8: 0.4,   # Short-range Turbo (recommended)
        6: 1,     # Short Fast
        5: 3,     # Short-range Slow
        7: 12,    # Long Range - moderate Fast
        4: 4,     # Medium Range - Fast (slowest recommended)
        3: 6,     # Medium Range - Slow
        1: 15,    # Long Range - Slow
        0: 8,     # Long Range - Fast
    }

    # Maximum tracked destination-to-node mappings (LRU eviction)
    MAX_DEST_CACHE = 20

    # Maximum pending assembly contexts per remote node before cleanup
    MAX_ASSEMBLY_PER_NODE = 8

    owner = None
    port = None
    speed = None
    databits = None
    parity = None
    stopbits = None
    serial = None

    def __init__(self, owner, configuration):
        import importlib.util
        if importlib.util.find_spec('meshtastic') is not None:
            import meshtastic
            from pubsub import pub
            self.mt_bin_port = meshtastic.portnums_pb2.RETICULUM_TUNNEL_APP
        else:
            RNS.log("Using this interface requires a meshtastic module to be installed.", RNS.LOG_CRITICAL)
            RNS.log("You can install one with the command: python3 -m pip install meshtastic", RNS.LOG_CRITICAL)
            RNS.panic()

        super().__init__()

        ifconf = Interface.get_config_obj(configuration)

        name = ifconf["name"]
        self.name = name

        port = ifconf["port"] if "port" in ifconf else None
        ble_port = ifconf["ble_port"] if "ble_port" in ifconf else None
        tcp_port = ifconf["tcp_port"] if "tcp_port" in ifconf else None
        speed = int(ifconf["data_speed"]) if "data_speed" in ifconf else 8
        hop_limit = int(ifconf["hop_limit"]) if "hop_limit" in ifconf else 1

        self.HW_MTU = 564
        self.online = False
        self.bitrate = ifconf["bitrate"] if "bitrate" in ifconf else 500

        self.owner = owner
        self.port = port
        self.ble_port = ble_port
        self.tcp_port = tcp_port
        self.speed = speed
        self.timeout = 100
        self.interface = None
        self.outgoing_packet_storage = {}
        self.packet_i_queue = []
        self.assembly_dict = {}
        self.expected_index = {}
        self.requested_index = {}
        self.dest_to_node_dict = {}
        self._dest_order = []  # LRU tracking for dest_to_node_dict
        self.packet_index = 0
        self.hop_limit = hop_limit

        pub.subscribe(self.process_message, "meshtastic.receive")
        pub.subscribe(self.connection_complete, "meshtastic.connection.established")
        pub.subscribe(self.connection_closed, "meshtastic.connection.lost")

        # Try initial connection with brief retries.
        # If all fail, start a background retry thread instead of raising.
        # This lets rnsd proceed to bind port 37428 even if meshtasticd
        # isn't ready yet (boot race condition).
        max_retries = 3
        base_delay = 5
        connected = False
        for attempt in range(max_retries):
            try:
                self.open_interface()
                connected = True
                break
            except Exception as e:
                if attempt == max_retries - 1:
                    RNS.log("Meshtastic: Could not connect after "
                            + str(max_retries) + " attempts: " + str(e),
                            RNS.LOG_WARNING)
                    RNS.log("Meshtastic: Will retry in background "
                            "— rnsd continues starting",
                            RNS.LOG_WARNING)
                else:
                    delay = base_delay * (2 ** attempt)
                    RNS.log("Meshtastic: Connect failed (" + str(e)
                            + "), retrying in " + str(delay) + "s ("
                            + str(attempt + 1) + "/" + str(max_retries)
                            + ")", RNS.LOG_WARNING)
                    time.sleep(delay)

        if not connected:
            self._start_background_connect()

    def open_interface(self):
        if self.port:
            RNS.log("Meshtastic: Opening serial port " + self.port + "...", RNS.LOG_VERBOSE)
            from meshtastic.serial_interface import SerialInterface
            self.interface = SerialInterface(devPath=self.port)
        elif self.ble_port:
            RNS.log("Meshtastic: Opening ble device " + self.ble_port + "...", RNS.LOG_VERBOSE)
            from meshtastic.ble_interface import BLEInterface
            self.interface = BLEInterface(address=self.ble_port)
        elif self.tcp_port:
            RNS.log("Meshtastic: Opening tcp device " + self.tcp_port + "...", RNS.LOG_VERBOSE)
            from meshtastic.tcp_interface import TCPInterface, DEFAULT_TCP_PORT
            host = self.tcp_port
            port = DEFAULT_TCP_PORT
            if ":" in self.tcp_port:
                host, port = self.tcp_port.split(":", maxsplit=1)
            self.interface = TCPInterface(hostname=host, portNumber=port)
        else:
            raise ValueError(f"No port, ble_port, or tcp_port specified for {self}")

    def _start_background_connect(self):
        """Start a background thread to retry connection so rnsd can proceed."""
        thread = threading.Thread(
            target=self._background_connect_loop, daemon=True)
        thread.start()

    def _background_connect_loop(self):
        """Retry connection with exponential backoff in the background.

        Modeled after connection_closed() which uses the same retry
        pattern without raising — lets rnsd continue running.
        """
        max_retries = 10
        base_delay = 10
        for attempt in range(max_retries):
            delay = min(base_delay * (2 ** min(attempt, 5)), 300)
            RNS.log("Meshtastic: Background connect in " + str(delay)
                    + "s (attempt " + str(attempt + 1) + "/"
                    + str(max_retries) + ")")
            time.sleep(delay)
            try:
                self.open_interface()
                RNS.log("Meshtastic: Background connection succeeded",
                        RNS.LOG_NOTICE)
                return
            except Exception as e:
                RNS.log("Meshtastic: Background connect failed: "
                        + str(e), RNS.LOG_WARNING)
        RNS.log("Meshtastic: All background connect attempts exhausted",
                RNS.LOG_ERROR)

    def configure_device(self, interface):
        ourNode = interface.getNode('^local')
        if ourNode.localConfig.lora.modem_preset != self.speed:
            ourNode.localConfig.lora.modem_preset = self.speed
            ourNode.writeConfig("lora")
            self.online = False
        else:
            thread = threading.Thread(target=self.write_loop)
            thread.daemon = True
            thread.start()
            self.online = True

    def _update_dest_cache(self, dest, from_addr):
        """Update destination-to-node mapping with LRU eviction."""
        # Remove old entry if it exists (to re-insert at front)
        if dest in self.dest_to_node_dict:
            self._dest_order.remove(dest)

        self.dest_to_node_dict[dest] = from_addr
        self._dest_order.insert(0, dest)

        # Evict least-recently-used entries beyond the limit
        while len(self._dest_order) > self.MAX_DEST_CACHE:
            old_dest = self._dest_order.pop()
            self.dest_to_node_dict.pop(old_dest, None)

    def check_dest_incoming(self, data, from_addr):
        bit_str = "{:08b}".format(int(data[0]))
        if re.match(r'00..11..', bit_str):
            dest = data[2:18]
            self._update_dest_cache(dest, from_addr)
        self.process_incoming(data)

    def process_incoming(self, data):
        self.rxb += len(data)
        self.owner.inbound(data, self)

    def process_outgoing(self, data: bytes):
        if len(self.packet_i_queue) < 256:
            from meshtastic import BROADCAST_ADDR
            dest = BROADCAST_ADDR
            if data[2:18] in self.dest_to_node_dict:
                dest = self.dest_to_node_dict[data[2:18]]
            handler = PacketHandler(data, self.packet_index, custom_destination_id=dest)
            for key in handler.get_keys():
                self.packet_i_queue.append((handler.index, key))
            self.outgoing_packet_storage[handler.index] = handler
            self.packet_index = calc_index(self.packet_index)

    def _cleanup_assembly(self, from_addr, completed_index):
        """Remove completed assembly and enforce per-node limits."""
        if from_addr in self.assembly_dict:
            self.assembly_dict[from_addr].pop(completed_index, None)
            # Evict oldest entries if too many pending assemblies
            node_assemblies = self.assembly_dict[from_addr]
            while len(node_assemblies) > self.MAX_ASSEMBLY_PER_NODE:
                oldest = next(iter(node_assemblies))
                node_assemblies.pop(oldest)

    def process_message(self, packet, interface):
        """Process meshtastic traffic incoming to system."""
        if "decoded" not in packet:
            return
        if packet["decoded"]["portnum"] != "RETICULUM_TUNNEL_APP":
            return

        from_addr = packet["from"]
        if from_addr not in self.expected_index:
            self.expected_index[from_addr] = []
        expected_index = self.expected_index[from_addr]
        if from_addr not in self.requested_index:
            self.requested_index[from_addr] = []
        requested_index = self.requested_index[from_addr]
        if from_addr not in self.assembly_dict:
            self.assembly_dict[from_addr] = {}

        payload = packet["decoded"]["payload"]
        packet_handler = PacketHandler()

        if payload[:3] == b'REQ':  # Request for retransmission
            new_index, pos = packet_handler.get_metadata(payload[3:])
            self.packet_i_queue.insert(0, (new_index, pos))
        else:  # Data packet
            new_index, pos = packet_handler.get_metadata(payload)
            expect_followup = True

            if (new_index, abs(pos)) in expected_index:
                while (new_index, abs(pos)) in expected_index:
                    expected_index.remove((new_index, abs(pos)))
            elif (new_index, abs(pos)) in requested_index:
                requested_index.remove((new_index, abs(pos)))
                expect_followup = False
            elif len(expected_index):
                # Unexpected packet — request retransmission of what we expected
                ex_index, ex_pos = expected_index.pop(0)
                requested_index.append((ex_index, abs(ex_pos)))
                if len(requested_index) > 10:
                    requested_index.pop(0)
                self.packet_i_queue.insert(0, (-1, 0))
                self.outgoing_packet_storage[-1] = PacketHandler()
                self.outgoing_packet_storage[-1].data_dict[0] = (
                    b'REQ' + struct.pack(PacketHandler.struct_format, ex_index, ex_pos)
                )

            if new_index in self.assembly_dict[from_addr]:
                old_handler = self.assembly_dict[from_addr][new_index]
                data = old_handler.process_packet(payload)
            else:
                data = packet_handler.process_packet(payload)
                self.assembly_dict[from_addr][new_index] = packet_handler

            if data:
                self.check_dest_incoming(data, from_addr)
                self._cleanup_assembly(from_addr, new_index)

            if pos < 0:
                expected = (calc_index(new_index), 1)
            else:
                expected = (new_index, (pos + 1))
            if expect_followup:
                expected_index.insert(0, expected)

    def write_loop(self):
        """Writes packets from queue to meshtastic device."""
        RNS.log('Meshtastic: outgoing loop started')
        sleep_time = self.speed_to_delay.get(self.speed, 7)
        import meshtastic
        while True:
            data = None
            dest = meshtastic.BROADCAST_ADDR
            while not data and self.packet_i_queue:
                index, position = self.packet_i_queue.pop(0)
                if index in self.outgoing_packet_storage:
                    stored = self.outgoing_packet_storage[index]
                    if isinstance(stored, PacketHandler):
                        data = stored[position]
                        dest = stored.destination_id or meshtastic.BROADCAST_ADDR
                    elif isinstance(stored, list):
                        # Legacy format: list of raw payloads
                        if position < len(stored):
                            data = stored[position]
            if data:
                self.txb += len(data) - struct.calcsize(PacketHandler.struct_format)
                self.interface.sendData(
                    data,
                    portNum=self.mt_bin_port,
                    destinationId=dest,
                    wantAck=False,
                    wantResponse=False,
                    channelIndex=0,
                    hopLimit=self.hop_limit,
                )
                # Clean up completed outgoing packets to prevent memory leak
                if index in self.outgoing_packet_storage:
                    stored = self.outgoing_packet_storage[index]
                    if isinstance(stored, PacketHandler) and stored.is_done():
                        del self.outgoing_packet_storage[index]
            time.sleep(sleep_time)

    def connection_complete(self, interface):
        """Process meshtastic connection opened."""
        RNS.log("Meshtastic: Connected")
        self.configure_device(interface)
        self.interface = interface

    def connection_closed(self, interface):
        """Handle meshtastic disconnection with bounded retry."""
        RNS.log("Meshtastic: Disconnected")
        self.online = False
        max_retries = 5
        base_delay = 10
        for attempt in range(max_retries):
            delay = base_delay * (2 ** attempt)
            RNS.log(f"Meshtastic: Reconnecting in {delay}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
            try:
                self.open_interface()
                return
            except Exception as e:
                RNS.log(f"Meshtastic: Reconnect failed: {e}", RNS.LOG_ERROR)
        RNS.log("Meshtastic: All reconnect attempts exhausted", RNS.LOG_CRITICAL)

    @staticmethod
    def should_ingress_limit():
        return False

    def __str__(self):
        return "MeshtasticInterface[" + self.name + "]"


class PacketHandler:
    struct_format = 'Bb'

    def __init__(self, data=None, index=None, max_payload=200, custom_destination_id=None):
        self.max_payload = max_payload
        self.index = index
        self.data_dict = {}
        self.loop_pos = 1
        self.done = False
        self.destination_id = custom_destination_id
        if data:
            self.split_data(data)

    def split_data(self, data: bytes):
        """Split data into even chunks and add metadata."""
        data_list = []
        data_len = len(data)
        # Calculate number of packets — avoid creating empty trailing packet
        num_packets = max(1, (data_len + self.max_payload - 1) // self.max_payload)
        packet_size = (data_len + num_packets - 1) // num_packets
        for i in range(0, data_len, packet_size):
            data_list.append(data[i:i + packet_size])
        for i, packet in enumerate(data_list):
            pos = i + 1
            if pos == len(data_list):
                pos = -pos
            meta_data = struct.pack(self.struct_format, self.index, pos)
            self.data_dict[pos] = meta_data + packet

    def get_next(self):
        """Get next packet to send."""
        if not self.data_dict:
            self.done = True
            return None
        ret = self[self.loop_pos]
        if self.loop_pos >= max(abs(k) for k in self.data_dict.keys()):
            self.loop_pos = 1
            self.done = True
        else:
            self.loop_pos += 1
        return ret

    def is_done(self):
        """Return True if the get_next loop is completed."""
        return self.done

    def __getitem__(self, i):
        """Get the packet at an index."""
        if i in self.data_dict:
            return self.data_dict[i]
        elif -i in self.data_dict:
            return self.data_dict[-i]
        return None

    def process_packet(self, packet: bytes):
        """Returns data if the packet is complete, None if it isn't."""
        new_index, pos = self.get_metadata(packet)
        self.index = new_index
        self.data_dict[abs(pos)] = packet
        if pos < 0:
            return self.assemble_data()
        return None

    def check_data(self):
        """Check content of data dict against the expected content."""
        expected = 1
        for key in sorted(self.data_dict.keys()):
            if key != expected:
                return False
            expected += 1
        return True

    def get_keys(self):
        return self.data_dict.keys()

    def assemble_data(self):
        """Put all the data together and return it or None on failure."""
        if self.check_data():
            header_size = struct.calcsize(self.struct_format)
            data = b''
            for key in sorted(self.data_dict.keys()):
                data += self.data_dict[key][header_size:]
            return data
        return None

    def get_metadata(self, packet):
        """Get and return metadata from packet."""
        size = struct.calcsize(self.struct_format)
        meta_data = packet[:size]
        new_index, pos = struct.unpack(self.struct_format, meta_data)
        return new_index, pos


def calc_index(curr_index):
    return (curr_index + 1) % 256


interface_class = MeshtasticInterface
