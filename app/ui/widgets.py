"""对话框与面板共用的小组件：分组卡片、状态胶囊、键值行、地址行、分隔线。

这些组件只负责「排版骨架」，不承载业务：所有文案、状态色由调用方传入。
自绘组件（StatusPill / AccentBar）跟随主题实时重绘，不依赖 QSS 重载。
"""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout,
    QWidget,
)

from .theme import MONO_FAMILY, RADIUS, blend, colors, normalize_theme


class Divider(QFrame):
    """1px 分隔线：水平用于分节，垂直用于命令分组之间。"""

    def __init__(self, theme: str, vertical: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Divider")
        if vertical:
            self.setFixedWidth(1)
            self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        else:
            self.setFixedHeight(1)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.set_theme(theme)

    def set_theme(self, theme: str) -> None:
        self.setStyleSheet(f"background: {colors(theme)['border']}; border: none;")


class AccentBar(QWidget):
    """4×14 的强调色小竖条，用在区块标题左侧标出层级。"""

    def __init__(self, theme: str, parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self.setFixedSize(3, 13)

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        grad = QColor(colors(self._theme)["accent"])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()), 1.5, 1.5)
        p.end()


class StatusPill(QWidget):
    """状态胶囊：小圆点 + 文字，底色与边框由状态色淡化而来（自绘，随主题刷新）。"""

    def __init__(self, theme: str, parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self._text = ""
        self._color = "#888888"
        self._font = QFont()
        self._font.setPixelSize(12)
        self._font.setWeight(QFont.Weight.DemiBold)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        self.update()

    def set_status(self, text: str, color: str) -> None:
        changed = (text != self._text) or (color != self._color)
        self._text = text
        self._color = color
        if changed:
            self.updateGeometry()
            self.update()

    def sizeHint(self) -> QSize:
        metrics = QFontMetrics(self._font)
        width = metrics.horizontalAdvance(self._text) + 34
        return QSize(max(width, 84), 28)

    def paintEvent(self, event) -> None:
        t = colors(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = rect.height() / 2

        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        p.fillPath(path, QColor(blend(self._color, 0.13, str(t["surface"]))))
        pen = QPen(QColor(blend(self._color, 0.32, str(t["surface"]))))
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # 状态点
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self._color))
        cx, cy = 13.0, self.height() / 2
        p.drawEllipse(QPointF(cx, cy), 3.3, 3.3)
        # 光晕
        halo = QColor(self._color)
        halo.setAlphaF(0.22)
        p.setBrush(halo)
        p.drawEllipse(QPointF(cx, cy), 6.5, 6.5)

        p.setFont(self._font)
        p.setPen(QColor(self._color))
        p.drawText(rect.adjusted(24, 0, -10, 0),
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._text)
        p.end()


