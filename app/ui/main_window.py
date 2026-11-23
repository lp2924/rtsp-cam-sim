"""主窗口。"""

from __future__ import annotations

import html
import os
import socket
import sys
import time
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QDialog, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget,
    QStyle, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..engine import Engine
from ..models import (
    CONFIG_PATH, PROJECT_ROOT, AppConfig, Route, StreamSpec, describe_source, is_sub_route,
    source_kind,
)
from ..vendors import route_label
from .glass import AuroraCanvas, GlassCard, LogoMark, TitleBarButton
from .settings_dialog import SettingsDialog
from .stream_dialog import StreamDialog
from .theme import (
    MONO_FAMILY, colors, normalize_theme, prepare_qss, set_windows_rounded_corners,
    set_windows_titlebar_dark,
)
from .widgets import Divider, EmptyState, KeyValue, StatusPill, ThemeSwitch, UrlRow

STATE_TEXT = {
    "idle": "未启动",
    "stopped": "已停止",
    "starting": "启动中",
    "running": "运行中",
    "error": "错误",
}

TABLE_HEADERS = ["状态", "名称", "编码", "主码流", "帧率", "码率", "子码流", "客户端",
                 "路径"]

# 表格列宽：名称列拉伸吃掉余量；路径列只给条数（完整地址在右侧详情里逐条显示，
# 表格里塞长路径只会被截成「/Streaming…nels/101」这种半截字）
COLUMN_WIDTHS = [76, 130, 52, 90, 44, 60, 84, 52, 46]


