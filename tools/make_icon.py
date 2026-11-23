"""生成应用图标 ``assets/app.ico``（多尺寸：16/32/48/64/128/256）。

依赖说明：venv 内未安装 Pillow，本脚本只用已有的 PySide6（``QImage`` / ``QPainter``）
做矢量绘制，再手工拼装 ICO 容器（Vista+ 支持 PNG 条目），不引入额外依赖。

设计：深色圆角底（与界面主题 bg ``#14181d`` 一致）+ 青绿（``#17a673``）极简摄像头
机身，镜头内留深色圆与播放三角，呼应「视频信号」语义；小尺寸（16px 任务栏）下保留
机身矩形 + 镜头圆两个主体轮廓，保证可辨认。

用法（项目根目录下执行）：
    .venv\\Scripts\\python.exe tools\\make_icon.py

输出：``assets\\app.ico``
"""

from __future__ import annotations

import os
import struct
import sys

from PySide6.QtCore import QByteArray, QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF

#: ICO 内包含的边长（像素）
SIZES = [16, 32, 48, 64, 128, 256]

BG = "#14181d"          # 与主题底色一致
BORDER = "#262e38"      # 与主题边框色一致
ACCENT = "#17a673"      # 主题强调色（机身）
ACCENT_LIGHT = "#1cbd84"  # 强调色 hover（镜头环 / 三角）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "assets", "app.ico")


def render(size: int) -> QImage:
    """按 256px 设计稿等比缩放到目标边长绘制一帧。"""
    k = size / 256.0
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # --- 深色圆角底 + 细边框 ---
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(BG))
    p.drawRoundedRect(QRectF(8 * k, 8 * k, 240 * k, 240 * k), 52 * k, 52 * k)
    border_pen = QPen(QColor(BORDER))
    border_pen.setWidthF(max(1.0, 3 * k))
    p.setPen(border_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(8 * k, 8 * k, 240 * k, 240 * k), 52 * k, 52 * k)

    # --- 机顶取景器凸起 ---
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT))
    p.drawRoundedRect(QRectF(86 * k, 62 * k, 50 * k, 26 * k), 7 * k, 7 * k)

    # --- 摄像头机身 ---
    p.drawRoundedRect(QRectF(46 * k, 86 * k, 164 * k, 98 * k), 20 * k, 20 * k)

    # --- 镜头：深色内圆 + 青绿环 ---
    p.setBrush(QColor(BG))
    p.drawEllipse(QRectF(96 * k, 102 * k, 64 * k, 64 * k))
    ring_pen = QPen(QColor(ACCENT_LIGHT))
    ring_pen.setWidthF(max(1.5, 7 * k))
    p.setPen(ring_pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QRectF(96 * k, 102 * k, 64 * k, 64 * k))

    # --- 镜头内播放三角（视频信号）---
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(ACCENT_LIGHT))
    triangle = QPolygonF([
        QPointF(116 * k, 116 * k),
        QPointF(116 * k, 152 * k),
        QPointF(146 * k, 134 * k),
    ])
    p.drawPolygon(triangle)

    p.end()
    return img


def _png_bytes(img: QImage) -> bytes:
    """把 QImage 编码成 PNG 字节（ICO 条目使用 PNG 数据）。"""
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def _write_ico(images: list[QImage], path: str) -> None:
    """手工拼装多尺寸 ICO：ICONDIR + ICONDIRENTRY[] + PNG 数据。"""
    payload = [_png_bytes(img) for img in images]
    count = len(images)

    # ICONDIR：保留位=0，类型=1（图标），条目数
    header = struct.pack("<HHH", 0, 1, count)

    entries = b""
    offset = 6 + 16 * count
    for img, data in zip(images, payload):
        # 256px 在 ICO 目录里用 0 表示
        w = 0 if img.width() == 256 else img.width()
        h = 0 if img.height() == 256 else img.height()
        entries += struct.pack(
            "<BBBBHHII", w, h, 0, 0, 1, 32, len(data), offset
        )
        offset += len(data)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(header + entries + b"".join(payload))


def main() -> int:
    images = [render(size) for size in SIZES]
    _write_ico(images, OUT_PATH)
    print(f"已生成 {OUT_PATH}，包含尺寸：{', '.join(str(s) for s in SIZES)} px")
    return 0


if __name__ == "__main__":
    sys.exit(main())
