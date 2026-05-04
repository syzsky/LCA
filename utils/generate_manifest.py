"""
版本清单生成工具
用于在服务器端生成版本清单文件(manifest.json)
运行此脚本扫描发布目录中的所有文件，生成版本信息
"""

import os
import sys
import json
import hashlib
import argparse
import logging
from pathlib import Path
from typing import Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)


class ManifestGenerator:
    """版本清单生成器"""

    def __init__(self, release_dir: str, version: str, exclude_patterns: List[str] = None):
        """
        初始化生成器

        Args:
            release_dir: 发布目录路径
            version: 版本号 (例如: "1.0.0")
            exclude_patterns: 要排除的文件/目录模式
        """
        self.release_dir = os.path.abspath(release_dir)
        self.version = version
        self.exclude_patterns = exclude_patterns or [
            "manifest.json",
            "version.json",
            "_backup",
            "_temp",
            "*.log",
            "*.tmp",
            "__pycache__",
            "*.pyc",
            ".git",
            ".gitignore",
            "config.json"  # 用户配置文件
        ]

    def should_exclude(self, file_path: str) -> bool:
        """判断文件是否应该被排除"""
        rel_path = os.path.relpath(file_path, self.release_dir)

        for pattern in self.exclude_patterns:
            # 简单的通配符匹配
            if pattern.startswith('*'):
                if rel_path.endswith(pattern[1:]):
                    return True
            elif pattern.endswith('*'):
                if rel_path.startswith(pattern[:-1]):
                    return True
            else:
                if pattern in rel_path or rel_path == pattern:
                    return True

        return False

    def calculate_file_hash(self, file_path: str) -> str:
        """计算文件SHA256哈希"""
        hash_obj = hashlib.sha256()

        try:
            with open(file_path, 'rb') as f:
                while chunk := f.read(8192):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()
        except Exception as e:
            logger.info(f"警告: 计算文件哈希失败 {file_path}: {e}")
            return ""

    def resolve_primary_package(self, files_info: Dict[str, Dict]) -> Dict:
        """
        解析客户端更新器使用的主安装包信息。

        优先匹配当前版本对应的安装包；若发布目录仅有一个文件，则回退为该文件。
        """
        installer_name = f"LCA_Setup_v{self.version}.exe"

        if installer_name in files_info:
            package_info = dict(files_info[installer_name])
            package_info["name"] = installer_name
            return package_info

        if len(files_info) == 1:
            only_name, only_info = next(iter(files_info.items()))
            package_info = dict(only_info)
            package_info["name"] = only_name
            logger.info(f"警告: 未找到标准安装包名，回退使用唯一文件: {only_name}")
            return package_info

        logger.info(f"警告: 未找到主安装包 {installer_name}")
        return {"name": "", "hash": "", "size": 0, "modified": 0}

    def scan_files(self) -> Dict[str, Dict]:
        """
        扫描发布目录中的所有文件

        Returns:
            文件信息字典 {相对路径: {hash, size, modified}}
        """
        files_info = {}

        logger.info(f"正在扫描目录: {self.release_dir}")

        for root, dirs, files in os.walk(self.release_dir):
            # 排除不需要的目录
            dirs[:] = [d for d in dirs if not self.should_exclude(os.path.join(root, d))]

            for file in files:
                file_path = os.path.join(root, file)

                # 排除文件
                if self.should_exclude(file_path):
                    continue

                # 计算相对路径（使用正斜杠，跨平台兼容）
                rel_path = os.path.relpath(file_path, self.release_dir)
                rel_path = rel_path.replace('\\', '/')

                # 获取文件信息
                file_size = os.path.getsize(file_path)
                file_hash = self.calculate_file_hash(file_path)
                file_mtime = os.path.getmtime(file_path)

                if file_hash:  # 只添加成功计算哈希的文件
                    files_info[rel_path] = {
                        'hash': file_hash,
                        'size': file_size,
                        'modified': int(file_mtime)
                    }

                    logger.info(f"  ✓ {rel_path} ({file_size} bytes)")

        logger.info(f"\n共扫描到 {len(files_info)} 个文件")
        return files_info

    def generate_manifest(
        self,
        changelog: List[str] = None,
        output_file: str = None
    ) -> Dict:
        """
        生成版本清单

        Args:
            changelog: 更新日志列表
            output_file: 输出文件路径（默认为release_dir/manifest.json）

        Returns:
            清单数据字典
        """
        if output_file is None:
            output_file = os.path.join(self.release_dir, "manifest.json")

        # 扫描文件
        files_info = self.scan_files()
        primary_package = self.resolve_primary_package(files_info)

        # 构建清单数据
        manifest_data = {
            "version": self.version,
            "build": int(datetime.now().timestamp()),
            "release_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changelog": changelog or [f"版本 {self.version} 更新"],
            "installer": primary_package["name"],
            "file_size": primary_package["size"],
            "sha256": primary_package["hash"],
            "files": files_info,
            "total_size": sum(f['size'] for f in files_info.values()),
            "file_count": len(files_info)
        }

        # 保存清单文件
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)

        logger.info(f"\n✓ 版本清单已生成: {output_file}")
        logger.info(f"  版本号: {self.version}")
        logger.info(f"  文件数: {manifest_data['file_count']}")
        logger.info(f"  总大小: {manifest_data['total_size'] / 1024 / 1024:.2f} MB")

        return manifest_data

    def compare_with_previous(self, previous_manifest_path: str) -> Dict:
        """
        与上一个版本对比

        Args:
            previous_manifest_path: 上一个版本的清单文件路径

        Returns:
            对比结果 {added: [], modified: [], deleted: []}
        """
        if not os.path.exists(previous_manifest_path):
            logger.info(f"警告: 上一版本清单不存在: {previous_manifest_path}")
            return {"added": [], "modified": [], "deleted": []}

        # 读取上一版本清单
        with open(previous_manifest_path, 'r', encoding='utf-8') as f:
            previous_manifest = json.load(f)

        # 当前版本文件信息
        current_files = self.scan_files()
        previous_files = previous_manifest.get('files', {})

        # 找出变化
        added = []
        modified = []
        deleted = []

        # 新增和修改的文件
        for file_path, file_info in current_files.items():
            if file_path not in previous_files:
                added.append(file_path)
            elif file_info['hash'] != previous_files[file_path]['hash']:
                modified.append(file_path)

        # 删除的文件
        for file_path in previous_files:
            if file_path not in current_files:
                deleted.append(file_path)

        # 打印对比结果
        logger.info("\n=== 版本对比 ===")
        logger.info(f"上一版本: {previous_manifest.get('version', 'unknown')}")
        logger.info(f"当前版本: {self.version}")
        logger.info(f"\n新增文件 ({len(added)}):")
        for f in added:
            logger.info(f"  + {f}")

        logger.info(f"\n修改文件 ({len(modified)}):")
        for f in modified:
            logger.info(f"  ~ {f}")

        logger.info(f"\n删除文件 ({len(deleted)}):")
        for f in deleted:
            logger.info(f"  - {f}")

        total_changed = len(added) + len(modified)
        if total_changed > 0:
            changed_size = sum(
                current_files[f]['size']
                for f in (added + modified)
            )
            logger.info(f"\n需要更新的文件: {total_changed} 个")
            logger.info(f"更新包大小: {changed_size / 1024 / 1024:.2f} MB")

        return {
            "added": added,
            "modified": modified,
            "deleted": deleted
        }


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(description='版本清单生成工具')

    parser.add_argument(
        'release_dir',
        help='发布目录路径'
    )

    parser.add_argument(
        '-v', '--version',
        required=True,
        help='版本号 (例如: 1.0.0)'
    )

    parser.add_argument(
        '-o', '--output',
        default=None,
        help='输出清单文件路径 (默认: release_dir/manifest.json)'
    )

    parser.add_argument(
        '-c', '--changelog',
        nargs='+',
        default=None,
        help='更新日志 (可以提供多条)'
    )

    parser.add_argument(
        '--compare',
        default=None,
        help='与上一版本清单对比的路径'
    )

    parser.add_argument(
        '--exclude',
        nargs='+',
        default=None,
        help='额外排除的文件模式'
    )

    args = parser.parse_args()

    # 检查发布目录
    if not os.path.exists(args.release_dir):
        logger.info(f"错误: 发布目录不存在: {args.release_dir}")
        sys.exit(1)

    # 创建生成器
    generator = ManifestGenerator(
        release_dir=args.release_dir,
        version=args.version,
        exclude_patterns=args.exclude
    )

    # 如果需要对比
    if args.compare:
        generator.compare_with_previous(args.compare)
        logger.info()

    # 生成清单
    generator.generate_manifest(
        changelog=args.changelog,
        output_file=args.output
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # 示例用法
    if len(sys.argv) == 1:
        logger.info("=" * 60)
        logger.info("版本清单生成工具")
        logger.info("=" * 60)
        logger.info("\n用法示例:")
        logger.info("\n1. 基本用法:")
        logger.info('  python generate_manifest.py "C:/MyApp/dist" -v 1.0.0')
        logger.info("\n2. 带更新日志:")
        logger.info('  python generate_manifest.py "C:/MyApp/dist" -v 1.0.1 \\')
        logger.info('    -c "修复了登录bug" "优化了性能" "新增了XX功能"')
        logger.info("\n3. 与上一版本对比:")
        logger.info('  python generate_manifest.py "C:/MyApp/dist" -v 1.0.2 \\')
        logger.info('    --compare "C:/MyApp/previous/manifest.json"')
        logger.info("\n4. 自定义输出路径:")
        logger.info('  python generate_manifest.py "C:/MyApp/dist" -v 1.0.0 \\')
        logger.info('    -o "C:/Server/updates/manifest.json"')
        logger.info("\n5. 排除额外文件:")
        logger.info('  python generate_manifest.py "C:/MyApp/dist" -v 1.0.0 \\')
        logger.info('    --exclude "*.pdb" "test_*"')
        logger.info("=" * 60)
    else:
        main()
