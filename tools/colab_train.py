#!/usr/bin/env python
"""
Colab YOLOv8 训练脚本
在 Google Colab 中运行 (免费T4 GPU)
"""
# %% [markdown]
# # 🎯 传奇2 YOLOv8 训练
# ## Google Colab 免费T4 GPU

# %% [code] 
# @title 1. 安装依赖
# @markdown 首次运行需要安装

!pip install ultralytics -q
!pip install albumentations -q

import os, zipfile, json, glob, shutil, random
from pathlib import Path
import ultralytics
ultralytics.checks()

# %% [code]
# @title 2. 上传数据集
# @markdown 上传 auto_annotate.py 生成的 dataset.zip

from google.colab import files
uploaded = files.upload()

# 解压
zip_name = list(uploaded.keys())[0]
with zipfile.ZipFile(zip_name, 'r') as zf:
    zf.extractall('/content/')

# 检查数据集
dataset_dir = '/content/dataset'
if not os.path.isdir(dataset_dir):
    # 尝试找子目录
    for d in glob.glob('/content/*/'):
        if os.path.isdir(os.path.join(d, 'images')):
            dataset_dir = d
            break

print(f"数据集目录: {dataset_dir}")
print(f"图片数: {len(glob.glob(os.path.join(dataset_dir, 'images', '*')))}")
print(f"标注数: {len(glob.glob(os.path.join(dataset_dir, 'labels', '*')))}")

# 修正dataset.yaml中的路径
yaml_path = os.path.join(dataset_dir, 'dataset.yaml')
with open(yaml_path, 'r') as f:
    yaml_content = f.read()
yaml_content = yaml_content.replace('./images', os.path.join(dataset_dir, 'images'))
yaml_content = yaml_content.replace('./val', os.path.join(dataset_dir, 'images'))
with open('/content/dataset.yaml', 'w') as f:
    f.write(yaml_content)

print("✅ 数据集就绪")

# %% [code]
# @title 3. 开始训练
# @markdown 点击运行，等10~30分钟

# 从yaml读取类别数
import yaml
with open('/content/dataset.yaml', 'r') as f:
    data = yaml.safe_load(f)
nc = data.get('nc', 1)
names = data.get('names', ['target'])

print(f"类别数: {nc}")
print(f"类别名: {names}")

# 训练参数
EPOCHS = 100       # 训练轮数
BATCH_SIZE = 16    # 批次大小
IMG_SIZE = 640     # 输入尺寸

!yolo train \
  model=yolov8n.pt \
  data=/content/dataset.yaml \
  epochs={EPOCHS} \
  imgsz={IMG_SIZE} \
  batch={BATCH_SIZE} \
  device=0 \
  workers=2 \
  augment \
  hsv_h=0.015 \
  hsv_s=0.7 \
  hsv_v=0.4 \
  degrees=5.0 \
  translate=0.1 \
  scale=0.5 \
  shear=2.0 \
  perspective=0.0 \
  flipud=0.0 \
  fliplr=0.5 \
  mosaic=1.0 \
  mixup=0.1

print("✅ 训练完成!")

# %% [code]
# @title 4. 导出ONNX并下载

# 找最好的模型
best_pt = '/content/runs/detect/train/weights/best.pt'
if not os.path.exists(best_pt):
    # 尝试其他路径
    possible = glob.glob('/content/runs/detect/*/weights/best.pt')
    if possible:
        best_pt = possible[0]

print(f"最佳模型: {best_pt}")

# 导出ONNX
!yolo export model={best_pt} format=onnx imgsz={IMG_SIZE}

# 找ONNX文件
onnx_files = glob.glob(os.path.join(os.path.dirname(best_pt), '*.onnx'))
if onnx_files:
    onnx_path = onnx_files[0]
    print(f"导出成功: {onnx_path}")
    
    # 重命名为好记的名字
    output_name = 'legend2_yolo.onnx'
    shutil.copy(onnx_path, f'/content/{output_name}')
    print(f"模型已保存: /content/{output_name}")
    
    # 下载到本地
    from google.colab import files
    files.download(f'/content/{output_name}')
    
    # 同时下载训练结果图表
    results_dir = '/content/runs/detect/train'
    if os.path.exists(results_dir):
        for chart in ['results.png', 'confusion_matrix.png', 'labels.jpg']:
            chart_path = os.path.join(results_dir, chart)
            if os.path.exists(chart_path):
                files.download(chart_path)
else:
    print("❌ ONNX导出失败")

print("\n✅ 完成! 把 legend2_yolo.onnx 放到 LCA/yolo/ 目录下")
print("   然后在工作流里添加 YOLO目标检测 卡片即可使用")