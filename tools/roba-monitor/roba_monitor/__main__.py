"""roba-monitor の入口.

    python -m roba_monitor              タスクトレイに常駐（通常の使い方）
    python -m roba_monitor --once       状態を 1 回だけ表示して終了
    python -m roba_monitor --diagnose   不具合調査用に、見えている GATT 情報をすべて表示
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .model import interpret, menu_lines

TOOL_DIR = Path(__file__).resolve().parent.parent


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="roba_monitor", description="roBa の左右の接続・電池状態を表示します")
    p.add_argument("--name", default="roBa", help="Windows 上でのキーボード名（前方一致、既定: roBa）")
    p.add_argument("--interval", type=float, default=15.0, help="確認する間隔（秒、既定: 15）")
    p.add_argument("--data-dir", type=Path, default=TOOL_DIR / "data", help="記録とログの保存先")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="1 回だけ確認して表示")
    mode.add_argument("--diagnose", action="store_true", help="GATT の詳細を表示（不具合調査用）")
    p.add_argument("--version", action="version", version=__version__)
    return p.parse_args(argv)


def _setup_logging(data_dir: Path, console: bool) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(data_dir / "monitor.log", maxBytes=512_000, backupCount=2, encoding="utf-8")
    ]
    if console and sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    interactive = args.once or args.diagnose
    _setup_logging(args.data_dir, console=interactive)

    if sys.platform != "win32":
        print("このツールは Windows 専用です。", file=sys.stderr)
        return 2

    from .ble_windows import WindowsBackend, diagnose

    if args.diagnose:
        print(asyncio.run(diagnose(args.name)))
        return 0

    if args.once:
        from .app import ProfileStore

        backend = WindowsBackend(args.name)
        raw = asyncio.run(backend.poll())
        backend.reset()
        status = interpret(raw)
        profiles = ProfileStore(args.data_dir)
        profiles.update(raw, status)
        print("\n".join(menu_lines(status)))
        print()
        print("\n".join(profiles.lines()))
        return 0 if status.found else 1

    from .app import MonitorApp, SingleInstance

    guard = SingleInstance()
    if guard.already_running:
        logging.getLogger(__name__).info("already running; exiting")
        return 0

    MonitorApp(WindowsBackend(args.name), args.data_dir, args.interval).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
