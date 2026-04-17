import asyncio
import platform
import re
import time
from dataclasses import dataclass
from threading import Thread
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

try:
    from gattlib import GATTRequester
except ImportError:
    GATTRequester = None

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    BleakClient = None
    BleakScanner = None


NotificationHandler = Callable[[bytes], None]

_MAC_ADDRESS_RE = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
_UUID_ADDRESS_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _coerce_uuid(value: str) -> str:
    value = value.strip().lower()
    if len(value) == 4 and all(char in "0123456789abcdef" for char in value):
        return f"0000{value}-0000-1000-8000-00805f9b34fb"
    return value


def _coerce_uuid_tuple(values: Optional[Iterable[str]]) -> Tuple[str, ...]:
    if values is None:
        return tuple()
    if isinstance(values, str):
        return (_coerce_uuid(values),)
    return tuple(_coerce_uuid(value) for value in values)


def looks_like_device_address(identifier: str) -> bool:
    return bool(_MAC_ADDRESS_RE.fullmatch(identifier) or _UUID_ADDRESS_RE.fullmatch(identifier))


@dataclass(frozen=True)
class GattProfile:
    name: str
    service_uuids: Tuple[str, ...]
    command_uuids: Tuple[str, ...]
    data_uuids: Tuple[str, ...]
    notify_uuids: Tuple[str, ...] = tuple()
    preferred_backend: str = "auto"
    strict_init: bool = True

    def with_overrides(
        self,
        service_uuids: Optional[Iterable[str]] = None,
        command_uuids: Optional[Iterable[str]] = None,
        data_uuids: Optional[Iterable[str]] = None,
        notify_uuids: Optional[Iterable[str]] = None,
    ) -> "GattProfile":
        return GattProfile(
            name=self.name,
            service_uuids=self.service_uuids if service_uuids is None else _coerce_uuid_tuple(service_uuids),
            command_uuids=self.command_uuids if command_uuids is None else _coerce_uuid_tuple(command_uuids),
            data_uuids=self.data_uuids if data_uuids is None else _coerce_uuid_tuple(data_uuids),
            notify_uuids=self.notify_uuids if notify_uuids is None else _coerce_uuid_tuple(notify_uuids),
            preferred_backend=self.preferred_backend,
            strict_init=self.strict_init,
        )


SPOTLED_PROFILE = GattProfile(
    name="spotled",
    service_uuids=("0000ff20-0000-1000-8000-00805f9b34fb",),
    command_uuids=("0000ff21-0000-1000-8000-00805f9b34fb",),
    data_uuids=("0000ff22-0000-1000-8000-00805f9b34fb",),
    notify_uuids=("0000ff21-0000-1000-8000-00805f9b34fb",),
    preferred_backend="gattlib",
    strict_init=True,
)

ILED_COLOR_AE_SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
ILED_COLOR_AE_COMMAND_UUID = "0000ae01-0000-1000-8000-00805f9b34fb"
ILED_COLOR_AE_NOTIFY_UUID = "0000ae02-0000-1000-8000-00805f9b34fb"

ILED_COLOR_PROGRAM_SERVICE_UUID = "0000a950-0000-1000-8000-00805f9b34fb"
ILED_COLOR_PROGRAM_COMMAND_UUID = "0000a951-0000-1000-8000-00805f9b34fb"
ILED_COLOR_PROGRAM_DATA_UUID = "0000a952-0000-1000-8000-00805f9b34fb"
ILED_COLOR_PROGRAM_NOTIFY_UUID = "0000a953-0000-1000-8000-00805f9b34fb"

ILED_COLOR_PROFILE = GattProfile(
    name="iledcolor",
    service_uuids=(ILED_COLOR_AE_SERVICE_UUID,),
    command_uuids=(ILED_COLOR_AE_COMMAND_UUID,),
    data_uuids=(ILED_COLOR_AE_COMMAND_UUID,),
    notify_uuids=(ILED_COLOR_AE_NOTIFY_UUID,),
    preferred_backend="bleak",
    strict_init=False,
)

ILED_COLOR_PROGRAM_PROFILE = GattProfile(
    name="iledcolor-program",
    service_uuids=(ILED_COLOR_PROGRAM_SERVICE_UUID,),
    command_uuids=(ILED_COLOR_PROGRAM_COMMAND_UUID,),
    data_uuids=(ILED_COLOR_PROGRAM_DATA_UUID,),
    notify_uuids=(ILED_COLOR_PROGRAM_NOTIFY_UUID,),
    preferred_backend="bleak",
    strict_init=False,
)

