import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Optional, Sequence, Tuple

from .ble import (
    ILED_COLOR_AE_COMMAND_UUID,
    ILED_COLOR_AE_NOTIFY_UUID,
    ILED_COLOR_COMBINED_PROFILE,
    create_transport,
    resolve_profile,
)
from .rcsp import (
    RCSP_AUTH_OK,
    RCSP_DEFAULT_AUTH_SEED,
    RCSP_DEFAULT_LINK_KEY,
    RCSP_RESET_AUTH_FLAG,
    RcspFrame,
    get_auth_data,
    get_random_auth_data,
    pack_rcsp_command,
    parse_rcsp_frame,
)


PACKET_TYPE_54 = 0x54
PACKET_INDEX_CONTINUE = 0x00
PACKET_INDEX_END = 0x01
PACKET_INDEX_START = 0x06
PACKET_INDEX_ENABLE = 0x0A
PACKET_INDEX_CONNECT = 0x0D
PACKET_INDEX_TEST_PASS = 0x0F
DEFAULT_TEST_PASSWORD = b"\x00" * 6
DEFAULT_STREAM_PREFIX = b"\x01" + (b"\x00" * 19)
DEFAULT_MANUFACTURER_ID = 0x5401
GIF_HEADER_SIGNATURES = (b"GIF87a", b"GIF89a")


def short_to_bytes(value: int) -> bytes:
    return int(value & 0xFFFF).to_bytes(2, byteorder="big")


def int_to_bytes(value: int) -> bytes:
    return int(value & 0xFFFFFFFF).to_bytes(4, byteorder="big")


def sum16(value: bytes) -> int:
    return sum(value) & 0xFFFF


def sum16_bytes(value: bytes) -> bytes:
    return short_to_bytes(sum16(value))


def _build_crc32c_table() -> Tuple[int, ...]:
    table = []
    polynomial = 0x82F63B78
    for index in range(256):
        crc = index
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ polynomial
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFF)
    return tuple(table)


_CRC32C_TABLE = _build_crc32c_table()


def crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for value in data:
        crc = _CRC32C_TABLE[(crc ^ value) & 0xFF] ^ (crc >> 8)
    return (~crc) & 0xFFFFFFFF


def crc32c_bytes(data: bytes) -> bytes:
    return int_to_bytes(crc32c(data))


def recommended_chunk_size(mtu: int) -> int:
    mtu = int(mtu)
    if mtu <= 0:
        raise ValueError("MTU must be a positive integer.")
    return max(1, mtu - 28)


