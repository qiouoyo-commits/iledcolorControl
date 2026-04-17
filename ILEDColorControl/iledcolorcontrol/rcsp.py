import os
import time
from collections import deque
from dataclasses import dataclass
from threading import Event, Lock
from typing import Optional

from .ble import ILED_COLOR_PROFILE, create_transport, resolve_profile


RCSP_SYNC_PREFIX = bytes.fromhex("FEDCBA")
RCSP_SYNC_SUFFIX = 0xEF
RCSP_RESET_AUTH_FLAG = bytes.fromhex("FEDCBAC00600020001EF")
RCSP_AUTH_OK = bytes.fromhex("0270617373")
RCSP_DEFAULT_LINK_KEY = bytes.fromhex("06775f87918dd423005df1d8cf0c142b")
RCSP_DEFAULT_AUTH_SEED = bytes.fromhex("112233332211")

RCSP_SCHEDULE_TABLE = bytes.fromhex(
    "64ac285ac9b337c50a10b7a3bab19746"
    "3d05dc666ef69af80d589567c6aaabec"
    "a0689b96d4ebbf434936e96a89d8c38a"
    "946399bc7bbec122bb5c71d51f92575d"
    "8f44411d51e64017fbfd193234b8612a"
    "ca236fda39f7a2017fd631e7de8004dd"
    "2c5982afa8e00fcda1123e30d11cd03a"
    "33722e4f9002130675ce87c2efb2ad7d"
    "3815e1529f7a6c2f27c4e281a9cf8dc0"
    "d7dfff6076148c5e5509e408c74220fc"
    "d25091d94c629ee8b9a6f91a00210bfa"
    "359c4e4b6948cb0ec8a45bea8407b418"
    "f4ae6bdba7cc3f8b4a0c3c25e5544d45"
    "83ed11f0b05393f27426b59d6d7cf32d"
    "f156247e471b86bd708e1e3b731603b6"
    "ac285ac9b337c50a10b7a3bab1974688"
)

RCSP_TABLE_A = bytes.fromhex(
    "012de293be4515ae780387a4b838cf3f08670994eb26a86bbd18341bbbbf72f7"
    "4035489c512f3b55e3c09fd8d3f38db1ffa73edc8677d7a611fbf4ba92916483"
    "f133efda2cb5b22b88d199cb8c841d14819771ca5fa38b573c82c4525c1ce8a0"
    "04b4854af61354b6df0c1a8edee039fc209b244ea9989eabf260d06ceafac7d9"
    "00d41f6e43bcec5389fe7a5d49c932c2f99af86d16db599644e9cde646428f0a"
    "c1ccb965b0d2c6ac1e4162292e0e7450025ac3257b8a2a5bf0060d476f709d7e"
    "10ce1227d54c4fd679306836757de4ed806a9037a25e76aac57f3dafa5e51961"
    "fd4d7cb70beead4b22f5e7732321c805e166ddb3586963560fa1319517073a28"
)

RCSP_TABLE_B = bytes.fromhex(
    "8000b00960efb9fd10129fe469baadf8c038c2654f0694fc19de6a1b5d4ea882"
    "70ede8ec72b315c3ffabb6474401ac25c9fa8e411a21cbd30d6efe2658da320f"
    "20a99d8498059cbb228c63e7c5e173c6af245b876627f757f496b1b75c8bd554"
    "79dfaaf63ea3f111caf5d1177b9383bcbd521eebaeccd63508c88ab4e2cdbfd9"
    "d050593f4d62340a4888b5564c2e6b9ed23d3c0313fb9751754a917123be762a"
    "5ff9d4550bdc37311674d777a7e607dba42f46f3614567e30ca23b1c8518041d"
    "29a08fb25ad8a67eee8d534ba19ac10e7a49a52c81c4c7362b7f439533f26c68"
    "6df00228cedd9bea5e997c1486cfe542b840782d3ae9641f92907d396fe08930"
)

RCSP_MASK_BITS = {0, 3, 4, 7, 8, 11, 12, 15}
RCSP_OPCODE_DATA = 0x01
RCSP_OPCODE_GET_TARGET_INFO = 0x03
RCSP_OPCODE_CUSTOM = 0xFF