ILED_COLOR_COMBINED_PROFILE = GattProfile(
    name="iledcolor-combined",
    service_uuids=(ILED_COLOR_PROGRAM_SERVICE_UUID, ILED_COLOR_AE_SERVICE_UUID),
    command_uuids=(ILED_COLOR_PROGRAM_COMMAND_UUID,),
    data_uuids=(ILED_COLOR_PROGRAM_DATA_UUID,),
    notify_uuids=(ILED_COLOR_PROGRAM_NOTIFY_UUID,),
    preferred_backend="bleak",
    strict_init=False,
)

ILED_COLOR_BUSINESS_PROFILE = ILED_COLOR_PROGRAM_PROFILE


PROFILES = {
    "spotled": SPOTLED_PROFILE,
    "spot-led": SPOTLED_PROFILE,
    "iledcolor": ILED_COLOR_PROFILE,
    "i-ledcolor": ILED_COLOR_PROFILE,
    "iled_color": ILED_COLOR_PROFILE,
    "iledcolor-program": ILED_COLOR_PROGRAM_PROFILE,
    "i-ledcolor-program": ILED_COLOR_PROGRAM_PROFILE,
    "iled_color_program": ILED_COLOR_PROGRAM_PROFILE,
    "iledcolor-combined": ILED_COLOR_COMBINED_PROFILE,
    "i-ledcolor-combined": ILED_COLOR_COMBINED_PROFILE,
    "iled_color_combined": ILED_COLOR_COMBINED_PROFILE,
    "iledcolor-business": ILED_COLOR_PROGRAM_PROFILE,
    "i-ledcolor-business": ILED_COLOR_PROGRAM_PROFILE,
    "iled_color_business": ILED_COLOR_PROGRAM_PROFILE,
}


def resolve_profile(profile) -> GattProfile:
    if isinstance(profile, GattProfile):
        return profile
    try:
        return PROFILES[str(profile).strip().lower()]
    except KeyError:
        raise ValueError(f"Unknown BLE profile: {profile}")


def _char_properties(characteristic) -> set:
    return {value.lower() for value in getattr(characteristic, "properties", [])}


def _char_supports(characteristic, properties: Sequence[str]) -> bool:
    available = _char_properties(characteristic)
    return any(value in available for value in properties)


def _run_coroutine_sync(coro):
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is None:
        return asyncio.run(coro)

    result = {}
    error = {}

    def _worker():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result["value"] = loop.run_until_complete(coro)
        except Exception as exc:
            error["value"] = exc
        finally:
            loop.close()

    thread = Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


async def _discover_bleak_devices_with_advertisement(scan_timeout: float):
    if BleakScanner is None:
        raise ImportError("bleak is required to scan for Bluetooth devices.")

    try:
        discoveries = await BleakScanner.discover(timeout=scan_timeout, return_adv=True)
    except TypeError:
        devices = await BleakScanner.discover(timeout=scan_timeout)
        return tuple((device, None) for device in devices)

    return tuple(discoveries.values())


def _iter_device_names(device, advertisement=None):
    seen = set()
    names = [getattr(device, "name", None)]
    if advertisement is not None:
        names.append(getattr(advertisement, "local_name", None))

    metadata = getattr(device, "metadata", {}) or {}
    names.append(metadata.get("local_name"))

    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        yield name


def _format_manufacturer_data(manufacturer_data) -> dict:
    return {
        f"0x{int(company_id) & 0xFFFF:04x}": bytes(payload).hex()
        for company_id, payload in (manufacturer_data or {}).items()
    }


def _format_service_data(service_data) -> dict:
    return {str(uuid): bytes(payload).hex() for uuid, payload in (service_data or {}).items()}


async def _resolve_ble_target(identifier: str, scan_timeout: float):
    if looks_like_device_address(identifier):
        return identifier
    if BleakScanner is None:
        raise ImportError("bleak is required to scan for Bluetooth devices by name.")

    wanted = identifier.strip().lower()
    exact_match = None
    partial_match = None

    for device, advertisement in await _discover_bleak_devices_with_advertisement(scan_timeout):
        for name in _iter_device_names(device, advertisement):
            lowered = name.lower()
            if lowered == wanted:
                exact_match = device
                break
            if wanted in lowered and partial_match is None:
                partial_match = device
        if exact_match is not None:
            break

    if exact_match is not None:
        return exact_match
    if partial_match is not None:
        return partial_match

    raise ValueError(f"Could not find a Bluetooth device matching '{identifier}'.")