def local_ipv4() -> List[str]:
    """枚举本机对外可用的 IPv4 地址。"""
    found: List[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        found.append(probe.getsockname()[0])
        probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    ordered: List[str] = []
    for ip in found:
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        if ip not in ordered:
            ordered.append(ip)
    return ordered or ["127.0.0.1"]


class _TitleBar(QWidget):
    """无边框窗口的拖动标题栏：左键拖动窗口，双击最大化 / 还原。"""

    # 光标移动超过该像素才认作拖动；否则可能是双击的第一下，不能抢走事件
    _DRAG_THRESHOLD = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_global = None    # 本次按下的全局坐标
        self._cursor_offset = None   # 「光标 - 窗口左上角」，手动拖动时用
        self._grabbed = False        # 已开始拖动（系统拖动或手动）
        self._manual = False         # 系统拖动不可用时自己搬窗口

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._cursor_offset = self._press_global - self.window().pos()
            self._grabbed = False
            self._manual = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if not self._grabbed and self._press_global is not None:
                moved = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
                if moved >= self._DRAG_THRESHOLD:
                    self._begin_drag(event.globalPosition().toPoint())
            elif self._manual and self._cursor_offset is not None:
                self.window().move(event.globalPosition().toPoint() - self._cursor_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._press_global = None
        self._cursor_offset = None
        self._grabbed = False
        self._manual = False
        super().mouseReleaseEvent(event)

    def _begin_drag(self, global_pos) -> None:
        """真正起拖：优先交给系统（带贴边分屏），不行再手动搬窗口。"""
        window = self.window()
        self._grabbed = True
        if window.isMaximized():
            self._restore_at(global_pos)
        # startSystemMove 属于 QWindow，QWidget 上没有这个接口
        handle = window.windowHandle()
        if handle is not None and handle.startSystemMove():
            return
        self._manual = True
        # 用按下点算偏移，避免把「越过阈值前那几像素」丢掉导致窗口滞后
        anchor = self._press_global or global_pos
        self._cursor_offset = anchor - window.pos()

    def _restore_at(self, global_pos) -> None:
        """最大化状态下起拖：先还原，并把光标按横向比例落回窗口，避免窗口跳走。"""
        window = self.window()
        width_before = max(window.width(), 1)
        ratio = (global_pos.x() - window.x()) / width_before
        window.showNormal()
        window.move(
            global_pos.x() - int(window.width() * min(max(ratio, 0.0), 1.0)),
            global_pos.y() - 16,
        )

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if hasattr(window, "_toggle_max"):
                window._toggle_max()
        super().mouseDoubleClickEvent(event)


def _band_path(rect: QRect, radius: float, round_left: bool, round_right: bool) -> QPainterPath:
    """画一条「只在首列/末列做圆角」的行底，避免逐格圆角在行内留下缺口。"""
    path = QPainterPath()
    left = rect.left()
    right = rect.right() + 1
    top = rect.top()
    bottom = rect.bottom() + 1
    path.moveTo(left + (radius if round_left else 0), top)
    path.lineTo(right - (radius if round_right else 0), top)
    if round_right:
        path.arcTo(QRectF(right - 2 * radius, top, 2 * radius, 2 * radius), 90, -90)
    path.lineTo(right, bottom - (radius if round_right else 0))
    if round_right:
        path.arcTo(QRectF(right - 2 * radius, bottom - 2 * radius, 2 * radius, 2 * radius), 0, -90)
    path.lineTo(left + (radius if round_left else 0), bottom)
    if round_left:
        path.arcTo(QRectF(left, bottom - 2 * radius, 2 * radius, 2 * radius), 270, -90)
    path.lineTo(left, top + (radius if round_left else 0))
    if round_left:
        path.arcTo(QRectF(left, top, 2 * radius, 2 * radius), 180, -90)
    path.closeSubpath()
    return path


class _RowDelegate(QStyledItemDelegate):
    """表格行自绘：圆角选中底 + 状态圆点，避免整行铺满饱和色。

    单元格文字仍由 QTableWidgetItem 提供（含对齐与前景色），这里只接管背景与
    第一列的状态点，所以业务侧刷新逻辑完全不用改。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tok = colors("dark")

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 只在纵向留白，横向不留缝：逐格缩边会在列交界处露出 2px 背景
        rect = option.rect.adjusted(0, 3, 0, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver) and not selected
        if selected or hovered:
            first = index.column() == 0
            last = index.column() == index.model().columnCount() - 1
            color = QColor(self.tok["sel_bg"] if selected else self.tok["hover_bg"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPath(_band_path(rect, 8, first, last))

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        color = index.data(Qt.ItemDataRole.ForegroundRole)
        if isinstance(color, QColor) and color.isValid():
            text_color = color
        elif selected:
            text_color = QColor(self.tok["sel_text"])
        else:
            text_color = QColor(self.tok["text"])

        align = index.data(Qt.ItemDataRole.TextAlignmentRole)
        if align is None:
            align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        else:
            align = int(align)

        font = option.font
        if index.column() >= 3:
            # 规格数字用等宽：分辨率 / 码率上下对齐才好对比
            font = QFont(option.font)
            font.setFamilies(["JetBrains Mono", "Cascadia Mono", "Consolas", "Courier New"])
            font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(text_color)

        text_rect = QRect(rect)
        if index.column() == 0:
            # 状态点 + 文字：点用状态色，文字跟随同一色系
            dot_color = text_color
            cx = rect.left() + 16
            cy = rect.center().y() + 1
            halo = QColor(dot_color)
            halo.setAlphaF(0.22)
            painter.setBrush(halo)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(rect.left() + 9, cy - 7, 14, 14)
            painter.setBrush(dot_color)
            painter.drawEllipse(rect.left() + 13, cy - 3, 6, 6)
            text_rect = rect.adjusted(28, 0, -6, 0)
            align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            painter.setPen(text_color)
        else:
            text_rect = rect.adjusted(8, 0, -8, 0)

        painter.drawText(text_rect, align, str(text))
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, config: AppConfig):
        super().__init__()
        self.engine = engine
        self.config = config
        self._row_ids: List[str] = []
        self._show_debug = False
        self._log_expanded = True
        self._url_rows: List[UrlRow] = []

        # 当前主题色值（随主题切换刷新；不重启编码器、不碰 Engine）
        self.config.theme = normalize_theme(self.config.theme)
        self._tok = colors(self.config.theme)
        self._state_color = self._build_state_colors()
        self._log_color = self._build_log_colors()
        self._cards: List[GlassCard] = []
        self._titlebar_buttons: List[TitleBarButton] = []
        self._dividers: List[Divider] = []

        # 无边框窗口：标题栏自绘，拖动 / 边缘缩放 / 最小化最大化关闭全部自行实现
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("RTSP 摄像头流模拟器")
        self.resize(1280, 820)
        self.setMinimumSize(1080, 700)
        self._build_ui()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_max_button()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Win11 DWM 圆角，失败静默回退直角
        set_windows_rounded_corners(self, True)

    # ------------------------------------------------------------------ #
    # 无边框窗口：边缘缩放（WM_NCHITTEST）
    # ------------------------------------------------------------------ #
    def nativeEvent(self, event_type, message):
        if sys.platform == "win32" and event_type == "windows_generic_MSG":
            try:
                import ctypes
                from ctypes import wintypes

                class MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt", wintypes.POINT),
                    ]

                msg = MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    if self.isMaximized():
                        return True, 1  # HTCLIENT
                    pos = self.mapFromGlobal(QCursor.pos())
                    margin = 6
                    left = pos.x() <= margin
                    right = pos.x() >= self.width() - margin
                    top = pos.y() <= margin
                    bottom = pos.y() >= self.height() - margin
                    if top and left:
                        return True, 13   # HTTOPLEFT
                    if top and right:
                        return True, 14   # HTTOPRIGHT
                    if bottom and left:
                        return True, 16   # HTBOTTOMLEFT
                    if bottom and right:
                        return True, 17   # HTBOTTOMRIGHT
                    if left:
                        return True, 10   # HTLEFT
                    if right:
                        return True, 11   # HTRIGHT
                    if top:
                        return True, 12   # HTTOP
                    if bottom:
                        return True, 15   # HTBOTTOM
                    return True, 1        # HTCLIENT
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    # ------------------------------------------------------------------ #
    # 主题
    # ------------------------------------------------------------------ #
    def _build_state_colors(self) -> Dict[str, str]:
        t = self._tok
        return {
            "idle": t["idle"],
            "stopped": t["stopped"],
            "starting": t["starting"],
            "running": t["running"],
            "error": t["error"],
        }

    def _build_log_colors(self) -> Dict[str, str]:
        t = self._tok
        return {
            "debug": t["log_debug"],
            "info": t["log_info"],
            "warn": t["log_warn"],
            "error": t["log_error"],
        }

    def apply_theme(self, theme: str, save: bool = True) -> None:
        """实时切换主题：只换样式与自绘状态色，绝不启停编码器 / 断开客户端。

        :param save: 是否把主题落盘到 config.json（冒烟测试可用临时路径或 False）。
        """
        theme = normalize_theme(theme)
        self.config.theme = theme
        self._tok = colors(theme)
        self._state_color = self._build_state_colors()
        self._log_color = self._build_log_colors()

        # 自绘背景 / 玻璃卡片 / 标题栏按钮跟随主题刷新；模糊缓存失效重算
        self.canvas.set_theme(theme)
        for card in self._cards:
            card.set_theme(theme)
            card.invalidate_blur()
        for btn in self._titlebar_buttons:
            btn.set_theme(theme)
        for divider in self._dividers:
            divider.set_theme(theme)
        self.logo.set_theme(theme)
        self.status_pill.set_theme(theme)
        self.detail_pill.set_theme(theme)
        for row in self._url_rows:
            row.set_theme(theme)
        if hasattr(self, "_row_delegate"):
            self._row_delegate.tok = self._tok

        QApplication.instance().setStyleSheet(prepare_qss(theme))
        # 开关自己点的切换已经在放动画，这里只处理「外部改主题」（设置对话框）的情况
        if hasattr(self, "theme_switch") and self.theme_switch.theme != theme:
            self.theme_switch.set_theme(theme, animate=False)
        set_windows_titlebar_dark(self, theme == "dark")

        if save:
            self.config.save()
        self._refresh()

    # ------------------------------------------------------------------ #
    # 界面搭建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        self.canvas = AuroraCanvas(self.config.theme)
        self.setCentralWidget(self.canvas)
        outer = QVBoxLayout(self.canvas)
        outer.setContentsMargins(18, 12, 18, 12)
        outer.setSpacing(14)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_command_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_table_card())
        splitter.addWidget(self._build_detail_panel())
        splitter.setSizes([860, 380])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setChildrenCollapsible(False)
        outer.addWidget(splitter, 1)

        outer.addWidget(self._build_log_card())
        outer.addWidget(self._build_footer())

    # ---- 顶部：品牌 + 服务状态 + 主题 + 窗口按钮 ---- #
    def _build_header(self) -> "_TitleBar":
        bar = _TitleBar(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 6, 0, 2)
        layout.setSpacing(12)

        self.logo = LogoMark(self.config.theme, 36)
        layout.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignVCenter)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("RTSP 摄像头流模拟器")
        title.setObjectName("Title")
        subtitle = QLabel("本地视频与网络源 → 标准 RTSP 流，按主流摄像机厂商的路径与 SDP 格式对外发布")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        layout.addLayout(title_box)
        layout.addStretch(1)

        self.status_pill = StatusPill(self.config.theme)
        self.status_pill.set_status("服务未启动", self._tok["stopped"])
        layout.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(self._build_theme_switch(), 0, Qt.AlignmentFlag.AlignVCenter)

        self.server_btn = QPushButton("启动服务")
        self.server_btn.setObjectName("Primary")
        self.server_btn.setMinimumWidth(112)
        self.server_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.server_btn.clicked.connect(self.on_toggle_server)
        layout.addWidget(self.server_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addSpacing(4)

        # 无边框窗口控制按钮：最小化 / 最大化还原 / 关闭
        self.btn_min = TitleBarButton(TitleBarButton.KIND_MIN, self.config.theme)
        self.btn_max = TitleBarButton(TitleBarButton.KIND_MAX, self.config.theme)
        self.btn_close = TitleBarButton(TitleBarButton.KIND_CLOSE, self.config.theme)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(self.close)
        self._titlebar_buttons.extend([self.btn_min, self.btn_max, self.btn_close])
        layout.addWidget(self.btn_min)
        layout.addWidget(self.btn_max)
        layout.addWidget(self.btn_close)
        return bar

    def _build_theme_switch(self) -> ThemeSwitch:
        """深色 / 浅色切换：太阳-月亮图标开关（点整块可切，也支持键盘空格）。"""
        self.theme_switch = ThemeSwitch(self.config.theme)
        self.theme_switch.themeChanged.connect(self.apply_theme)
        return self.theme_switch

    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _update_max_button(self) -> None:
        kind = (TitleBarButton.KIND_RESTORE if self.isMaximized()
                else TitleBarButton.KIND_MAX)
        self.btn_max._kind = kind
        self.btn_max.update()

    # ---- 命令栏：按「流管理 / 运行控制 / 工具」分成三组 ---- #
    def _build_command_bar(self) -> GlassCard:
        card = GlassCard(self.config.theme)
        self._cards.append(card)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        manage = self._tool_group([
            self._button("＋  添加流", self.on_add, ghost=True, accent=True),
            self._button("编辑", self.on_edit, ghost=True),
            self._button("删除", self.on_delete, ghost=True, danger=True),
        ])
        layout.addWidget(manage)

        run = self._tool_group([
            self._button("启动", self.on_start, ghost=True),
            self._button("停止", self.on_stop, ghost=True),
            Divider(self.config.theme, vertical=True),
            self._button("全部启动", self.on_start_all, ghost=True),
            self._button("全部停止", self.on_stop_all, ghost=True),
        ], spacing=4)
        layout.addWidget(run)

        layout.addStretch(1)

        tools = self._tool_group([
            self._button("载入示例流", self.on_sample, ghost=True),
            self._button("设置", self.on_settings, ghost=True),
        ])
        layout.addWidget(tools)
        return card

    def _tool_group(self, buttons: List[QWidget], spacing: int = 2) -> QFrame:
        """把一组动作装进下沉胶囊，用间距而不是边框表达「它们是一伙的」。"""
        group = QFrame()
        group.setObjectName("ToolGroup")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(spacing)
        for widget in buttons:
            layout.addWidget(widget)
        return group

    # ---- 流列表 ---- #
    def _build_table_card(self) -> GlassCard:
        card = GlassCard(self.config.theme)
        card.setMinimumWidth(520)
        self._cards.append(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        title = QLabel("流列表")
        title.setObjectName("SectionTitle")
        head_row.addWidget(title)
        self.table_count = QLabel("")
        self.table_count.setObjectName("SectionHint")
        head_row.addWidget(self.table_count)
        head_row.addStretch(1)
        self.table_summary = QLabel("")
        self.table_summary.setObjectName("SectionHint")
        head_row.addWidget(self.table_summary)
        layout.addLayout(head_row)

        self.table = QTableWidget(0, len(TABLE_HEADERS))
        self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.setMouseTracking(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        header_view = self.table.horizontalHeader()
        header_view.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setStretchLastSection(False)
        header_view.setHighlightSections(False)
        header_view.setFixedHeight(38)
        for index, width in enumerate(COLUMN_WIDTHS):
            self.table.setColumnWidth(index, width)
        # 表头对齐跟随数据列：状态与名称靠左，数字列居中
        for index in range(len(TABLE_HEADERS)):
            header_item = self.table.horizontalHeaderItem(index)
            if header_item is not None:
                align = (Qt.AlignmentFlag.AlignLeft if index < 2 else Qt.AlignmentFlag.AlignHCenter)
                header_item.setTextAlignment(int(align | Qt.AlignmentFlag.AlignVCenter))
        self.table.itemSelectionChanged.connect(self._update_detail)
        self.table.cellDoubleClicked.connect(lambda *_: self.on_edit())

        self._row_delegate = _RowDelegate(self.table)
        self._row_delegate.tok = self._tok
        self.table.setItemDelegate(self._row_delegate)

        self.table_empty = EmptyState("还没有任何流",
                                      "点上方「添加流」选择本地视频，或先「载入示例流」试一下")
        self.table_empty.hint_label.setMaximumWidth(360)

        self.table_stack = QStackedWidget()
        self.table_stack.addWidget(self.table)
        self.table_stack.addWidget(self.table_empty)
        layout.addWidget(self.table_stack, 1)
        return card

    # ---- 右侧详情：标题 + 状态 + 信息网格 + 地址卡 ---- #
    def _build_detail_panel(self) -> GlassCard:
        panel = GlassCard(self.config.theme)
        panel.setMinimumWidth(360)
        self._cards.append(panel)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(0)

        self.detail_stack = QStackedWidget()
        self.detail_empty = EmptyState("未选择流",
                                       "在左侧列表中点选一路流，这里会显示编码参数与对外地址")
        self.detail_empty.hint_label.setMaximumWidth(260)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.detail_name = QLabel("未选择流")
        self.detail_name.setObjectName("Title")
        self.detail_name.setStyleSheet("font-size: 16px;")
        head.addWidget(self.detail_name, 1)
        self.detail_clients = QLabel("")
        self.detail_clients.setObjectName("SectionHint")
        head.addWidget(self.detail_clients, 0, Qt.AlignmentFlag.AlignVCenter)
        self.detail_pill = StatusPill(self.config.theme)
        self.detail_pill.set_status("—", self._tok["muted"])
        head.addWidget(self.detail_pill, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(head)

        self.detail_source = QLabel("在左侧列表中选择一路流查看详情")
        self.detail_source.setObjectName("Hint")
        self.detail_source.setWordWrap(True)
        self.detail_source.setSizePolicy(QSizePolicy.Policy.Preferred,
                                         QSizePolicy.Policy.Minimum)
        layout.addWidget(self.detail_source)

        # 信息网格：两列键值，窄栏里比长句子更好扫读。
        # 用 QGridLayout 而不是两个竖列：同一行的两项共享行高，长值换行时不会把
        # 左右两列错开成阶梯。
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.kv_policy = KeyValue("播放策略", self.config.theme)
        self.kv_codec = KeyValue("编码", self.config.theme)
        self.kv_main = KeyValue("主码流", self.config.theme, mono=True)
        self.kv_sub = KeyValue("子码流", self.config.theme, mono=True)
        grid.addWidget(self.kv_policy, 0, 0)
        grid.addWidget(self.kv_codec, 0, 1)
        grid.addWidget(self.kv_main, 1, 0)
        grid.addWidget(self.kv_sub, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        layout.addWidget(Divider(self.config.theme))

        url_head = QHBoxLayout()
        url_head.setSpacing(8)
        url_title = QLabel("对外 RTSP 地址")
        url_title.setObjectName("SectionTitle")
        url_head.addWidget(url_title)
        self.url_count = QLabel("")
        self.url_count.setObjectName("SectionHint")
        url_head.addWidget(self.url_count)
        url_head.addStretch(1)
        self.copy_all_btn = QPushButton("复制全部")
        self.copy_all_btn.setObjectName("Tiny")
        self.copy_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_all_btn.clicked.connect(self.on_copy)
        url_head.addWidget(self.copy_all_btn)
        layout.addLayout(url_head)

        # 所有地址共享同一前缀，单独标一次；行内只列路径差异
        self.url_prefix = QLabel("")
        self.url_prefix.setObjectName("UrlCaption")
        self.url_prefix.setStyleSheet(
            f'font-family: {MONO_FAMILY}; font-size: 11px;')
        layout.addWidget(self.url_prefix)

        self.url_scroll = QScrollArea()
        self.url_scroll.setWidgetResizable(True)
        self.url_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.url_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.url_scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.url_holder = QWidget()
        self.url_holder.setStyleSheet("background: transparent;")
        self.url_layout = QVBoxLayout(self.url_holder)
        self.url_layout.setContentsMargins(0, 0, 0, 0)
        self.url_layout.setSpacing(6)
        self.url_layout.addStretch(1)
        self.url_scroll.setWidget(self.url_holder)
        layout.addWidget(self.url_scroll, 1)

        self.detail_hint = QLabel("")
        self.detail_hint.setObjectName("Warn")
        self.detail_hint.setWordWrap(True)
        self.detail_hint.setVisible(False)
        layout.addWidget(self.detail_hint)

        self.detail_stack.addWidget(self.detail_empty)
        self.detail_stack.addWidget(content)
        panel_layout.addWidget(self.detail_stack)
        return panel

    # ---- 日志：可折叠成一条窄栏 ---- #
    def _build_log_card(self) -> GlassCard:
        card = GlassCard(self.config.theme)
        self._cards.append(card)
        self.log_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 10, 16, 12)
        layout.setSpacing(8)

        head_row = QHBoxLayout()
        head_row.setSpacing(8)
        log_title = QLabel("运行日志")
        log_title.setObjectName("SectionTitle")
        head_row.addWidget(log_title)
        self.log_count = QLabel("")
        self.log_count.setObjectName("SectionHint")
        head_row.addWidget(self.log_count)
        head_row.addStretch(1)
        self.debug_check = QCheckBox("显示协议细节")
        self.debug_check.toggled.connect(self._on_debug_toggled)
        head_row.addWidget(self.debug_check)
        clear_btn = QPushButton("清空")
        clear_btn.setObjectName("Tiny")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        head_row.addWidget(clear_btn)
        self.log_toggle = QPushButton("收起")
        self.log_toggle.setObjectName("Tiny")
        self.log_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.log_toggle.clicked.connect(self._toggle_log)
        head_row.addWidget(self.log_toggle)
        layout.addLayout(head_row)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("暂无日志…")
        self.log_view.setFixedHeight(96)
        self.log_view.document().setMaximumBlockCount(1200)
        layout.addWidget(self.log_view)
        return card

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        footer.setFixedHeight(20)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(14)

        self.footer_listen = QLabel("")
        self.footer_run = QLabel("")
        self.footer_url = QLabel("")
        for label in (self.footer_listen, self.footer_run, self.footer_url):
            label.setObjectName("FooterText")
        layout.addWidget(self.footer_listen)
        layout.addWidget(self.footer_run)
        layout.addStretch(1)
        layout.addWidget(self.footer_url)
        return footer

    def _toggle_log(self) -> None:
        """折叠日志：只隐藏正文，标题条保留，随时能展开。"""
        self._log_expanded = not self._log_expanded
        self.log_view.setVisible(self._log_expanded)
        self.log_toggle.setText("收起" if self._log_expanded else "展开")

    def _button(self, text: str, slot, ghost: bool = False, accent: bool = False,
                danger: bool = False) -> QPushButton:
        button = QPushButton(text)
        if ghost:
            button.setObjectName("GhostAccent" if accent else ("GhostDanger" if danger else "Ghost"))
        elif danger:
            button.setObjectName("Danger")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------------ #
    # 启动流程
    # ------------------------------------------------------------------ #
    def boot(self) -> None:
        if not self.config.advertise_ip:
            self.config.advertise_ip = local_ipv4()[0]
            self.config.save()

        ffmpeg = self.engine.ffmpeg_path
        self.append_log("info", f"FFmpeg：{ffmpeg or '未找到，请到「设置」里指定'}")
        self.append_log("info", f"配置文件：{CONFIG_PATH}")

        try:
            self.engine.start_server()
            self.append_log("info", f"RTSP 服务已监听 {self.config.host}:{self.config.port}")
        except Exception as exc:
            self.append_log("error", f"RTSP 服务启动失败：{exc}")

        if self.config.auto_start:
            self.engine.start_all()
        self._refresh()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _on_debug_toggled(self, checked: bool) -> None:
        self._show_debug = checked
        if checked:
            self.append_log("info", "已开启协议细节日志：后续客户端的 OPTIONS / DESCRIBE / SETUP 请求会显示在这里")

    def append_log(self, level: str, message: str) -> None:
        if level == "debug" and not self._show_debug:
            return
        color = self._log_color.get(level, self._log_color["info"])
        stamp = time.strftime("%H:%M:%S")
        body = html.escape(message).replace("\n", "<br/>")
        self.log_view.appendHtml(
            f'<span style="color:{self._tok["log_time"]}">{stamp}</span>&nbsp;&nbsp;'
            f'<span style="color:{color}">{body}</span>'
        )
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        self.log_count.setText(f"{self.log_view.document().blockCount()} 条")

    # ------------------------------------------------------------------ #
    # 表格与详情刷新
    # ------------------------------------------------------------------ #
    def _refresh(self) -> None:
        running_server = self.engine.server_running
        stats = self.engine.stats()

        if running_server:
            self.status_pill.set_status("服务运行中", self._state_color["running"])
            self.server_btn.setText("停止服务")
        else:
            self.status_pill.set_status("服务未启动", self._state_color["stopped"])
            self.server_btn.setText("启动服务")

        specs = self.config.streams
        ids = [spec.id for spec in specs]
        if ids != self._row_ids:
            self.table.setRowCount(len(specs))
            for row in range(len(specs)):
                for col in range(self.table.columnCount()):
                    if self.table.item(row, col) is None:
                        item = QTableWidgetItem("")
                        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                        self.table.setItem(row, col, item)
            self._row_ids = ids

        self.table_stack.setCurrentWidget(self.table if len(specs) > 0 else self.table_empty)
        self.table_count.setText(f"共 {len(specs)} 路")

        active_clients = 0
        running_streams = 0
        for row, spec in enumerate(specs):
            info = stats.get(spec.id)
            if info:
                state = info["state"]
                clients = info["clients"]
                error = info["error"]
            else:
                state = "stopped" if running_server else "idle"
                clients = 0
                error = ""
            if state == "running":
                running_streams += 1
            active_clients += clients

            values = [
                STATE_TEXT.get(state, state),
                spec.name,
                "H264" if spec.codec == "h264" else "H265",
                f"{spec.width}×{spec.height}",
                f"{spec.fps:g}",
                str(spec.kbps),
                (f"{spec.sub_width}×{spec.sub_height}" if spec.sub_active else "—"),
                str(clients),
                f"{len(spec.routes)} 条",
            ]
            for col, value in enumerate(values):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem("")
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(row, col, item)
                if item.text() != value:
                    item.setText(value)
                if col == 0:
                    item.setForeground(QColor(self._state_color.get(state, self._tok["muted"])))
                    item.setToolTip(error or "")
                elif col == 8:
                    item.setToolTip("\n".join(self.engine.url_for(r.path) for r in spec.routes))
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                elif col in (2, 3, 4, 5, 6, 7, 8):
                    # 规格数字用次要色：一列列纯白等宽字会比名称更抢眼，也更容易刺眼
                    item.setForeground(QColor(self._tok["secondary"]))
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
                else:
                    item.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))

        self.table_summary.setText(
            f"运行 {running_streams} · 客户端 {active_clients}" if specs else "")

        self.footer_listen.setText(f"监听 {self.config.host}:{self.config.port}")
        self.footer_run.setText(f"运行中流 {running_streams}/{len(specs)}　·　客户端 {active_clients}")
        self.footer_url.setText(f"对外前缀 rtsp://{self.config.advertise_ip}:{self.config.port}")
        self._update_detail()

    def _selected_spec(self) -> Optional[StreamSpec]:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.config.streams):
            return None
        return self.config.streams[row]

    def _sync_url_rows(self, entries: List[tuple]) -> None:
        """按需创建 / 复用地址行，多余的隐藏（不销毁，避免频繁抖动）。"""
        while len(self._url_rows) < len(entries):
            row = UrlRow(self.config.theme, on_copy=self._copy_one)
            self._url_rows.append(row)
            self.url_layout.insertWidget(self.url_layout.count() - 1, row)
        for index, row in enumerate(self._url_rows):
            if index < len(entries):
                row.set_content(*entries[index])
                row.setVisible(True)
            else:
                row.setVisible(False)

    def _copy_one(self, url: str) -> None:
        QApplication.clipboard().setText(url)
        self.append_log("info", f"已复制地址：{url}")

    def _update_detail(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            self.detail_stack.setCurrentWidget(self.detail_empty)
            self.detail_name.setText("未选择流")
            self.detail_source.setText("在左侧列表中选择一路流查看详情")
            self.detail_pill.set_status("—", self._tok["muted"])
            self.detail_clients.setText("")
            for kv in (self.kv_policy, self.kv_codec, self.kv_main, self.kv_sub):
                kv.set_value("—")
            self._sync_url_rows([])
            self.url_count.setText("")
            self.url_prefix.setText("")
            self.detail_hint.setText("")
            self.detail_hint.setVisible(False)
            return

        self.detail_stack.setCurrentIndex(1)
        info = self.engine.stats().get(spec.id)
        state = info["state"] if info else ("stopped" if self.engine.server_running else "idle")
        self.detail_name.setText(spec.name)
        self.detail_pill.set_status(STATE_TEXT.get(state, state),
                                    self._state_color.get(state, self._tok["muted"]))
        self.detail_source.setText(f"{describe_source(spec.source)}　·　{source_kind(spec.source)}")
        self.kv_policy.set_value(spec.loop_text)
        self.kv_codec.set_value(f"{'H.264' if spec.codec == 'h264' else 'H.265'}　@ {spec.fps:g}fps")
        self.kv_main.set_value(f"{spec.width}×{spec.height} · {spec.kbps}kbps")
        if spec.sub_active:
            sub_state = STATE_TEXT.get((info or {}).get("sub_state", ""), "未知")
            self.kv_sub.set_value(f"{spec.sub_width}×{spec.sub_height} · {spec.sub_kbps}kbps")
            self.kv_sub.key_label.setText(f"子码流 · {sub_state}")
            self.detail_clients.setText(
                f"客户端 主 {info['clients'] if info else 0} · 子 {info['sub_clients'] if info else 0}")
        else:
            self.kv_sub.key_label.setText("子码流")
            self.kv_sub.set_value("未启用")
            self.detail_clients.setText(f"客户端 {info['clients'] if info else 0}")

        entries = []
        for route in spec.routes:
            caption = route_label(route.path)
            if spec.sub_active:
                size = (f"{spec.sub_width}×{spec.sub_height}" if is_sub_route(route)
                        else f"{spec.width}×{spec.height}")
                caption = f"{caption} · {size}"
            entries.append((self.engine.url_for(route.path), route.path, caption))
        self._sync_url_rows(entries)
        self.url_count.setText(f"{len(entries)} 条")
        self.url_prefix.setText(self.engine.url_for(""))

        hint = ""
        if info and info.get("error"):
            hint = "错误信息：" + info["error"]
        self.detail_hint.setText(hint)
        self.detail_hint.setVisible(bool(hint))

    # ------------------------------------------------------------------ #
    # 服务
    # ------------------------------------------------------------------ #
    def on_toggle_server(self) -> None:
        if self.engine.server_running:
            self.engine.stop_server()
            self.append_log("info", "RTSP 服务已停止")
        else:
            try:
                self.engine.start_server()
                self.append_log("info", f"RTSP 服务已监听 {self.config.host}:{self.config.port}")
            except Exception as exc:
                self.append_log("error", f"启动失败：{exc}")
                QMessageBox.warning(self, "启动失败", str(exc))
        self._refresh()

    # ------------------------------------------------------------------ #
    # 流操作
    # ------------------------------------------------------------------ #
    def on_add(self) -> None:
        dialog = StreamDialog(self.config, used_paths=self.config.used_paths(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.spec is None:
            return
        self.config.streams.append(dialog.spec)
        self.config.save()
        self.append_log("info", f"已添加流「{dialog.spec.name}」")
        self._refresh()
        if self.engine.server_running:
            self._start_spec(dialog.spec)

    def on_edit(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        used = {path: name for path, name in self.config.used_paths().items() if name != spec.name}
        dialog = StreamDialog(self.config, spec=spec, used_paths=used, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.spec is None:
            return
        new_spec = dialog.spec
        new_spec.id = spec.id
        was_running = self.engine.is_stream_running(spec.id)
        index = self.config.streams.index(spec)
        self.engine.update_stream(spec.id, new_spec)
        self.config.streams[index] = new_spec
        self.config.save()
        self.append_log("info", f"已更新流「{new_spec.name}」" + ("，并已重新启动" if was_running else ""))
        self._refresh()

    def on_delete(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        answer = QMessageBox.question(
            self, "删除流", f"确定删除「{spec.name}」吗？该流的 RTSP 地址将立即失效。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.engine.remove_stream(spec.id)
        self.config.streams.remove(spec)
        self.config.save()
        self.append_log("info", f"已删除流「{spec.name}」")
        self._refresh()

    def _start_spec(self, spec: StreamSpec) -> None:
        try:
            self.engine.start_stream(spec)
            self.append_log("info", f"「{spec.name}」开始对外提供 RTSP 流")
        except Exception as exc:
            self.append_log("error", f"「{spec.name}」启动失败：{exc}")
            QMessageBox.warning(self, "启动失败", str(exc))
        self._refresh()

    def on_start(self) -> None:
        spec = self._selected_spec()
        if spec is not None:
            self._start_spec(spec)

    def on_stop(self) -> None:
        spec = self._selected_spec()
        if spec is not None:
            self.engine.stop_stream(spec.id)
            self.append_log("info", f"「{spec.name}」已停止")
            self._refresh()

    def on_start_all(self) -> None:
        if not self.engine.server_running:
            QMessageBox.information(self, "服务未启动", "请先启动 RTSP 服务。")
            return
        self.engine.start_all()
        self._refresh()

    def on_stop_all(self) -> None:
        self.engine.stop_all()
        self.append_log("info", "已停止全部流")
        self._refresh()

    def on_copy(self) -> None:
        spec = self._selected_spec()
        if spec is None:
            return
        text = "\n".join(self.engine.url_for(route.path) for route in spec.routes)
        QApplication.clipboard().setText(text)
        self.append_log("info", f"已复制「{spec.name}」的 {len(spec.routes)} 条地址到剪贴板")

    # ------------------------------------------------------------------ #
    def on_sample(self) -> None:
        sample = os.path.join(PROJECT_ROOT, "samples", "demo_720p.mp4")
        if not os.path.isfile(sample):
            QMessageBox.information(self, "没有示例视频",
                                    f"未找到示例文件：\n{sample}\n请先添加自己的视频。")
            return
        routes = [Route("/Streaming/Channels/101", "hikvision"),
                  Route("/cam/realmonitor?channel=1&subtype=0", "dahua")]
        used = self.config.used_paths()
        blocked = [r.path for r in routes if r.path in used]
        if blocked:
            QMessageBox.information(self, "路径已被占用", "示例路径已被占用：\n" + "\n".join(blocked))
            return
        spec = StreamSpec(name="示例流", source=sample, codec="h264",
                          width=1280, height=720, fps=25.0, kbps=1500, routes=routes)
        self.config.streams.append(spec)
        self.config.save()
        self.append_log("info", "已载入示例流（海康 101 + 大华 realmonitor 两条路径）")
        self._refresh()
        if self.engine.server_running:
            self._start_spec(spec)

    def on_settings(self) -> None:
        running_ids = self.engine.running_spec_ids()
        dialog = SettingsDialog(self.config, local_ipv4(), parent=self,
                                on_theme_change=self.apply_theme)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.config.save()
        self.append_log("info", "设置已保存")
        if self.engine.server_running:
            self.engine.stop_server()
            try:
                self.engine.start_server()
            except Exception as exc:
                self.append_log("error", f"重启服务失败：{exc}")
            for spec_id in running_ids:
                spec = self.config.stream_by_id(spec_id)
                if spec:
                    self._start_spec(spec)
            self.append_log("info", "服务已按新设置重启")
        self._refresh()
