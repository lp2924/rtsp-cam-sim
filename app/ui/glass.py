"""Aurora Glass 自绘组件：背景画布、玻璃卡片、无边框标题栏按钮、品牌标记。

实现要点（红线：不使用 WA_TranslucentBackground 整窗透明）
----------------------------------------------------------
* :class:`AuroraCanvas` 是最底层自绘背景：竖向渐变 + 三团径向彩色光晕 + 暗角 +
  极淡噪点，全部缓存到一张 QPixmap，resize / 换主题时才重算。
  光晕是玻璃「透光」的唯一色彩来源——没有它，白 alpha 玻璃叠在灰黑底上只会得到
  一块脏灰色板子。
* :class:`GlassCard` 从画布缓存中截取自身几何对应的背景切片，做离屏高斯模糊后缓存，
  再叠玻璃填充 / 1px 玻璃边 / 顶部弧形高光 / 柔和投影。模糊只在尺寸变化或换主题时
  重算，paintEvent 不做实时模糊，避免拖动窗口卡顿。
* 所有绘制都在窗口内部完成，``widget.grab()`` 截出来的图就是真实观感。
"""

from __future__ import annotations

import random

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QGraphicsPixmapItem,
    QGraphicsScene, QPushButton, QWidget,
)

from .theme import RADIUS, colors, normalize_theme

BLUR_RADIUS = 22          # 背景模糊半径（px）
NOISE_DENSITY = 300       # 每多少像素一个噪点
NOISE_ALPHA = 0.030       # 噪点透明度（≤3%）