def discover_devices(name: Optional[str] = None, scan_timeout: float = 5.0) -> List[dict]:
    if BleakScanner is None:
        raise ImportError("bleak is required to scan for Bluetooth devices.")

    async def _discover():
        results = []
        for device, advertisement in await _discover_bleak_devices_with_advertisement(scan_timeout):
            names = tuple(_iter_device_names(device, advertisement))
            if name and not any(name.lower() in candidate.lower() for candidate in names):
                continue

            manufacturer_data = {}
            service_data = {}
            service_uuids = []
            tx_power = None
            local_name = None
            rssi = getattr(device, "rssi", None)

            if advertisement is not None:
                manufacturer_data = _format_manufacturer_data(getattr(advertisement, "manufacturer_data", None))
                service_data = _format_service_data(getattr(advertisement, "service_data", None))
                service_uuids = list(getattr(advertisement, "service_uuids", []) or [])
                tx_power = getattr(advertisement, "tx_power", None)
                local_name = getattr(advertisement, "local_name", None)
                rssi = getattr(advertisement, "rssi", rssi)

            metadata = getattr(device, "metadata", {}) or {}
            if local_name is None:
                local_name = metadata.get("local_name")

            results.append(
                {
                    "name": getattr(device, "name", None),
                    "local_name": local_name,
                    "address": device.address,
                    "rssi": rssi,
                    "manufacturer_data": manufacturer_data,
                    "service_data": service_data,
                    "service_uuids": service_uuids,
                    "tx_power": tx_power,
                }
            )
        results.sort(key=lambda item: (((item["local_name"] or item["name"] or "")).lower(), item["address"]))
        return results

    return _run_coroutine_sync(_discover())


def probe_device(identifier: str, scan_timeout: float = 5.0) -> dict:
    if BleakClient is None:
        raise ImportError("bleak is required to probe Bluetooth GATT services.")

    async def _probe():
        target = await _resolve_ble_target(identifier, scan_timeout)
        client = BleakClient(target)
        await client.connect()
        try:
            services = client.services
            if services is None:
                services = await client.get_services()

            result = {
                "name": getattr(target, "name", None),
                "address": getattr(target, "address", identifier),
                "services": [],
            }

            for service in services:
                service_entry = {
                    "uuid": _coerce_uuid(service.uuid),
                    "description": getattr(service, "description", ""),
                    "characteristics": [],
                }
                for characteristic in service.characteristics:
                    service_entry["characteristics"].append(
                        {
                            "uuid": _coerce_uuid(characteristic.uuid),
                            "properties": list(getattr(characteristic, "properties", [])),
                            "handle": getattr(characteristic, "handle", None),
                        }
                    )
                result["services"].append(service_entry)
            return result
        finally:
            await client.disconnect()

    return _run_coroutine_sync(_probe())


def _find_service(primary_services, uuid_candidates: Sequence[str]):
    for candidate in uuid_candidates:
        for service in primary_services:
            if _coerce_uuid(service["uuid"]) == candidate:
                return service
    raise KeyError(f"Could not find any matching service for {uuid_candidates}.")


def _find_characteristic_handle(characteristics, uuid_candidates: Sequence[str]):
    for candidate in uuid_candidates:
        for characteristic in characteristics:
            if _coerce_uuid(characteristic["uuid"]) == candidate:
                return characteristic["value_handle"]
    raise KeyError(f"Could not find any matching characteristic for {uuid_candidates}.")


def _pick_bleak_characteristic(characteristics, uuid_candidates: Sequence[str], required_properties: Sequence[str]):
    for candidate in uuid_candidates:
        for characteristic in characteristics:
            if _coerce_uuid(characteristic.uuid) == candidate and _char_supports(characteristic, required_properties):
                return characteristic

    for candidate in uuid_candidates:
        for characteristic in characteristics:
            if _coerce_uuid(characteristic.uuid) == candidate:
                return characteristic

    for characteristic in characteristics:
        if _char_supports(characteristic, required_properties):
            return characteristic

    raise KeyError(
        f"Could not find a characteristic with properties {required_properties} for UUIDs {uuid_candidates}."
    )


class _AsyncLoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result()

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=1)
        self.loop.close()


