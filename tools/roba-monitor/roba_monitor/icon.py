"""タスクトレイ用アイコンの描画.

左に左手、右に右手の電池を縦棒で描く（キーボードの並びと同じ向き）。
16px に縮小されても読めるよう、細部は描かず色と高さだけで伝える。
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .model import Half, Link, Status

SIZE = 64

GREEN = (46, 160, 67, 255)
AMBER = (219, 154, 4, 255)
RED = (207, 34, 46, 255)
GRAY = (140, 140, 140, 255)
OUTLINE = (235, 235, 235, 255)
SHADOW = (30, 30, 30, 255)


def _color(level: int) -> tuple[int, int, int, int]:
    if level > 50:
        return GREEN
    if level > 20:
        return AMBER
    return RED


def _draw_half(draw: ImageDraw.ImageDraw, x0: int, half: Half) -> None:
    w, top, bottom = 26, 4, SIZE - 3
    x1 = x0 + w
    # 縮小時に輪郭が背景に溶けないよう、暗い縁取りの上に明るい枠を重ねる
    draw.rectangle((x0 - 1, top - 1, x1 + 1, bottom + 1), outline=SHADOW, width=2)
    draw.rectangle((x0, top, x1, bottom), outline=OUTLINE, width=3)

    inner = (x0 + 5, top + 5, x1 - 5, bottom - 5)
    if half.link is Link.CONNECTED and half.battery is not None:
        level = max(0, min(100, half.battery))
        height = inner[3] - inner[1]
        fill_top = inner[3] - round(height * max(level, 10) / 100)
        draw.rectangle((inner[0], fill_top, inner[2], inner[3]), fill=_color(level))
    elif half.link is Link.CONNECTED:
        # 接続しているが残量が読めない
        draw.rectangle(inner, fill=GRAY)
    elif half.link is Link.DISCONNECTED:
        draw.line((inner[0], inner[1], inner[2], inner[3]), fill=RED, width=8)
        draw.line((inner[0], inner[3], inner[2], inner[1]), fill=RED, width=8)
    else:  # UNKNOWN
        mid = (inner[1] + inner[3]) // 2
        draw.rectangle((inner[0], mid - 4, inner[2], mid + 4), fill=GRAY)


def render(status: Status | None) -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unknown = Half(Link.UNKNOWN)
    if status is None or not status.found:
        left = right = unknown
    else:
        left, right = status.left, status.right
    _draw_half(draw, 3, left)
    _draw_half(draw, SIZE - 3 - 26, right)
    return image
