"""界面主题：Aurora Glass（现代毛玻璃）深色 / 浅色双套设计 token + 共享 QSS 模板。

设计约定
--------
* 两套主题共用同一套尺寸 token（间距栅格 / 圆角 / 字阶 / 控件高度），只换色值与玻璃参数。
* 色值全部编译进 Python 代码，不依赖外部 .qss 文件（打包 onefile 不会漏资源）。
* 玻璃质感在窗口内部自绘（AuroraCanvas 背景 + GlassCard 模糊切片），不使用
  WA_TranslucentBackground 整窗透明，保证 ``widget.grab()`` 截出来的图与真实观感一致。
* 背景画布铺三团彩色光晕（紫 / 青 / 玫红）：玻璃只「透光」，没有色彩来源就会退化成
  灰色磨砂板，这是旧版发闷的根因。
* 下拉箭头 / 对勾 / SpinBox 上下箭头沿用「运行时 QPainter 画到 %TEMP%、QSS url() 用
  绝对正斜杠路径引用」的机制；文件名按主题区分，避免切主题读到旧图标。
"""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from typing import Dict

# --------------------------------------------------------------------------- #
# 尺寸 token（两套主题完全一致）
# --------------------------------------------------------------------------- #

# 8px 间距栅格
SPACE = {
    "xs": 8,
    "sm": 12,
    "md": 16,
    "lg": 24,
    "xl": 32,
}

# 圆角：卡片 16 / 控件与输入 10 / 小控件与胶囊 8
RADIUS = {
    "card": 16,
    "control": 10,
    "small": 8,
}

# 字阶：窗口标题 22/600、区块标题 13/600、正文 13/400、次要 12、说明 11
FONT = {
    "display": 22,
    "title": 16,
    "section": 13,
    "body": 13,
    "secondary": 12,
    "caption": 11,
}

CONTROL_HEIGHT = 34      # 主按钮 / 次按钮高度
INPUT_HEIGHT = 34        # 输入框 / 下拉 / 数字框高度
ROW_HEIGHT = 44          # 表格行高
HEADER_HEIGHT = 38       # 表头高度

# 等宽字族：地址、码率、时间戳统一用它，读起来像设备日志而不像正文
MONO_FAMILY = '"JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New", monospace'
UI_FAMILY = '"Microsoft YaHei UI", "Segoe UI", "PingFang SC", "SimHei"'

THEMES = ("dark", "light")
DEFAULT_THEME = "dark"


def normalize_theme(value: str | None) -> str:
    """把外部传入的主题名收敛到 dark / light，非法值回退默认深色。"""
    return value if value in THEMES else DEFAULT_THEME


def _channel(fg: int, alpha: float, bg: int) -> int:
    return round(alpha * fg + (1 - alpha) * bg)


def blend(hex_fg: str, alpha: float, hex_bg: str) -> str:
    """把 ``hex_fg`` 以 ``alpha`` 叠在 ``hex_bg`` 上，返回合成后的实色十六进制。

    QSS 的 rgba() 在部分 QWidget 背景上不生效，这里在 Python 侧预先合成实色，
    层次仍由玻璃自绘层（GlassCard）用带 alpha 的 QColor 补充。
    """
    fh = hex_fg.lstrip("#")
    bh = hex_bg.lstrip("#")
    fr, fg_, fb = int(fh[0:2], 16), int(fh[2:4], 16), int(fh[4:6], 16)
    br, bg_, bb = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
    r = _channel(fr, alpha, br)
    g = _channel(fg_, alpha, bg_)
    b = _channel(fb, alpha, bb)
    return f"#{r:02X}{g:02X}{b:02X}"


# --------------------------------------------------------------------------- #
# 色板 token（WCAG 正文对比度由 docs/设计说明.md 的核算表保证）
# --------------------------------------------------------------------------- #

