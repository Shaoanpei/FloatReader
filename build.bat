@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   FloatReader 打包脚本
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 没找到 python，请先安装 Python 3.10+ 并勾选 "Add Python to PATH"
    pause
    exit /b 1
)

python -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [1/3] 正在安装 PyInstaller ...
    python -m pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] PyInstaller 安装失败
        pause
        exit /b 1
    )
) else (
    echo [1/3] PyInstaller 已安装
)

echo [2/3] 清理旧产物 ...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [3/3] 开始打包，大约 1-3 分钟，请稍等 ...
python -m PyInstaller reader.py ^
    --name FloatReader ^
    --windowed ^
    --noconfirm --clean ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineQuick ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtPdf ^
    --exclude-module PySide6.QtTest ^
    --exclude-module PySide6.QtOpenGL ^
    --exclude-module tkinter

if errorlevel 1 (
    echo.
    echo [错误] 打包失败，把上面的报错发我看看
    pause
    exit /b 1
)

echo.
echo ============================================
echo   打包完成
echo ============================================
echo   exe 位置：dist\FloatReader\FloatReader.exe
echo.
echo   注意：要把整个 dist\FloatReader 文件夹一起拷走
echo        （exe 依赖同目录下的 _internal 文件夹）
echo        config.json 会生成在 exe 旁边
echo ============================================
echo.

echo [附加] 正在创建桌面快捷方式 ...
set "VBS=%TEMP%\fr_mklink.vbs"
>"%VBS%" echo Set oWS = WScript.CreateObject("WScript.Shell")
>>"%VBS%" echo sFile = oWS.SpecialFolders("Desktop") ^& "\FloatReader.lnk"
>>"%VBS%" echo Set oLink = oWS.CreateShortcut(sFile)
>>"%VBS%" echo oLink.TargetPath = "%~dp0dist\FloatReader\FloatReader.exe"
>>"%VBS%" echo oLink.WorkingDirectory = "%~dp0dist\FloatReader"
>>"%VBS%" echo oLink.WindowStyle = 1
>>"%VBS%" echo oLink.Description = "FloatReader 悬浮阅读器"
>>"%VBS%" echo oLink.Save
cscript //nologo "%VBS%" >nul 2>nul
del "%VBS%" >nul 2>nul

if exist "%USERPROFILE%\Desktop\FloatReader.lnk" (
    echo   已在桌面创建 FloatReader 快捷方式，双击即可使用
) else (
    echo   快捷方式已尝试创建；若桌面没看到图标（桌面被 OneDrive 重定向时会出现），
    echo   可直接双击 dist\FloatReader\FloatReader.exe
    echo   （想放桌面：右键该 exe - 发送到 - 桌面快捷方式）
)
echo.
pause