class BleakTransport:
    def __init__(
        self,
        identifier: str,
        profile: GattProfile,
        notification_handler: Optional[NotificationHandler] = None,
        scan_timeout: float = 5.0,
    ):
        if BleakClient is None:
            raise ImportError("bleak is required for the Bleak backend.")

        self.identifier = identifier
        self.profile = profile
        self.notification_handler = notification_handler
        self.scan_timeout = scan_timeout

        self._runner = None
        self.client = None
        self.command_characteristic = None
        self.data_characteristic = None
        self.notify_characteristic = None
        self.characteristics_by_uuid = {}
        self._notify_characteristics = {}
        self.address = None
        self.mtu = 23
        self.max_data_chunk_size = 20

    def _handle_notification(self, _characteristic, data: bytearray):
        if self.notification_handler is not None:
            self.notification_handler(bytes(data))

    def _get_characteristic(self, uuid: str, required_properties: Sequence[str] = ()):
        candidate = _coerce_uuid(uuid)
        characteristics = self.characteristics_by_uuid.get(candidate, ())
        if not characteristics:
            raise KeyError(f"Could not find characteristic {candidate}.")
        if required_properties:
            for characteristic in characteristics:
                if _char_supports(characteristic, required_properties):
                    return characteristic
        return characteristics[0]

    def _ensure_runner(self):
        if self._runner is None:
            self._runner = _AsyncLoopThread()

    async def _connect_async(self):
        target = await _resolve_ble_target(self.identifier, self.scan_timeout)
        self.address = getattr(target, "address", self.identifier)
        self.client = BleakClient(target)
        await self.client.connect()

        services = self.client.services
        if services is None:
            services = await self.client.get_services()

        service_uuids = set(self.profile.service_uuids)
        characteristics = []
        for service in services:
            if service_uuids and _coerce_uuid(service.uuid) not in service_uuids:
                continue
            characteristics.extend(service.characteristics)

        if not characteristics:
            raise KeyError(f"No characteristics found for services {self.profile.service_uuids}.")

        self.characteristics_by_uuid = {}
        for characteristic in characteristics:
            self.characteristics_by_uuid.setdefault(_coerce_uuid(characteristic.uuid), []).append(characteristic)

        self.command_characteristic = _pick_bleak_characteristic(
            characteristics, self.profile.command_uuids, ("write", "write-without-response")
        )
        self.data_characteristic = _pick_bleak_characteristic(
            characteristics, self.profile.data_uuids, ("write", "write-without-response")
        )

        notify_candidates = self.profile.notify_uuids or self.profile.command_uuids or self.profile.data_uuids
        self.notify_characteristic = _pick_bleak_characteristic(
            characteristics, notify_candidates, ("notify", "indicate")
        )

        await self._start_notify_async(self.notify_characteristic)

        self.mtu = getattr(self.client, "mtu_size", 23) or 23
        max_write = getattr(self.data_characteristic, "max_write_without_response_size", None)
        self.max_data_chunk_size = max(20, max_write or (self.mtu - 3))

    async def _start_notify_async(self, characteristic):
        uuid = _coerce_uuid(characteristic.uuid)
        if uuid in self._notify_characteristics:
            return
        await self.client.start_notify(characteristic, self._handle_notification)
        self._notify_characteristics[uuid] = characteristic

    async def _stop_notify_async(self, characteristic):
        uuid = _coerce_uuid(characteristic.uuid)
        if uuid not in self._notify_characteristics:
            return
        await self.client.stop_notify(characteristic)
        self._notify_characteristics.pop(uuid, None)

    async def _disconnect_async(self):
        if self.client is None:
            return
        try:
            if self.client.is_connected:
                try:
                    for characteristic in tuple(self._notify_characteristics.values()):
                        await self._stop_notify_async(characteristic)
                except Exception:
                    pass
                await self.client.disconnect()
        finally:
            self.client = None
            self.command_characteristic = None
            self.data_characteristic = None
            self.notify_characteristic = None
            self.characteristics_by_uuid = {}
            self._notify_characteristics = {}

    async def _write_async(self, characteristic, value: bytes):
        if self.client is None or not self.client.is_connected:
            raise ConnectionError("Bluetooth device is not connected.")
        properties = _char_properties(characteristic)
        response = "write-without-response" not in properties and "write" in properties
        await self.client.write_gatt_char(characteristic, value, response=response)

    def connect(self):
        self._ensure_runner()
        if not self.is_connected():
            self._runner.run(self._connect_async())

    def disconnect(self):
        if self._runner is None:
            return
        try:
            self._runner.run(self._disconnect_async())
        finally:
            self._runner.close()
            self._runner = None

    def is_connected(self) -> bool:
        return self.client is not None and self.client.is_connected

    def write_command(self, value: bytes):
        self._runner.run(self._write_async(self.command_characteristic, value))

    def write_data(self, value: bytes):
        self._runner.run(self._write_async(self.data_characteristic, value))

    def write_uuid(self, uuid: str, value: bytes):
        characteristic = self._get_characteristic(uuid, ("write", "write-without-response"))
        self._runner.run(self._write_async(characteristic, value))

    def start_notify_uuid(self, uuid: str):
        characteristic = self._get_characteristic(uuid, ("notify", "indicate"))
        self._runner.run(self._start_notify_async(characteristic))