TOKENS: Dict[str, Dict[str, str | float]] = {
    "dark": {
        # 画布渐变（AuroraCanvas 自绘）
        "canvas_top": "#080B12",
        "canvas_bottom": "#04060A",
        "glow1": "#6B4DFF",          # 左上紫罗兰光晕
        "glow1_alpha": 0.26,
        "glow2": "#1FC8E3",          # 右下青绿光晕
        "glow2_alpha": 0.17,
        "glow3": "#E85D9B",          # 左下垂落的玫红光晕
        "glow3_alpha": 0.11,
        "vignette_alpha": 0.60,
        # 玻璃层参数（GlassCard 自绘用）：深色主题用「更深的墨蓝」而不是白色提亮，
        # 才能在彩色光晕上形成真正的磨砂板，而不是一层灰雾
        "glass_tint": "#0C1220",
        "glass_alpha": 0.70,
        "glass_strong_alpha": 0.80,
        "border_alpha": 0.13,
        "border_strong_alpha": 0.22,
        "border_rgb": "#FFFFFF",
        # 合成实色（供 QSS 使用）
        "bg": "#0A0E17",
        "surface": "#151A26",
        "surface_alt": "#1A2030",
        "elevated": "#202838",
        "input_bg": "#0F1420",
        "terminal_bg": "#0A0E16",
        "border": "#232B3B",
        "border_strong": "#33405A",
        # 正文色刻意用「中性偏暖」而不是偏蓝：背景是冷紫，文字再带蓝就会互相串色、
        # 看起来像蒙了一层灰紫膜，长时间盯会累
        # 深色底上不用接近纯白：对比度压到 11~13:1 这一档，长时间看不会有「发光」
        # 的光晕感；文字色取中性偏暖，避免和冷紫背景互相串色
        "text": "#DCDFE6",
        "secondary": "#A3A8B3",
        "muted": "#868B97",
        "title": "#EDEFF4",
        "accent": "#7583F5",
        "accent_hover": "#8E9BFF",
        "accent_dim": "#5A66C8",
        "on_accent": "#070A14",
        "info": "#7B8CFF",
        # 状态色降一档饱和度：高饱和绿/红压在紫色玻璃上会「跳」出来，刺眼
        "running": "#41C99B",
        "starting": "#E2AE55",
        "error": "#F27A85",
        "stopped": "#868B97",
        "idle": "#868B97",
        "sel_bg": "#252A38",
        "sel_text": "#EDEFF4",
        "hover_bg": "#1C2028",
        "danger_bg": "#2A1A22",
        "danger_border": "#6B3648",
        "danger_text": "#FF8FA3",
        "danger_hover": "#3A2231",
        "chrome_bg": "#0E131E",
        "shadow": "#000000",
        "shadow_alpha": 0.42,
        "arrow": "#A7B4CB",
        "check": "#070A14",
        "tooltip_bg": "#202838",
        "log_debug": "#868B97",
        "log_info": "#9BA0AB",
        "log_warn": "#E2AE55",
        "log_error": "#F27A85",
        "log_time": "#6E7280",
    },
    "light": {
        # 画布渐变（AuroraCanvas 自绘）
        "canvas_top": "#E8EDF7",
        "canvas_bottom": "#F7F9FD",
        "glow1": "#6B4DFF",
        "glow1_alpha": 0.16,
        "glow2": "#1FC8E3",
        "glow2_alpha": 0.13,
        "glow3": "#F078AD",
        "glow3_alpha": 0.10,
        "vignette_alpha": 0.0,
        # 玻璃层参数：浅色主题用白色霜面，深色主题见上
        "glass_tint": "#FFFFFF",
        "glass_alpha": 0.70,
        "glass_strong_alpha": 0.86,
        "border_alpha": 0.055,
        "border_strong_alpha": 0.11,
        "border_rgb": "#0B1220",
        # 合成实色（供 QSS 使用）
        "bg": "#F2F5FB",
        "surface": "#FFFFFF",
        "surface_alt": "#F4F7FD",
        "elevated": "#FFFFFF",
        "input_bg": "#FFFFFF",
        "terminal_bg": "#F4F7FD",
        "border": "#E3E9F3",
        "border_strong": "#C9D4E5",
        "text": "#111A2B",
        "secondary": "#48586F",
        "muted": "#5F6E82",
        "title": "#0A1220",
        "accent": "#4B4FE0",
        "accent_hover": "#6366F1",
        "accent_dim": "#3730A3",
        "on_accent": "#FFFFFF",
        "info": "#4B4FE0",
        "running": "#067048",
        "starting": "#B45309",
        "error": "#D22B4B",
        "stopped": "#5F6E82",
        "idle": "#5F6E82",
        "sel_bg": "#E4E8FF",
        "sel_text": "#1E2470",
        "hover_bg": "#F2F5FD",
        "danger_bg": "#FDECEF",
        "danger_border": "#F0B9C4",
        "danger_text": "#C11434",
        "danger_hover": "#FBDDE3",
        "chrome_bg": "#FFFFFF",
        "shadow": "#1B2A4A",
        "shadow_alpha": 0.13,
        "arrow": "#48586F",
        "check": "#FFFFFF",
        "tooltip_bg": "#111A2B",
        "log_debug": "#68788E",
        "log_info": "#48586F",
        "log_warn": "#B45309",
        "log_error": "#C11434",
        "log_time": "#8B99AC",
    },
}