class SectionCard(QFrame):
    """对话框里的参数分组：标题条 + 两列字段网格 + 可选脚注。

    字段布局统一为「固定宽标签列 + 伸展内容列」，标签左对齐：右对齐标签会让每行
    起点参差不齐，表单看起来像没对齐。
    """

    LABEL_WIDTH = 78

    def __init__(self, title: str, hint: str = "", theme: str = "dark", parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self.setObjectName("Section")
        self._grid_rows = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._bar = AccentBar(self._theme)
        head.addWidget(self._bar)
        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        head.addWidget(title_label)
        head.addStretch(1)
        outer.addLayout(head)

        # 说明单独占一行：挤在标题右边会折成两行，看起来像没排完的标题
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("SectionHint")
            hint_label.setWordWrap(True)
            hint_label.setContentsMargins(11, 0, 0, 0)
            outer.addWidget(hint_label)

        self._grid = QGridLayout()
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(10)
        self._grid.setColumnMinimumWidth(0, self.LABEL_WIDTH)
        self._grid.setColumnStretch(1, 1)
        outer.addSpacing(4)
        outer.addLayout(self._grid)

        self._footer = QLabel()
        self._footer.setObjectName("SectionHint")
        self._footer.setWordWrap(True)
        self._footer.setVisible(False)
        outer.addWidget(self._footer)

    def set_theme(self, theme: str) -> None:
        self._theme = normalize_theme(theme)
        self._bar.set_theme(theme)

    def add_field(self, label: str, widget: QWidget) -> QWidget:
        """加一行「标签 + 控件」，返回控件本身方便调用方继续配置。"""
        row = self._grid_rows
        if label:
            tag = QLabel(label)
            tag.setObjectName("KvLabel")
            tag.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            tag.setFixedWidth(self.LABEL_WIDTH)
            self._grid.addWidget(tag, row, 0)
            self._grid.addWidget(widget, row, 1)
        else:
            self._grid.addWidget(widget, row, 0, 1, 2)
        self._grid_rows += 1
        return widget

    def add_full(self, widget: QWidget) -> QWidget:
        """整行通栏（不分标签列）。"""
        self._grid.addWidget(widget, self._grid_rows, 0, 1, 2)
        self._grid_rows += 1
        return widget

    def add_footer(self, text: str) -> None:
        self._footer.setText(text)
        self._footer.setVisible(bool(text))

    def set_footer(self, text: str) -> None:
        self.add_footer(text)


class KeyValue(QWidget):
    """键值行：小号灰标签在上、值在下（用于详情栏这种窄栏信息）。"""

    def __init__(self, key: str, theme: str = "dark", mono: bool = False, parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.key_label = QLabel(key)
        self.key_label.setObjectName("KvLabel")
        layout.addWidget(self.key_label)

        self.value_label = QLabel("—")
        self.value_label.setObjectName("KvValue")
        self.value_label.setWordWrap(True)
        if mono:
            self.value_label.setStyleSheet(f'font-family: {MONO_FAMILY}; font-size: 12px;')
        layout.addWidget(self.value_label)

    def set_value(self, text: str) -> None:
        if self.value_label.text() != text:
            self.value_label.setText(text)


class UrlRow(QFrame):
    """一条对外地址：路径 + 说明 + 复制按钮。

    同一路流的地址共享同一个 ``rtsp://ip:port`` 前缀，所以行内只显示**路径**（这是各条
    地址真正的差异所在），完整地址放到 tooltip 里，复制按钮永远复制完整地址。
    """

    def __init__(self, theme: str, url: str = "", path: str = "", caption: str = "",
                 on_copy: Optional[callable] = None, parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self._url = url
        self._path = path
        self.setObjectName("UrlRow")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 8, 7)
        layout.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.path_label = QLabel(path)
        self.path_label.setObjectName("UrlText")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Ignored：宽度完全由布局决定，长路径才不会把行撑宽
        self.path_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.path_label.setMinimumWidth(60)
        self.path_label.setToolTip(url)
        top.addWidget(self.path_label, 1)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("Tiny")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setFixedWidth(46)
        self.copy_btn.setToolTip(f"复制 {url}")
        if on_copy is not None:
            self.copy_btn.clicked.connect(lambda: on_copy(self._url))
        top.addWidget(self.copy_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        self.caption_label = QLabel(caption)
        self.caption_label.setObjectName("UrlCaption")
        layout.addWidget(self.caption_label)

    def _apply_elide(self) -> None:
        fm = QFontMetrics(self.path_label.font())
        width = max(self.path_label.width(), 60)
        self.path_label.setText(
            fm.elidedText(self._path, Qt.TextElideMode.ElideRight, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elide()

    def set_theme(self, theme: str) -> None:
        """地址行本身不写死颜色（交给 QSS），换主题只需重绘。"""
        self._theme = normalize_theme(theme)
        self._apply_elide()
        self.update()

    def set_content(self, url: str, path: str, caption: str) -> None:
        self._url = url
        self._path = path
        self.path_label.setToolTip(url)
        self.copy_btn.setToolTip(f"复制 {url}")
        self._apply_elide()
        if self.caption_label.text() != caption:
            self.caption_label.setText(caption)


class EmptyState(QWidget):
    """空状态：一行主文案 + 一行下一步指引。"""

    def __init__(self, title: str, hint: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 32)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("EmptyTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("Hint")
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def set_text(self, title: str, hint: str) -> None:
        self.title_label.setText(title)
        self.hint_label.setText(hint)


class ThemeSwitch(QWidget):
    """深浅色主题开关：太阳 / 月亮两枚图标 + 会滑动的滑块。

    交互与外观约定
    --------------
    * 左端月亮 = 深色，右端太阳 = 浅色；点整块都能切，不要求精确点到图标。
    * 滑块位置用 150ms 缓动动画过渡；外部（设置对话框）改主题时用 ``set_theme``
      直接落位、不放动画，避免两处控件互相拉扯。
    * 键盘可达：Tab 能聚焦，空格 / 回车切换，聚焦时画一圈强调色焦点环。
    * 图标纯 QPainter 绘制（不依赖图片资源），颜色随主题实时变化。
    """

    themeChanged = Signal(str)          # "dark" / "light"

    TRACK_W = 76
    TRACK_H = 30
    _PAD = 3
    _DURATION = 150

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self._theme = normalize_theme(theme)
        self._pos = 0.0                  # 0 = 深色（左），1 = 浅色（右）
        self._hover = False
        self._ring = False               # 只有键盘 Tab 进来才画焦点环

        self.setFixedSize(self.TRACK_W, self.TRACK_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setToolTip("切换深色 / 浅色主题")
        self.setAccessibleName("切换深色或浅色主题")

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(self._DURATION)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim_tick)
        self._pos = 0.0 if self._theme == "dark" else 1.0

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    @property
    def theme(self) -> str:
        return self._theme

    def set_theme(self, theme: str, animate: bool = True) -> None:
        """同步主题：外部改主题时调用；animate=False 时直接落位。"""
        name = normalize_theme(theme)
        target = 0.0 if name == "dark" else 1.0
        self._theme = name
        if not animate or abs(self._pos - target) < 0.01:
            self._anim.stop()
            self._pos = target
            self.update()
            return
        self._anim.stop()
        self._anim.setStartValue(float(self._pos))
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim_tick(self, value) -> None:
        self._pos = float(value)
        self.update()

    def toggle(self) -> None:
        new_theme = "light" if self._theme == "dark" else "dark"
        self.set_theme(new_theme, animate=True)
        self.themeChanged.emit(new_theme)

    # ------------------------------------------------------------------ #
    # 事件
    # ------------------------------------------------------------------ #
    def focusInEvent(self, event) -> None:
        # 鼠标点击也会给焦点，但那时画环会显得像「选中态」；只有键盘导航才提示
        reason = event.reason()
        self._ring = reason in (Qt.FocusReason.TabFocusReason,
                                Qt.FocusReason.BacktabFocusReason,
                                Qt.FocusReason.ShortcutFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._ring = False
        self.update()
        super().focusOutEvent(event)

    def mousePressEvent(self, event) -> None:
        self._ring = False
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.toggle()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:
        return QSize(self.TRACK_W, self.TRACK_H)

    # ------------------------------------------------------------------ #
    # 绘制
    # ------------------------------------------------------------------ #
    def _draw_moon(self, p: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
        """弯月：大圆裁掉一枚偏移圆，比画弧线稳定。"""
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), r, r)
        bite = QPainterPath()
        bite.addEllipse(QPointF(cx + r * 0.62, cy - r * 0.42), r * 0.92, r * 0.92)
        crescent = path.subtracted(bite)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(crescent)

    def _draw_sun(self, p: QPainter, cx: float, cy: float, r: float, color: QColor) -> None:
        """实心小圆核 + 8 根短射线：核太大、射线太长会糊成一颗「齿轮」。

        换主题时镂空 / 实心两种画法差别很大，这里固定用实心核，靠射线长度控制体量。
        """
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(cx, cy), r * 0.33, r * 0.33)
        pen = QPen(color)
        pen.setWidthF(1.25)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for index in range(8):
            rad = math.radians(index * 45.0)
            inner = r * 0.62
            outer = r * 1.04
            p.drawLine(
                QPointF(cx + inner * math.cos(rad), cy + inner * math.sin(rad)),
                QPointF(cx + outer * math.cos(rad), cy + outer * math.sin(rad)),
            )

    def paintEvent(self, event) -> None:
        t = colors(self._theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        radius = h / 2.0
        track = QRectF(0.5, 0.5, w - 1, h - 1)

        # 轨道：比控件底浅一点点的下沉槽，hover / 聚焦时略微提亮
        track_fill = blend(t["text"], 0.075 if self._hover else 0.055, t["surface"])
        path = QPainterPath()
        path.addRoundedRect(track, radius, radius)
        p.fillPath(path, QColor(track_fill))
        border = QColor(t["text"])
        border.setAlphaF(0.12 if not self._hover else 0.2)
        pen = QPen(border)
        pen.setWidth(1)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

        # 滑块：两端各留 3px，直径 = 高 - 6
        knob_d = h - 2 * self._PAD
        x0 = self._PAD
        x1 = w - self._PAD - knob_d
        knob_x = x0 + (x1 - x0) * self._pos
        knob_rect = QRectF(knob_x, self._PAD, knob_d, knob_d)

        knob_color = QColor("#FFFFFF") if self._theme == "light" else QColor(
            blend(t["text"], 0.22, t["surface"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(knob_color)
        p.drawEllipse(knob_rect)
        # 顶部弧形高光 + 1px 边：让圆钮看起来是凸起的实体
        shine = QColor(255, 255, 255)
        shine.setAlphaF(0.22 if self._theme == "dark" else 0.9)
        p.setPen(QPen(shine, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(knob_rect.adjusted(1.5, 1.5, -1.5, -1.5), 30 * 16, 120 * 16)
        knob_edge = QColor(t["text"])
        knob_edge.setAlphaF(0.12)
        p.setPen(QPen(knob_edge, 1))
        p.drawEllipse(knob_rect.adjusted(0.5, 0.5, -0.5, -0.5))

        # 两枚图标：被滑块盖住的那端用正文色，另一端用 muted
        icon_color_active = QColor(t["text"])
        icon_color_idle = QColor(t["muted"])
        if self._theme == "light" and self._pos > 0.5:
            icon_color_active = QColor("#1B2333")   # 白滑块上不能再用浅色图标
        cy = h / 2.0
        left_cx = x0 + knob_d / 2.0
        right_cx = x1 + knob_d / 2.0
        dark_active = self._pos < 0.5
        self._draw_moon(p, left_cx, cy, 6.2,
                        icon_color_active if dark_active else icon_color_idle)
        self._draw_sun(p, right_cx, cy, 6.0,
                       icon_color_idle if dark_active else icon_color_active)

        # 焦点环：键盘 Tab 到这里才需要提示
        if self._ring and self.hasFocus():
            ring = QColor(t["accent"])
            pen = QPen(ring)
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-1.5, -1.5, 1.5, 1.5), radius + 1.5, radius + 1.5)
        p.end()
