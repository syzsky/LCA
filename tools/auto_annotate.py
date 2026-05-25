#!/usr/bin/env python
"""
传奇2 YOLO自动标注工具
用模板匹配自动生成YOLO训练标注

用法:
  1. 提供模板图片(每个目标1张) + 场景截图(20~50张)
  2. 运行脚本自动生成YOLO标注
  3. 打包上传Colab训练

模板目录结构:
  templates/
    npc.png          → class 0
    monster.png      → class 1
    hp_bar.png       → class 2
    item.png         → class 3
    btn_enter.png    → class 4
    ...

场景截图放:
  screenshots/
    screen_01.png
    screen_02.png
    ...

输出:
  dataset/
    images/     (场景截图copy)
    labels/     (YOLO格式标注)
    dataset.yaml
"""

import cv2
import numpy as np
import os
import sys
import json
from pathlib import Path
from glob import glob

# ============================================================
# 配置区 - 修改这里
# ============================================================

# 模板目录 (每个模板1张图)
TEMPLATES_DIR = "templates"

# 场景截图目录 (游戏截图)
SCREENSHOTS_DIR = "screenshots"

# 输出目录
OUTPUT_DIR = "dataset"

# 匹配阈值 (0~1, 越高越严格)
MATCH_THRESHOLD = 0.75

# ============================================================
# 类名配置 - 按模板文件名自动映射
# ============================================================

def load_templates(templates_dir: str) -> list:
    """加载模板图片, 按文件名排序确定class_id"""
    supported = ('.png', '.jpg', '.jpeg', '.bmp')
    files = sorted([
        f for f in os.listdir(templates_dir)
        if f.lower().endswith(supported)
    ])

    if not files:
        print(f"❌ 模板目录为空: {templates_dir}")
        print(f"   请放入模板图片, 如: npc.png, monster.png, hp_bar.png")
        return []

    templates = []
    for i, fname in enumerate(files):
        path = os.path.join(templates_dir, fname)
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            print(f"⚠️  无法读取: {fname}, 跳过")
            continue

        name = Path(fname).stem  # 去掉扩展名
        templates.append({
            'class_id': i,
            'class_name': name,
            'image': img,
            'filename': fname,
        })
        print(f"  class {i}: {name} ({fname}) [{img.shape[1]}x{img.shape[0]}]")

    return templates


def match_template(screenshot: np.ndarray, template: dict, threshold: float) -> list:
    """在截图中匹配模板, 返回所有匹配结果"""
    h, w = template['image'].shape[:2]
    result = cv2.matchTemplate(screenshot, template['image'], cv2.TM_CCOEFF_NORMED)
    locations = np.where(result >= threshold)

    detections = []
    for pt in zip(*locations[::-1]):
        x1, y1 = pt[0], pt[1]
        x2, y2 = x1 + w, y1 + h

        # 去重: 检查是否与已有结果重叠
        dup = False
        for d in detections:
            dx1, dy1, dx2, dy2 = d['bbox']
            overlap_x = max(0, min(x2, dx2) - max(x1, dx1))
            overlap_y = max(0, min(y2, dy2) - max(y1, dy1))
            if overlap_x > 0 and overlap_y > 0:
                dup = True
                break

        if not dup:
            detections.append({
                'class_id': template['class_id'],
                'class_name': template['class_name'],
                'bbox': (x1, y1, x2, y2),
                'confidence': float(result[pt[1], pt[0]]),
            })

    return detections


def to_yolo_format(bbox, img_w: int, img_h: int) -> tuple:
    """转为YOLO格式: class_id x_center y_center width height (归一化)"""
    x1, y1, x2, y2 = bbox
    x_center = ((x1 + x2) / 2) / img_w
    y_center = ((y1 + y2) / 2) / img_h
    width = (x2 - x1) / img_w
    height = (y2 - y1) / img_h
    return (x_center, y_center, width, height)