def colors(theme: str) -> Dict[str, object]:
    """取某套主题的色值字典（非法主题名回退深色）。"""
    return TOKENS[normalize_theme(theme)]


def polish_scroll_holders(root, theme: str) -> None:
    """递归扫描 ``root`` 下所有滚动条，给其内部容器刷实色底色。

    QScrollArea / QTreeWidget / QPlainTextEdit 都会为滚动条包一个内部 QWidget 容器，
    QSS 选择器命中不到它；该容器背景默认未填充，``widget.grab()`` 时透明区会被拍成
    黑白棋盘格。这里在代码层把每个滚动条的容器背景刷成与对话框一致的实色底色，
    内联样式表优先级高于全局 QSS，保证截图与真实观感一致。
    """
    from PySide6.QtWidgets import QScrollBar, QWidget

    bg = str(colors(theme)["bg"])

    def walk(widget: QWidget) -> None:
        for child in widget.children():
            if isinstance(child, QScrollBar):
                holder = child.parentWidget()
                if holder is not None:
                    holder.setAutoFillBackground(True)
                    holder.setStyleSheet(f"background: {bg};")
            elif isinstance(child, QWidget):
                walk(child)

    walk(root)


# --------------------------------------------------------------------------- #
# 运行时绘制的小图标（下拉箭头 / 对勾 / SpinBox 上下箭头）
# --------------------------------------------------------------------------- #

def _icon_filename(kind: str, theme: str) -> str:
    """图标文件名按主题区分，切主题后不会读到上一套主题的旧图。"""
    return f"rtsp_cam_sim_v2_{kind}_{normalize_theme(theme)}.png"


