#!/usr/bin/env python3
"""Parse BTSnoop HCI logs and decode BLE ATT traffic.

The original helper was good for tiny packets, but it treated each ACL record
as a full ATT packet. That breaks on larger writes because BLE ATT values are
often fragmented across multiple HCI ACL records. This version reassembles
L2CAP PDUs before decoding ATT, and adds best-effort parsing for the confirmed
ILEDColor `0x54` protocol used on the A95x characteristics.
"""

from __future__ import annotations

import os
import struct
import sys
from typing import Dict, Iterable, List, Optional, Tuple


ILED54_COMMANDS = {
    0x00: "Continue",
    0x01: "EndStream",
    0x06: "StartStream",
    0x09: "Brightness",
    0x0A: "LedEnable",
    0x0D: "Connect",
    0x0E: "SetPass",
    0x0F: "TestPass",
}


def read_btsnoop(path: str) -> List[Tuple[int, int, bytes]]:
    with open(path, "rb") as f:
        data = f.read()

    magic = data[:8]
    if magic == b"btsnoop\x00":
        pass
    elif magic == b"\x00hctsnoop":
        print("Warning: swapped endianness")
    else:
        raise ValueError(f"Unknown magic: {magic!r}")

    version, datalink = struct.unpack_from(">II", data, 8)
    print(f"Version: {version}, Datalink: {datalink}")
    offset = 16

    records = []
    while offset < len(data):
        orig_len, incl_len, flags, drops, ts = struct.unpack_from(">IIIIq", data, offset)
        offset += 24
        pkt = data[offset:offset + incl_len]
        offset += incl_len
        if len(pkt) != incl_len:
            break
        records.append((ts, flags, pkt))
    return records


def parse_att(att_payload: bytes) -> Optional[Tuple[str, bytes]]:
    if len(att_payload) < 1:
        return None
    opcode = att_payload[0]
    op_map = {
        0x01: "ErrorRsp",
        0x04: "FindInfoReq",
        0x05: "FindInfoRsp",
        0x08: "ReadByTypeReq",
        0x09: "ReadByTypeRsp",
        0x0A: "ReadReq",
        0x0B: "ReadRsp",
        0x10: "ReadByGrpTypeReq",
        0x11: "ReadByGrpTypeRsp",
        0x12: "WriteReq",
        0x13: "WriteRsp",
        0x52: "WriteCmd",
        0x1B: "HandleValueNotification",
        0x1D: "HandleValueIndication",
        0x1E: "HandleValueConfirmation",
    }
    return op_map.get(opcode, f"Opcode_0x{opcode:02x}"), att_payload


def reassemble_l2cap(records: Iterable[Tuple[int, int, bytes]]) -> List[Tuple[int, str, int, bytes]]:
    """Reassemble ACL fragments into full L2CAP PDUs.

    Returns tuples of `(timestamp, direction, cid, full_l2cap_pdu)`.
    """
    pending: Dict[Tuple[str, int], Dict[str, object]] = {}
    complete: List[Tuple[int, str, int, bytes]] = []

    for ts, flags, pkt in records:
        if not pkt or pkt[0] != 0x02:
            continue

        handle_flags, data_len = struct.unpack_from("<HH", pkt, 1)
        conn_handle = handle_flags & 0x0FFF
        pb_flag = (handle_flags >> 12) & 0x3
        acl_data = pkt[5:5 + data_len]
        if not acl_data:
            continue

        direction = "HOST->CTRL" if flags == 0x00 else "CTRL->HOST"
        key = (direction, conn_handle)

        # Packet boundary flags:
        #   0b10 = first automatically flushable packet of a higher layer PDU
        #   0b01 = continuation fragment
        # In practice logs can be a bit messy, so we also accept any fragment
        # with a valid L2CAP header as a fresh start.
        starts_pdu = pb_flag == 0x2 or (pb_flag != 0x1 and len(acl_data) >= 4)

        if starts_pdu:
            if len(acl_data) < 4:
                continue
            l2cap_len, cid = struct.unpack_from("<HH", acl_data, 0)
            pending[key] = {
                "ts": ts,
                "direction": direction,
                "cid": cid,
                "expected_len": 4 + l2cap_len,
                "data": bytearray(acl_data),
            }
        elif pb_flag == 0x1 and key in pending:
            pending[key]["data"].extend(acl_data)
        else:
            continue

        current = pending.get(key)
        if not current:
            continue

        data_buf = current["data"]
        expected_len = current["expected_len"]
        if len(data_buf) >= expected_len:
            payload = bytes(data_buf[:expected_len])
            complete.append((current["ts"], current["direction"], current["cid"], payload))
            del pending[key]

    return complete