@dataclass(frozen=True)
class RcspFrame:
    raw: bytes
    flags: int
    opcode: int
    param_len: int
    body: bytes

    @property
    def is_command(self) -> bool:
        return bool(self.flags & 0x80)

    @property
    def has_response(self) -> bool:
        return bool(self.flags & 0x40)

    @property
    def sequence(self) -> Optional[int]:
        if not self.body:
            return None
        return self.body[0]

    @property
    def xm_opcode(self) -> Optional[int]:
        if self.opcode != RCSP_OPCODE_DATA or len(self.body) < 2:
            return None
        return self.body[1]

    @property
    def param_data(self) -> bytes:
        if not self.body:
            return b""
        if self.opcode == RCSP_OPCODE_DATA:
            return self.body[2:]
        return self.body[1:]


def _u8(value: int) -> int:
    return value & 0xFF


def _u32(value: int) -> int:
    return value & 0xFFFFFFFF


def _rol8(value: int, bits: int) -> int:
    value &= 0xFF
    return ((value << bits) | (value >> (8 - bits))) & 0xFF


def _mix_mode_a(left: bytes, right: bytes) -> bytearray:
    mixed = bytearray(16)
    for index in range(16):
        if index in RCSP_MASK_BITS:
            mixed[index] = _u8(left[index] ^ right[index])
        else:
            mixed[index] = _u8(left[index] + right[index])
    return mixed


def _mix_mode_b(left: bytes, right: bytes) -> bytearray:
    mixed = bytearray(16)
    for index in range(16):
        if index in RCSP_MASK_BITS:
            mixed[index] = _u8(left[index] + right[index])
        else:
            mixed[index] = _u8(left[index] ^ right[index])
    return mixed


def _linear_mix(block: bytes) -> bytearray:
    w16, w17, w3, w4, w5, w6, w7, w19, w20, w21, w22, w23, w24, w25, w26, w27 = [value & 0xFF for value in block]

    w28 = _u32(w17 + (w16 << 1))
    w16 = _u32(w17 + w16)
    w17 = _u32(w4 + (w3 << 1))
    w3 = _u32(w4 + w3)
    w4 = _u32(w6 + (w5 << 1))
    w5 = _u32(w6 + w5)
    w6 = _u32(w19 + (w7 << 1))
    w7 = _u32(w19 + w7)
    w19 = _u32(w21 + (w20 << 1))
    w20 = _u32(w21 + w20)
    w21 = _u32(w23 + (w22 << 1))
    w22 = _u32(w23 + w22)
    w23 = _u32(w25 + (w24 << 1))
    w24 = _u32(w25 + w24)
    w25 = _u32(w27 + (w26 << 1))
    w26 = _u32(w27 + w26)
    w27 = _u32(w22 + (w19 << 1))
    w19 = _u32(w22 + w19)
    w22 = _u32(w26 + (w23 << 1))
    w23 = _u32(w26 + w23)
    w26 = _u32(w16 + (w17 << 1))
    w16 = _u32(w17 + w16)
    w17 = _u32(w5 + (w6 << 1))
    w5 = _u32(w6 + w5)
    w6 = _u32(w20 + (w21 << 1))
    w20 = _u32(w21 + w20)
    w21 = _u32(w24 + (w25 << 1))
    w24 = _u32(w25 + w24)
    w25 = _u32(w7 + (w28 << 1))
    w7 = _u32(w7 + w28)
    w28 = _u32(w3 + (w4 << 1))
    w3 = _u32(w4 + w3)
    w4 = _u32(w24 + (w6 << 1))
    w6 = _u32(w24 + w6)
    w24 = _u32(w3 + (w25 << 1))
    w3 = _u32(w25 + w3)
    w25 = _u32(w19 + (w22 << 1))
    w19 = _u32(w22 + w19)
    w22 = _u32(w16 + (w17 << 1))
    w16 = _u32(w17 + w16)
    w17 = _u32(w20 + (w21 << 1))
    w20 = _u32(w21 + w20)
    w21 = _u32(w7 + (w28 << 1))
    w7 = _u32(w7 + w28)
    w28 = _u32(w5 + (w27 << 1))
    w5 = _u32(w27 + w5)
    w27 = _u32(w23 + (w26 << 1))
    w23 = _u32(w23 + w26)
    w26 = _u32(w7 + (w17 << 1))
    w17 = _u32(w17 + w7)
    w7 = _u32(w23 + (w28 << 1))
    w23 = _u32(w23 + w28)
    w28 = _u32(w6 + (w24 << 1))
    w6 = _u32(w6 + w24)
    w24 = _u32(w19 + (w22 << 1))
    w19 = _u32(w19 + w22)
    w22 = _u32(w20 + (w21 << 1))
    w20 = _u32(w20 + w21)
    w21 = _u32(w5 + (w27 << 1))
    w5 = _u32(w27 + w5)
    w27 = _u32(w16 + (w4 << 1))
    w16 = _u32(w4 + w16)
    w4 = _u32(w3 + (w25 << 1))
    w3 = _u32(w25 + w3)

    return bytearray(
        [
            w26 & 0xFF,
            w17 & 0xFF,
            w7 & 0xFF,
            w23 & 0xFF,
            w28 & 0xFF,
            w6 & 0xFF,
            w24 & 0xFF,
            w19 & 0xFF,
            w22 & 0xFF,
            w20 & 0xFF,
            w21 & 0xFF,
            w5 & 0xFF,
            w27 & 0xFF,
            w16 & 0xFF,
            w4 & 0xFF,
            w3 & 0xFF,
        ]
    )