def _arrow_icon_path(theme: str) -> str:
    """生成下拉三角箭头（32×32，透明底，颜色随主题）。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygonF

    tok = colors(theme)
    path = os.path.join(tempfile.gettempdir(), _icon_filename("arrow", theme))
    if not os.path.isfile(path):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tok["arrow"]))
        painter.drawPolygon(QPolygonF([QPointF(9, 12), QPointF(23, 12), QPointF(16, 21)]))
        painter.end()
        pixmap.save(path, "PNG")
    return path


def _check_icon_path(theme: str) -> str:
    """生成勾选框对勾（20×20）：深色主题用深墨对勾，浅色主题用白色对勾。"""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

    tok = colors(theme)
    path = os.path.join(tempfile.gettempdir(), _icon_filename("check", theme))
    if not os.path.isfile(path):
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(tok["check"]))
        pen.setWidth(3)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(3.5, 10.5), QPointF(8, 15), QPointF(16.5, 5.5)]))
        painter.end()
        pixmap.save(path, "PNG")
    return path


def _spin_arrow_icon_path(theme: str, direction: str) -> str:
    """生成 SpinBox 上下微调箭头（32×32，透明底，颜色随主题）。

    direction: "up" / "down"。文件名按主题 + 方向区分，避免切主题读到旧图标。
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap, QPolygonF

    tok = colors(theme)
    path = os.path.join(tempfile.gettempdir(),
                        f"rtsp_cam_sim_v2_spin_{direction}_{normalize_theme(theme)}.png")
    if not os.path.isfile(path):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tok["arrow"]))
        if direction == "up":
            points = [QPointF(9, 21), QPointF(23, 21), QPointF(16, 12)]
        else:
            points = [QPointF(9, 12), QPointF(23, 12), QPointF(16, 21)]
        painter.drawPolygon(QPolygonF(points))
        painter.end()
        pixmap.save(path, "PNG")
    return path


def _chevron_icon_path(theme: str, direction: str = "down") -> str:
    """生成小箭头（chevron），用于「展开/收起」这类轻量动作（32×32）。

    与下拉三角区分开：chevron 是 2px 折线，不是实心三角，视觉上更轻。
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

    tok = colors(theme)
    path = os.path.join(tempfile.gettempdir(),
                        f"rtsp_cam_sim_v2_chevron_{direction}_{normalize_theme(theme)}.png")
    if not os.path.isfile(path):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(tok["arrow"]))
        pen.setWidth(2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if direction == "down":
            painter.drawPolyline(QPolygonF([QPointF(10, 13), QPointF(16, 19), QPointF(22, 13)]))
        elif direction == "up":
            painter.drawPolyline(QPolygonF([QPointF(10, 19), QPointF(16, 13), QPointF(22, 19)]))
        else:  # right
            painter.drawPolyline(QPolygonF([QPointF(13, 10), QPointF(19, 16), QPointF(13, 22)]))
        painter.end()
        pixmap.save(path, "PNG")
    return path


# --------------------------------------------------------------------------- #
# QSS 模板（占位符 __KEY__ 在 prepare_qss 里按主题替换）
# --------------------------------------------------------------------------- #

_QSS_TEMPLATE = """
QWidget {
    color: __TEXT__;
    font-family: __UI_FAMILY__;
    font-size: 13px;
}
QDialog { background: __BG__; }
QScrollArea { background: __BG__; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QMainWindow { background: __BG__; }

/* ---- 文本角色 ---- */
#Title { font-size: 22px; font-weight: 700; color: __TITLE__; letter-spacing: 0.2px; }
#Subtitle { font-size: 12px; color: __SECONDARY__; }
#SectionTitle { font-size: 13px; font-weight: 600; color: __TEXT__; }
#SectionHint { font-size: 11px; color: __MUTED__; }
#Hint { color: __SECONDARY__; font-size: 12px; }
#Warn { color: __ERROR__; font-size: 12px; }
#Mono { font-family: __MONO_FAMILY__; font-size: 12px; color: __SECONDARY__; }
#KvLabel { color: __MUTED__; font-size: 11px; }
#KvValue { color: __TEXT__; font-size: 12px; }
#EmptyTitle { color: __SECONDARY__; font-size: 14px; }
#Divider { background: __BORDER__; }

/* ---- 按钮体系 ---- */
/* 次按钮：玻璃底 + 1px 玻璃边 + 次要文字 */
QPushButton {
    background: __SURFACE__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: __R_CONTROL__px;
    padding: 6px 16px;
    min-height: 32px;
    color: __TEXT__;
}
QPushButton:hover { background: __ELEVATED__; border-color: __BORDER_STRONG__; }
QPushButton:pressed { background: __SURFACE_ALT__; }
QPushButton:disabled { color: __MUTED__; background: __SURFACE__; border-color: __BORDER__; }

/* 唯一最重要动作（启动服务）：实心强调色 */
QPushButton#Primary {
    background: __ACCENT__; border: 1px solid __ACCENT__; color: __ACCENT_TEXT__;
    font-weight: 600; padding: 6px 20px;
}
QPushButton#Primary:hover { background: __ACCENT_HOVER__; border-color: __ACCENT_HOVER__; }
QPushButton#Primary:pressed { background: __ACCENT_DIM__; }
QPushButton#Primary:disabled { color: __MUTED__; background: __SURFACE__; border-color: __BORDER__; }

