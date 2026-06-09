#!/usr/bin/env python3
"""
飞书缺陷数据抓取脚本 (已废弃 - 遗留脚本)
功能: 从飞书项目抓取缺陷数据保存到本地

注意: Claude Code 环境下使用飞书MCP直接获取缺陷信息，无需此脚本。
保留仅作数据迁移用途。
"""

import json
import sys
from pathlib import Path

# 输出目录 (相对于脚本)
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "feishu-bugs"
ATTACHMENTS_DIR = OUTPUT_DIR / "attachments"
DOCS_DIR = OUTPUT_DIR / "docs"


def main():
    print("此脚本已从飞书插件API迁移至飞书MCP")
    print("在 Claude Code 环境中，直接使用飞书 MCP 工具获取缺陷信息:")
    print("  - get_workitem_brief: 获取缺陷概况")
    print("  - list_workitem_comments: 获取评论")
    print("  - get_download_url: 获取附件下载链接")
    return 0


if __name__ == "__main__":
    sys.exit(main())
