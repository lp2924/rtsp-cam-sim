"""流配置对话框：视频源 + 编码参数 + 多个厂商路径。"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..models import (
    AppConfig, Route, StreamSpec, default_stream_name, is_network_source,
    is_sub_route, source_kind,
)
from ..vendors import VENDORS, get_vendor, route_label
from .theme import MONO_FAMILY, normalize_theme, polish_scroll_holders
from .widgets import Divider, SectionCard

VIDEO_FILTER = (
    "视频文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.ts *.m4v *.webm *.mpg *.mpeg "
    "*.h264 *.h265 *.264 *.265 *.hevc);;所有文件 (*.*)"
)

RESOLUTIONS = [
    (1920, 1080, "1920×1080（1080P）"),
    (1280, 720, "1280×720（720P）"),
    (704, 576, "704×576（D1）"),
    (640, 480, "640×480（VGA）"),
]

# 子码流常见的几档（摄像机出厂默认通常是 D1 或 720P）
SUB_RESOLUTIONS = [
    (1280, 720, "1280×720（720P）"),
    (704, 576, "704×576（D1）"),
    (640, 480, "640×480（VGA）"),
    (352, 288, "352×288（CIF）"),
]


class StreamDialog(QDialog):
    def __init__(self, config: AppConfig, *, spec: StreamSpec | None = None,
                 used_paths: Dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.config = config
        self.used_paths = {k: v for k, v in (used_paths or {}).items()}
        self._editing_id = spec.id if spec else ""
        self._loading = True
        self._path_items: Dict[str, Tuple[QTreeWidgetItem, str, str]] = {}   # path -> (节点, 厂商, main/sub)
        self._sub_autofill = ""          # 最近一次「按厂商辅流默认」自动填入的说明
        self._sections: List[SectionCard] = []
        self.spec: StreamSpec | None = None

        self.setWindowTitle("编辑流配置" if spec else "添加流")
        self.setMinimumSize(720, 480)
        self._build_ui()
        self._fit_to_screen()
        self._load(spec)
        self._loading = False
        self._on_sub_toggled(self.sub_check.isChecked())   # 统一按开关状态启用/禁用子码流控件
        self._sync_preview()
        polish_scroll_holders(self, normalize_theme(self.config.theme))

    def _fit_to_screen(self) -> None:
        """按屏幕可用高度决定初始尺寸，避免小屏笔记本上超出屏幕。"""
        height = 900
        screen = self.screen()
        if screen is not None:
            height = min(900, max(480, screen.availableGeometry().height() - 80))
        self.resize(780, height)

    def scroll_to_routes(self) -> None:
        """滚动到路径树区域（截图 / 定位用），保证核心路径勾选区可见。"""
        area = self.findChild(QScrollArea)
        if area is not None:
            area.ensureWidgetVisible(self.tree, 0, 8)

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self._scroll = QScrollArea()
        scroll = self._scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(scroll, 1)

        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(18, 4, 18, 18)
        root.setSpacing(12)
        scroll.setWidget(content)

        root.addWidget(self._source_section())
        root.addWidget(self._codec_section())
        root.addWidget(self._sub_section())
        root.addWidget(self._route_section())
        root.addWidget(self._preview_section())
        root.addStretch(1)

        outer.addWidget(self._build_footer())

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("DialogHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(18, 16, 18, 10)
        layout.setSpacing(4)
        title = QLabel(self.windowTitle())
        title.setObjectName("Title")
        title.setStyleSheet("font-size: 20px;")
        subtitle = QLabel("一路流 = 一个视频源 + 一套编码参数 + 若干条厂商路径；同一路流可同时模拟多家厂商")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(2)
        layout.addWidget(Divider(self.config.theme))
        return header

    # ---- 视频源 ---- #
    def _source_section(self) -> SectionCard:
        card = SectionCard("视频源", "本地文件或网络地址", self.config.theme)

        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText(
            "本地视频文件，或网络地址：http/https（mp4、m3u8）、rtsp/rtsps、rtmp、udp、srt …")
        self.file_edit.textChanged.connect(self._on_source_changed)
        browse = QPushButton("选择文件…")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self._pick_file)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.file_edit, 1)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        card.add_field("视频源", holder)

        self.kind_label = QLabel("支持本地视频文件，也支持网络地址（断线自动重连）")
        self.kind_label.setObjectName("SectionHint")
        self.kind_label.setWordWrap(True)
        card.add_full(self.kind_label)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("留空则自动取文件名或地址末段")
        card.add_field("流名称", self.name_edit)

        self.loop_check = QCheckBox("播放结束后自动循环（等价于永不结束的摄像机）")
        self.loop_check.setChecked(True)
        self.loop_check.toggled.connect(self._on_loop_changed)
        card.add_full(self.loop_check)

        self.loop_label = QLabel()
        self.loop_label.setObjectName("SectionHint")
        self.loop_label.setWordWrap(True)
        card.add_full(self.loop_label)
        self._sections.append(card)
        return card

    # ---- 编码参数 ---- #
    def _codec_section(self) -> SectionCard:
        card = SectionCard("编码参数", "主码流", self.config.theme)

        self.codec_combo = QComboBox()
        self.codec_combo.addItem("H.264（兼容性最好，推荐）", "h264")
        self.codec_combo.addItem("H.265 / HEVC（码率更低，部分客户端不支持）", "h265")
        self.codec_combo.currentIndexChanged.connect(self._sync_preview)
        card.add_field("编码格式", self.codec_combo)

        self.res_combo = QComboBox()
        for width, height, label in RESOLUTIONS:
            self.res_combo.addItem(label, (width, height))
        self.res_combo.addItem("自定义", (0, 0))
        self.res_combo.currentIndexChanged.connect(self._on_resolution_changed)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(160, 7680)
        self.width_spin.setSingleStep(16)
        self.width_spin.setValue(1920)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(120, 4320)
        self.height_spin.setSingleStep(16)
        self.height_spin.setValue(1080)
        res_row = QHBoxLayout()
        res_row.setSpacing(8)
        res_row.addWidget(self.res_combo, 1)
        self.width_label = QLabel("宽")
        self.height_label = QLabel("高")
        res_row.addWidget(self.width_label)
        res_row.addWidget(self.width_spin)
        res_row.addWidget(self.height_label)
        res_row.addWidget(self.height_spin)
        res_widget = QWidget()
        res_widget.setLayout(res_row)
        card.add_field("分辨率", res_widget)

        param_row = QHBoxLayout()
        param_row.setSpacing(8)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(25)
        self.fps_spin.setSuffix(" fps")
        self.kbps_spin = QSpinBox()
        self.kbps_spin.setRange(128, 20480)
        self.kbps_spin.setSingleStep(128)
        self.kbps_spin.setValue(4096)
        self.kbps_spin.setSuffix(" kbps")
        param_row.addWidget(self.fps_spin, 1)
        param_row.addWidget(self.kbps_spin, 1)
        param_widget = QWidget()
        param_widget.setLayout(param_row)
        card.add_field("帧率 / 码率", param_widget)
        self._sections.append(card)
        return card

    # ---- 子码流（可选，第二编码器）---- #
    def _sub_section(self) -> SectionCard:
        card = SectionCard("子码流", "可选，第二编码器", self.config.theme)

        self.sub_check = QCheckBox("给子流路径单独编码（102 / subtype=1 / video2 这类路径）")
        self.sub_check.toggled.connect(self._on_sub_toggled)
        card.add_full(self.sub_check)

        self.sub_res_combo = QComboBox()
        for width, height, label in SUB_RESOLUTIONS:
            self.sub_res_combo.addItem(label, (width, height))
        self.sub_res_combo.addItem("自定义", (0, 0))
        self.sub_res_combo.currentIndexChanged.connect(self._on_sub_resolution_changed)
        self.sub_width_spin = QSpinBox()
        self.sub_width_spin.setRange(160, 3840)
        self.sub_width_spin.setSingleStep(16)
        self.sub_width_spin.setValue(704)
        self.sub_height_spin = QSpinBox()
        self.sub_height_spin.setRange(120, 2160)
        self.sub_height_spin.setSingleStep(16)
        self.sub_height_spin.setValue(576)
        sub_row = QHBoxLayout()
        sub_row.setSpacing(8)
        sub_row.addWidget(self.sub_res_combo, 1)
        self.sub_width_label = QLabel("宽")
        self.sub_height_label = QLabel("高")
        sub_row.addWidget(self.sub_width_label)
        sub_row.addWidget(self.sub_width_spin)
        sub_row.addWidget(self.sub_height_label)
        sub_row.addWidget(self.sub_height_spin)
        self.sub_res_widget = QWidget()
        self.sub_res_widget.setLayout(sub_row)
        card.add_field("分辨率", self.sub_res_widget)

        self.sub_fps_spin = QSpinBox()
        self.sub_fps_spin.setRange(1, 60)
        self.sub_fps_spin.setValue(25)
        self.sub_fps_spin.setSuffix(" fps")
        self.sub_fps_spin.valueChanged.connect(lambda _v: setattr(self, "_sub_autofill", ""))
        self.sub_kbps_spin = QSpinBox()
        self.sub_kbps_spin.setRange(128, 10240)
        self.sub_kbps_spin.setSingleStep(128)
        self.sub_kbps_spin.setValue(512)
        self.sub_kbps_spin.setSuffix(" kbps")
        self.sub_kbps_spin.valueChanged.connect(lambda _v: setattr(self, "_sub_autofill", ""))
        sub_param_row = QHBoxLayout()
        sub_param_row.setSpacing(8)
        sub_param_row.addWidget(self.sub_fps_spin, 1)
        sub_param_row.addWidget(self.sub_kbps_spin, 1)
        self.sub_param_widget = QWidget()
        self.sub_param_widget.setLayout(sub_param_row)
        card.add_field("帧率 / 码率", self.sub_param_widget)

        # 默认取 D1（704×576）——摄像机的辅流出厂档（等控件齐全后再选，避免回调访问未创建控件）
        for index in range(self.sub_res_combo.count()):
            data = self.sub_res_combo.itemData(index)
            if data and data[0] == 704 and data[1] == 576:
                self.sub_res_combo.setCurrentIndex(index)
                break
        self.sub_width_spin.setValue(704)
        self.sub_height_spin.setValue(576)
        self._toggle_custom_sub_size(False)

        self.sub_hint = QLabel("提示：只有勾选了子流路径（如海康 102）才会真的启用第二个编码器；"
                               "不启用时子流路径与主码流用同一套参数。")
        self.sub_hint.setObjectName("SectionHint")
        self.sub_hint.setWordWrap(True)
        card.add_full(self.sub_hint)
        self._sections.append(card)
        return card

    # ---- 对外路径 ---- #
    def _route_section(self) -> SectionCard:
        card = SectionCard("对外 RTSP 路径", "可多选，同一路流可模拟多个厂商", self.config.theme)

        self.vendor_combo = QComboBox()
        for vendor in VENDORS.values():
            self.vendor_combo.addItem(vendor.label, vendor.key)
        self.vendor_combo.setCurrentIndex(list(VENDORS).index("hikvision"))
        self.vendor_combo.currentIndexChanged.connect(self._apply_vendor_defaults)
        self.type_combo = QComboBox()
        self.type_combo.addItem("主码流", "main")
        self.type_combo.addItem("子码流", "sub")
        self.type_combo.currentIndexChanged.connect(self._apply_vendor_defaults)
        pick_row = QHBoxLayout()
        pick_row.setSpacing(8)
        pick_row.addWidget(self.vendor_combo, 1)
        pick_row.addWidget(self.type_combo)
        pick_widget = QWidget()
        pick_widget.setLayout(pick_row)
        card.add_field("厂商预设", pick_widget)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        # 固定高度：只展开当前厂商，其余收起，一屏之内能看完所有厂商名
        self.tree.setFixedHeight(300)
        self.tree.itemChanged.connect(self._on_tree_changed)
        self._build_tree()
        card.add_full(self.tree)

        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("自定义路径，多个用逗号分隔，例如 /test/main,/test/sub")
        self.custom_edit.textChanged.connect(self._sync_preview)
        card.add_full(self.custom_edit)
        self._sections.append(card)
        return card

    # ---- 地址预览 ---- #
    def _preview_section(self) -> SectionCard:
        card = SectionCard("地址预览", "客户端实际填写的地址", self.config.theme)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(78)
        self.preview.setStyleSheet(
            f'QPlainTextEdit {{ font-family: {MONO_FAMILY}; font-size: 12px; }}')
        card.add_full(self.preview)
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("Warn")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        card.add_full(self.warn_label)
        self._sections.append(card)
        return card

    def _build_footer(self) -> QWidget:
        buttons = QDialogButtonBox()
        ok = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("Primary")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        # 按钮栏固定在底部，带独立背景与上边线，绝不覆盖滚动区内的内容
        footer = QWidget()
        footer.setObjectName("DialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 12, 18, 14)
        footer_layout.addStretch(1)
        footer_layout.addWidget(buttons)
        return footer

    # ------------------------------------------------------------------ #
    def _build_tree(self) -> None:
        self._loading = True
        current_vendor = None
        parent: QTreeWidgetItem | None = None
        for key, label, stream_type, path in self._route_options():
            if key != current_vendor:
                parent = QTreeWidgetItem([label])
                parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                font = parent.font(0)
                font.setBold(True)
                parent.setFont(0, font)
                self.tree.addTopLevelItem(parent)
                current_vendor = key
            child = QTreeWidgetItem([path])
            child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            child.setCheckState(0, Qt.CheckState.Unchecked)
            child.setToolTip(0, route_label(path))
            child.setData(0, Qt.ItemDataRole.UserRole, (key, path, stream_type))
            parent.addChild(child)
            self._path_items[path] = (child, key, stream_type)
        # 只展开当前选中的厂商：15 行全展开会把列表撑成滚动区，反而看不清有哪些厂商
        self.tree.collapseAll()
        current = self.vendor_combo.currentData()
        for index in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(index)
            if node.childCount() and node.child(0).data(0, Qt.ItemDataRole.UserRole):
                vendor_key = node.child(0).data(0, Qt.ItemDataRole.UserRole)[0]
                node.setExpanded(vendor_key == current)

    @staticmethod
    def _route_options():
        for vendor in VENDORS.values():
            yield vendor.key, vendor.label, "main", vendor.main_path
            if vendor.sub_path:
                yield vendor.key, vendor.label, "sub", vendor.sub_path

    # ------------------------------------------------------------------ #
    def _load(self, spec: StreamSpec | None) -> None:
        if spec is None:
            return
        self.file_edit.setText(spec.source)
        self.name_edit.setText(spec.name)
        index = self.codec_combo.findData(spec.codec)
        self.codec_combo.setCurrentIndex(max(index, 0))
        self._set_resolution(spec.width, spec.height)
        self.fps_spin.setValue(int(round(spec.fps)))
        self.kbps_spin.setValue(spec.kbps)
        self.sub_check.setChecked(bool(spec.sub_enabled))
        self._set_sub_resolution(spec.sub_width, spec.sub_height)
        self.sub_fps_spin.setValue(int(round(spec.sub_fps)))
        self.sub_kbps_spin.setValue(spec.sub_kbps)
        self.loop_check.setChecked(bool(spec.loop))
        self._update_loop_hint()

        custom_paths: List[str] = []
        for route in spec.routes:
            entry = self._path_items.get(route.path)
            if entry:
                entry[0].setCheckState(0, Qt.CheckState.Checked)
            else:
                custom_paths.append(route.path)
        if custom_paths:
            self.custom_edit.setText(",".join(custom_paths))

        # 打开已有流时，把已勾选路径所属的厂商展开，用户一眼看到当前配置
        for info in self._path_items.values():
            if info[0].checkState(0) == Qt.CheckState.Checked:
                self._expand_vendor(info[1])
                break

    # ------------------------------------------------------------------ #
    def _on_source_changed(self, text: str) -> None:
        """实时识别填的是本地文件还是网络地址。"""
        value = (text or "").strip()
        lower = value.lower()
        if not value:
            self.kind_label.setText("支持本地视频文件，也支持网络地址与哔哩哔哩视频页")
        elif "bilibili.com/" in lower or "b23.tv/" in lower:
            self.kind_label.setText(
                "识别为：哔哩哔哩视频页　—　启动时自动解析直链（默认要求 1080P，"
                "清晰度不足会报错，可在「设置」里填 SESSDATA）"
            )
        elif is_network_source(value):
            self.kind_label.setText(
                f"识别为：{source_kind(value)}　—　由 ffmpeg 主动拉流，断线后自动重连"
            )
        elif os.path.isfile(value):
            self.kind_label.setText("识别为：本地文件（循环播放，等价于永不结束的摄像机）")
        else:
            self.kind_label.setText("识别为：本地文件路径（文件不存在，请检查）")
        self._update_loop_hint()

    def _on_loop_changed(self, *args) -> None:
        self._update_loop_hint()

    def _update_loop_hint(self) -> None:
        """循环策略的说明随「本地/网络」和勾选状态变化。"""
        network = is_network_source(self.file_edit.text().strip())
        if self.loop_check.isChecked():
            if network:
                text = ("播完自动重播：点播类地址（mp4、有限的 m3u8 列表）放到末尾后重新打开再来一遍；"
                        "直播源本身无限，这条设置不影响它")
            else:
                text = "播完自动重播：从头再来，等价于「永不结束的摄像机」（保留最新 GOP 供中途接入）"
        else:
            if network:
                text = ("播完即停止：源放到末尾后不再重播（出错仍会自动重连），"
                        "流状态变为「已停止」，已连客户端会断开")
            else:
                text = ("播完即停止：放到文件尾后编码进程结束，流状态变为「已停止」，"
                        "已连客户端会断开")
        self.loop_label.setText(text)

    def _pick_file(self) -> None:
        current = self.file_edit.text().strip()
        start = os.path.dirname(current) if current and not is_network_source(current) else ""
        path, _ = QFileDialog.getOpenFileName(self, "选择视频文件", start, VIDEO_FILTER)
        if path:
            self.file_edit.setText(path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(default_stream_name(path))
            self._sync_preview()

    def _on_resolution_changed(self) -> None:
        data = self.res_combo.currentData()
        if data and data[0]:
            self.width_spin.setValue(data[0])
            self.height_spin.setValue(data[1])
        self._toggle_custom_size(bool(data and data[0] == 0))
        self._sync_preview()

    # ---- 子码流控件 ----
    def _on_sub_toggled(self, checked: bool) -> None:
        for widget in (self.sub_res_widget, self.sub_param_widget):
            widget.setEnabled(checked)
        self._refresh_sub_hint()
        self._sync_preview()

    def _has_checked_sub_route(self) -> bool:
        return any(info[2] == "sub" and info[0].checkState(0) == Qt.CheckState.Checked
                   for info in self._path_items.values())

    def _refresh_sub_hint(self) -> None:
        if self.sub_check.isChecked():
            text = ("子码流：勾选的子流路径（102 / subtype=1 / video2）会用下面这套参数单独编码，"
                    "主码流不受影响。")
            if not self._has_checked_sub_route():
                text += "（当前还没勾选任何子流路径，启用后也不会起第二个编码器）"
            elif self._sub_autofill:
                text += f"当前参数已按厂商默认填入：{self._sub_autofill}，可按需修改。"
        elif self._has_checked_sub_route():
            text = "提示：已勾选了子流路径，勾上上方「启用」即可让它单独按另一档分辨率编码；不启用则与主码流同参数。"
        else:
            text = ("提示：只有勾选了子流路径（如海康 102）才会真的启用第二个编码器；"
                    "不启用时子流路径与主码流用同一套参数。")
        self.sub_hint.setText(text)

    def _apply_vendor_sub_default(self, vendor_key: str) -> None:
        """勾上某厂商的子流路径时，按该厂商的辅流出厂参数自动填入。"""
        vendor = get_vendor(vendor_key)
        width, height, fps, kbps = vendor.default_size("sub")
        self._set_sub_resolution(width, height)
        self.sub_fps_spin.setValue(int(round(fps)))
        self.sub_kbps_spin.setValue(kbps)
        self._sub_autofill = f"{width}×{height} @ {int(round(fps))}fps {kbps}kbps"

    def _on_sub_resolution_changed(self) -> None:
        self._sub_autofill = ""          # 用户改过分辨率就不再声称「按厂商默认」
        data = self.sub_res_combo.currentData()
        if data and data[0]:
            self.sub_width_spin.setValue(data[0])
            self.sub_height_spin.setValue(data[1])
        self._toggle_custom_sub_size(bool(data and data[0] == 0))
        self._sync_preview()

    def _toggle_custom_sub_size(self, visible: bool) -> None:
        for widget in (self.sub_width_label, self.sub_width_spin,
                       self.sub_height_label, self.sub_height_spin):
            widget.setVisible(visible)
        self.sub_width_spin.setEnabled(visible)
        self.sub_height_spin.setEnabled(visible)

    def _set_sub_resolution(self, width: int, height: int) -> None:
        for index in range(self.sub_res_combo.count()):
            data = self.sub_res_combo.itemData(index)
            if data and data[0] == width and data[1] == height:
                self.sub_res_combo.setCurrentIndex(index)
                self.sub_width_spin.setValue(width)
                self.sub_height_spin.setValue(height)
                self._toggle_custom_sub_size(False)
                return
        self.sub_res_combo.setCurrentIndex(self.sub_res_combo.count() - 1)
        self.sub_width_spin.setValue(width)
        self.sub_height_spin.setValue(height)
        self._toggle_custom_sub_size(True)

    def _toggle_custom_size(self, visible: bool) -> None:
        """只有「自定义」时才显示宽高手动输入。"""
        for widget in (self.width_label, self.width_spin, self.height_label, self.height_spin):
            widget.setVisible(visible)
        self.width_spin.setEnabled(visible)
        self.height_spin.setEnabled(visible)

    def _set_resolution(self, width: int, height: int) -> None:
        for index in range(self.res_combo.count()):
            data = self.res_combo.itemData(index)
            if data and data[0] == width and data[1] == height:
                self.res_combo.setCurrentIndex(index)
                self.width_spin.setValue(width)
                self.height_spin.setValue(height)
                self._toggle_custom_size(False)
                return
        self.res_combo.setCurrentIndex(self.res_combo.count() - 1)
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        self._toggle_custom_size(True)

    # ------------------------------------------------------------------ #
    def _apply_vendor_defaults(self) -> None:
        if self._loading:
            return
        key = self.vendor_combo.currentData()
        stream_type = self.type_combo.currentData()
        vendor = get_vendor(key)
        width, height, fps, kbps = vendor.default_size(stream_type)
        self._set_resolution(width, height)
        self.fps_spin.setValue(int(round(fps)))
        self.kbps_spin.setValue(kbps)
        self._expand_vendor(key)
        path = vendor.main_path if stream_type == "main" else (vendor.sub_path or vendor.main_path)
        entry = self._path_items.get(path)
        if entry:
            self._loading = True
            entry[0].setCheckState(0, Qt.CheckState.Checked)
            self._loading = False
        self._sync_preview()

    def _expand_vendor(self, vendor_key: str) -> None:
        """只展开指定厂商，其余收起（保持列表短、焦点明确）。"""
        for index in range(self.tree.topLevelItemCount()):
            node = self.tree.topLevelItem(index)
            if node.childCount() == 0:
                continue
            data = node.child(0).data(0, Qt.ItemDataRole.UserRole)
            if data:
                node.setExpanded(data[0] == vendor_key)

    def _on_tree_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data:
            self.vendor_combo.setCurrentIndex(max(self.vendor_combo.findData(data[0]), 0))
        self._expand_vendor(data[0])
        # 勾上某厂商的子流路径 → 自动按该厂商辅流出厂参数填入并启用子码流
        if (data and len(data) > 2 and data[2] == "sub"
                and item.checkState(0) == Qt.CheckState.Checked):
            self._apply_vendor_sub_default(data[0])
            if not self.sub_check.isChecked():
                self.sub_check.setChecked(True)
        self._refresh_sub_hint()
        self._sync_preview()

    # ------------------------------------------------------------------ #
    def collect_routes(self) -> List[Route]:
        routes: List[Route] = []
        seen = set()
        for path, info in self._path_items.items():
            item, vendor_key = info[0], info[1]
            stream_type = info[2] if len(info) > 2 else ""
            if item.checkState(0) == Qt.CheckState.Checked:
                routes.append(Route(path, vendor_key,
                                    sub=(stream_type == "sub") or is_sub_route(Route(path, vendor_key))))
                seen.add(path)
        raw = self.custom_edit.text().replace("，", ",")
        for chunk in raw.split(","):
            path = chunk.strip()
            if not path:
                continue
            if not path.startswith("/"):
                path = "/" + path
            path = re.sub(r"\s+", "", path)
            if path in seen:
                continue
            seen.add(path)
            routes.append(Route(path, "generic", sub=is_sub_route(Route(path, "generic"))))
        return routes

    def _sync_preview(self) -> None:
        if self._loading:
            return
        routes = self.collect_routes()
        ip = self.config.advertise_ip or "127.0.0.1"
        use_sub = self.sub_check.isChecked()
        lines = []
        for route in routes:
            if use_sub:
                size = (f"{self.sub_width_spin.value()}×{self.sub_height_spin.value()}"
                        if route.sub else f"{self.width_spin.value()}×{self.height_spin.value()}")
                lines.append(f"rtsp://{ip}:{self.config.port}{route.path}    [{size}]")
            else:
                lines.append(f"rtsp://{ip}:{self.config.port}{route.path}")
        self.preview.setPlainText("\n".join(lines) or "（尚未选择任何路径）")

        conflicts = []
        for route in routes:
            owner = self.used_paths.get(route.path)
            if owner:
                conflicts.append(f"{route.path} 已被「{owner}」占用")
        if conflicts:
            self.warn_label.setText("路径冲突：" + "；".join(conflicts))
            self.warn_label.setVisible(True)
        else:
            self.warn_label.setText("")
            self.warn_label.setVisible(False)

    # ------------------------------------------------------------------ #
    def _on_accept(self) -> None:
        source = self.file_edit.text().strip()
        if not source:
            QMessageBox.warning(self, "视频源为空", "请选择本地视频文件，或填写网络视频地址。")
            return
        if is_network_source(source):
            if not source.split("://", 1)[1]:
                QMessageBox.warning(self, "地址不完整", "网络地址格式不正确，形如 rtsp://ip:554/Streaming/Channels/101")
                return
        elif not os.path.isfile(source):
            QMessageBox.warning(self, "视频文件无效", "本地文件不存在，请重新选择。")
            return

        name = self.name_edit.text().strip() or default_stream_name(source)
        routes = self.collect_routes()
        if not routes:
            QMessageBox.warning(self, "缺少路径", "请至少勾选或填写一条 RTSP 路径。")
            return
        conflicts = [r.path for r in routes if r.path in self.used_paths]
        if conflicts:
            QMessageBox.warning(self, "路径冲突",
                                "以下路径已被其他流占用：\n" + "\n".join(conflicts))
            return

        width = self.width_spin.value()
        height = self.height_spin.value()
        self.spec = StreamSpec(
            name=name, source=source,
            codec=self.codec_combo.currentData() or "h264",
            width=width, height=height,
            fps=float(self.fps_spin.value()),
            kbps=self.kbps_spin.value(),
            routes=routes,
            loop=self.loop_check.isChecked(),
            sub_enabled=self.sub_check.isChecked(),
            sub_width=self.sub_width_spin.value(),
            sub_height=self.sub_height_spin.value(),
            sub_fps=float(self.sub_fps_spin.value()),
            sub_kbps=self.sub_kbps_spin.value(),
            id=self._editing_id or StreamSpec(name=name, source=source).id,
        )
        self.accept()
