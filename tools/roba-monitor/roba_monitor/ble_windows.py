"""Windows (WinRT) から roBa の Battery Service を読むバックエンド.

roBa は Windows に HID キーボードとしてペアリング・接続されている前提。
Windows が維持している接続をそのまま使って GATT を読むので、
キーボードの接続を奪ったり、再ペアリングしたりはしない。

メソッド名は PyWinRT v3 の名前を使う（v4 でも互換の別名として残っている）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from winrt.windows.devices.bluetooth import (
    BluetoothCacheMode,
    BluetoothConnectionStatus,
    BluetoothLEDevice,
)
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattCommunicationStatus,
)
from winrt.windows.devices.enumeration import DeviceInformation

from .model import BatteryReading, RawSnapshot

log = logging.getLogger(__name__)


def _uuid16(short: int) -> uuid.UUID:
    return uuid.UUID(f"0000{short:04x}-0000-1000-8000-00805f9b34fb")


BATTERY_SERVICE = _uuid16(0x180F)
BATTERY_LEVEL = _uuid16(0x2A19)
USER_DESCRIPTION = _uuid16(0x2901)

SUCCESS = GattCommunicationStatus.SUCCESS
CACHED = BluetoothCacheMode.CACHED
UNCACHED = BluetoothCacheMode.UNCACHED


def _bytes(buffer) -> bytes:
    return bytes(memoryview(buffer)) if buffer is not None else b""


def _close(obj) -> None:
    try:
        obj.close()
    except Exception:  # noqa: BLE001 - 後始末の失敗は無視してよい
        pass


async def _paired_le_devices() -> list:
    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    return list(await DeviceInformation.find_all_async_aqs_filter(selector))


class WindowsBackend:
    def __init__(self, name: str = "roBa", timeout: float = 15.0) -> None:
        self.name = name
        self.timeout = timeout
        self._device = None
        self._descriptions: dict[int, str] = {}

    # ------------------------------------------------------------ public

    async def poll(self) -> RawSnapshot:
        try:
            return await asyncio.wait_for(self._poll(), self.timeout)
        except asyncio.TimeoutError:
            connected = self._is_connected()
            return RawSnapshot(
                found=self._device is not None,
                host_connected=connected,
                error="応答がタイムアウトしました",
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("poll failed")
            self.reset()
            return RawSnapshot(found=False, error=f"{type(exc).__name__}: {exc}")

    def reset(self) -> None:
        if self._device is not None:
            _close(self._device)
        self._device = None
        self._descriptions.clear()

    # ------------------------------------------------------------ internals

    def _is_connected(self) -> bool:
        try:
            return (
                self._device is not None
                and self._device.connection_status == BluetoothConnectionStatus.CONNECTED
            )
        except Exception:  # noqa: BLE001
            return False

    async def _ensure_device(self):
        if self._device is not None:
            return self._device
        wanted = self.name.lower()
        candidates = [
            info for info in await _paired_le_devices()
            if (info.name or "").lower().startswith(wanted)
        ]
        devices = []
        for info in candidates:
            device = await BluetoothLEDevice.from_id_async(info.id)
            if device is not None:
                devices.append(device)
        if not devices:
            return None
        # 同名の古いペアリングが残っている場合は、接続中のものを優先する
        devices.sort(key=lambda d: d.connection_status != BluetoothConnectionStatus.CONNECTED)
        self._device = devices[0]
        for extra in devices[1:]:
            _close(extra)
        log.info("using device %s (%s)", self._device.name, self._device.device_id)
        return self._device

    async def _poll(self) -> RawSnapshot:
        device = await self._ensure_device()
        if device is None:
            return RawSnapshot(found=False, error=f"「{self.name}」という名前のペアリング済み機器がありません")

        if not self._is_connected():
            return RawSnapshot(found=True, host_connected=False, device_name=device.name)

        readings, error = await self._read_batteries(device)
        return RawSnapshot(
            found=True,
            host_connected=self._is_connected(),
            readings=tuple(readings),
            error=error,
            device_name=device.name,
        )

    async def _read_batteries(self, device) -> tuple[list[BatteryReading], str | None]:
        result = await device.get_gatt_services_for_uuid_with_cache_mode_async(BATTERY_SERVICE, CACHED)
        services = list(result.services) if result.status == SUCCESS else []
        if not services:
            # 初回やファーム更新後はキャッシュが空・古いことがあるので取り直す
            result = await device.get_gatt_services_for_uuid_with_cache_mode_async(BATTERY_SERVICE, UNCACHED)
            services = list(result.services) if result.status == SUCCESS else []
        if not services:
            return [], f"Battery Service を取得できません（status={result.status}）"

        readings: list[BatteryReading] = []
        errors: list[str] = []
        try:
            for service in services:
                chars = await service.get_characteristics_for_uuid_with_cache_mode_async(BATTERY_LEVEL, CACHED)
                if chars.status != SUCCESS:
                    errors.append(f"特性の取得に失敗（status={chars.status}）")
                    continue
                for characteristic in chars.characteristics:
                    readings.append(await self._read_level(characteristic))
        finally:
            for service in services:
                _close(service)

        if readings and all(r.level is None for r in readings):
            errors.append("電池残量を読めませんでした")
        return readings, ("; ".join(errors) or None)

    async def _read_level(self, characteristic) -> BatteryReading:
        description = await self._description(characteristic)
        value = await characteristic.read_value_with_cache_mode_async(UNCACHED)
        if value.status != SUCCESS:
            return BatteryReading(None, description)
        data = _bytes(value.value)
        return BatteryReading(data[0] if data else None, description)

    async def _description(self, characteristic) -> str:
        handle = characteristic.attribute_handle
        if handle in self._descriptions:
            return self._descriptions[handle]
        text = ""
        try:
            text = characteristic.user_description or ""
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            descs = await characteristic.get_descriptors_for_uuid_with_cache_mode_async(USER_DESCRIPTION, CACHED)
            if descs.status == SUCCESS:
                for descriptor in descs.descriptors:
                    read = await descriptor.read_value_with_cache_mode_async(CACHED)
                    if read.status == SUCCESS:
                        text = _bytes(read.value).decode("utf-8", "replace").rstrip("\x00")
                        break
        self._descriptions[handle] = text
        return text


# ---------------------------------------------------------------- 診断


async def diagnose(name: str = "roBa") -> str:
    """不具合の調査用に、見えている情報をすべて文字列にする."""
    lines: list[str] = []
    out = lines.append

    infos = await _paired_le_devices()
    out(f"ペアリング済み BLE 機器: {len(infos)} 台")
    for info in infos:
        out(f"  - {info.name!r}  id={info.id}")

    wanted = name.lower()
    targets = [i for i in infos if (i.name or "").lower().startswith(wanted)]
    if not targets:
        out(f"\n「{name}」で始まる機器がありません。")
        return "\n".join(lines)

    for info in targets:
        device = await BluetoothLEDevice.from_id_async(info.id)
        if device is None:
            out(f"\n{info.name}: BluetoothLEDevice を開けません")
            continue
        try:
            out(f"\n== {device.name}  address={device.bluetooth_address:012X}")
            out(f"connection_status = {device.connection_status.name}")
            result = await device.get_gatt_services_with_cache_mode_async(UNCACHED)
            out(f"GATT services: status={result.status.name}")
            for service in result.services:
                out(f"  service {service.uuid}")
                try:
                    chars = await service.get_characteristics_with_cache_mode_async(UNCACHED)
                    for ch in chars.characteristics:
                        desc = ""
                        try:
                            desc = ch.user_description
                        except Exception:  # noqa: BLE001
                            pass
                        line = f"    char {ch.uuid} handle={ch.attribute_handle} desc={desc!r}"
                        if ch.uuid == BATTERY_LEVEL:
                            value = await ch.read_value_with_cache_mode_async(UNCACHED)
                            data = _bytes(value.value) if value.status == SUCCESS else b""
                            line += f" read={value.status.name} value={list(data)}"
                        out(line)
                except Exception as exc:  # noqa: BLE001
                    out(f"    (特性を列挙できません: {exc})")
                finally:
                    _close(service)
        finally:
            _close(device)

    out("\n--- monitor の判定 ---")
    backend = WindowsBackend(name)
    snapshot = await backend.poll()
    backend.reset()
    out(repr(snapshot))
    return "\n".join(lines)
