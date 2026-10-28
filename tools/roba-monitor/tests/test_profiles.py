from datetime import datetime

import pytest

from roba_monitor.app import ProfileStore
from roba_monitor.model import (
    BatteryReading,
    RawSnapshot,
    format_address,
    interpret,
    names_template,
    parse_names,
    parse_profiles,
    profile_lines,
)

PC = "11:22:33:44:55:66"
IPAD = "AA:BB:CC:DD:EE:FF"
AT = datetime(2026, 10, 28, 19, 30)


def entry(flags, address=None):
    raw = bytes.fromhex(address.replace(":", ""))[::-1] if address else bytes(6)
    return bytes([flags, 0]) + raw


def table_bytes(active=0, entries=None):
    entries = entries if entries is not None else [
        entry(0b11, PC), entry(0b01, IPAD), entry(0), entry(0), entry(0)]
    return bytes([1, len(entries), active]) + b"".join(entries)


# ------------------------------------------------------------------ 解析


def test_format_address_from_little_endian_bytes_and_int():
    assert format_address(bytes([0x66, 0x55, 0x44, 0x33, 0x22, 0x11])) == PC
    assert format_address(0x112233445566) == PC


def test_parse_profiles():
    table = parse_profiles(table_bytes(active=1), AT)
    assert table.active == 1
    assert [e.paired for e in table.entries] == [True, True, False, False, False]
    assert table.entries[0].connected and not table.entries[1].connected
    assert table.entries[0].address == PC
    assert table.entries[1].address == IPAD
    assert table.entries[2].address is None


@pytest.mark.parametrize("data", [b"", b"\x01\x05", bytes([2, 0, 0]), table_bytes()[:20]])
def test_parse_profiles_rejects_bad_data(data):
    with pytest.raises(ValueError):
        parse_profiles(data)


def test_parse_names():
    text = f"""
    # コメント
    {IPAD.lower()} = iPad  # 行末のコメント
    aa-bb-cc-00-00-01 = スマホ
    壊れた行
    # {PC} = 無効
    """
    assert parse_names(text) == {IPAD: "iPad", "AA:BB:CC:00:00:01": "スマホ"}


def test_names_template_lists_other_devices_commented_out():
    text = names_template(parse_profiles(table_bytes()), PC)
    assert f"# {IPAD} = （BT2 の端末の名前）" in text
    assert PC not in text
    assert parse_names(text) == {}  # 全部コメントなので、まだ何も有効にならない


# ------------------------------------------------------------------ 表示


def test_profile_lines_live():
    lines = profile_lines(parse_profiles(table_bytes(active=0), AT), {IPAD: "iPad"}, PC, live=True)
    assert lines == [
        "BT 設定（今の状態）",
        "  BT1: この PC（選択中・接続中）",
        "  BT2: iPad",
        "  BT3: 空き",
        "  BT4: 空き",
        "  BT5: 空き",
    ]


def test_profile_lines_unnamed_shows_address_and_cached_hides_connected():
    lines = profile_lines(parse_profiles(table_bytes(active=1), AT), {}, PC, live=False)
    assert lines[0] == "BT 設定（最後に読んだ 10/28 19:30 時点）"
    assert lines[1] == "  BT1: この PC"
    assert lines[2] == f"  BT2: {IPAD}（選択中）"


def test_profile_lines_without_table():
    assert profile_lines(None, {}, PC, live=False)[0].startswith("BT 設定: まだ読めていません")


# ------------------------------------------------------------------ 保存


def raw_with(profiles, connected=True):
    return RawSnapshot(found=True, host_connected=connected,
                       readings=(BatteryReading(90, ""),), profiles=profiles, this_pc=PC)


def test_store_saves_reloads_and_creates_names_file(tmp_path):
    store = ProfileStore(tmp_path)
    raw = raw_with(table_bytes())
    store.update(raw, interpret(raw, AT))
    assert store.live
    assert (tmp_path / "profiles.json").exists()
    assert IPAD in (tmp_path / "device_names.txt").read_text(encoding="utf-8")

    (tmp_path / "device_names.txt").write_text(f"{IPAD} = iPad\n", encoding="utf-8")
    again = ProfileStore(tmp_path)
    assert not again.live
    assert again.this_pc == PC
    assert again.lines()[0] == "BT 設定（最後に読んだ 10/28 19:30 時点）"
    assert "  BT2: iPad" in again.lines()


def test_store_keeps_last_table_when_on_another_device(tmp_path):
    store = ProfileStore(tmp_path)
    raw = raw_with(table_bytes())
    store.update(raw, interpret(raw, AT))
    away = raw_with(None, connected=False)
    store.update(away, interpret(away))
    assert not store.live
    assert store.table is not None and store.table.entries[1].address == IPAD


def test_store_does_not_overwrite_names_file(tmp_path):
    (tmp_path / "device_names.txt").write_text("# mine\n", encoding="utf-8")
    store = ProfileStore(tmp_path)
    raw = raw_with(table_bytes())
    store.update(raw, interpret(raw, AT))
    assert (tmp_path / "device_names.txt").read_text(encoding="utf-8") == "# mine\n"


def test_store_ignores_old_firmware(tmp_path):
    store = ProfileStore(tmp_path)
    raw = raw_with(None)
    store.update(raw, interpret(raw, AT))
    assert store.table is None and not (tmp_path / "profiles.json").exists()
