#!/usr/bin/env python3
"""
批量获取缺陷详情和评论 (已废弃 - 遗留脚本)

注意: Claude Code 环境下使用飞书MCP直接获取缺陷信息，无需此脚本。
保留仅作数据迁移用途。
"""

import sys

def main():
    print("此脚本已从飞书API直接调用迁移至飞书MCP")
    print("在 Claude Code 环境中，直接使用飞书 MCP 工具获取缺陷信息:")
    print("  - get_workitem_brief: 获取缺陷概况")
    print("  - list_workitem_comments: 获取评论")
    return 0

if __name__ == "__main__":
    sys.exit(main())
