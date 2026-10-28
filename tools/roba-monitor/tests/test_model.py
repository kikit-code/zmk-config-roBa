import asyncio
import csv
from datetime import datetime, timedelta

from roba_monitor.app import BatteryLog, MonitorApp
from roba_monitor.icon import SIZE, render
from roba_monitor.model import (
    BatteryReading,
    Half,
    Link,
    Notifier,
    RawSnapshot,
    Status,
    interpret,
    menu_lines,
    tooltip,
)

RIGHT = BatteryReading(76, "")
LEFT = BatteryReading(82, "Peripheral 0")


def snap(*readings, connected=True, found=True):
    return RawSnapshot(found=found, host_connected=connected, readings=tuple(readings))


# ------------------------------------------------------------------ interpret


def test_both_connected():
    s = interpret(snap(RIGHT, LEFT))
    assert s.right == Half(Link.CONNECTED, 76)
    assert s.left == Half(Link.CONNECTED, 82)


def test_order_of_characteristics_does_not_matter():
    s = interpret(snap(LEFT, RIGHT))
    assert s.right.battery == 76 and s.left.battery == 82


def test_left_zero_means_left_disconnected():
    # ZMK は左手が切断されるとプロキシ値を 0 にする
    s = interpret(snap(RIGHT, BatteryReading(0, "Peripheral 0")))
    assert s.right == Half(Link.CONNECTED, 76)
    assert s.left == Half(Link.DISCONNECTED)


def test_right_not_connected_to_pc_makes_left_unknown():
    s = interpret(snap(connected=False))
    assert s.right.link is Link.DISCONNECTED
    assert s.left.link is Link.UNKNOWN


def test_device_not_paired():
    s = interpret(snap(found=False))
    assert not s.found
    assert "見つかりません" in tooltip(s)
    assert "roBa が見つかりません" in menu_lines(s)[0]


def test_no_proxy_characteristic_means_left_unknown():
    s = interpret(snap(RIGHT))
    assert s.left.link is Link.UNKNOWN


def test_unreadable_right_battery_is_still_connected():
    s = interpret(snap(BatteryReading(None, ""), LEFT))
    assert s.right == Half(Link.CONNECTED, None)


def test_out_of_range_values_are_clamped():
    s = interpret(snap(BatteryReading(250, ""), LEFT))
    assert s.right.battery == 100


def test_cud_with_trailing_space():
    s = interpret(snap(RIGHT, BatteryReading(40, " Peripheral 0 ")))
    assert s.left.battery == 40


# ------------------------------------------------------------------ text


def test_tooltip_is_short_and_ordered_left_then_right():
    text = tooltip(interpret(snap(RIGHT, LEFT)))
    assert text == "roBa  左 82% / 右 76%"
    assert len(text) < 128


def test_menu_lines_mention_both_halves():
    lines = menu_lines(interpret(snap(RIGHT, BatteryReading(0, "Peripheral 0"))))
    assert lines[0] == "右手（PC と）: 接続中 76%"
    assert lines[1] == "左手（右手と）: 未接続"


def test_menu_lines_explain_right_disconnected():
    lines = menu_lines(interpret(snap(connected=False)))
    assert any("別の機器" in line for line in lines)


# ------------------------------------------------------------------ notifier


def status(left_level, right_connected=True, right_level=70):
    readings = (BatteryReading(right_level, ""), BatteryReading(left_level, "Peripheral 0"))
    return interpret(snap(*readings, connected=right_connected))


def test_no_notification_at_startup():
    n = Notifier()
    assert n.update(status(0)) == []


def test_left_disconnect_needs_two_polls():
    n = Notifier()
    n.update(status(80))
    assert n.update(status(0)) == []
    assert n.update(status(0)) == ["左手と右手の接続が切れました"]
    assert n.update(status(0)) == []


def test_single_glitch_is_ignored():
    n = Notifier()
    n.update(status(80))
    assert n.update(status(0)) == []
    assert n.update(status(80)) == []
    assert n.update(status(0)) == []  # カウントはやり直し


def test_left_reconnect():
    n = Notifier()
    n.update(status(0))
    n.update(status(80))
    assert n.update(status(80)) == ["左手が右手に再接続しました"]


