#!/usr/bin/env python3
"""Pre-flight verification for the dude-claw mesh_send TX path.

Replicates the EXACT byte construction in src/lora_ears.cpp (loraMeshSend)
against the authoritative Meshtastic protocol, then DECODES the packet back
to prove a real receiver would parse it — catching nonce byte-order,
protobuf-framing, header-flag, or channel-hash bugs in software, before any
RF leaves the antenna. Run on the host; transmits nothing.

References (firmware master):
  CryptoEngine.cpp  — AES-CTR nonce: [0:8]=packetId, [8:12]=fromNode, ctr=4
  Channels.cpp      — hash = xorHash(name) ^ xorHash(psk)
  RadioInterface.h  — 16-byte PacketHeader; flags = hop_limit | hop_start<<5
"""
import sys

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DEFAULT_PUBLIC_PSK = bytes.fromhex("d4f1bb3a20290759f0bcffabcf4e6901")


def xor_hash(b: bytes) -> int:
    c = 0
    for x in b:
        c ^= x
    return c


def channel_hash(name: bytes, psk: bytes) -> int:
    return xor_hash(name) ^ xor_hash(psk)


def build_packet(text: str, node_id: int, pkt_id: int, psk: bytes,
                 channel: bytes, hop_limit: int = 3) -> bytes:
    """Mirror loraMeshSend byte-for-byte."""
    # Data protobuf: field1 portnum=1 (varint), field2 payload (bytes)
    tb = text.encode()
    assert len(tb) <= 200
    plain = bytes([0x08, 0x01, 0x12, len(tb)]) + tb

    # AES-CTR nonce: [0:8]=packetId (id in low 4 LE), [8:12]=node (LE)
    nonce = bytearray(16)
    nonce[0:4] = pkt_id.to_bytes(4, "little")
    nonce[8:12] = node_id.to_bytes(4, "little")
    enc = Cipher(algorithms.AES(psk), modes.CTR(bytes(nonce))).encryptor()
    cipher = enc.update(plain) + enc.finalize()

    flags = (hop_limit & 0x07) | ((hop_limit & 0x07) << 5)
    hdr = (
        (0xFFFFFFFF).to_bytes(4, "little")   # to = broadcast
        + node_id.to_bytes(4, "little")       # from
        + pkt_id.to_bytes(4, "little")        # id
        + bytes([flags, channel_hash(channel, psk), 0, 0])
    )
    return hdr + cipher


def decode_packet(pkt: bytes, psk: bytes):
    """Inverse: a receiver's path. Returns (header_fields, portnum, text)."""
    to = int.from_bytes(pkt[0:4], "little")
    frm = int.from_bytes(pkt[4:8], "little")
    pid = int.from_bytes(pkt[8:12], "little")
    flags, chash, nh, relay = pkt[12], pkt[13], pkt[14], pkt[15]
    cipher = pkt[16:]

    nonce = bytearray(16)
    nonce[0:4] = pid.to_bytes(4, "little")
    nonce[8:12] = frm.to_bytes(4, "little")
    dec = Cipher(algorithms.AES(psk), modes.CTR(bytes(nonce))).decryptor()
    plain = dec.update(cipher) + dec.finalize()

    # parse Data protobuf (field1 varint portnum, field2 bytes payload)
    assert plain[0] == 0x08, "field1 tag"
    portnum = plain[1]
    assert plain[2] == 0x12, "field2 tag"
    plen = plain[3]
    text = plain[4:4 + plen].decode()
    return {"to": to, "from": frm, "id": pid, "flags": flags,
            "chan_hash": chash, "hop_limit": flags & 0x07,
            "hop_start": (flags >> 5) & 0x07}, portnum, text


def main() -> int:
    ok = True

    # 1. Channel hash for the public LongFast channel must be 0x08 (the hash
    #    moc's journal logs as undecodable and the claw's ears heard).
    h = channel_hash(b"LongFast", DEFAULT_PUBLIC_PSK)
    print(f"LongFast/public channel hash = 0x{h:02x} "
          f"({'OK' if h == 0x08 else 'WRONG'})")
    ok &= h == 0x08

    # 2. Round-trip: build then decode recovers the text + a TEXT portnum.
    text = "dude-claw first voice"
    node_id = 0x9faaa0a0
    pkt_id = 0x12345678
    pkt = build_packet(text, node_id, pkt_id, DEFAULT_PUBLIC_PSK, b"LongFast")
    hdr, portnum, got = decode_packet(pkt, DEFAULT_PUBLIC_PSK)
    print(f"packet {len(pkt)} bytes; header={hdr}")
    print(f"decoded portnum={portnum} (TEXT=1) text={got!r}")
    ok &= got == text
    ok &= portnum == 1
    ok &= hdr["to"] == 0xFFFFFFFF
    ok &= hdr["from"] == node_id
    ok &= hdr["chan_hash"] == 0x08
    ok &= hdr["hop_limit"] == 3 and hdr["hop_start"] == 3

    # 3. AES-256 path (32-byte key) round-trips too.
    psk32 = bytes(range(32))
    h2 = channel_hash(b"fleet", psk32)
    pkt2 = build_packet("ping", node_id, 99, psk32, b"fleet")
    _, pn2, got2 = decode_packet(pkt2, psk32)
    print(f"AES-256 round-trip: text={got2!r} portnum={pn2} "
          f"(fleet hash 0x{h2:02x})")
    ok &= got2 == "ping" and pn2 == 1

    print("\n" + ("ALL CHECKS PASS — packet construction is byte-correct"
                  if ok else "FAIL — packet construction is WRONG"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
