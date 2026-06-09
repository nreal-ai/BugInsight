#!/usr/bin/env python3
"""
飞书数据获取器 - 使用飞书MCP获取缺陷信息

注意: Claude Code 环境下应直接使用飞书 MCP 工具，此脚本仅作备用。
MCP 工具:
  - get_workitem_brief: 获取缺陷概况
  - list_workitem_comments: 获取评论
  - get_download_url: 获取附件下载链接
"""

import json
import os
import tempfile
import zipfile
from pathlib import Path


def extract_zip(zip_path: str, dest_dir: str = None) -> str:
    """解压 ZIP 文件"""
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="bug_")

    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(dest_dir)

    return dest_dir


def list_extracted_files(extracted_dir: str) -> list:
    """列出解压后的文件"""
    files = []
    for root, dirs, filenames in os.walk(extracted_dir):
        for f in filenames:
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, extracted_dir)
            files.append(rel_path)
    return files


if __name__ == "__main__":
    if len(sys.argv) > 1:
        dest = extract_zip(sys.argv[1])
        print(f"解压到: {dest}")
        for f in list_extracted_files(dest):
            print(f"  {f}")
