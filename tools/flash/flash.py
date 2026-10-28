"""roBa のファームウェアを GitHub Actions から取得して、XIAO に書き込む。

使い方（flash.cmd 経由）:
    flash.cmd                     対話式（左右どちらに書くかを聞く）
    flash.cmd both                右手 → 左手の順に書き込む
    flash.cmd right / left        片方だけ書き込む
    flash.cmd both --branch keymap/keyball44-port
    flash.cmd --list              最近の成功ビルドを表示する
    flash.cmd both --settings-reset   ペアリング情報を消す（確認あり）

Python の標準ライブラリと gh コマンドだけで動く。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DATA_DIR = HERE / "data" / "firmware"

WORKFLOW = "build.yml"
ARTIFACT = "firmware"
DEFAULT_BRANCH = "main"

SIDE_LABEL = {"right": "右手", "left": "左手"}
# 書き込む順番。右手（セントラル）を先にする
SIDE_ORDER = ("right", "left")


@dataclass
class Build:
    run_id: int
    branch: str
    sha: str
    title: str
    created: str

    def describe(self) -> str:
        return f"#{self.run_id}  {self.created[:16].replace('T', ' ')} UTC  {self.branch}@{self.sha[:7]}  {self.title}"


# ---------------------------------------------------------------- 純粋なロジック


def uf2_name(side: str, settings_reset: bool) -> str:
    """成果物の中で、その側に書き込む UF2 のファイル名の頭。"""
    if settings_reset:
        return "settings_reset-"
    return {"right": "roBa_R-", "left": "roBa_L-"}[side]


def find_uf2(files: list[Path], side: str, settings_reset: bool) -> Path:
    prefix = uf2_name(side, settings_reset)
    hits = [f for f in files if f.name.startswith(prefix) and f.suffix.lower() == ".uf2"]
    if len(hits) != 1:
        names = ", ".join(sorted(f.name for f in files)) or "(なし)"
        raise FlashError(f"{prefix}*.uf2 が1つに定まりません。成果物の中身: {names}")
    return hits[0]


def sides_for(target: str) -> list[str]:
    if target == "both":
        return list(SIDE_ORDER)
    return [target]


def is_xiao_nrf52840(info_text: str) -> bool:
    return "nrf52840" in info_text.lower()


def new_drives(before: set[str], now: set[str]) -> list[str]:
    return sorted(now - before)


def parse_builds(raw: str) -> list[Build]:
    return [
        Build(
            run_id=int(r["databaseId"]),
            branch=r["headBranch"],
            sha=r["headSha"],
            title=r["displayTitle"],
            created=r["createdAt"],
        )
        for r in json.loads(raw)
    ]


class FlashError(Exception):
    pass


# ---------------------------------------------------------------- gh / git


def run(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        raise FlashError(f"{cmd[0]} コマンドが見つかりません。")
    if p.returncode != 0:
        raise FlashError(f"{' '.join(cmd)} が失敗しました:\n{p.stderr.strip()}")
    return p.stdout


def current_branch() -> str:
    try:
        name = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    except FlashError:
        return DEFAULT_BRANCH
    return DEFAULT_BRANCH if name in ("", "HEAD") else name


def list_builds(branch: str | None, limit: int) -> list[Build]:
    cmd = ["gh", "run", "list", "--workflow", WORKFLOW, "--status", "success",
           "-L", str(limit), "--json", "databaseId,headBranch,headSha,displayTitle,createdAt"]
    if branch:
        cmd += ["--branch", branch]
    return parse_builds(run(cmd))


def warn_if_newer(branch: str, build: Build) -> None:
    """成功ビルドより新しいビルドが、実行中または失敗していたら知らせる。"""
    raw = run(["gh", "run", "list", "--workflow", WORKFLOW, "--branch", branch, "-L", "1",
               "--json", "databaseId,status,conclusion"])
    latest = json.loads(raw)
    if not latest or int(latest[0]["databaseId"]) == build.run_id:
        return
    status, conclusion = latest[0]["status"], latest[0]["conclusion"]
    if status != "completed":
        print(f"[注意] もっと新しいビルド #{latest[0]['databaseId']} が実行中です。"
              "それを使うなら、終わってからやり直してください。")
    elif conclusion != "success":
        print(f"[注意] 最新のビルド #{latest[0]['databaseId']} は失敗しています（{conclusion}）。"
              "1つ前の成功ビルドを使います。")


def get_build(run_id: int) -> Build:
    raw = run(["gh", "run", "view", str(run_id), "--json",
               "databaseId,headBranch,headSha,displayTitle,createdAt,conclusion"])
    data = json.loads(raw)
    if data.get("conclusion") != "success":
        raise FlashError(f"ビルド #{run_id} は成功していません（{data.get('conclusion')}）。")
    return parse_builds(json.dumps([data]))[0]


def download(build: Build) -> list[Path]:
    """成果物を data/firmware/<run_id>/ に取ってくる。取得済みならそれを使う。"""
    dest = DATA_DIR / str(build.run_id)
    files = list(dest.glob("*.uf2")) if dest.exists() else []
    if files:
        print(f"取得済みのファームウェアを使います: {dest}")
        return files
    tmp = dest.with_name(dest.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    print("ファームウェアをダウンロードしています...")
    run(["gh", "run", "download", str(build.run_id), "-n", ARTIFACT, "-D", str(tmp)])
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return list(dest.glob("*.uf2"))


# ---------------------------------------------------------------- ドライブ


def uf2_drives() -> set[str]:
    """INFO_UF2.TXT がある、取り外し可能ドライブ（例: 'E:\\'）の集合。"""
    import ctypes

    k32 = ctypes.windll.kernel32
    # 空のカードリーダーなどで「ディスクを挿入してください」が出ないようにする
    old = k32.SetErrorMode(0x0001)  # SEM_FAILCRITICALERRORS
    try:
        mask = k32.GetLogicalDrives()
        found = set()
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = f"{chr(65 + i)}:\\"
            if k32.GetDriveTypeW(root) != 2:  # DRIVE_REMOVABLE
                continue
            if os.path.isfile(root + "INFO_UF2.TXT"):
                found.add(root)
        return found
    finally:
        k32.SetErrorMode(old)


def wait_for(cond, timeout: float, interval: float = 0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = cond()
        if value:
            return value
        time.sleep(interval)
    return None


def wait_new_drive(side: str, timeout: float) -> str:
    label = SIDE_LABEL[side]
    present = uf2_drives()
    if len(present) == 1:
        drive = next(iter(present))
        print(f"\nブートローダーのドライブ {drive} がすでに出ています。")
        if ask(f"これは{label}ですか？ (y/N): "):
            return drive
    elif len(present) > 1:
        raise FlashError(f"ブートローダーのドライブが複数あります（{', '.join(sorted(present))}）。"
                         "1つだけ接続した状態でやり直してください。")

    print(f"\n{label}を USB で PC につなぎ、ブートローダーに入れてください。")
    print("  ・XIAO のリセットボタンを素早く2回押す")
    print("  ・または SYS レイヤーの bootloader キーを押す（PR #2 以降のキーマップ）")
    print(f"ドライブが現れるのを待っています（最大 {int(timeout)} 秒、中止は Ctrl+C）...")
    hit = wait_for(lambda: new_drives(present, uf2_drives()), timeout)
    if not hit:
        raise FlashError("ドライブが現れませんでした。")
    if len(hit) > 1:
        raise FlashError(f"ドライブが同時に複数現れました（{', '.join(hit)}）。")
    return hit[0]


def flash_one(side: str, uf2: Path, timeout: float) -> None:
    drive = wait_new_drive(side, timeout)
    info = Path(drive, "INFO_UF2.TXT").read_text(encoding="utf-8", errors="replace")
    if not is_xiao_nrf52840(info):
        print(info)
        if not ask(f"{drive} は XIAO nRF52840 ではないようです。それでも書き込みますか？ (y/N): "):
            raise FlashError("中止しました。")

    print(f"{SIDE_LABEL[side]}に書き込みます: {uf2.name} → {drive}")
    try:
        shutil.copyfile(uf2, Path(drive, uf2.name))
    except OSError as e:
        # 書き込み完了と同時に XIAO が再起動して、ドライブが消えることがある
        if os.path.exists(drive):
            raise FlashError(f"コピーに失敗しました: {e}")
    if wait_for(lambda: not os.path.exists(drive + "INFO_UF2.TXT"), 30) is None:
        raise FlashError(f"{drive} が消えません。書き込みに失敗した可能性があります。")
    print(f"{SIDE_LABEL[side]}の書き込みが終わりました（XIAO が再起動しました）。")


# ---------------------------------------------------------------- 入出力


def read_answer(prompt: str) -> str:
    """入力を読む。日本語入力の全角（２、ｙ）や BOM が混ざっても通るようにする。"""
    try:
        text = input(prompt)
    except EOFError:
        return ""
    return unicodedata.normalize("NFKC", text).strip().lstrip("﻿").lower()


def ask(prompt: str) -> bool:
    return read_answer(prompt) in ("y", "yes")


def choose_target() -> str:
    print("どちらに書き込みますか？")
    print("  1) 両方（右手 → 左手）")
    print("  2) 右手だけ")
    print("  3) 左手だけ")
    choice = read_answer("番号を入力して Enter: ")
    target = {"1": "both", "2": "right", "3": "left"}.get(choice)
    if not target:
        raise FlashError("中止しました。")
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flash", description="roBa のファームウェアを書き込む")
    ap.add_argument("target", nargs="?", choices=["both", "right", "left"],
                    help="書き込む側（省略すると聞きます）")
    ap.add_argument("--branch", help="このブランチの最新の成功ビルドを使う（既定: 今のブランチ）")
    ap.add_argument("--run", type=int, help="ビルド番号（run ID）を直接指定する")
    ap.add_argument("--list", action="store_true", help="最近の成功ビルドを表示して終わる")
    ap.add_argument("--download-only", action="store_true", help="ダウンロードだけして終わる")
    ap.add_argument("--settings-reset", action="store_true",
                    help="settings_reset を書き込む（ペアリング情報が消えます）")
    ap.add_argument("--timeout", type=float, default=180, help="ドライブを待つ秒数（既定 180）")
    args = ap.parse_args(argv)
    # メッセージとエラーが出た順に表示されるようにする
    sys.stdout.reconfigure(line_buffering=True)

    try:
        if args.list:
            for b in list_builds(args.branch, 10):
                print(b.describe())
            return 0

        if args.run:
            build = get_build(args.run)
        else:
            branch = args.branch or current_branch()
            builds = list_builds(branch, 1)
            if not builds and not args.branch and branch != DEFAULT_BRANCH:
                print(f"ブランチ {branch} にはビルドがないので、{DEFAULT_BRANCH} のビルドを使います。")
                branch = DEFAULT_BRANCH
                builds = list_builds(branch, 1)
            if not builds:
                raise FlashError(f"ブランチ {branch} に成功したビルドがありません。")
            build = builds[0]
            warn_if_newer(branch, build)
        print(f"使うビルド: {build.describe()}")
        files = download(build)

        if args.download_only:
            for f in sorted(files):
                print(f"  {f}")
            return 0

        target = args.target or choose_target()
        plan = [(side, find_uf2(files, side, args.settings_reset)) for side in sides_for(target)]

        if args.settings_reset:
            print("\n[注意] settings_reset を書き込むと、ペアリング情報が消えます。")
            print("       あとで通常のファームウェアを書き直し、左右と PC のペアリングをやり直す必要があります。")
            if not ask("本当に続けますか？ (y/N): "):
                raise FlashError("中止しました。")

        for side, uf2 in plan:
            flash_one(side, uf2, args.timeout)
        print("\nすべて完了しました。")
        return 0
    except FlashError as e:
        print(f"\n[エラー] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中止しました。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
