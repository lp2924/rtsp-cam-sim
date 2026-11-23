"""GUI 冒烟测试：真实平台起界面、启动一路流、双主题截图，验证布局与数据联动。

用法：.venv\\Scripts\\python.exe tools\\gui_smoke.py

* 截图由真实主题切换动作产生（window.apply_theme 实时换 QSS），文件名带主题后缀；
* 切主题前后对比编码器子进程 PID 与客户端数，确认换主题不会重启流 / 断开客户端；
* 配置只在内存里构造，不落盘到真实 config.json。
"""

from __future__ import annotations

import os
import sys

if os.environ.get("SMOKE_OFFSCREEN") == "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer, Signal    # noqa: E402
from PySide6.QtGui import QMouseEvent                                     # noqa: E402
from PySide6.QtWidgets import QApplication                                # noqa: E402

from app.engine import Engine                            # noqa: E402
from app.models import AppConfig, Route, StreamSpec      # noqa: E402
from app.ui.main_window import MainWindow, _TitleBar     # noqa: E402
from app.ui.settings_dialog import SettingsDialog        # noqa: E402
from app.ui.stream_dialog import StreamDialog            # noqa: E402
from app.ui.theme import prepare_qss                     # noqa: E402

PORT = int(os.environ.get("SMOKE_PORT", "8556"))
SHOT_DIR = os.path.join(ROOT, "docs")

# 旧版无主题后缀截图（已被双主题版本替换，避免 docs 下留两套重复文件）
LEGACY_SHOTS = [
    "界面预览-主窗口.png", "界面预览-流配置.png", "界面预览-子码流.png",
    "界面预览-网络源.png", "界面预览-循环关闭.png", "界面预览-哔哩哔哩.png",
    "界面预览-设置.png",
]


class Bridge(QObject):
    """日志必须经信号转到 GUI 线程，工作线程不能直接碰控件。"""

    message = Signal(str, str)

    def log(self, level, msg):
        self.message.emit(level, msg)


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(prepare_qss("dark"))

    os.makedirs(SHOT_DIR, exist_ok=True)
    for legacy in LEGACY_SHOTS:
        old = os.path.join(SHOT_DIR, legacy)
        if os.path.isfile(old):
            os.remove(old)

    sample = os.path.join(ROOT, "samples", "demo_720p.mp4")
    config = AppConfig(
        host="127.0.0.1", port=PORT, advertise_ip="192.168.1.146", auto_start=False,
        theme="dark",
        streams=[StreamSpec(
            name="车间门口-摄像机", source=sample, codec="h264",
            width=1920, height=1080, fps=25.0, kbps=4096,
            routes=[Route("/Streaming/Channels/101", "hikvision"),
                    Route("/Streaming/Channels/102", "hikvision", sub=True),
                    Route("/cam/realmonitor?channel=1&subtype=0", "dahua")],
            sub_enabled=True, sub_width=704, sub_height=576,
            sub_fps=25.0, sub_kbps=512,
        )],
    )

    engine = Engine(config, log=lambda level, msg: None)
    # 冒烟全程只在内存构造配置：禁用落盘，绝不写真实 config.json（含主题 / 流列表 / 端口）。
    config.save = lambda *a, **k: None
    window = MainWindow(engine, config)
    bridge = Bridge()
    bridge.message.connect(window.append_log, Qt.ConnectionType.QueuedConnection)
    engine._log = bridge.log
    window.show()
    window.boot()

    engine.start_stream(config.streams[0])

    def snapshot(widget, name: str) -> str:
        app.processEvents()
        shot = widget.grab()
        path = os.path.join(SHOT_DIR, name)
        shot.save(path)
        print("screenshot:", path, shot.width(), "x", shot.height())
        return path

    def switch_theme(theme: str) -> None:
        # save=False：冒烟测试不写真实 config.json
        window.apply_theme(theme, save=False)
        app.processEvents()

    def encoder_pids() -> dict:
        """采集主/子编码器子进程 PID（用于验证切主题不重启编码器）。"""
        pids: dict = {}
        for sid, src in engine.sources.items():
            pids[f"{sid}:main"] = src._proc.pid if getattr(src, "_proc", None) else None
        for sid, src in engine.sub_sources.items():
            pids[f"{sid}:sub"] = src._proc.pid if getattr(src, "_proc", None) else None
        return pids

    def stats_snapshot() -> dict:
        info = engine.stats().get(config.streams[0].id) or {}
        return {"state": info.get("state"), "clients": info.get("clients"),
                "sub_state": info.get("sub_state"), "sub_clients": info.get("sub_clients")}

    def check_titlebar() -> None:
        """标题栏拖动 / 双击回归。

        这里曾因把 startSystemMove 当成 QWidget 方法调用而直接抛 AttributeError，
        静态检查与截图都发现不了，只能用合成鼠标事件压一遍。
        """
        bar = window.findChild(_TitleBar)
        if bar is None:
            print("标题栏：未找到，跳过拖动检查")
            return

        local = (bar.width() // 2, bar.height() // 2)
        gx, gy = window.x() + local[0], window.y() + local[1]

        def send(kind, offset, buttons, button=Qt.MouseButton.NoButton) -> None:
            global_pos = QPointF(gx + offset[0], gy + offset[1])
            QApplication.sendEvent(bar, QMouseEvent(
                kind, QPointF(*local), global_pos, button, buttons,
                Qt.KeyboardModifier.NoModifier))

        send(QEvent.Type.MouseButtonPress, (0, 0), Qt.MouseButton.LeftButton,
             Qt.MouseButton.LeftButton)
        send(QEvent.Type.MouseMove, (2, 0), Qt.MouseButton.LeftButton)
        jitter_grabbed = bar._grabbed
        send(QEvent.Type.MouseMove, (40, 24), Qt.MouseButton.LeftButton)
        crossed = bar._grabbed
        send(QEvent.Type.MouseButtonRelease, (40, 24), Qt.MouseButton.NoButton)
        print(f"标题栏拖动：抖动 2px 不起拖 = {not jitter_grabbed}；越过阈值起拖 = {crossed}")

        before = window.isMaximized()
        send(QEvent.Type.MouseButtonDblClick, (0, 0), Qt.MouseButton.LeftButton,
             Qt.MouseButton.LeftButton)
        app.processEvents()
        maximized = window.isMaximized()
        send(QEvent.Type.MouseButtonDblClick, (0, 0), Qt.MouseButton.LeftButton,
             Qt.MouseButton.LeftButton)
        app.processEvents()
        if window.isMaximized():   # 兜底回正常态，避免后续截图被放大
            window.showNormal()
        print(f"标题栏双击：{before} -> {maximized} -> {window.isMaximized()}（应来回切换）")
        app.processEvents()

    def capture():
        check_titlebar()
        window.table.selectRow(0)
        app.processEvents()

        # ---- 主题切换不打断流：记录基线 PID / 状态 / 客户端数 ----
        base_pids = encoder_pids()
        base_stats = stats_snapshot()
        print("切主题前 PID:", base_pids, "| 状态:", base_stats)

        switch_theme("light")
        mid_pids = encoder_pids()
        mid_stats = stats_snapshot()
        switch_theme("dark")
        after_pids = encoder_pids()
        after_stats = stats_snapshot()
        print("切到浅色 PID:", mid_pids, "| 状态:", mid_stats)
        print("切回深色 PID:", after_pids, "| 状态:", after_stats)
        pid_ok = base_pids == mid_pids == after_pids
        state_ok = (base_stats["state"] == mid_stats["state"] == after_stats["state"]
                    and base_stats["clients"] == mid_stats["clients"] == after_stats["clients"])
        print(f"主题切换不重启编码器：PID 不变 = {pid_ok}；状态/客户端数不变 = {state_ok}")

        # ---- 主窗口：深色 / 浅色 ----
        switch_theme("dark")
        snapshot(window, "界面预览-主窗口-深色.png")
        switch_theme("light")
        snapshot(window, "界面预览-主窗口-浅色.png")

        # ---- 流配置对话框：深色 / 浅色 ----
        switch_theme("dark")
        dialog = StreamDialog(config, spec=config.streams[0],
                              used_paths={"x": "y"}, parent=window)
        dialog.show()
        app.processEvents()
        dialog.scroll_to_routes()
        app.processEvents()
        snapshot(dialog, "界面预览-流配置-深色.png")
        dialog.close()

        switch_theme("light")
        dialog = StreamDialog(config, spec=config.streams[0],
                              used_paths={"x": "y"}, parent=window)
        dialog.show()
        app.processEvents()
        dialog.scroll_to_routes()
        app.processEvents()
        snapshot(dialog, "界面预览-流配置-浅色.png")
        dialog.close()

        # ---- 子码流两档参数：浅色 + 深色 ----
        sub_spec = StreamSpec(
            name="仓库-主子码流", source=sample, codec="h264",
            width=1920, height=1080, fps=25.0, kbps=4096,
            routes=[Route("/Streaming/Channels/101", "hikvision"),
                    Route("/Streaming/Channels/102", "hikvision", sub=True)],
            sub_enabled=True, sub_width=704, sub_height=576, sub_fps=25.0, sub_kbps=512,
        )
        switch_theme("light")
        sub_dialog = StreamDialog(config, spec=sub_spec, used_paths={"s": "t"}, parent=window)
        sub_dialog.show()
        app.processEvents()
        snapshot(sub_dialog, "界面预览-子码流-浅色.png")
        sub_dialog.close()

        switch_theme("dark")
        sub_dialog = StreamDialog(config, spec=sub_spec, used_paths={"s": "t"}, parent=window)
        sub_dialog.show()
        app.processEvents()
        snapshot(sub_dialog, "界面预览-子码流-深色.png")
        sub_dialog.close()

        # 勾上厂商子流路径 → 应自动填入该厂商辅流出厂参数并启用子码流
        auto_dialog = StreamDialog(config, used_paths={}, parent=window)
        auto_dialog.show()
        entry = auto_dialog._path_items.get("/Streaming/Channels/102")
        entry[0].setCheckState(0, Qt.CheckState.Checked)
        app.processEvents()
        print("勾选海康 102 自动填入：启用 =", auto_dialog.sub_check.isChecked(),
              "| 分辨率 =", auto_dialog.sub_res_combo.currentText(),
              "| 帧率 =", auto_dialog.sub_fps_spin.value(),
              "| 码率 =", auto_dialog.sub_kbps_spin.value())
        auto_dialog.close()

        # ---- 网络源识别提示（浅色）----
        net_spec = StreamSpec(
            name="公网演示源", source="https://example.com/live/camera.m3u8", codec="h264",
            width=1280, height=720, fps=25.0, kbps=1500,
            routes=[Route("/Streaming/Channels/101", "hikvision")],
        )
        switch_theme("light")
        net_dialog = StreamDialog(config, spec=net_spec, used_paths={"y": "z"}, parent=window)
        net_dialog.show()
        app.processEvents()
        snapshot(net_dialog, "界面预览-网络源-浅色.png")

        # 关掉循环，展示「播完即停」的说明文案
        net_dialog.loop_check.setChecked(False)
        app.processEvents()
        snapshot(net_dialog, "界面预览-循环关闭-浅色.png")
        net_dialog.close()

        # ---- 哔哩哔哩视频页识别提示（浅色）----
        bili_spec = StreamSpec(
            name="哔哩哔哩 BV1hh4y1N7k2",
            source="https://www.bilibili.com/video/BV1hh4y1N7k2/?spm_id_from=333.337.search-card.all.click",
            codec="h264", width=1920, height=1080, fps=25.0, kbps=4096,
            routes=[Route("/Streaming/Channels/101", "hikvision")],
        )
        bili_dialog = StreamDialog(config, spec=bili_spec, used_paths={"z": "w"}, parent=window)
        bili_dialog.show()
        app.processEvents()
        snapshot(bili_dialog, "界面预览-哔哩哔哩-浅色.png")
        bili_dialog.close()

        # ---- 设置对话框：浅色 + 深色 ----
        switch_theme("light")
        settings = SettingsDialog(config, ["192.168.1.146"], parent=window,
                                   on_theme_change=lambda t: window.apply_theme(t, save=False))
        settings.show()
        app.processEvents()
        snapshot(settings, "界面预览-设置-浅色.png")
        settings.close()

        switch_theme("dark")
        settings = SettingsDialog(config, ["192.168.1.146"], parent=window,
                                   on_theme_change=lambda t: window.apply_theme(t, save=False))
        settings.show()
        app.processEvents()
        snapshot(settings, "界面预览-设置-深色.png")
        settings.close()

        # ---- 表格与详情文本核对 ----
        row0 = window.table.item(0, 0).text()
        print("状态列:", row0, "| 主码流:", window.table.item(0, 3).text(),
              "| 子码流:", window.table.item(0, 6).text(),
              "| 客户端列:", window.table.item(0, 7).text())
        # 详情栏地址改用逐条卡片展示：前缀单独一行，行内只列路径
        paths = [row._path for row in window._url_rows if row.isVisible()]
        print("详情地址前缀:", window.url_prefix.text(), "| 条数:", window.url_count.text())
        print("详情地址:\n" + "\n".join(f"  {p}" for p in paths))
        app.quit()

    QTimer.singleShot(4000, capture)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
