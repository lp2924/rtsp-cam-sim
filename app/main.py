"""程序入口。"""

from __future__ import annotations

import os
import sys
import traceback

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from .engine import Engine
from .models import PROJECT_ROOT, AppConfig
from .ui.main_window import MainWindow
from .ui.theme import prepare_qss, set_windows_titlebar_dark


def _app_icon() -> QIcon:
    """运行时绘制应用图标（青绿摄像机方块），避免打包额外二进制资源。

    注意：PySide6/Qt6 没有 ``drawRoundedRect(x, y, w, h, r)`` 这种 5 个整数的重载，
    必须传 ``QRectF`` 且圆角是 (xRadius, yRadius) 两个参数，否则会在启动时抛
    TypeErrors（曾导致 onefile exe 一打开就报 Unhandled exception）。
    """
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)

    painter.setBrush(QColor("#2FD3B0"))
    painter.drawRoundedRect(QRectF(6, 14, 52, 36), 10, 10)
    painter.setBrush(QColor("#0E1114"))
    painter.drawRoundedRect(QRectF(16, 23, 24, 18), 5, 5)
    painter.setBrush(QColor("#2FD3B0"))
    painter.drawEllipse(22, 27, 12, 10)
    painter.setBrush(QColor("#0E1114"))
    painter.drawEllipse(25, 29, 6, 6)
    painter.end()
    return QIcon(pixmap)


def _find_asset(*parts: str) -> str:
    """在「打包资源目录（_MEIPASS）/ 源码项目根」里找资源，找不到返回空串。"""
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    roots.append(PROJECT_ROOT)
    for root in roots:
        path = os.path.join(root, *parts)
        if os.path.isfile(path):
            return path
    return ""


class LogBridge(QObject):
    """把工作线程里的日志安全地转发到 GUI 线程。"""

    message = Signal(str, str)

    def emit_log(self, level: str, msg: str) -> None:
        self.message.emit(level, msg)


def _load_app_icon() -> QIcon:
    """优先使用 assets/app.ico（源码或 onefile 的 _MEIPASS 里）；缺失时回退到运行时绘制的图标。"""
    ico = _find_asset("assets", "app.ico")
    if ico:
        icon = QIcon(ico)
        if not icon.isNull():
            return icon
    return _app_icon()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("RTSP 摄像头流模拟器")
    app.setWindowIcon(_load_app_icon())

    # 先读配置再套主题：旧配置缺 theme 字段时默认深色
    config = AppConfig.load()
    app.setStyleSheet(prepare_qss(config.theme))

    bridge = LogBridge()
    engine = Engine(config, log=bridge.emit_log)

    window = MainWindow(engine, config)
    bridge.message.connect(window.append_log, Qt.ConnectionType.QueuedConnection)

    window.show()
    set_windows_titlebar_dark(window, config.theme == "dark")
    window.boot()

    def _excepthook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        window.append_log("error", "未捕获异常：\n" + text)
        QMessageBox.critical(window, "程序异常", text[-1500:])

    sys.excepthook = _excepthook
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
