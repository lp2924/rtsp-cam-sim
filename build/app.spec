# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件（onefile）、无控制台窗口、内置 ffmpeg/ffprobe。

路径约定：
  · 本文件位于 ``build/``，项目根为其上一级；Analysis 入口是根目录薄入口 ``run_app.py``。
  · ffmpeg.exe / ffprobe.exe 打到 exe 内部的 ``bin/`` 子目录；ffplay.exe 不打包。
  · 主题样式由 ``app.ui.theme`` 编译进代码，箭头/勾选图标运行时绘制到临时目录，
    因此本程序没有外部 .qss / 图片等 datas。

构建命令见 ``build/build.ps1``，也可直接：
    .venv\\Scripts\\python.exe -m PyInstaller --noconfirm --clean build\\app.spec
"""

import os

# SPECPATH 由 PyInstaller 注入，为本 spec 文件所在目录（build/）
SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(SPEC_DIR)

# ffmpeg 来源目录：可用环境变量 FFMPEG_BIN_DIR 覆盖，默认本机 essentials build
FFMPEG_BIN_DIR = os.environ.get(
    "FFMPEG_BIN_DIR", r"D:\ffmpeg-6.0-essentials_build\bin"
)

# --- 随包二进制：只打 ffmpeg / ffprobe，目标目录 bin/ ---
binaries = [
    (os.path.join(FFMPEG_BIN_DIR, "ffmpeg.exe"), "bin"),
    (os.path.join(FFMPEG_BIN_DIR, "ffprobe.exe"), "bin"),
]

# --- 随包数据 ---
# 运行时 main.py 会读取 assets/app.ico 作为窗口图标（frozen 下位于 _MEIPASS\\assets），
# 必须随包打入；主题样式 token 编译进代码，下拉/勾选/Spin 箭头运行时画到 %TEMP%，
# 除此之外没有其它外部 qss / 字体 / 图片资源。
datas = [
    (os.path.join(ROOT, "assets", "app.ico"), "assets"),
]

# --- 裁剪未使用的模块 ---
# 注意：QtNetwork / QtSvg 未在代码中直接 import，但体积小且属于易误伤项，这里保留；
# 下面列出的均为 grep 全量源码确认未使用的重型模块。
excludes = [
    # 标准库 / 测试文档模块
    "tkinter",
    "unittest",
    "pydoc",
    "doctest",
    # Qt WebEngine（Qt6WebEngineCore.dll 约 194MB，纯 Widgets 程序不需要）
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    # Qt Qml / Quick / Quick3D
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    # Qt Multimedia（自带 avcodec/avformat DLL；本程序转码走外部 ffmpeg）
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    # Qt 3D 全家桶
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    # 其它未使用的功能模块
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtNfc",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
    "PySide6.QtDesigner",
    "PySide6.QtCharts",
    "PySide6.QtGraphs",
    "PySide6.QtDataVisualization",
    "PySide6.QtHelp",
    "PySide6.QtPrintSupport",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtWebChannel",
]

a = Analysis(
    [os.path.join(ROOT, "run_app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

# --- 过滤误打包的系统 CRT ---
# 本机 PATH 带了 JDK bin，夹带 ucrtbase.dll / api-ms-win-*.dll 的副本；这些是
# Windows 系统 DLL，绝不能随包打包——包内 ucrtbase 与系统 ucrtbase 混用会造成
# CRT 堆损坏，进程以 BEX64 / 0xc0000409 fail-fast 崩溃。这里统一剔除，运行时由
# 系统 System32 与 API set 解析。
def _is_system_crt(dest: str) -> bool:
    name = os.path.basename(dest).lower()
    return name == "ucrtbase.dll" or name.startswith("api-ms-win-")


_before = len(a.binaries)
a.binaries = [b for b in a.binaries if not _is_system_crt(b[0])]
print(f"[spec] 剔除系统 CRT DLL {_before - len(a.binaries)} 个")

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="RtspCamSim",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                       # 不使用 UPX，避免加壳引发杀软误报
    console=False,                    # windowed：不弹出控制台窗口
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "app.ico"),
)