def test_left_state_is_held_while_right_is_away_from_pc():
    n = Notifier()
    n.update(status(80))
    n.update(status(80, right_connected=False))
    assert n.update(status(80, right_connected=False)) == ["右手と PC の接続が切れました"]
    # 右手が戻ってきた時、左手は以前と同じく接続中なので左手の通知は出ない
    n.update(status(80))
    assert n.update(status(80)) == ["右手が PC に接続しました"]


def test_low_battery_warns_once_per_threshold():
    n = Notifier()
    assert n.update(status(25)) == []
    assert n.update(status(20)) == ["左手の電池が残り 20% です"]
    assert n.update(status(19)) == []
    assert n.update(status(10)) == ["左手の電池が残り 10% です"]
    assert n.update(status(9)) == []


def test_low_battery_at_startup_warns_lowest_threshold_once():
    n = Notifier()
    assert n.update(status(8)) == ["左手の電池が残り 8% です"]
    assert n.update(status(8)) == []


def test_low_battery_rearms_after_charging():
    n = Notifier()
    n.update(status(18))
    n.update(status(90))
    assert n.update(status(20)) == ["左手の電池が残り 20% です"]


# ------------------------------------------------------------------ icon


def test_icon_renders_every_state():
    states = [
        None,
        interpret(snap(found=False)),
        interpret(snap(connected=False)),
        interpret(snap(RIGHT, LEFT)),
        interpret(snap(BatteryReading(5, ""), BatteryReading(0, "Peripheral 0"))),
        interpret(snap(BatteryReading(None, ""), BatteryReading(100, "Peripheral 0"))),
    ]
    for st in states:
        img = render(st)
        assert img.size == (SIZE, SIZE)
        assert img.getbbox() is not None


def test_icon_left_bar_is_on_the_left():
    img = render(interpret(snap(BatteryReading(0, ""), BatteryReading(100, "Peripheral 0"))))
    green = (46, 160, 67, 255)
    xs = [x for x in range(SIZE) for y in range(SIZE) if img.getpixel((x, y)) == green]
    assert xs and max(xs) < SIZE // 2


# ------------------------------------------------------------------ log


def test_battery_log_writes_on_change_and_heartbeat(tmp_path):
    log = BatteryLog(tmp_path / "battery_log.csv")
    t0 = datetime(2026, 10, 28, 17, 0)
    s = interpret(snap(RIGHT, LEFT), at=t0)
    log.write(s)
    log.write(interpret(snap(RIGHT, LEFT), at=t0 + timedelta(minutes=1)))  # 変化なし→書かない
    log.write(interpret(snap(RIGHT, BatteryReading(81, "Peripheral 0")), at=t0 + timedelta(minutes=2)))
    log.write(interpret(snap(RIGHT, BatteryReading(81, "Peripheral 0")), at=t0 + timedelta(minutes=40)))
    rows = list(csv.reader((tmp_path / "battery_log.csv").open(encoding="utf-8")))
    assert rows[0] == BatteryLog.FIELDS
    assert [r[4] for r in rows[1:]] == ["82", "81", "81"]


# ------------------------------------------------------------------ app loop


class FakeBackend:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.polls = 0
        self.was_reset = False

    async def poll(self):
        self.polls += 1
        return self.snapshots[min(self.polls, len(self.snapshots)) - 1]

    def reset(self):
        self.was_reset = True


class FakeIcon:
    HAS_NOTIFICATION = True

    def __init__(self):
        self.icon = None
        self.title = ""
        self.notifications = []
        self.menu_updates = 0

    def update_menu(self):
        self.menu_updates += 1

    def notify(self, message, title):
        self.notifications.append(message)


def test_app_loop_updates_icon_and_notifies(tmp_path):
    backend = FakeBackend([
        snap(RIGHT, LEFT),
        snap(RIGHT, BatteryReading(0, "Peripheral 0")),
        snap(RIGHT, BatteryReading(0, "Peripheral 0")),
    ])
    app = MonitorApp(backend, tmp_path, interval=0.01)
    app.icon = FakeIcon()

    async def run():
        task = asyncio.create_task(app._poll_forever())
        while backend.polls < 3:
            await asyncio.sleep(0.01)
        app._stopping = True
        app._wake.set()
        await task

    asyncio.run(run())
    assert app.icon.notifications == ["左手と右手の接続が切れました"]
    assert app.icon.title == "roBa  左 未接続 / 右 76%"
    assert app.icon.menu_updates >= 3
    assert backend.was_reset
    assert (tmp_path / "battery_log.csv").exists()
