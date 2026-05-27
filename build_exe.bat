@echo off
chcp 65001 >nul 2>&1
title LCA 一键打包工具
echo ========================================
echo   LCA 一键打包工具
echo ========================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [✓] Python 已检测到

:: 检查并创建虚拟环境
if not exist "venv" (
    echo [1/4] 创建虚拟环境...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
echo [✓] 虚拟环境就绪

:: 激活虚拟环境
echo [2/4] 激活虚拟环境...
call venv\Scripts\activate.bat

:: 升级 pip
python -m pip install --upgrade pip -q

:: 安装依赖
echo [3/4] 安装依赖（可能需要几分钟）...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)
echo [✓] 依赖安装完成

:: 打包 EXE
echo [4/4] 打包 EXE（需要几分钟）...
pip install pyinstaller -q
pyinstaller --noconfirm --onedir --windowed --name "LCA" --icon "NONE" --add-data "resources;resources" --add-data "themes;themes" --add-data "ui;ui" --add-data "tasks;tasks" --add-data "services;services" --add-data "utils;utils" --add-data "tools;tools" --add-data "plugins;plugins" --add-data "app_core;app_core" --add-data "task_workflow;task_workflow" --add-data "workflows;workflows" --add-data "images;images" --hidden-import=PySide6 --hidden-import=PySide6.QtCore --hidden-import=PySide6.QtGui --hidden-import=PySide6.QtWidgets --hidden-import=cv2 --hidden-import=numpy --hidden-import=PIL --hidden-import=pywin32 --hidden-import=win32api --hidden-import=win32con --hidden-import=win32gui --hidden-import=pynput --hidden-import=keyboard --hidden-import=mouse --hidden-import=mss --hidden-import=uiautomation main.py

if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo  打包完成！
echo  EXE 位置: dist\LCA\LCA.exe
echo ========================================
echo.
echo 使用方法:
echo   1. 把 dist\LCA 整个文件夹复制到任意位置
echo   2. 双击 LCA.exe 运行
echo   3. 首次使用请导入工作流文件
echo.
pause