/* 强调描边按钮：淡强调填充 + accent 文字 */
QPushButton#AccentOutline {
    background: __ACCENT_SOFT__; border-color: __ACCENT_BORDER__; color: __ACCENT__; font-weight: 600;
}
QPushButton#AccentOutline:hover { background: __ACCENT_SOFT_HOVER__; border-color: __ACCENT_HOVER__; color: __ACCENT_HOVER__; }

/* 危险按钮：透明底 + danger 细边 + danger 文字 */
QPushButton#Danger {
    background: transparent; border-color: __DANGER_BORDER__; color: __DANGER_TEXT__;
}
QPushButton#Danger:hover { background: __DANGER_HOVER__; }
QPushButton#Danger:disabled { color: __MUTED__; border-color: __BORDER__; background: transparent; }

/* 幽灵按钮：命令分组内的无边框动作 */
QPushButton#Ghost {
    background: transparent; border: 1px solid transparent; color: __SECONDARY__;
    padding: 5px 12px; min-height: 28px;
}
QPushButton#Ghost:hover { background: __GHOST_HOVER__; color: __TEXT__; }
QPushButton#Ghost:pressed { background: __SURFACE_ALT__; }
QPushButton#Ghost:disabled { color: __MUTED__; background: transparent; }

/* 图标 / 文字小按钮（日志清空、折叠） */
QPushButton#Tiny {
    background: transparent; border: 1px solid transparent; color: __MUTED__;
    padding: 2px 10px; min-height: 24px; font-size: 12px;
}
QPushButton#Tiny:hover { background: __GHOST_HOVER__; color: __TEXT__; }

/* 分组内的强调动作（添加流）：透明底 + accent 文字，hover 才铺淡强调底 */
QPushButton#GhostAccent {
    background: transparent; border: 1px solid transparent;
    color: __ACCENT__; font-weight: 600; padding: 5px 12px; min-height: 28px;
}
QPushButton#GhostAccent:hover { background: __ACCENT_SOFT__; color: __ACCENT_HOVER__; }
QPushButton#GhostAccent:pressed { background: __ACCENT_SOFT_HOVER__; }
QPushButton#GhostAccent:disabled { color: __MUTED__; background: transparent; }

/* 分组内的危险动作（删除）：默认与幽灵按钮同色，hover 才显红 */
QPushButton#GhostDanger {
    background: transparent; border: 1px solid transparent;
    color: __SECONDARY__; padding: 5px 12px; min-height: 28px;
}
QPushButton#GhostDanger:hover { background: __DANGER_HOVER__; color: __DANGER_TEXT__; }
QPushButton#GhostDanger:pressed { background: __DANGER_BG__; }
QPushButton#GhostDanger:disabled { color: __MUTED__; background: transparent; }

/* 底部页脚：地址与计数，等宽小字 */
#FooterText { color: __MUTED__; font-size: 11px; font-family: __MONO_FAMILY__; }

/* 命令分组容器：把一组动作装进一个下沉的胶囊里 */
QFrame#ToolGroup {
    background: __GROUP_BG__;
    border: 1px solid __BORDER__;
    border-radius: __R_CONTROL__px;
}

/* 主题切换改用自绘图标开关（widgets.ThemeSwitch），不再有分段按钮样式 */