def _coerce_rgb_color(color) -> Tuple[int, int, int]:
    if isinstance(color, int):
        if not 0 <= color <= 0xFFFFFF:
            raise ValueError("Integer colours must fit in 24 bits as 0xRRGGBB.")
        return ((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF)

    try:
        red, green, blue = color
    except (TypeError, ValueError):
        raise ValueError("Colours must be 24-bit integers or 3-item RGB iterables.")

    values = []
    for channel_name, channel in (("red", red), ("green", green), ("blue", blue)):
        channel = int(channel)
        if not 0 <= channel <= 0xFF:
            raise ValueError(f"{channel_name} must be between 0 and 255.")
        values.append(channel)
    return tuple(values)


def _gamma_correct_channel(value: int) -> int:
    normalized = int(value) / 255.0
    corrected = round((normalized ** 2.0) * 255.0)
    return max(0, min(255, corrected))


@dataclass(frozen=True)
class IledColorAdvertisementInfo:
    manufacturer_id: Optional[int]
    raw_data: bytes
    blebean_bytes: bytes
    width: int
    height: int
    screen_color_type: int
    fun_code: int
    supports_time: bool
    supports_gif: bool


@dataclass(frozen=True)
class DirectStreamNotification:
    raw: bytes
    packet_type: int
    packet_index: int
    declared_length: int
    payload: bytes
    checksum: Optional[int]
    checksum_valid: Optional[bool]
    framing: str


@dataclass(frozen=True)
class DirectStreamPlan:
    image_bytes: bytes
    stream_payload: bytes
    connect_packet: bytes
    test_pass_packet: bytes
    enable_packet: Optional[bytes]
    start_packet: bytes
    continue_packets: Tuple[bytes, ...]
    end_packet: bytes
    mtu: Optional[int] = None
    chunk_size: Optional[int] = None


@dataclass(frozen=True)
class DirectStreamSendResult:
    plan: DirectStreamPlan
    connect_notification: Optional[DirectStreamNotification]
    test_pass_notification: Optional[DirectStreamNotification]
    enable_notification: Optional[DirectStreamNotification]
    start_notification: Optional[DirectStreamNotification]
    continue_notifications: Tuple[Optional[DirectStreamNotification], ...]
    end_notification: Optional[DirectStreamNotification]


def build_blebean_bytes(
    manufacturer_data: bytes,
    *,
    manufacturer_id: Optional[int] = DEFAULT_MANUFACTURER_ID,
) -> bytes:
    manufacturer_data = bytes(manufacturer_data)
    if manufacturer_id is None:
        return manufacturer_data

    manufacturer_prefix = int(manufacturer_id & 0xFFFF).to_bytes(2, byteorder="little")
    if manufacturer_data.startswith(manufacturer_prefix):
        return manufacturer_data
    return manufacturer_prefix + manufacturer_data


def decode_iledcolor_advertisement(
    manufacturer_data: bytes,
    *,
    manufacturer_id: Optional[int] = DEFAULT_MANUFACTURER_ID,
) -> IledColorAdvertisementInfo:
    blebean_bytes = build_blebean_bytes(manufacturer_data, manufacturer_id=manufacturer_id)
    if len(blebean_bytes) < 9:
        raise ValueError("Need at least 9 bytes to decode an iLEDColor advertisement payload.")
    screen_color_type = blebean_bytes[9] if len(blebean_bytes) > 9 else 3
    fun_code = ((blebean_bytes[15] << 8) | blebean_bytes[16]) if len(blebean_bytes) > 16 else 0
    return IledColorAdvertisementInfo(
        manufacturer_id=manufacturer_id,
        raw_data=bytes(manufacturer_data),
        blebean_bytes=blebean_bytes,
        width=(blebean_bytes[7] << 8) | blebean_bytes[8],
        height=(blebean_bytes[5] << 8) | blebean_bytes[6],
        screen_color_type=screen_color_type,
        fun_code=fun_code,
        supports_time=bool(fun_code & 0x0001),
        supports_gif=bool(fun_code & 0x0004),
    )


def parse_direct_notification(data: bytes) -> Optional[DirectStreamNotification]:
    data = bytes(data)
    if len(data) < 4:
        return None

    packet_type = data[0]
    packet_index = data[1]
    declared_length = int.from_bytes(data[2:4], byteorder="big")

    if declared_length >= 2 and len(data) == declared_length + 4:
        payload = data[4:-2]
        checksum = int.from_bytes(data[-2:], byteorder="big")
        return DirectStreamNotification(
            raw=data,
            packet_type=packet_type,
            packet_index=packet_index,
            declared_length=declared_length,
            payload=payload,
            checksum=checksum,
            checksum_valid=sum16(data[:-2]) == checksum,
            framing="payload_plus_checksum",
        )

    if len(data) >= 6 and len(data) == declared_length + 6:
        payload = data[4:-2]
        checksum = int.from_bytes(data[-2:], byteorder="big")
        return DirectStreamNotification(
            raw=data,
            packet_type=packet_type,
            packet_index=packet_index,
            declared_length=declared_length,
            payload=payload,
            checksum=checksum,
            checksum_valid=sum16(data[:-2]) == checksum,
            framing="payload_only",
        )

    return DirectStreamNotification(
        raw=data,
        packet_type=packet_type,
        packet_index=packet_index,
        declared_length=declared_length,
        payload=data[4:],
        checksum=None,
        checksum_valid=None,
        framing="raw",
    )


def build_direct_stream_packet(
    packet_index: int,
    payload: bytes = b"",
    *,
    packet_type: int = PACKET_TYPE_54,
    sequence: Optional[int] = None,
    data_length: Optional[int] = None,
) -> bytes:
    payload = bytes(payload)
    body = bytearray()
    if sequence is not None:
        body.extend(int_to_bytes(sequence))
    if data_length is not None:
        body.extend(short_to_bytes(data_length))
    body.extend(payload)
    framed = bytes([packet_type & 0xFF, packet_index & 0xFF]) + short_to_bytes(len(body) + 2) + bytes(body)
    return framed + sum16_bytes(framed)


def build_direct_stream_connect_packet() -> bytes:
    return build_direct_stream_packet(PACKET_INDEX_CONNECT, b"\x00")


def build_direct_stream_test_pass_packet(password: bytes = DEFAULT_TEST_PASSWORD) -> bytes:
    password = bytes(password)
    if len(password) != 6:
        raise ValueError("Direct-stream test-pass passwords must be exactly 6 bytes.")
    return build_direct_stream_packet(PACKET_INDEX_TEST_PASS, password)


def build_direct_stream_enable_packet(enabled: bool = True) -> bytes:
    return build_direct_stream_packet(
        PACKET_INDEX_ENABLE,
        bytes([0x01 if enabled else 0x00]) + (b"\x00" * 8),
    )


def build_direct_stream_image_bytes(
    pixels: Sequence[Sequence[Sequence[int]]],
    *,
    gamma_correct: bool = False,
) -> bytes:
    rows = tuple(tuple(row) for row in pixels)
    if not rows:
        raise ValueError("pixels must contain at least one row.")
    width = len(rows[0])
    if width == 0:
        raise ValueError("pixels rows must not be empty.")
    if any(len(row) != width for row in rows):
        raise ValueError("All pixel rows must have the same width.")

    encoded = bytearray()
    for row in rows:
        for color in row:
            red, green, blue = _coerce_rgb_color(color)
            if gamma_correct:
                red = _gamma_correct_channel(red)
                green = _gamma_correct_channel(green)
                blue = _gamma_correct_channel(blue)
            encoded.extend((red, green, blue))

    metadata_words = (
        0x0000,
        0x0000,
        width & 0xFFFF,
        len(rows) & 0xFFFF,
        0x0000,
        0x0001,
        0x0001,
        0x0001,
        0x0032,
        0x0064,
        0x0000,
    )
    return b"".join(short_to_bytes(word) for word in metadata_words) + bytes(encoded)


def parse_gif_logical_screen_size(gif_bytes: bytes) -> Tuple[int, int]:
    gif_bytes = bytes(gif_bytes)
    if len(gif_bytes) < 10:
        raise ValueError("GIF data must be at least 10 bytes long.")
    if not gif_bytes.startswith(GIF_HEADER_SIGNATURES):
        raise ValueError("GIF data must start with GIF87a or GIF89a.")

    width = int.from_bytes(gif_bytes[6:8], byteorder="little")
    height = int.from_bytes(gif_bytes[8:10], byteorder="little")
    if width <= 0 or height <= 0:
        raise ValueError("GIF logical screen size must be positive.")
    return width, height


def build_direct_stream_gif_bytes(
    gif_bytes: bytes,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> bytes:
    """
    Build a direct-stream GIF payload using the metadata shape inferred from
    the external Rust reference and validated on real hardware.

    The original GIF file bytes are kept intact and prefixed with GIF-specific
    metadata before being sent through the same A95x transport used by RGB888
    direct-stream uploads.
    """

    gif_bytes = bytes(gif_bytes)
    parsed_width, parsed_height = parse_gif_logical_screen_size(gif_bytes)
    width = parsed_width if width is None else int(width)
    height = parsed_height if height is None else int(height)
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive integers.")

    metadata_words = (
        0x0000,
        0x0000,
        width & 0xFFFF,
        height & 0xFFFF,
        0x0000,
        0x0006,
        0x0001,
        0x0064,
        0x0400,
        0x0064,
        0x0000,
    )
    return b"".join(short_to_bytes(word) for word in metadata_words) + gif_bytes


def build_direct_stream_gif_file_bytes(
    path,
    *,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> bytes:
    gif_path = Path(path)
    return build_direct_stream_gif_bytes(
        gif_path.read_bytes(),
        width=width,
        height=height,
    )


def build_direct_stream_solid_image_bytes(
    color,
    *,
    width: int = 32,
    height: int = 16,
    gamma_correct: bool = False,
) -> bytes:
    pixel = _coerce_rgb_color(color)
    pixels = tuple(tuple(pixel for _ in range(width)) for _ in range(height))
    return build_direct_stream_image_bytes(
        pixels,
        gamma_correct=gamma_correct,
    )


def wrap_direct_stream_payload(image_bytes: bytes) -> bytes:
    image_bytes = bytes(image_bytes)
    return crc32c_bytes(image_bytes) + DEFAULT_STREAM_PREFIX + image_bytes


def build_direct_stream_start_packet(image_bytes: bytes) -> bytes:
    image_bytes = bytes(image_bytes)
    stream_payload = wrap_direct_stream_payload(image_bytes)
    if len(stream_payload) > 0xFFFF:
        raise ValueError("Direct-stream payload exceeds the 16-bit length field used by StartStream.")
    payload = (
        crc32c_bytes(image_bytes)
        + b"\x00\x00"
        + short_to_bytes(len(stream_payload))
        + b"\x00\x00\x00"
    )
    return build_direct_stream_packet(PACKET_INDEX_START, payload)


def build_direct_stream_continue_packets(
    stream_payload: bytes,
    *,
    mtu: Optional[int] = None,
    chunk_size: Optional[int] = None,
) -> Tuple[bytes, ...]:
    stream_payload = bytes(stream_payload)
    if chunk_size is None:
        if mtu is None:
            raise ValueError("Provide either mtu or chunk_size when building direct-stream packets.")
        chunk_size = recommended_chunk_size(mtu)
    chunk_size = int(chunk_size)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be a positive integer.")

    packets = []
    for sequence, start in enumerate(range(0, len(stream_payload), chunk_size)):
        chunk = stream_payload[start : start + chunk_size]
        packets.append(
            build_direct_stream_packet(
                PACKET_INDEX_CONTINUE,
                chunk,
                sequence=sequence,
                data_length=len(chunk),
            )
        )
    return tuple(packets)


def build_direct_stream_end_packet() -> bytes:
    return build_direct_stream_packet(PACKET_INDEX_END, b"\x01")


class IledColorController:
    def __init__(
        self,
        address: str = "iledcolor",
        *,
        profile=ILED_COLOR_COMBINED_PROFILE,
        backend: str = "auto",
        scan_timeout: float = 5.0,
        service_uuid=None,
        command_uuid=None,
        data_uuid=None,
        notify_uuid=None,
        auto_auth: bool = True,
        auth_timeout: float = 5.0,
        auth_reset_delay: float = 0.5,
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
        self._next_rcsp_sequence_value = 0

        self.mtu = 23
        self.max_data_chunk_size = 20

        self._ae_notifications = deque()
        self._ae_event = Event()
        self._ae_lock = Lock()

        self._direct_notifications = deque()
        self._direct_event = Event()
        self._direct_lock = Lock()

        self._ensure_connection()
        if auto_auth:
            self.authenticate()

    def _on_notification(self, data: bytes):
        if data.startswith(b"\xfe\xdc\xba") or data == RCSP_AUTH_OK or (len(data) == 17 and data[:1] in (b"\x00", b"\x01")):
            with self._ae_lock:
                self._ae_notifications.append(bytes(data))
                self._ae_event.set()
            return

        with self._direct_lock:
            self._direct_notifications.append(bytes(data))
            self._direct_event.set()

    def _sync_transport_state(self):
        self.mtu = getattr(self.transport, "mtu", self.mtu)
        self.max_data_chunk_size = getattr(self.transport, "max_data_chunk_size", self.max_data_chunk_size)

    def _ensure_connection(self):
        if not self.transport.is_connected():
            self.transport.connect()
        self._sync_transport_state()

    def _push_front(self, queue, event, lock, values):
        if not values:
            return
        with lock:
            for value in reversed(values):
                queue.appendleft(value)
            event.set()

    def _pop_notification(self, queue, event, lock, timeout: float, label: str) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            with lock:
                if queue:
                    value = queue.popleft()
                    if not queue:
                        event.clear()
                    return value
                event.clear()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timeout exceeded waiting for {label} notification.")
            event.wait(remaining)

    def _wait_for_matching(self, queue, event, lock, predicate, timeout: float, label: str) -> bytes:
        deadline = time.monotonic() + timeout
        held = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Timeout exceeded waiting for matching {label} notification.")
                packet = self._pop_notification(queue, event, lock, remaining, label)
                if predicate(packet):
                    return packet
                held.append(packet)
        finally:
            self._push_front(queue, event, lock, held)

    def _next_rcsp_sequence(self) -> int:
        value = self._next_rcsp_sequence_value & 0xFF
        self._next_rcsp_sequence_value = (self._next_rcsp_sequence_value + 1) & 0xFF
        return value

    def wait_for_ae_notification(self, timeout: float = 3.0) -> bytes:
        return self._pop_notification(self._ae_notifications, self._ae_event, self._ae_lock, timeout, "AE")

    def wait_for_direct_notification(self, timeout: float = 3.0) -> bytes:
        return self._pop_notification(
            self._direct_notifications,
            self._direct_event,
            self._direct_lock,
            timeout,
            "direct-stream",
        )

    def wait_for_direct_parsed_notification(self, timeout: float = 3.0) -> Optional[DirectStreamNotification]:
        return parse_direct_notification(self.wait_for_direct_notification(timeout))

    def drain_ae_notifications(self) -> Tuple[bytes, ...]:
        with self._ae_lock:
            values = tuple(self._ae_notifications)
            self._ae_notifications.clear()
            self._ae_event.clear()
            return values

    def drain_direct_notifications(self) -> Tuple[bytes, ...]:
        with self._direct_lock:
            values = tuple(self._direct_notifications)
            self._direct_notifications.clear()
            self._direct_event.clear()
            return values

    def send_ae_packet(self, packet: bytes) -> bytes:
        packet = bytes(packet)
        self._ensure_connection()
        if not hasattr(self.transport, "start_notify_uuid") or not hasattr(self.transport, "write_uuid"):
            raise NotImplementedError("AE/RCSP control currently requires the Bleak transport.")
        self.transport.start_notify_uuid(ILED_COLOR_AE_NOTIFY_UUID)
        self.transport.write_uuid(ILED_COLOR_AE_COMMAND_UUID, packet)
        return packet

    def send_command_packet(self, packet: bytes) -> bytes:
        packet = bytes(packet)
        self._ensure_connection()
        self.transport.write_command(packet)
        return packet

    def send_data_packet(self, packet: bytes) -> bytes:
        packet = bytes(packet)
        self._ensure_connection()
        self.transport.write_data(packet)
        return packet

    def authenticate(self, timeout: Optional[float] = None, reset_delay: Optional[float] = None) -> bool:
        timeout = self.auth_timeout if timeout is None else timeout
        reset_delay = self.auth_reset_delay if reset_delay is None else reset_delay
        self._ensure_connection()
        self.authenticated = False

        challenge = get_random_auth_data()
        expected = get_auth_data(challenge, link_key=self.link_key, auth_seed=self.auth_seed)

        self.send_ae_packet(RCSP_RESET_AUTH_FLAG)
        time.sleep(reset_delay)
        self.send_ae_packet(challenge)

        deadline = time.monotonic() + timeout
        held = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timeout exceeded waiting for RCSP authentication.")

                packet = self._pop_notification(self._ae_notifications, self._ae_event, self._ae_lock, remaining, "AE")
                if packet == expected:
                    self.send_ae_packet(RCSP_AUTH_OK)
                    continue
                if packet == RCSP_AUTH_OK:
                    self.authenticated = True
                    return True
                if len(packet) == 17 and packet[:1] == b"\x00":
                    self.send_ae_packet(get_auth_data(packet, link_key=self.link_key, auth_seed=self.auth_seed))
                    continue
                held.append(packet)
        finally:
            self._push_front(self._ae_notifications, self._ae_event, self._ae_lock, held)

    def send_rcsp_command(
        self,
        opcode: int,
        param_data: bytes = b"",
        *,
        need_response: bool = True,
        timeout: float = 3.0,
    ) -> Optional[RcspFrame]:
        if not self.authenticated:
            self.authenticate()

        packet = pack_rcsp_command(
            opcode,
            param_data=param_data,
            need_response=need_response,
            sequence=self._next_rcsp_sequence(),
        )
        self.send_ae_packet(packet)
        if not need_response:
            return None

        response = self._wait_for_matching(
            self._ae_notifications,
            self._ae_event,
            self._ae_lock,
            lambda value: (frame := parse_rcsp_frame(value)) is not None and not frame.is_command and frame.opcode == opcode,
            timeout,
            "AE",
        )
        return parse_rcsp_frame(response)

    def get_target_info(self, mask: int = 0xFFFFFFFF, platform: int = 0, timeout: float = 3.0) -> RcspFrame:
        params = int(mask & 0xFFFFFFFF).to_bytes(4, byteorder="big") + bytes([platform & 0xFF])
        return self.send_rcsp_command(0x03, params, need_response=True, timeout=timeout)

    def build_direct_stream_plan(
        self,
        image_bytes: bytes,
        *,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
    ) -> DirectStreamPlan:
        image_bytes = bytes(image_bytes)
        stream_payload = wrap_direct_stream_payload(image_bytes)
        return DirectStreamPlan(
            image_bytes=image_bytes,
            stream_payload=stream_payload,
            connect_packet=build_direct_stream_connect_packet(),
            test_pass_packet=build_direct_stream_test_pass_packet(password),
            enable_packet=build_direct_stream_enable_packet(True) if enable_display else None,
            start_packet=build_direct_stream_start_packet(image_bytes),
            continue_packets=build_direct_stream_continue_packets(
                stream_payload,
                mtu=self.mtu,
                chunk_size=chunk_size,
            ),
            end_packet=build_direct_stream_end_packet(),
            mtu=self.mtu,
            chunk_size=chunk_size,
        )

    def send_direct_stream_plan(
        self,
        plan: DirectStreamPlan,
        *,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        if clear_pending:
            self.drain_direct_notifications()

        def _send_and_wait(send_fn, packet, timeout):
            send_fn(packet)
            try:
                return self.wait_for_direct_parsed_notification(timeout=timeout)
            except TimeoutError:
                return None

        connect_notification = _send_and_wait(self.send_command_packet, plan.connect_packet, connect_timeout)
        test_pass_notification = _send_and_wait(self.send_command_packet, plan.test_pass_packet, test_pass_timeout)
        enable_notification = None
        if plan.enable_packet is not None:
            enable_notification = _send_and_wait(self.send_command_packet, plan.enable_packet, enable_timeout)

        start_notification = _send_and_wait(self.send_command_packet, plan.start_packet, start_timeout)

        continue_notifications = []
        for packet in plan.continue_packets:
            continue_notifications.append(_send_and_wait(self.send_data_packet, packet, chunk_timeout))
            if inter_packet_delay > 0:
                time.sleep(inter_packet_delay)

        end_notification = _send_and_wait(self.send_data_packet, plan.end_packet, end_timeout)

        return DirectStreamSendResult(
            plan=plan,
            connect_notification=connect_notification,
            test_pass_notification=test_pass_notification,
            enable_notification=enable_notification,
            start_notification=start_notification,
            continue_notifications=tuple(continue_notifications),
            end_notification=end_notification,
        )

    def send_direct_image(
        self,
        image_bytes: bytes,
        *,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        return self.send_direct_stream_plan(
            self.build_direct_stream_plan(
                image_bytes,
                chunk_size=chunk_size,
                password=password,
                enable_display=enable_display,
            ),
            connect_timeout=connect_timeout,
            test_pass_timeout=test_pass_timeout,
            enable_timeout=enable_timeout,
            start_timeout=start_timeout,
            chunk_timeout=chunk_timeout,
            end_timeout=end_timeout,
            inter_packet_delay=inter_packet_delay,
            clear_pending=clear_pending,
        )

    def send_direct_solid_color(
        self,
        color,
        *,
        width: int = 32,
        height: int = 16,
        gamma_correct: bool = False,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        return self.send_direct_image(
            build_direct_stream_solid_image_bytes(
                color,
                width=width,
                height=height,
                gamma_correct=gamma_correct,
            ),
            chunk_size=chunk_size,
            password=password,
            enable_display=enable_display,
            connect_timeout=connect_timeout,
            test_pass_timeout=test_pass_timeout,
            enable_timeout=enable_timeout,
            start_timeout=start_timeout,
            chunk_timeout=chunk_timeout,
            end_timeout=end_timeout,
            inter_packet_delay=inter_packet_delay,
            clear_pending=clear_pending,
        )

    def send_gif(
        self,
        gif_bytes: bytes,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        return self.send_direct_image(
            build_direct_stream_gif_bytes(
                gif_bytes,
                width=width,
                height=height,
            ),
            chunk_size=chunk_size,
            password=password,
            enable_display=enable_display,
            connect_timeout=connect_timeout,
            test_pass_timeout=test_pass_timeout,
            enable_timeout=enable_timeout,
            start_timeout=start_timeout,
            chunk_timeout=chunk_timeout,
            end_timeout=end_timeout,
            inter_packet_delay=inter_packet_delay,
            clear_pending=clear_pending,
        )

    def send_gif_file(
        self,
        path,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        return self.send_gif(
            Path(path).read_bytes(),
            width=width,
            height=height,
            chunk_size=chunk_size,
            password=password,
            enable_display=enable_display,
            connect_timeout=connect_timeout,
            test_pass_timeout=test_pass_timeout,
            enable_timeout=enable_timeout,
            start_timeout=start_timeout,
            chunk_timeout=chunk_timeout,
            end_timeout=end_timeout,
            inter_packet_delay=inter_packet_delay,
            clear_pending=clear_pending,
        )

    def send_experimental_gif(
        self,
        gif_bytes: bytes,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        """
        Backwards-compatible alias for send_gif().
        """
        return self.send_gif(
            gif_bytes,
            width=width,
            height=height,
            chunk_size=chunk_size,
            password=password,
            enable_display=enable_display,
            connect_timeout=connect_timeout,
            test_pass_timeout=test_pass_timeout,
            enable_timeout=enable_timeout,
            start_timeout=start_timeout,
            chunk_timeout=chunk_timeout,
            end_timeout=end_timeout,
            inter_packet_delay=inter_packet_delay,
            clear_pending=clear_pending,
        )

    def send_experimental_gif_file(
        self,
        path,
        *,
        width: Optional[int] = None,
        height: Optional[int] = None,
        chunk_size: Optional[int] = None,
        password: bytes = DEFAULT_TEST_PASSWORD,
        enable_display: bool = False,
        connect_timeout: float = 1.0,
        test_pass_timeout: float = 1.0,
        enable_timeout: float = 1.0,
        start_timeout: float = 1.0,
        chunk_timeout: float = 1.0,
        end_timeout: float = 1.0,
        inter_packet_delay: float = 0.02,
        clear_pending: bool = True,
    ) -> DirectStreamSendResult:
        """
        Backwards-compatible alias for send_gif_file().
        """
        return self.send_gif_file(
            path,
            width=width,
            height=height,
            chunk_size=chunk_size,
            password=password,
            enable_display=enable_display,
            connect_timeout=connect_timeout,
            test_pass_timeout=test_pass_timeout,
            enable_timeout=enable_timeout,
            start_timeout=start_timeout,
            chunk_timeout=chunk_timeout,
            end_timeout=end_timeout,
            inter_packet_delay=inter_packet_delay,
            clear_pending=clear_pending,
        )

    def disconnect(self):
        self.transport.disconnect()
        self.authenticated = False