class GattlibTransport:
    def __init__(
        self,
        identifier: str,
        profile: GattProfile,
        notification_handler: Optional[NotificationHandler] = None,
        scan_timeout: float = 5.0,
    ):
        if GATTRequester is None:
            raise ImportError("gattlib is required for the gattlib backend.")

        self.identifier = identifier
        self.profile = profile
        self.notification_handler = notification_handler
        self.scan_timeout = scan_timeout

        self.connection = None
        self.address = None
        self.mtu = 23
        self.max_data_chunk_size = 20
        self.command_handle = None
        self.data_handle = None
        self.notify_value_handle = None
        self.notify_enable_handle = None

    def _set_mtu(self, mtu):
        self.mtu = mtu
        self.max_data_chunk_size = max(20, mtu - 3)

    def _handle_notification(self, handle, data):
        if handle == self.notify_value_handle and self.notification_handler is not None:
            self.notification_handler(bytes(data))

    def _resolve_address(self):
        if self.address is not None:
            return self.address
        target = _run_coroutine_sync(_resolve_ble_target(self.identifier, self.scan_timeout))
        self.address = getattr(target, "address", target)
        return self.address

    def connect(self):
        if self.connection is not None and self.connection.is_connected():
            return

        address = self._resolve_address()
        self.connection = GATTRequester(address)
        self.connection.on_connect = lambda mtu: self._set_mtu(mtu)
        self.connection.on_notification = lambda handle, data: self._handle_notification(handle, data)

        try:
            self.connection.connect()
        except Exception:
            pass

        for _ in range(50):
            if self.connection.is_connected():
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("Timeout exceeded waiting for bluetooth connection.")

        primary_services = self.connection.discover_primary()
        service = _find_service(primary_services, self.profile.service_uuids)
        characteristics = self.connection.discover_characteristics(service["start"], service["end"])

        self.command_handle = _find_characteristic_handle(characteristics, self.profile.command_uuids)
        self.data_handle = _find_characteristic_handle(characteristics, self.profile.data_uuids)

        notify_candidates = self.profile.notify_uuids or self.profile.command_uuids
        self.notify_value_handle = _find_characteristic_handle(characteristics, notify_candidates)
        self.notify_enable_handle = self.notify_value_handle + 1
        self.connection.write_by_handle(self.notify_enable_handle, b"\x00\x00\x00\x01")

    def disconnect(self):
        if self.connection is not None:
            self.connection.disconnect()
            self.connection = None

    def is_connected(self) -> bool:
        return self.connection is not None and self.connection.is_connected()

    def write_command(self, value: bytes):
        self.connection.write_cmd(self.command_handle, value)

    def write_data(self, value: bytes):
        self.connection.write_cmd(self.data_handle, value)

    def write_uuid(self, uuid: str, value: bytes):
        raise NotImplementedError("Dynamic UUID writes are only implemented for the Bleak transport.")

    def start_notify_uuid(self, uuid: str):
        raise NotImplementedError("Dynamic UUID notifications are only implemented for the Bleak transport.")


def create_transport(
    identifier: str,
    profile: GattProfile,
    backend: str = "auto",
    notification_handler: Optional[NotificationHandler] = None,
    scan_timeout: float = 5.0,
):
    backend = (backend or "auto").lower()
    if backend not in ("auto", "bleak", "gattlib"):
        raise ValueError("backend must be 'auto', 'bleak', or 'gattlib'.")

    if backend == "auto":
        preferred = profile.preferred_backend
        if preferred == "auto":
            if platform.system() == "Darwin":
                preferred = "bleak"
            elif GATTRequester is not None and looks_like_device_address(identifier):
                preferred = "gattlib"
            else:
                preferred = "bleak"
        backend = preferred

    if backend == "gattlib":
        if GATTRequester is not None:
            return GattlibTransport(identifier, profile, notification_handler=notification_handler, scan_timeout=scan_timeout)
        if BleakClient is not None:
            return BleakTransport(identifier, profile, notification_handler=notification_handler, scan_timeout=scan_timeout)
        raise ImportError("Neither gattlib nor bleak is installed.")

    if BleakClient is not None:
        return BleakTransport(identifier, profile, notification_handler=notification_handler, scan_timeout=scan_timeout)
    if GATTRequester is not None:
        return GattlibTransport(identifier, profile, notification_handler=notification_handler, scan_timeout=scan_timeout)
    raise ImportError("Neither bleak nor gattlib is installed.")