/* ---- 表格：去竖网格，只留极淡横向分隔 ---- */
QTableWidget {
    background: transparent;
    alternate-background-color: transparent;
    gridline-color: transparent;
    border: none;
    selection-background-color: transparent;
    selection-color: __TEXT__;
    outline: 0;
}
QHeaderView::section {
    background: transparent;
    color: __SECONDARY__;
    border: none;
    border-bottom: 1px solid __BORDER__;
    padding: 6px 8px;
    font-weight: 600;
    font-size: 11px;
}
QTableWidget::item { padding: 4px 6px; border: none; }
QTableWidget::item:hover { background: transparent; }
QTableWidget::item:selected { background: transparent; color: __TEXT__; }

/* ---- 输入类控件 ---- */
QPlainTextEdit, QTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: __INPUT_BG__;
    border: 1px solid __BORDER_STRONG__;
    border-radius: __R_CONTROL__px;
    padding: 6px 10px;
    min-height: 20px;
    selection-background-color: __SEL_BG__;
    selection-color: __SEL_TEXT__;
}
QPlainTextEdit:hover, QTextEdit:hover, QLineEdit:hover, QComboBox:hover,
QSpinBox:hover, QDoubleSpinBox:hover { border-color: __INPUT_HOVER__; }
QPlainTextEdit:readOnly, QTextEdit:readOnly { background: __SURFACE_ALT__; color: __SECONDARY__; }
QPlainTextEdit:disabled, QTextEdit:disabled { color: __MUTED__; background: __INPUT_DISABLED__; }
QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QLineEdit:disabled {
    color: __MUTED__; background: __INPUT_DISABLED__; border-color: __BORDER__;
}
QComboBox:disabled::drop-down { border: none; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: __ACCENT__; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow { image: url("__ARROW__"); width: 10px; height: 10px; margin-right: 6px; }
QComboBox QAbstractItemView {
    background: __ELEVATED__; border: 1px solid __BORDER_STRONG__;
    border-radius: 8px; padding: 4px;
    selection-background-color: __SEL_BG__; selection-color: __SEL_TEXT__;
    outline: 0;
}
QSpinBox, QDoubleSpinBox { padding-right: 26px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid __BORDER__;
    border-top-right-radius: __R_CONTROL__px;
    background: transparent;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border-left: 1px solid __BORDER__;
    border-bottom-right-radius: __R_CONTROL__px;
    background: transparent;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: __GHOST_HOVER__; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url("__ARROW_UP__"); width: 9px; height: 9px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url("__ARROW_DOWN__"); width: 9px; height: 9px;
}

/* ---- 树 / 列表 ---- */
QTreeWidget, QListWidget {
    background: __INPUT_BG__;
    border: 1px solid __BORDER__;
    border-radius: __R_SMALL__px;
    padding: 4px;
    outline: 0;
}
QTreeWidget::item { padding: 3px 2px; border-radius: 6px; }
QTreeWidget::item:hover { background: __GHOST_HOVER__; }
QTreeWidget::item:selected { background: __SEL_BG__; color: __SEL_TEXT__; }

QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }

/* ---- 滚动条：8px 细条、圆角、透明槽、hover 显色 ---- */
QScrollBar:vertical, QAbstractItemView QScrollBar:vertical {
    background: transparent; width: 8px; margin: 2px; border: none;
}
QScrollBar::groove:vertical, QAbstractItemView QScrollBar::groove:vertical {
    background: transparent; border: none;
}
QScrollBar::handle:vertical, QAbstractItemView QScrollBar::handle:vertical {
    background: __SCROLL_HANDLE__; border-radius: 4px; min-height: 28px;
}
QScrollBar::handle:vertical:hover, QAbstractItemView QScrollBar::handle:vertical:hover {
    background: __MUTED__;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
QAbstractItemView QScrollBar::add-page:vertical, QAbstractItemView QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal, QAbstractItemView QScrollBar:horizontal {
    background: transparent; height: 8px; margin: 2px; border: none;
}
QScrollBar::groove:horizontal, QAbstractItemView QScrollBar::groove:horizontal {
    background: transparent; border: none;
}
QScrollBar::handle:horizontal, QAbstractItemView QScrollBar::handle:horizontal {
    background: __SCROLL_HANDLE__; border-radius: 4px; min-width: 28px;
}
QScrollBar::handle:horizontal:hover, QAbstractItemView QScrollBar::handle:horizontal:hover {
    background: __MUTED__;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* ---- 勾选框 ---- */
QCheckBox { background: transparent; spacing: 8px; color: __TEXT__; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid __BORDER_STRONG__; border-radius: 5px; background: __INPUT_BG__;
}
QCheckBox::indicator:hover { border-color: __ACCENT__; }
QCheckBox::indicator:checked { background: __ACCENT__; border-color: __ACCENT__; image: url(__CHECK__); }
QCheckBox::indicator:checked:hover { background: __ACCENT_HOVER__; border-color: __ACCENT_HOVER__; }
QCheckBox::indicator:disabled { border-color: __BORDER__; background: __SURFACE__; }
QTreeView::indicator {
    width: 15px; height: 15px;
    border: 1px solid __BORDER_STRONG__; border-radius: 4px; background: __INPUT_BG__;
}
QTreeView::indicator:checked {
    background: __ACCENT__; border-color: __ACCENT__; image: url(__CHECK__);
}

/* ---- 分组框（对话框内的参数区）---- */
QGroupBox {
    border: 1px solid __BORDER__;
    border-radius: __R_CARD__px;
    margin-top: 14px;
    padding: 18px 16px 12px 16px;
    background: __GROUP_BG__;
    color: __TEXT__;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    font-size: 12px;
    font-weight: 600;
    color: __SECONDARY__;
}

/* 对话框参数分组（SectionCard）：比输入框浅一档的玻璃面板 */
QFrame#Section {
    background: __GROUP_BG__;
    border: 1px solid __BORDER__;
    border-radius: 14px;
}

/* 地址行（详情栏 / 对话框预览） */
QFrame#UrlRow {
    background: __GROUP_BG__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
}
QFrame#UrlRow:hover { border-color: __BORDER_STRONG__; }
#UrlText {
    font-family: __MONO_FAMILY__;
    font-size: 12px;
    color: __TEXT__;
}
#UrlCaption { font-size: 11px; color: __MUTED__; }

/* 运行日志：等宽、贴底、低对比，像设备控制台 */
#LogView {
    font-family: __MONO_FAMILY__;
    font-size: 12px;
    background: __TERMINAL_BG__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
    padding: 8px 10px;
    color: __SECONDARY__;
}
#LogHeader { background: transparent; }

/* 对话框顶部标题区 / 底部固定按钮栏 */
#DialogHeader { background: transparent; }
#DialogFooter { background: __CHROME_BG__; border-top: 1px solid __BORDER__; }