def main():
    print("=" * 55)
    print("  传奇2 YOLO 自动标注工具")
    print("=" * 55)

    # 检查目录
    if not os.path.isdir(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        print(f"\n📁 已创建模板目录: {TEMPLATES_DIR}/")
        print(f"   请放入模板图片 (每类目标1张)")
        print(f"   例如: npc.png, monster.png, hp_bar.png")
        return

    if not os.path.isdir(SCREENSHOTS_DIR):
        os.makedirs(SCREENSHOTS_DIR, exist_ok=True)
        print(f"\n📁 已创建截图目录: {SCREENSHOTS_DIR}/")
        print(f"   请放入游戏场景截图 (20~50张)")
        return

    # 加载模板
    print("\n📦 加载模板...")
    templates = load_templates(TEMPLATES_DIR)
    if not templates:
        return

    # 加载截图
    supported = ('*.png', '*.jpg', '*.jpeg', '*.bmp')
    screenshot_files = []
    for ext in supported:
        screenshot_files.extend(glob(os.path.join(SCREENSHOTS_DIR, ext)))
    screenshot_files.sort()

    if not screenshot_files:
        print(f"\n❌ 截图目录为空: {SCREENSHOTS_DIR}/")
        return

    print(f"\n📸 加载截图: {len(screenshot_files)} 张")

    # 创建输出目录
    images_dir = os.path.join(OUTPUT_DIR, "images")
    labels_dir = os.path.join(OUTPUT_DIR, "labels")
    os.makedirs(images_dir, exist_ok=True)
    os.makedirs(labels_dir, exist_ok=True)

    # 统计
    total_annotations = 0
    class_counts = {t['class_name']: 0 for t in templates}
    no_detection_files = []

    # 处理每张截图
    print("\n🔍 开始自动标注...")
    for idx, img_path in enumerate(screenshot_files):
        fname = Path(img_path).name
        screenshot = cv2.imread(img_path, cv2.IMREAD_COLOR)
        if screenshot is None:
            print(f"⚠️  无法读取: {fname}, 跳过")
            continue

        img_h, img_w = screenshot.shape[:2]

        # 复制图片到dataset
        import shutil
        dst_img = os.path.join(images_dir, fname)
        shutil.copy2(img_path, dst_img)

        # 对所有模板做匹配
        all_detections = []
        for template in templates:
            detections = match_template(screenshot, template, MATCH_THRESHOLD)
            all_detections.extend(detections)

        # 写入YOLO标注
        label_path = os.path.join(labels_dir, Path(fname).stem + ".txt")
        with open(label_path, 'w', encoding='utf-8') as f:
            for d in all_detections:
                xc, yc, w, h = to_yolo_format(d['bbox'], img_w, img_h)
                f.write(f"{d['class_id']} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")
                class_counts[d['class_name']] += 1
                total_annotations += 1

        if not all_detections:
            no_detection_files.append(fname)

        # 进度
        if (idx + 1) % 5 == 0 or idx == len(screenshot_files) - 1:
            print(f"  进度: {idx+1}/{len(screenshot_files)}  (当前共 {total_annotations} 个标注)")

    # 输出统计
    print(f"\n{'='*55}")
    print(f"✅ 自动标注完成!")
    print(f"{'='*55}")
    print(f"  截图: {len(screenshot_files)} 张")
    print(f"  标注: {total_annotations} 个")
    print(f"\n  各类别统计:")
    for name, count in class_counts.items():
        print(f"    class {templates[[t['class_name'] for t in templates].index(name)]['class_id']}: {name} → {count} 个")

    if no_detection_files:
        print(f"\n⚠️  以下截图未检测到任何目标 ({len(no_detection_files)}张):")
        for f in no_detection_files[:5]:
            print(f"    - {f}")
        if len(no_detection_files) > 5:
            print(f"    ... 还有 {len(no_detection_files)-5} 张")

    # 生成dataset.yaml
    class_names = [t['class_name'] for t in templates]
    yaml_path = os.path.join(OUTPUT_DIR, "dataset.yaml")
    with open(yaml_path, 'w', encoding='utf-8') as f:
        f.write(f"# 传奇2 YOLO数据集\n")
        f.write(f"# 自动生成\n\n")
        f.write(f"train: ./images\n")
        f.write(f"val: ./images\n\n")
        f.write(f"nc: {len(class_names)}\n")
        f.write(f"names: {json.dumps(class_names, ensure_ascii=False)}\n")

    print(f"\n📁 输出目录: {OUTPUT_DIR}/")
    print(f"   ├── images/      ({len(screenshot_files)} 张图)")
    print(f"   ├── labels/      ({total_annotations} 个标注)")
    print(f"   └── dataset.yaml (数据集配置)")
    print(f"\n📦 打包命令:")
    print(f"   zip -r dataset.zip {OUTPUT_DIR}/")
    print(f"\n🚀 上传到 Colab 训练即可")


if __name__ == '__main__':
    main()