def _build_schedule(key: bytes) -> bytearray:
    if len(key) != 16:
        raise ValueError("The RCSP link key must be exactly 16 bytes.")

    context = bytearray(0x110)
    context[:16] = key

    work = bytearray(key)
    checksum = 0
    for value in key:
        checksum ^= value
    work.append(checksum & 0xFF)

    for round_index in range(16):
        for index in range(16):
            work[index] = _rol8(work[index], 3)
        work[16] = _rol8(work[16], 3)

        rotated = work[round_index + 1 : 17] + work[:round_index]
        constants = RCSP_SCHEDULE_TABLE[round_index * 16 : (round_index + 1) * 16][::-1]
        offset = 16 + round_index * 16

        for index in range(16):
            context[offset + index] = _u8(rotated[index] + constants[index])

    return context


def _derive_intermediate_key(link_key: bytes) -> bytes:
    if len(link_key) != 16:
        raise ValueError("The RCSP link key must be exactly 16 bytes.")

    derived = [0] * 16
    derived[0] = _u8(link_key[0] - 0x17)
    derived[1] = _u8(link_key[1] ^ 0xE5)
    derived[2] = _u8(link_key[2] - 0x21)
    derived[3] = _u8(link_key[3] ^ 0xC1)
    derived[4] = _u8(link_key[4] - 0x4D)
    derived[5] = _u8(link_key[5] ^ 0xA7)
    derived[6] = _u8(link_key[6] - 0x6B)
    derived[7] = _u8(link_key[7] ^ 0x83)
    derived[8] = _u8(link_key[8] ^ 0xE9)
    derived[9] = _u8(link_key[9] - 0x1B)
    derived[10] = _u8(link_key[10] ^ 0xDF)
    derived[11] = _u8(link_key[11] - 0x3F)
    derived[12] = _u8(link_key[12] ^ 0xB3)
    derived[13] = _u8(link_key[13] - 0x59)
    derived[14] = _u8(link_key[14] ^ 0x95)
    derived[15] = _u8(link_key[15] - 0x7D)
    return bytes(derived)


def _transform_block(block: bytes, schedule: bytes, special_flag: bool) -> bytes:
    block = bytearray(block)
    original = bytearray(block)

    for round_index in range(8):
        if special_flag and round_index == 2:
            block = _mix_mode_a(block, original)

        offset = round_index * 32
        block = _mix_mode_a(block, schedule[offset : offset + 16])

        for index in (0, 3, 4, 7, 8, 11, 12, 15):
            block[index] = RCSP_TABLE_A[block[index]]
        for index in (1, 2, 5, 6, 9, 10, 13, 14):
            block[index] = RCSP_TABLE_B[block[index]]

        block = _mix_mode_b(block, schedule[offset + 16 : offset + 32])
        block = _linear_mix(block)

    return bytes(_mix_mode_a(block, schedule[0x100:0x110]))


def get_random_auth_data() -> bytes:
    return b"\x00" + os.urandom(16)


