# -*- coding: utf-8 -*-
"""
一键安装 Native 插件所需依赖
"""
import subprocess
import sys

def install(package):
    print(f"正在安装 {package}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
    print(f"  {package} 安装成功!")

def main():
    print("=" * 50)
    print("Native 插件依赖安装")
    print("=" * 50)
    
    # 核心依赖（必须）
    core_deps = [
        "pyautogui",
        "opencv-python",
        "numpy",
        "pynput",
        "pywin32",
    ]
    
    # 可选依赖
    optional_deps = [
        "pygetwindow",
        "paddleocr",
        "paddlepaddle",  # CPU 版本，GPU 版本用 paddlepaddle-gpu
    ]
    
    print("\n[1/2] 安装核心依赖...")
    for dep in core_deps:
        try:
            install(dep)
        except Exception as e:
            print(f"  {dep} 安装失败: {e}")
    
    print("\n[2/2] 安装可选依赖（OCR和窗口管理）...")
    for dep in optional_deps:
        try:
            install(dep)
        except Exception as e:
            print(f"  {dep} 安装失败: {e}")
    
    print("\n" + "=" * 50)
    print("安装完成！")
    print("=" * 50)
    print("\n依赖清单:")
    print("  pyautogui     - 截图和鼠标/键盘控制")
    print("  opencv-python - 图像匹配（找图/找色）")
    print("  numpy         - 图像数组处理")
    print("  pynput        - 精细鼠标/键盘控制")
    print("  pywin32       - Windows 窗口操作")
    print("  pygetwindow   - 窗口查找/枚举")
    print("  paddleocr     - OCR 文字识别")
    print("  paddlepaddle  - PaddleOCR 后端引擎")
    print("\n如果 paddlepaddle 安装太慢，可以使用清华源:")
    print("  pip install paddlepaddle -i https://pypi.tuna.tsinghua.edu.cn/simple")

if __name__ == "__main__":
    main()