QStatusBar { background: transparent; color: __MUTED__; font-size: 11px; }
QStatusBar::item { border: none; }
QToolTip {
    background: __TOOLTIP_BG__; color: __TEXT__; border: 1px solid __BORDER_STRONG__;
    padding: 5px 8px; border-radius: 6px;
}
"""


def prepare_qss(theme: str = DEFAULT_THEME) -> str:
    """生成某套主题的 QSS（需在 QApplication 创建之后调用）。

    机制与早期版本一致：图标路径在运行时画到 %TEMP%，QSS url() 用绝对正斜杠路径
    引用（file:/// 写法在 QSS 里无效）。
    """
    name = normalize_theme(theme)
    tok = colors(name)
    accent_soft = blend(tok["accent"], 0.10, tok["surface"])
    accent_soft_hover = blend(tok["accent"], 0.18, tok["surface"])
    accent_border = blend(tok["accent"], 0.42, tok["surface"])
    ghost_hover = blend(tok["text"], 0.06, tok["surface"])
    group_bg = blend(tok["text"], 0.03, tok["surface"])
    scroll_handle = blend(tok["text"], 0.16, tok["surface"])
    input_hover = blend(tok["accent"], 0.35, tok["border_strong"])
    # 禁用态要肉眼可分：浅色主题里输入框与面板都是白的，只改边框根本看不出来
    input_disabled = blend(tok["text"], 0.05, tok["surface"])

    qss = _QSS_TEMPLATE
    replacements = {
        "__UI_FAMILY__": UI_FAMILY,
        "__MONO_FAMILY__": MONO_FAMILY,
        "__BG__": tok["bg"],
        "__SURFACE__": tok["surface"],
        "__SURFACE_ALT__": tok["surface_alt"],
        "__ELEVATED__": tok["elevated"],
        "__BORDER__": tok["border"],
        "__BORDER_STRONG__": tok["border_strong"],
        "__TEXT__": tok["text"],
        "__TITLE__": tok["title"],
        "__SECONDARY__": tok["secondary"],
        "__MUTED__": tok["muted"],
        "__ACCENT__": tok["accent"],
        "__ACCENT_HOVER__": tok["accent_hover"],
        "__ACCENT_DIM__": tok["accent_dim"],
        "__ACCENT_TEXT__": tok["on_accent"],
        "__ACCENT_SOFT__": accent_soft,
        "__ACCENT_SOFT_HOVER__": accent_soft_hover,
        "__ACCENT_BORDER__": accent_border,
        "__SEL_BG__": tok["sel_bg"],
        "__SEL_TEXT__": tok["sel_text"],
        "__HOVER_BG__": tok["hover_bg"],
        "__INPUT_BG__": tok["input_bg"],
        "__INPUT_HOVER__": input_hover,
        "__INPUT_DISABLED__": input_disabled,
        "__GROUP_BG__": group_bg,
        "__GHOST_HOVER__": ghost_hover,
        "__SCROLL_HANDLE__": scroll_handle,
        "__CHROME_BG__": tok["chrome_bg"],
        "__TERMINAL_BG__": tok["terminal_bg"],
        "__ERROR__": tok["error"],
        "__DANGER_BG__": tok["danger_bg"],
        "__DANGER_BORDER__": tok["danger_border"],
        "__DANGER_TEXT__": tok["danger_text"],
        "__DANGER_HOVER__": tok["danger_hover"],
        "__TOOLTIP_BG__": tok["tooltip_bg"],
        "__R_CARD__": str(RADIUS["card"]),
        "__R_CONTROL__": str(RADIUS["control"]),
        "__R_SMALL__": str(RADIUS["small"]),
        "__ARROW__": _arrow_icon_path(name).replace("\\", "/"),
        "__ARROW_UP__": _spin_arrow_icon_path(name, "up").replace("\\", "/"),
        "__ARROW_DOWN__": _spin_arrow_icon_path(name, "down").replace("\\", "/"),
        "__CHECK__": _check_icon_path(name).replace("\\", "/"),
        "__CHEVRON_DOWN__": _chevron_icon_path(name, "down").replace("\\", "/"),
    }
    # 长的占位符先替换，避免 __ARROW__ 把 __ARROW_UP__ 的前缀吃掉
    for key in sorted(replacements, key=len, reverse=True):
        qss = qss.replace(key, str(replacements[key]))
    return qss


def set_windows_titlebar_dark(window, dark: bool) -> None:
    """按主题切换 Windows 标题栏深浅色（DWMWA_USE_IMMERSIVE_DARK_MODE = 20）。

    非 Windows 或调用失败时静默跳过，不影响主流程。
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        value = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


def set_windows_rounded_corners(window, enabled: bool = True) -> None:
    """Win11 DWM 圆角（DWMWA_WINDOW_CORNER_PREFERENCE = 33），失败静默回退直角。"""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        # DWMWCP_ROUND = 2, DWMWCP_DEFAULT = 0
        value = ctypes.c_int(2 if enabled else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass
