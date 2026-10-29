@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
    echo [错误] 没有找到 pythonw，请先安装 Python 3.10+ 并勾选 "Add Python to PATH"
    echo 然后运行: pip install PySide6 beautifulsoup4
    pause
    exit /b 1
)
start "" pythonw "reader.py"
