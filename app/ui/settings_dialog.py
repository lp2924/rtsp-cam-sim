"""全局设置对话框。"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..media.pipeline import find_ffmpeg
from ..models import AppConfig
from ..ui.theme import normalize_theme, polish_scroll_holders
from .widgets import Divider, SectionCard


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, local_ips: List[str], parent=None,
                 on_theme_change: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(620)
        self.resize(640, 740)
        screen = self.screen()
        if screen is not None:
            # 小屏笔记本上别让对话框超出可用高度，内容交给滚动区
            self.resize(640, min(740, max(520, screen.availableGeometry().height() - 80)))
        self.config = config
        self.local_ips = local_ips
        self._on_theme_change = on_theme_change
        self._loading = True
        self._sections: List[SectionCard] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        self._scroll = QScrollArea()
        scroll = self._scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(18, 4, 18, 18)
        content_layout.setSpacing(12)
        scroll.setWidget(content)

        self._build_widgets()

        content_layout.addWidget(self._network_section())
        content_layout.addWidget(self._encoding_section())
        content_layout.addWidget(self._ui_section())
        content_layout.addWidget(self._bilibili_section())
        content_layout.addStretch(1)

        outer.addWidget(self._build_footer())

        self._loading = False
        polish_scroll_holders(self, normalize_theme(self.config.theme))

    # ------------------------------------------------------------------ #
    # 骨架
    # ------------------------------------------------------------------ #
    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("DialogHeader")
        layout = QVBoxLayout(header)
        layout.setContentsMargins(18, 16, 18, 10)
        layout.setSpacing(4)
        title = QLabel("设置")
        title.setObjectName("Title")
        title.setStyleSheet("font-size: 20px;")
        subtitle = QLabel("网络监听、发布地址与编码通道；保存后正在运行的服务会按新设置重启")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(2)
        layout.addWidget(Divider(self.config.theme))
        return header

    def _build_widgets(self) -> None:
        self.host_combo = QComboBox()
        self.host_combo.setEditable(True)
        for item in ["0.0.0.0"] + self.local_ips:
            self.host_combo.addItem(item)
        self.host_combo.setCurrentText(self.config.host)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(self.config.port)

        self.ip_combo = QComboBox()
        self.ip_combo.setEditable(True)
        for item in self.local_ips:
            self.ip_combo.addItem(item)
        if self.config.advertise_ip:
            self.ip_combo.setCurrentText(self.config.advertise_ip)
        elif self.local_ips:
            self.ip_combo.setCurrentIndex(0)

        self.auto_start = QCheckBox("启动程序后自动开启所有已配置的流")
        self.auto_start.setChecked(self.config.auto_start)

        self.ffmpeg_edit = QLineEdit(self.config.ffmpeg)
        self.ffmpeg_edit.setPlaceholderText("留空则自动检测（PATH 与常见安装目录）")
        browse_btn = QDialogButtonBox()
        browse = browse_btn.addButton("浏览…", QDialogButtonBox.ButtonRole.ActionRole)
        browse.clicked.connect(self._pick_ffmpeg)
        detect = browse_btn.addButton("自动检测", QDialogButtonBox.ButtonRole.ActionRole)
        detect.clicked.connect(self._detect_ffmpeg)
        self.ffmpeg_row = QHBoxLayout()
        self.ffmpeg_row.setSpacing(8)
        self.ffmpeg_row.addWidget(self.ffmpeg_edit, 1)
        self.ffmpeg_row.addWidget(browse)
        self.ffmpeg_row.addWidget(detect)
        self.ffmpeg_widget = QWidget()
        self.ffmpeg_widget.setLayout(self.ffmpeg_row)

        self.preset_combo = QComboBox()
        for label, value in [("ultrafast（最快，画质最低）", "ultrafast"),
                             ("veryfast（推荐）", "veryfast"),
                             ("faster", "faster"),
                             ("medium（最慢，画质最好）", "medium")]:
            self.preset_combo.addItem(label, value)
        self.preset_combo.setCurrentIndex(max(self.preset_combo.findData(self.config.preset), 0))

        self.mtu_spin = QSpinBox()
        self.mtu_spin.setRange(576, 1472)
        self.mtu_spin.setValue(self.config.mtu)
        self.mtu_spin.setSuffix(" 字节")

        self.pull_transport_combo = QComboBox()
        self.pull_transport_combo.addItem("TCP（推荐，跨网段更稳）", "tcp")
        self.pull_transport_combo.addItem("UDP（低延迟，可能丢包）", "udp")
        self.pull_transport_combo.setCurrentIndex(
            max(self.pull_transport_combo.findData(self.config.rtsp_input_transport), 0))

        # 界面主题：与主窗口顶栏分段控件同一选项，切换实时生效并互相同步
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色（Obsidian 极光）", "dark")
        self.theme_combo.addItem("浅色（Frost 霜面）", "light")
        self.theme_combo.setCurrentIndex(
            max(self.theme_combo.findData(normalize_theme(self.config.theme)), 0))
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)

        self.bili_cookie_edit = QLineEdit(self.config.bilibili_cookie)
        self.bili_cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.bili_cookie_edit.setPlaceholderText("可选：SESSDATA=xxxx（留空则匿名，只给到 1080P 上限）")
        self.bili_fhd_check = QCheckBox("要求 1080P：拿不到就报错，不静默降级")
        self.bili_fhd_check.setChecked(self.config.bilibili_require_fhd)

    def _network_section(self) -> SectionCard:
        card = SectionCard("网络与发布", "服务监听在本机，客户端用对外地址连进来",
                           self.config.theme)
        card.add_field("监听地址", self.host_combo)
        card.add_field("监听端口", self.port_spin)
        card.add_field("对外发布 IP", self.ip_combo)
        card.add_full(self.auto_start)
        card.add_footer("对外发布 IP 用于拼接 RTSP 地址，填本机在测试网络中的地址；"
                        "监听地址填 0.0.0.0 表示接受任意网卡连接。")
        self._sections.append(card)
        return card

    def _encoding_section(self) -> SectionCard:
        card = SectionCard("编码通道", "ffmpeg 编码参数，影响 CPU 占用与画质",
                           self.config.theme)
        card.add_field("FFmpeg 路径", self.ffmpeg_widget)
        card.add_field("速度预设", self.preset_combo)
        card.add_field("拉流传输", self.pull_transport_combo)
        card.add_field("RTP 分片", self.mtu_spin)
        card.add_footer("速度预设越快画质越低；RTP 分片建议 1400 字节（标准以太网 MTU 1500）。"
                        "「拉流传输」只影响本项目去拉 rtsp:// 源时使用的通道。")
        self._sections.append(card)
        return card

    def _ui_section(self) -> SectionCard:
        card = SectionCard("界面", "切换即时生效，不重启编码器、不断开已连客户端",
                           self.config.theme)
        card.add_field("界面主题", self.theme_combo)
        self._sections.append(card)
        return card

    def _bilibili_section(self) -> SectionCard:
        card = SectionCard("哔哩哔哩源", "只影响填了 bilibili.com 视频页的流",
                           self.config.theme)
        card.add_field("Cookie", self.bili_cookie_edit)
        card.add_full(self.bili_fhd_check)
        card.add_footer("可留空：程序默认带 try_look 参数，普通视频匿名即可解析到 1080P。"
                        "若提示「需要登录 / 只能拿到 720P」，再填浏览器里的 SESSDATA"
                        "（B 站 → F12 → Application → Cookies）。")
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

        # 按钮栏固定在底部，带独立背景与上边线，不压住滚动区内的内容
        footer = QWidget()
        footer.setObjectName("DialogFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(18, 12, 18, 14)
        footer_layout.addWidget(buttons)
        return footer

    # ------------------------------------------------------------------ #
    def _on_theme_changed(self) -> None:
        """主题下拉切换：实时通知主窗口应用（不重启编码器、不断开客户端）。"""
        if self._loading:
            return
        theme = normalize_theme(self.theme_combo.currentData())
        self.config.theme = theme
        for card in self._sections:
            card.set_theme(theme)
        if self._on_theme_change is not None:
            self._on_theme_change(theme)

    def _pick_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 ffmpeg.exe", "", "可执行文件 (*.exe);;所有文件 (*.*)")
        if path:
            self.ffmpeg_edit.setText(path)

    def _detect_ffmpeg(self) -> None:
        found = find_ffmpeg(self.ffmpeg_edit.text().strip() or None)
        if found:
            self.ffmpeg_edit.setText(found)
        else:
            QMessageBox.warning(self, "未找到 ffmpeg", "没有检测到 ffmpeg.exe，请手动指定路径。")

    def _on_accept(self) -> None:
        if self.ffmpeg_edit.text().strip() and not find_ffmpeg(self.ffmpeg_edit.text().strip()):
            QMessageBox.warning(self, "路径无效", "指定的 ffmpeg 路径不存在。")
            return
        self.config.host = self.host_combo.currentText().strip() or "0.0.0.0"
        self.config.port = self.port_spin.value()
        self.config.advertise_ip = self.ip_combo.currentText().strip()
        self.config.ffmpeg = self.ffmpeg_edit.text().strip()
        self.config.preset = self.preset_combo.currentData() or "veryfast"
        self.config.rtsp_input_transport = self.pull_transport_combo.currentData() or "tcp"
        self.config.mtu = self.mtu_spin.value()
        self.config.bilibili_cookie = self.bili_cookie_edit.text().strip()
        self.config.bilibili_require_fhd = self.bili_fhd_check.isChecked()
        self.config.auto_start = self.auto_start.isChecked()
        self.config.theme = normalize_theme(self.theme_combo.currentData())
        self.accept()
