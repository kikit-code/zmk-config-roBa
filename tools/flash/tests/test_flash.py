import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flash  # noqa: E402

FILES = [
    Path("roBa_L-seeeduino_xiao_ble-zmk.uf2"),
    Path("roBa_R-seeeduino_xiao_ble-zmk.uf2"),
    Path("settings_reset-seeeduino_xiao_ble-zmk.uf2"),
]


def test_find_uf2_picks_each_side():
    assert flash.find_uf2(FILES, "right", False).name.startswith("roBa_R-")
    assert flash.find_uf2(FILES, "left", False).name.startswith("roBa_L-")


def test_find_uf2_settings_reset_is_same_for_both_sides():
    assert flash.find_uf2(FILES, "right", True).name.startswith("settings_reset-")
    assert flash.find_uf2(FILES, "left", True).name.startswith("settings_reset-")


def test_find_uf2_missing_raises():
    with pytest.raises(flash.FlashError):
        flash.find_uf2(FILES[:1], "right", False)


def test_sides_for_both_is_right_then_left():
    assert flash.sides_for("both") == ["right", "left"]
    assert flash.sides_for("left") == ["left"]


def test_is_xiao_nrf52840():
    info = "UF2 Bootloader 0.6.0\nModel: Seeed XIAO nRF52840\nBoard-ID: nRF52840-SeeedXiao-v1\n"
    assert flash.is_xiao_nrf52840(info)
    assert not flash.is_xiao_nrf52840("Model: Raspberry Pi RP2\nBoard-ID: RPI-RP2\n")


def test_new_drives():
    assert flash.new_drives({"E:\\"}, {"E:\\", "F:\\"}) == ["F:\\"]
    assert flash.new_drives({"E:\\"}, {"E:\\"}) == []


@pytest.mark.parametrize("typed, expected", [("2", "2"), ("２", "2"), ("﻿2\n", "2"), ("Ｙ", "y")])
def test_read_answer_normalizes(monkeypatch, typed, expected):
    monkeypatch.setattr("builtins.input", lambda prompt: typed)
    assert flash.read_answer("") == expected


def test_flash_one_copies_and_waits_for_reboot(tmp_path, monkeypatch):
    """ドライブが現れる → コピー → XIAO が再起動してドライブが消える、の流れ。"""
    drive = str(tmp_path) + "\\"
    info = tmp_path / "INFO_UF2.TXT"
    info.write_text("Model: Seeed XIAO nRF52840\n")
    uf2 = tmp_path.parent / "roBa_R-seeeduino_xiao_ble-zmk.uf2"
    uf2.write_bytes(b"UF2\n" * 10)

    appeared = iter([set(), {drive}])
    monkeypatch.setattr(flash, "uf2_drives", lambda: next(appeared, {drive}))

    def fake_copy(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes())
        info.unlink()  # 書き込みが終わると XIAO が再起動する

    monkeypatch.setattr(flash.shutil, "copyfile", fake_copy)
    monkeypatch.setattr(flash.time, "sleep", lambda s: None)
    flash.flash_one("right", uf2, timeout=5)
    assert (tmp_path / uf2.name).read_bytes() == uf2.read_bytes()


def test_parse_builds():
    raw = json.dumps([{
        "databaseId": 40660801334, "headBranch": "main", "headSha": "ee0fa4e1234",
        "displayTitle": "Merge pull request #1", "createdAt": "2026-10-28T08:59:42Z",
    }])
    [b] = flash.parse_builds(raw)
    assert b.run_id == 40660801334
    assert "main@ee0fa4e" in b.describe()
    assert "2026-10-28 08:59" in b.describe()