class AuroraCanvas(QWidget):
    """窗口底层背景画布：渐变 + 三团光晕 + 暗角 + 噪点，缓存为一张 Pixmap。"""

    def __init__(self, theme: str, parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self._pixmap = QPixmap()
        self.setAutoFillBackground(False)

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        self._rebuild()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild()

    # ------------------------------------------------------------------ #
    def _rebuild(self) -> None:
        t = colors(self._theme)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        base = QLinearGradient(0, 0, 0, h)
        base.setColorAt(0, QColor(t["canvas_top"]))
        base.setColorAt(1, QColor(t["canvas_bottom"]))
        p.fillRect(0, 0, w, h, base)

        span = max(w, h)
        self._paint_glow(p, w * 0.02, h * -0.04, span * 0.54,
                         t["glow1"], float(t["glow1_alpha"]))
        self._paint_glow(p, w * 1.02, h * 0.86, span * 0.52,
                         t["glow2"], float(t["glow2_alpha"]))
        self._paint_glow(p, w * 0.26, h * 1.04, span * 0.42,
                         t["glow3"], float(t["glow3_alpha"]))
        self._paint_vignette(p, w, h, t["shadow"], float(t["vignette_alpha"]))
        self._paint_noise(p, w, h)
        p.end()
        self._pixmap = pm

    def _paint_glow(self, p: QPainter, cx: float, cy: float, radius: float,
                    color: str, alpha: float) -> None:
        grad = QRadialGradient(QPointF(cx, cy), radius)
        inner = QColor(color)
        inner.setAlphaF(alpha)
        mid = QColor(color)
        mid.setAlphaF(alpha * 0.42)
        outer = QColor(color)
        outer.setAlphaF(0.0)
        grad.setColorAt(0, inner)
        grad.setColorAt(0.42, mid)
        grad.setColorAt(1, outer)
        p.fillRect(0, 0, self.width(), self.height(), grad)

    def _paint_vignette(self, p: QPainter, w: int, h: int, color: str, alpha: float) -> None:
        """四角压暗：让中间的内容区更「亮」，玻璃卡片才有浮起来的感觉。"""
        if alpha <= 0:
            return
        grad = QRadialGradient(QPointF(w * 0.5, h * 0.45), max(w, h) * 0.78)
        edge = QColor(color)
        edge.setAlphaF(alpha)
        mid = QColor(color)
        mid.setAlphaF(0.0)
        grad.setColorAt(0.0, mid)
        grad.setColorAt(0.62, mid)
        grad.setColorAt(1.0, edge)
        p.fillRect(0, 0, w, h, grad)

    def _paint_noise(self, p: QPainter, w: int, h: int) -> None:
        rng = random.Random(7)
        p.setPen(Qt.PenStyle.NoPen)
        dot = QColor(255, 255, 255) if self._theme == "dark" else QColor(15, 30, 50)
        dot.setAlphaF(NOISE_ALPHA)
        count = max(0, int(w * h / NOISE_DENSITY))
        for _ in range(count):
            p.fillRect(rng.randrange(w), rng.randrange(h), 1, 1, dot)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        if not self._pixmap.isNull():
            p.drawPixmap(0, 0, self._pixmap)
        p.end()

    def region(self, rect: QRect) -> QPixmap:
        """截取画布缓存中 ``rect``（画布坐标系）对应的一块背景。"""
        if self._pixmap.isNull():
            return QPixmap()
        r = rect.intersected(self._pixmap.rect())
        if r.isEmpty():
            return QPixmap()
        return self._pixmap.copy(r)


class GlassCard(QFrame):
    """玻璃卡片：模糊背景切片 + 玻璃填充 + 细边 + 顶部高光 + 柔和投影。"""

    def __init__(self, theme: str, parent=None, strong: bool = False):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self._strong = strong
        self._blur = QPixmap()
        self._canvas: AuroraCanvas | None = None
        self.setObjectName("GlassCard")

        t = colors(self._theme)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow_color = QColor(t["shadow"])
        shadow_color.setAlphaF(float(t["shadow_alpha"]))
        shadow.setColor(shadow_color)
        self.setGraphicsEffect(shadow)

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        t = colors(self._theme)
        effect = self.graphicsEffect()
        if isinstance(effect, QGraphicsDropShadowEffect):
            shadow_color = QColor(t["shadow"])
            shadow_color.setAlphaF(float(t["shadow_alpha"]))
            effect.setColor(shadow_color)
        self._blur = QPixmap()
        self.update()

    def invalidate_blur(self) -> None:
        """画布重绘后调用，清掉模糊切片缓存以便按新背景重算。"""
        self._blur = QPixmap()
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._blur = QPixmap()

    # ------------------------------------------------------------------ #
    def _find_canvas(self) -> AuroraCanvas | None:
        if self._canvas is None:
            node: QWidget | None = self.parentWidget()
            while node is not None and not isinstance(node, AuroraCanvas):
                node = node.parentWidget()
            self._canvas = node  # type: ignore[assignment]
        return self._canvas

    def _ensure_blur(self) -> None:
        if not self._blur.isNull():
            return
        pad = BLUR_RADIUS
        size = QSize(self.width() + 2 * pad, self.height() + 2 * pad)
        self._blur = QPixmap(size)
        self._blur.fill(Qt.GlobalColor.transparent)
        canvas = self._find_canvas()
        if canvas is None:
            return
        origin = self.mapTo(canvas, QPoint(0, 0))
        src_rect = QRect(origin.x() - pad, origin.y() - pad, size.width(), size.height())
        src = canvas.region(src_rect)
        if src.isNull():
            return

        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(src)
        effect = QGraphicsBlurEffect()
        effect.setBlurRadius(BLUR_RADIUS)
        item.setGraphicsEffect(effect)
        scene.addItem(item)
        painter = QPainter(self._blur)
        scene.render(painter)
        painter.end()

    def paintEvent(self, event) -> None:
        self._ensure_blur()
        t = colors(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        radius = RADIUS["card"]
        w, h = self.width(), self.height()

        path = QPainterPath()
        path.addRoundedRect(0, 0, w, h, radius, radius)
        p.setClipPath(path)

        pad = BLUR_RADIUS
        if not self._blur.isNull():
            p.drawPixmap(-pad, -pad, self._blur)

        # 玻璃填充：用主题的玻璃色调做半透明霜面（深色=墨蓝、浅色=白），
        # 顶部略实、底部略透，模拟光从上方穿过玻璃
        glass_alpha = float(t["glass_strong_alpha"] if self._strong else t["glass_alpha"])
        top = QColor(t["glass_tint"])
        top.setAlphaF(min(glass_alpha * 1.08, 0.96))
        bottom = QColor(t["glass_tint"])
        bottom.setAlphaF(glass_alpha * 0.88)
        fill = QLinearGradient(0, 0, 0, h)
        fill.setColorAt(0, top)
        fill.setColorAt(1, bottom)
        p.fillPath(path, fill)
        p.setClipping(False)

        # 1px 玻璃边
        border = QColor(t["border_rgb"])
        border.setAlphaF(float(t["border_alpha"]))
        pen = QPen(border)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)

        # 顶部高光：中间最亮、两端渐隐，模拟顶光打在玻璃边缘
        highlight = QLinearGradient(0, 0, w, 0)
        a = float(t["border_strong_alpha"]) * 0.9
        hl_mid = QColor(255, 255, 255)
        hl_mid.setAlphaF(a)
        hl_edge = QColor(255, 255, 255, 0)
        highlight.setColorAt(0.0, hl_edge)
        highlight.setColorAt(0.5, hl_mid)
        highlight.setColorAt(1.0, hl_edge)
        p.setPen(Qt.PenStyle.NoPen)
        p.fillRect(QRectF(1, 1, w - 2, 1), highlight)
        p.end()


class TitleBarButton(QPushButton):
    """无边框窗口标题栏的最小化 / 最大化还原 / 关闭图标按钮。"""

    KIND_MIN = "min"
    KIND_MAX = "max"
    KIND_RESTORE = "restore"
    KIND_CLOSE = "close"

    def __init__(self, kind: str, theme: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._theme = normalize_theme(theme)
        self.setFixedSize(40, 30)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip({"min": "最小化", "max": "最大化",
                         "restore": "还原", "close": "关闭"}.get(kind, ""))

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        self.update()

    def paintEvent(self, event) -> None:
        t = colors(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        hover = self.underMouse()
        icon_color = QColor(t["text"])
        if hover:
            if self._kind == self.KIND_CLOSE:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#E5484D"))
                p.drawRoundedRect(self.rect().adjusted(3, 2, -3, -2), 6, 6)
                icon_color = QColor("#FFFFFF")
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(255, 255, 255, 22))
                p.drawRoundedRect(self.rect().adjusted(3, 2, -3, -2), 6, 6)
                icon_color = QColor(t["text"])

        cx = self.width() / 2
        cy = self.height() / 2
        pen = QPen(icon_color)
        pen.setWidth(1.3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)

        if self._kind == self.KIND_MIN:
            p.drawLine(int(cx - 6), int(cy + 3), int(cx + 6), int(cy + 3))
        elif self._kind == self.KIND_MAX:
            p.drawRoundedRect(QRectF(cx - 5.5, cy - 5, 11, 10), 1.5, 1.5)
        elif self._kind == self.KIND_RESTORE:
            p.drawRoundedRect(QRectF(cx - 2.5, cy - 6, 10, 9), 1.5, 1.5)
            p.drawPolyline(QPolygonF([
                QPointF(cx - 5.5, cy - 2.5), QPointF(cx - 5.5, cy + 5), QPointF(cx + 3.5, cy + 5),
            ]))
        elif self._kind == self.KIND_CLOSE:
            p.drawLine(int(cx - 5), int(cy - 5), int(cx + 5), int(cy + 5))
            p.drawLine(int(cx + 5), int(cy - 5), int(cx - 5), int(cy + 5))
        p.end()


class LogoMark(QWidget):
    """品牌标记：圆角方块 + 渐变光圈 + 中心镜头点（纯自绘，无图片资源）。"""

    def __init__(self, theme: str, size: int = 34, parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self.setFixedSize(size, size)

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        self.update()

    def paintEvent(self, event) -> None:
        t = colors(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        side = min(self.width(), self.height())
        r = side * 0.30

        # 方块本体：强调色 → 青色斜向渐变
        grad = QLinearGradient(0, 0, side, side)
        grad.setColorAt(0.0, QColor(t["accent"]))
        grad.setColorAt(1.0, QColor(t["glow2"]))
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, side, side), r, r)
        p.fillPath(path, grad)

        # 镜头：外环 + 中心点
        cx = cy = side / 2
        p.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 255, 255, 210))
        pen.setWidth(max(1.4, side * 0.045))
        p.setPen(pen)
        p.drawEllipse(QPointF(cx, cy), side * 0.26, side * 0.26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawEllipse(QPointF(cx, cy), side * 0.095, side * 0.095)
        p.end()
