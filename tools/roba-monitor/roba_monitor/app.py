"""タスクトレイ常駐アプリ本体.

* メインスレッド: pystray（Windows のメッセージループ）
* 別スレッド: asyncio で一定間隔ごとに状態を読み、アイコン・メニュー・通知・記録を更新
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .model import Notifier, Status, interpret, menu_lines, tooltip

log = logging.getLogger(__name__)

MENU_LINES = 6  # 状態表示に使うメニュー行の最大数
HEARTBEAT = timedelta(minutes=30)


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

        menu = pystray.Menu(
            *(status_item(i) for i in range(MENU_LINES)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("今すぐ確認", self._on_refresh),
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
            self._apply(interpret(raw))
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