def get_auth_data(
    auth_data: bytes,
    link_key: bytes = RCSP_DEFAULT_LINK_KEY,
    auth_seed: bytes = RCSP_DEFAULT_AUTH_SEED,
) -> bytes:
    if len(auth_data) != 17:
        raise ValueError("RCSP auth packets must be exactly 17 bytes long.")
    if len(link_key) != 16:
        raise ValueError("The RCSP link key must be exactly 16 bytes.")
    if len(auth_seed) != 6:
        raise ValueError("The RCSP auth seed must be exactly 6 bytes.")

    seed = (auth_seed * 3)[:16]
    block = bytearray(auth_data[1:])

    schedule = _build_schedule(link_key)
    block = bytearray(_transform_block(block, schedule, special_flag=False))

    for index in range(16):
        block[index] = _u8(seed[index] + (block[index] ^ auth_data[index + 1]))

    schedule = _build_schedule(_derive_intermediate_key(link_key))
    block = _transform_block(block, schedule, special_flag=True)
    return b"\x01" + block


def pack_rcsp_command(
    opcode: int,
    param_data: bytes = b"",
    *,
    need_response: bool = True,
    sequence: int = 0,
    xm_opcode: Optional[int] = None,
) -> bytes:
    body = bytearray([sequence & 0xFF])
    if opcode == RCSP_OPCODE_DATA:
        if xm_opcode is None:
            raise ValueError("Data commands require xm_opcode.")
        body.append(xm_opcode & 0xFF)
    elif xm_opcode is not None:
        raise ValueError("xm_opcode is only valid for data commands.")

    body.extend(param_data)

    flags = 0x80
    if need_response:
        flags |= 0x40

    return (
        RCSP_SYNC_PREFIX
        + bytes([flags, opcode & 0xFF])
        + len(body).to_bytes(2, byteorder="big")
        + bytes(body)
        + bytes([RCSP_SYNC_SUFFIX])
    )


def parse_rcsp_frame(data: bytes) -> Optional[RcspFrame]:
    if len(data) < 8:
        return None
    if not data.startswith(RCSP_SYNC_PREFIX):
        return None
    if data[-1] != RCSP_SYNC_SUFFIX:
        return None

    param_len = int.from_bytes(data[5:7], byteorder="big")
    expected_length = param_len + 8
    if expected_length != len(data):
        return None

    return RcspFrame(
        raw=bytes(data),
        flags=data[3],
        opcode=data[4],
        param_len=param_len,
        body=bytes(data[7:-1]),
    )


class RcspConnection:
    def __init__(
        self,
        address="iledcolor",
        *,
        profile=ILED_COLOR_PROFILE,
        backend="auto",
        scan_timeout=5.0,
        service_uuid=None,
        command_uuid=None,
        data_uuid=None,
        notify_uuid=None,
        auto_auth=True,
        auth_timeout=5.0,
        auth_reset_delay=0.5,
        link_key: bytes = RCSP_DEFAULT_LINK_KEY,
        auth_seed: bytes = RCSP_DEFAULT_AUTH_SEED,
    ):
        self.profile = resolve_profile(profile).with_overrides(
            service_uuids=service_uuid,
            command_uuids=command_uuid,
            data_uuids=data_uuid,
            notify_uuids=notify_uuid,
        )
        self.transport = create_transport(
            address,
            self.profile,
            backend=backend,
            notification_handler=self._on_notification,
            scan_timeout=scan_timeout,
        )

        self.link_key = bytes(link_key)
        self.auth_seed = bytes(auth_seed)
        self.auth_timeout = auth_timeout
        self.auth_reset_delay = auth_reset_delay
        self.authenticated = False
        self._next_sequence_value = 0
        self._pending_notifications = deque()
        self._notification_event = Event()
        self._notification_lock = Lock()

        self._ensure_connection()
        if auto_auth:
            self.authenticate(timeout=auth_timeout)

    def _on_notification(self, data: bytes):
        with self._notification_lock:
            self._pending_notifications.append(bytes(data))
            self._notification_event.set()

    def _push_front(self, values):
        if not values:
            return
        with self._notification_lock:
            for value in reversed(values):
                self._pending_notifications.appendleft(value)
            self._notification_event.set()

    def _pop_notification(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with self._notification_lock:
                if self._pending_notifications:
                    value = self._pending_notifications.popleft()
                    if not self._pending_notifications:
                        self._notification_event.clear()
                    return value
                self._notification_event.clear()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Timeout exceeded waiting for RCSP notification.")
            self._notification_event.wait(remaining)

    def _wait_for_matching(self, predicate, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        held = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timeout exceeded waiting for matching RCSP notification.")
                packet = self._pop_notification(remaining)
                if predicate(packet):
                    return packet
                held.append(packet)
        finally:
            self._push_front(held)

    def _ensure_connection(self):
        if not self.transport.is_connected():
            self.transport.connect()

    def _next_sequence(self) -> int:
        value = self._next_sequence_value & 0xFF
        self._next_sequence_value = (self._next_sequence_value + 1) & 0xFF
        return value

    def send_raw(self, data: bytes):
        self._ensure_connection()
        self.transport.write_command(bytes(data))

    def wait_for_notification(self, timeout: float = 3.0) -> bytes:
        return self._pop_notification(timeout)

    def authenticate(self, timeout: Optional[float] = None) -> bool:
        timeout = self.auth_timeout if timeout is None else timeout
        self._ensure_connection()
        self.authenticated = False

        held = []
        challenge = get_random_auth_data()
        expected = get_auth_data(challenge, link_key=self.link_key, auth_seed=self.auth_seed)

        self.send_raw(RCSP_RESET_AUTH_FLAG)
        time.sleep(self.auth_reset_delay)
        self.send_raw(challenge)

        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timeout exceeded waiting for RCSP authentication.")

                packet = self._pop_notification(remaining)
                if packet == expected:
                    self.send_raw(RCSP_AUTH_OK)
                    continue
                if packet == RCSP_AUTH_OK:
                    self.authenticated = True
                    return True
                if len(packet) == 17 and packet[:1] == b"\x00":
                    self.send_raw(get_auth_data(packet, link_key=self.link_key, auth_seed=self.auth_seed))
                    continue
                held.append(packet)
        finally:
            self._push_front(held)

    def send_rcsp_command(
        self,
        opcode: int,
        param_data: bytes = b"",
        *,
        need_response: bool = True,
        xm_opcode: Optional[int] = None,
        timeout: float = 3.0,
    ) -> Optional[RcspFrame]:
        if not self.authenticated:
            self.authenticate()

        packet = pack_rcsp_command(
            opcode,
            param_data=param_data,
            need_response=need_response,
            sequence=self._next_sequence(),
            xm_opcode=xm_opcode,
        )
        self.send_raw(packet)
        if not need_response:
            return None
        return self.wait_for_frame(timeout=timeout, opcode=opcode)

    def wait_for_frame(self, timeout: float = 3.0, opcode: Optional[int] = None) -> RcspFrame:
        def _matches(value: bytes) -> bool:
            frame = parse_rcsp_frame(value)
            if frame is None:
                return False
            if frame.is_command:
                return False
            if opcode is not None and frame.opcode != opcode:
                return False
            return True

        packet = self._wait_for_matching(_matches, timeout)
        return parse_rcsp_frame(packet)

    def get_target_info(self, mask: int = 0xFFFFFFFF, platform: int = 0, timeout: float = 3.0) -> RcspFrame:
        params = int(mask & 0xFFFFFFFF).to_bytes(4, byteorder="big") + bytes([platform & 0xFF])
        return self.send_rcsp_command(
            RCSP_OPCODE_GET_TARGET_INFO,
            params,
            need_response=True,
            timeout=timeout,
        )

    def send_custom_command(self, data: bytes, *, need_response: bool = True, timeout: float = 3.0) -> Optional[RcspFrame]:
        return self.send_rcsp_command(
            RCSP_OPCODE_CUSTOM,
            data,
            need_response=need_response,
            timeout=timeout,
        )

    def send_data_command(
        self,
        xm_opcode: int,
        data: bytes = b"",
        *,
        need_response: bool = True,
        timeout: float = 3.0,
    ) -> Optional[RcspFrame]:
        return self.send_rcsp_command(
            RCSP_OPCODE_DATA,
            data,
            need_response=need_response,
            xm_opcode=xm_opcode,
            timeout=timeout,
        )

    def disconnect(self):
        self.transport.disconnect()
        self.authenticated = False


class IledColorRcspConnection(RcspConnection):
    def __init__(self, address="iledcolor", **kwargs):
        kwargs.setdefault("profile", ILED_COLOR_PROFILE)
        super().__init__(address, **kwargs)


ILEDColorRcspConnection = IledColorRcspConnection
