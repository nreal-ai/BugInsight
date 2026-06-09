#!/usr/bin/env python3
"""
飞书缺陷知识库生成脚本 (已废弃 - 遗留脚本)
功能: 从本地JSON读取缺陷数据，生成Markdown知识库

注意: Claude Code 环境下使用飞书MCP直接获取缺陷信息，无需此脚本。
保留仅作数据迁移用途。
"""

import json
import os
import argparse
from pathlib import Path

# 输出目录 (相对于脚本)
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "feishu-bugs"
DOCS_DIR = OUTPUT_DIR / "docs"


def generate_markdown(bug: dict) -> str:
    """生成缺陷Markdown文档"""
    name = bug.get('name', bug.get('title', '未知缺陷'))
    status = bug.get('status', '')
    desc = bug.get('description', '')

    detail = bug.get('detail', {})
    module = detail.get('module', {}).get('name', '')
    function = detail.get('function', {}).get('name', '')

    md = f"# {name}\n\n"
    md += f"- **状态**: {status}\n"
    md += f"- **模块**: {module}\n"
    md += f"- **功能**: {function}\n"
    md += f"\n## 描述\n\n{desc}\n"

    comments = bug.get('comments', [])
    if comments:
        md += "\n## 评论/解决方案\n\n"
        for c in comments:
            md += f"- {c.get('content', '')}\n"

    return md


def main():
    parser = argparse.ArgumentParser(description="从本地JSON生成缺陷知识库")
    parser.add_argument("input", help="输入JSON文件路径")
    parser.add_argument("-o", "--output", help="输出目录", default=str(DOCS_DIR))
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    with open(input_path, 'r', encoding='utf-8') as f:
        bugs = json.load(f)

    if isinstance(bugs, dict):
        bugs = bugs.get('data', [])

    count = 0
    for bug in bugs:
        bug_id = bug.get('id')
        if not bug_id:
            continue

        md = generate_markdown(bug)
        out_file = output_dir / f"bug_{bug_id}.md"
        with open(out_file, 'w', encoding='utf-8') as f:
            f.write(md)
        count += 1

    print(f"生成 {count} 个缺陷文档到 {output_dir}")


if __name__ == "__main__":
    main()