def extract_att(records: Iterable[Tuple[int, int, bytes]]) -> List[Tuple[int, str, str, bytes]]:
    """Extract ATT packets from reassembled L2CAP traffic."""
    results = []
    for ts, direction, cid, l2cap_pdu in reassemble_l2cap(records):
        if cid != 0x0004:
            continue
        l2cap_len = struct.unpack_from("<H", l2cap_pdu, 0)[0]
        att = l2cap_pdu[4:4 + l2cap_len]
        parsed = parse_att(att)
        if parsed:
            results.append((ts, direction, parsed[0], parsed[1]))
    return results


def checksum16_sum(packet: bytes) -> int:
    return sum(packet[:-2]) & 0xFFFF


def decode_iled54(value: bytes) -> Optional[dict]:
    if len(value) < 6 or value[0] != 0x54:
        return None

    command = value[1]
    declared_length = struct.unpack_from(">H", value, 2)[0]
    body = value[4:]
    payload = value[4:-2]
    checksum_seen = struct.unpack_from(">H", value, len(value) - 2)[0]
    checksum_expected = checksum16_sum(value)

    decoded = {
        "command": command,
        "command_name": ILED54_COMMANDS.get(command, f"0x{command:02x}"),
        "declared_length": declared_length,
        "actual_length": len(body),
        "length_matches": declared_length == len(body),
        "checksum_seen": checksum_seen,
        "checksum_expected": checksum_expected,
        "checksum_valid": checksum_seen == checksum_expected,
        "details": [],
    }

    if command == 0x00 and len(payload) >= 6:
        sequence = struct.unpack_from(">I", payload, 0)[0]
        data_len = struct.unpack_from(">H", payload, 4)[0]
        chunk = payload[6:]
        decoded["details"].append(f"sequence={sequence}")
        decoded["details"].append(f"data_len={data_len}")
        decoded["details"].append(f"chunk_len={len(chunk)}")
    elif command == 0x06 and len(payload) == 11:
        crc32 = struct.unpack_from(">I", payload, 0)[0]
        total_len = struct.unpack_from(">H", payload, 6)[0]
        decoded["details"].append(f"crc32=0x{crc32:08x}")
        decoded["details"].append(f"stream_len={total_len}")
        decoded["details"].append(f"reserved={payload[4:6].hex()}:{payload[8:11].hex()}")
    elif payload:
        decoded["details"].append(f"payload={payload.hex()}")

    return decoded


def format_iled54(decoded: dict) -> str:
    parts = [
        f"cmd={decoded['command_name']}",
        f"declared={decoded['declared_length']}",
        f"actual={decoded['actual_length']}",
        f"len_ok={decoded['length_matches']}",
        f"checksum=0x{decoded['checksum_seen']:04x}",
        f"checksum_ok={decoded['checksum_valid']}",
    ]
    parts.extend(decoded["details"])
    return " | ".join(parts)


def main() -> None:
    default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "research", "btsnoop_hci.cfa")
    path = sys.argv[1] if len(sys.argv) > 1 else default_path
    records = read_btsnoop(path)
    print(f"Total records: {len(records)}")

    l2cap_pdus = reassemble_l2cap(records)
    print(f"Reassembled L2CAP PDUs: {len(l2cap_pdus)}")

    att_pkts = extract_att(records)
    print(f"ATT packets: {len(att_pkts)}")

    interesting = [
        pkt for pkt in att_pkts
        if pkt[2] in ("WriteReq", "WriteCmd", "HandleValueNotification")
    ]
    print(f"Interesting ATT packets: {len(interesting)}")
    print()

    prev_ts = None
    for ts, direction, op_name, payload in interesting:
        ts_sec = ts / 1_000_000
        delta = ""
        if prev_ts is not None:
            delta = f" (+{ts_sec - prev_ts:.3f}s)"
        prev_ts = ts_sec

        if len(payload) < 3:
            print(f"[{ts_sec:.3f}s]{delta} {direction} {op_name} hex={payload.hex()}")
            continue

        handle = struct.unpack_from("<H", payload, 1)[0]
        value = payload[3:]
        line = (
            f"[{ts_sec:.3f}s]{delta} {direction} {op_name} "
            f"handle=0x{handle:04x} len={len(value)} hex={value.hex()}"
        )

        decoded = decode_iled54(value)
        if decoded:
            line += f" | {format_iled54(decoded)}"

        print(line)


if __name__ == "__main__":
    main()
