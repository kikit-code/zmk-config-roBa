"""roBa の左右状態を判定する純粋ロジック（Windows API に依存しない）.

ZMK (v0.3) の右手（セントラル）は、PC に対して Battery Service を2つ公開する:

* 右手自身の電池残量（Zephyr 標準の BAS。説明ディスクリプタなし）
* 左手の電池残量のプロキシ（説明ディスクリプタ "Peripheral 0"）

左手が右手から切断されると、右手は左手の残量を 0% にして通知する
(zmk app/src/split/bluetooth/central.c: split_central_disconnected)。
そのため「プロキシ値が 0」を「左手が未接続」とみなす。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

PERIPHERAL_PREFIX = "Peripheral"
LOW_BATTERY_THRESHOLDS = (20, 10)


class Link(enum.Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BatteryReading:
    """PC から読めた Battery Level 特性 1 つ分."""

    level: int | None
    description: str = ""

    @property
    def is_peripheral(self) -> bool:
        return self.description.strip().startswith(PERIPHERAL_PREFIX)


@dataclass(frozen=True)
class RawSnapshot:
    """バックエンドが 1 回のポーリングで得た生データ."""

    found: bool
    host_connected: bool = False
    readings: tuple[BatteryReading, ...] = ()
    error: str | None = None
    device_name: str | None = None


@dataclass(frozen=True)
class Half:
    link: Link
    battery: int | None = None


@dataclass(frozen=True)
class Status:
    right: Half
    left: Half
    found: bool = True
    error: str | None = None
    at: datetime = field(default_factory=datetime.now)

    def key(self) -> tuple:
        """記録や比較に使う、時刻を除いた中身."""
        return (
            self.found,
            self.right.link,
            self.right.battery,
            self.left.link,
            self.left.battery,
            self.error,
        )


def _clamp(level: int | None) -> int | None:
    if level is None:
        return None
    return max(0, min(100, int(level)))


def interpret(raw: RawSnapshot, at: datetime | None = None) -> Status:
    at = at or datetime.now()
    unknown = Half(Link.UNKNOWN)

    if not raw.found:
        return Status(unknown, unknown, found=False, error=raw.error, at=at)

    if not raw.host_connected:
        # 右手が PC と繋がっていない（別の機器に切替中・スリープ・電源オフ）。
        # この場合は左手の状態も PC からは分からない。
        return Status(Half(Link.DISCONNECTED), unknown, error=raw.error, at=at)

    central = next((r for r in raw.readings if not r.is_peripheral), None)
    peripheral = next((r for r in raw.readings if r.is_peripheral), None)

    right = Half(Link.CONNECTED, _clamp(central.level) if central else None)

    if peripheral is None or peripheral.level is None:
        left = unknown
    elif peripheral.level <= 0:
        left = Half(Link.DISCONNECTED)
    else:
        left = Half(Link.CONNECTED, _clamp(peripheral.level))

    return Status(right, left, error=raw.error, at=at)


# ---------------------------------------------------------------- 表示用文字列

LINK_LABEL = {
    Link.CONNECTED: "接続中",
    Link.DISCONNECTED: "未接続",
    Link.UNKNOWN: "不明",
}


def describe_half(half: Half) -> str:
    text = LINK_LABEL[half.link]
    if half.battery is not None:
        text += f" {half.battery}%"
    return text


def menu_lines(status: Status | None) -> list[str]:
    if status is None:
        return ["確認中…"]
    if not status.found:
        return [
            "roBa が見つかりません",
            "（Windows の Bluetooth 設定でペアリングを確認）",
        ]
    lines = [
        f"右手（PC と）: {describe_half(status.right)}",
        f"左手（右手と）: {describe_half(status.left)}",
    ]
    if status.right.link is Link.DISCONNECTED:
        lines.append("右手が別の機器に接続中か、スリープ／電源オフです")
    if status.error:
        lines.append(f"エラー: {status.error}"[:80])
    lines.append(f"最終確認 {status.at:%H:%M:%S}")
    return lines


def tooltip(status: Status | None) -> str:
    """トレイアイコンのツールチップ（Windows の上限 127 文字に収める）."""
    if status is None:
        return "roBa: 確認中…"
    if not status.found:
        return "roBa: 見つかりません"

    def short(half: Half) -> str:
        if half.link is Link.CONNECTED:
            return f"{half.battery}%" if half.battery is not None else "接続中"
        return LINK_LABEL[half.link]

    return f"roBa  左 {short(status.left)} / 右 {short(status.right)}"[:127]


# ---------------------------------------------------------------- 通知の判定


@dataclass
class Notifier:
    """状態の変化から、ユーザーに知らせるべき出来事を取り出す.

    一瞬の揺らぎ（右手の再起動直後に左手の値が 0 になる等）で通知しないよう、
    接続状態の変化は ``confirm_polls`` 回続けて同じだった時だけ確定させる。
    """

    confirm_polls: int = 2
    thresholds: tuple[int, ...] = LOW_BATTERY_THRESHOLDS
    _stable: dict[str, Link] = field(default_factory=dict)
    _pending: dict[str, tuple[Link, int]] = field(default_factory=dict)
    _warned: dict[str, int] = field(default_factory=dict)

    def update(self, status: Status) -> list[str]:
        if not status.found:
            return []
        messages: list[str] = []
        messages += self._link("right", status.right.link)
        # 右手が PC と繋がっていない間、左手の状態は判断材料がないので保留する
        if status.right.link is Link.CONNECTED:
            messages += self._link("left", status.left.link)
        for side, half in (("left", status.left), ("right", status.right)):
            messages += self._battery(side, half)
        return messages

    def _link(self, side: str, link: Link) -> list[str]:
        if link is Link.UNKNOWN:
            return []
        stable = self._stable.get(side)
        if stable is None:  # 起動直後の状態は通知しない
            self._stable[side] = link
            return []
        if link is stable:
            self._pending.pop(side, None)
            return []
        seen, count = self._pending.get(side, (link, 0))
        count = count + 1 if seen is link else 1
        if count < self.confirm_polls:
            self._pending[side] = (link, count)
            return []
        self._pending.pop(side, None)
        self._stable[side] = link
        return [_LINK_MESSAGES[(side, link)]]

    def _battery(self, side: str, half: Half) -> list[str]:
        if half.link is not Link.CONNECTED or half.battery is None:
            return []
        level = half.battery
        warned = self._warned.get(side)
        # 充電などで十分回復したら、次に下がった時にまた知らせる
        if warned is not None and level > warned + 5:
            self._warned.pop(side)
            warned = None
        crossed = [t for t in self.thresholds if level <= t and (warned is None or t < warned)]
        if not crossed:
            return []
        self._warned[side] = min(crossed)
        name = "左手" if side == "left" else "右手"
        return [f"{name}の電池が残り {level}% です"]


_LINK_MESSAGES = {
    ("right", Link.CONNECTED): "右手が PC に接続しました",
    ("right", Link.DISCONNECTED): "右手と PC の接続が切れました",
    ("left", Link.CONNECTED): "左手が右手に再接続しました",
    ("left", Link.DISCONNECTED): "左手と右手の接続が切れました",
}
