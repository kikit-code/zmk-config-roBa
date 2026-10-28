"""タスクトレイ常駐アプリ本体.

* メインスレッド: pystray（Windows のメッセージループ）
* 別スレッド: asyncio で一定間隔ごとに状態を読み、アイコン・メニュー・通知・記録を更新
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .model import (
    Link,
    Notifier,
    ProfileTable,
    RawSnapshot,
    Status,
    interpret,
    menu_lines,
    names_template,
    parse_names,
    parse_profiles,
    profile_lines,
    tooltip,
)

log = logging.getLogger(__name__)

MENU_LINES = 6  # 状態表示に使うメニュー行の最大数
PROFILE_MENU_LINES = 7  # BT 設定の一覧に使うメニュー行の最大数
HEARTBEAT = timedelta(minutes=30)


class ProfileStore:
    """BT1〜BT5 の一覧を覚えておく.

    右手が別の端末に切り替わっている間は PC から読めないので、最後に読めた一覧を
    data/profiles.json に保存して表示に使う。端末の名前は data/device_names.txt に書く。
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "profiles.json"
        self.names_path = data_dir / "device_names.txt"
        self.table: ProfileTable | None = None
        self.this_pc: str | None = None
        self.live = False
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.table = parse_profiles(bytes.fromhex(saved["data"]), datetime.fromisoformat(saved["read_at"]))
            self.this_pc = saved.get("this_pc")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError):
            log.exception("ignoring unreadable %s", self.path)

    def update(self, raw: RawSnapshot, status: Status) -> None:
        self.live = False
        if raw.this_pc:
            self.this_pc = raw.this_pc
        if raw.profiles is None or status.right.link is not Link.CONNECTED:
            return
        try:
            table = parse_profiles(raw.profiles, status.at)
        except ValueError:
            log.exception("bad profile data: %s", raw.profiles.hex())
            return
        self.live = True
        changed = self.table is None or self.table.entries != table.entries or self.table.active != table.active
        self.table = table
        if changed or not self.path.exists():
            self._save(raw.profiles)
        if not self.names_path.exists():
            self.names_path.parent.mkdir(parents=True, exist_ok=True)
            self.names_path.write_text(names_template(table, self.this_pc), encoding="utf-8")

    def _save(self, data: bytes) -> None:
        assert self.table is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "read_at": self.table.read_at.isoformat(timespec="seconds"),
            "data": data.hex(),
            "this_pc": self.this_pc,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def names(self) -> dict[str, str]:
        try:
            return parse_names(self.names_path.read_text(encoding="utf-8"))
        except OSError:
            return {}

    def lines(self) -> list[str]:
        return profile_lines(self.table, self.names(), self.this_pc, self.live)


class BatteryLog:
    """状態が変わった時と、30 分ごとに 1 行ずつ CSV に記録する（電池持ちの分析用）."""

    FIELDS = ["time", "right_link", "right_battery", "left_link", "left_battery", "error"]

    def __init__(self, path: Path) -> None:
        self.path = path
        self._last_key = None
        self._last_time: datetime | None = None

    def write(self, status: Status) -> None:
        key = status.key()
        due = self._last_time is None or status.at - self._last_time >= HEARTBEAT
        if key == self._last_key and not due:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            if new:
                writer.writerow(self.FIELDS)
            writer.writerow([
                status.at.isoformat(timespec="seconds"),
                status.right.link.value if status.found else "not_found",
                "" if status.right.battery is None else status.right.battery,
                status.left.link.value if status.found else "not_found",
                "" if status.left.battery is None else status.left.battery,
                status.error or "",
            ])
        self._last_key, self._last_time = key, status.at


class MonitorApp:
    def __init__(self, backend, data_dir: Path, interval: float = 15.0) -> None:
        self.backend = backend
        self.data_dir = data_dir
        self.interval = interval
        self.status: Status | None = None
        self.notifier = Notifier()
        self.battery_log = BatteryLog(data_dir / "battery_log.csv")
        self.profiles = ProfileStore(data_dir)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self.icon = None

    # ------------------------------------------------------------ tray

    def run(self) -> None:
        import pystray

        from .icon import render

        def status_item(index: int):
            return pystray.MenuItem(
                lambda item: self._line(index),
                None,
                enabled=False,
                visible=lambda item: self._line(index) != "",
            )

        def profile_item(index: int):
            return pystray.MenuItem(
                lambda item: self._profile_line(index),
                None,
                enabled=False,
                visible=lambda item: self._profile_line(index) != "",
            )

        menu = pystray.Menu(
            *(status_item(i) for i in range(MENU_LINES)),
            pystray.Menu.SEPARATOR,
            *(profile_item(i) for i in range(PROFILE_MENU_LINES)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("今すぐ確認", self._on_refresh),
            pystray.MenuItem("端末の名前を編集", self._on_edit_names),
            pystray.MenuItem("記録フォルダを開く", self._on_open_folder),
            pystray.MenuItem("終了", self._on_quit),
        )
        self.icon = pystray.Icon("roba-monitor", render(None), tooltip(None), menu)

        worker = threading.Thread(target=self._worker, name="roba-poller", daemon=True)
        worker.start()
        self.icon.run()  # 終了メニューが押されるまで戻らない
        self._stopping = True
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)
        worker.join(timeout=5)

    def _line(self, index: int) -> str:
        lines = menu_lines(self.status)
        return lines[index] if index < len(lines) else ""

    def _profile_line(self, index: int) -> str:
        lines = self.profiles.lines()
        return lines[index] if index < len(lines) else ""

    def _on_edit_names(self, icon, item) -> None:
        path = self.profiles.names_path
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(names_template(self.profiles.table, self.profiles.this_pc), encoding="utf-8")
        os.startfile(path)  # type: ignore[attr-defined]  # 既定のエディタ（メモ帳）で開く

    def _on_refresh(self, icon, item) -> None:
        if self._loop is not None and self._wake is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def _on_open_folder(self, icon, item) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.data_dir)  # type: ignore[attr-defined]  # Windows 専用

    def _on_quit(self, icon, item) -> None:
        icon.stop()

    # ------------------------------------------------------------ polling

    def _worker(self) -> None:
        try:
            asyncio.run(self._poll_forever())
        except Exception:  # noqa: BLE001
            log.exception("poller crashed")

    async def _poll_forever(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while not self._stopping:
            raw = await self.backend.poll()
            status = interpret(raw)
            self.profiles.update(raw, status)
            self._apply(status)
            try:
                await asyncio.wait_for(self._wake.wait(), self.interval)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
        self.backend.reset()

    def _apply(self, status: Status) -> None:
        from .icon import render

        self.status = status
        try:
            self.battery_log.write(status)
        except OSError:
            log.exception("failed to write battery log")

        icon = self.icon
        if icon is None:
            return
        icon.icon = render(status)
        icon.title = tooltip(status)
        icon.update_menu()
        for message in self.notifier.update(status):
            log.info("notify: %s", message)
            if getattr(icon, "HAS_NOTIFICATION", False):
                icon.notify(message, "roBa")


# ---------------------------------------------------------------- 多重起動防止


class SingleInstance:
    """Windows の名前付きミューテックスで、二重起動を防ぐ."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, name: str = "Local\\roba-monitor") -> None:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = kernel32.CreateMutexW(None, False, name)
        self.already_running = ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS
