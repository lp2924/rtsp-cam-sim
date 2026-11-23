@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] 首次运行，正在创建虚拟环境...
    py -3.10 -m venv .venv 2>nul || py -3 -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo.
        echo 创建虚拟环境失败，请确认已安装 Python 3.10 及以上版本。
        pause
        exit /b 1
    )
    echo [2/2] 正在安装依赖 PySide6，请稍候...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

".venv\Scripts\python.exe" -m app.main
if errorlevel 1 pause
