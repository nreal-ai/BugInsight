#!/usr/bin/env python3
"""
飞书历史 Bug 批量导入 OpenViking（纯 MCP 版本）

通过 MCP search_by_mql 分页获取全部 Bug（含多字段），
写入 Markdown 文件后注册到 OpenViking 语义搜索。

用法:
    python3 import_to_openviking.py --all          # 全量导入
    python3 import_to_openviking.py --recent 200   # 只导入最近 N 条
    python3 import_to_openviking.py --resume       # 断点续传
    python3 import_to_openviking.py --register-only  # 只注册已有文件
"""

import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

# 路径
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
CONFIG_FILE = SKILL_DIR / "config.json"
OV_WORKSPACE = Path(os.path.expanduser("~/.openviking/data/viking/default"))
BUGS_DIR = OV_WORKSPACE / "feishu-bugs"
BUGS_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = None  # 延迟初始化，支持 project-aware

# MQL 查询的字段
SELECT_FIELDS = [
    "work_item_id", "name", "work_item_status", "description",
    "severity", "priority", "start_time", "updated_at",
    "bug_classification", "business", "template", "issue_stage",
    "field_5d89c8",  # 模块
    "field_80e08e",  # 问题产生原因
    "field_efcfd1",  # 问题回归建议
    "field_b7a1fd",  # 研发自测结论
    "field_a7bb74",  # 标签
]
SELECT_STR = ", ".join(SELECT_FIELDS)


def load_config():
    cfg = {
        "project_key": "sw_team",
        "mcp_user_token": "",
        "page_size": 50,
        "rate_limit_delay": 0.5,
    }
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            cfg.update(json.load(f))
    # 环境变量覆盖（优先于 config.json）
    if os.getenv("FEISHU_PROJECT_KEY"):
        cfg["project_key"] = os.getenv("FEISHU_PROJECT_KEY")
    if os.getenv("FEISHU_MCP_TOKEN"):
        cfg["mcp_user_token"] = os.getenv("FEISHU_MCP_TOKEN")
    return cfg

CONFIG = load_config()
PROJECT_KEY = CONFIG["project_key"]
MCP_TOKEN = CONFIG.get("mcp_user_token", "")
MCP_URL = "https://project.feishu.cn/mcp_server/v1"
PAGE_SIZE = CONFIG.get("page_size", 50)


def mcp_call(tool_name: str, args: dict) -> Optional[dict]:
    """调用 MCP 工具"""
    if not MCP_TOKEN:
        print("  MCP Token 未配置")
        return None
    payload = {
        "jsonrpc": "2.0", "method": "tools/call", "id": 1,
        "params": {"name": tool_name, "arguments": args},
    }
    headers = {"Content-Type": "application/json", "X-Mcp-Token": MCP_TOKEN}
    try:
        resp = __import__("requests").post(MCP_URL, headers=headers, json=payload, timeout=60)
        data = resp.json()
        content = data.get("result", {}).get("content", [])
        for c in content:
            t = c.get("text", "")
            if t.startswith("[") or t.startswith("{"):
                return json.loads(t)
    except Exception as e:
        print(f"  MCP 错误: {e}")
    return None


def parse_field_value(field: dict) -> str:
    """从 MQL 字段值中提取可读文本"""
    val = field.get("value", {})
    if val is None:
        return ""

    vtype = field.get("value_type", "")

    if vtype == "string_value":
        return val.get("string_value", "") or ""
    elif vtype == "long_value":
        return str(val.get("long_value", "") or "")
    elif vtype == "key_label_value":
        kv = val.get("key_label_value", {})
        return kv.get("label", kv.get("key", "")) if kv else ""
    elif vtype == "key_label_value_list":
        items = val.get("key_label_value_list", [])
        return ", ".join([item.get("label", item.get("key", "")) for item in items]) if items else ""
    elif vtype == "cascade_key_label_value":
        cv = val.get("cascade_key_label_value", {})
        if cv:
            parts = [cv.get("label", "")]
            children = cv.get("children", [])
            if children:
                parts.extend([c.get("label", "") for c in children])
            return " / ".join(filter(None, parts))
        return ""
    elif vtype == "text_value":
        return val.get("text_value", "") or ""
    return ""


def parse_mql_bugs(result: dict) -> List[dict]:
    """解析 MQL 返回结果为 Bug 字典列表"""
    bugs = []
    data_groups = result.get("data", {})
    for group_id, items in data_groups.items():
        for item in items:
            bug = {}
            for field in item.get("moql_field_list", []):
                bug[field["key"]] = parse_field_value(field)
            if bug.get("work_item_id"):
                bugs.append(bug)
    return bugs


def bug_to_markdown(bug: dict) -> str:
    """将 Bug 数据转换为 Markdown"""
    bid = bug.get("work_item_id", "")
    name = bug.get("name", "")
    status = bug.get("work_item_status", "")
    severity = bug.get("severity", "")
    priority = bug.get("priority", "")
    module_name = bug.get("field_5d89c8", "")
    bug_class = bug.get("bug_classification", "")
    business = bug.get("business", "")
    template = bug.get("template", "")
    stage = bug.get("issue_stage", "")
    root_cause = bug.get("field_80e08e", "")
    regression = bug.get("field_efcfd1", "")
    self_test = bug.get("field_b7a1fd", "")
    tags = bug.get("field_a7bb74", "")
    description = bug.get("description", "")
    start_time = bug.get("start_time", "")
    updated_at = bug.get("updated_at", "")

    lines = [
        f"# {name}",
        "",
        f"- **Bug ID**: {bid}",
        f"- **状态**: {status}",
        f"- **严重程度**: {severity}",
        f"- **优先级**: {priority}",
        f"- **模块**: {module_name}",
        f"- **分类**: {bug_class}",
        f"- **业务线**: {business}",
        f"- **缺陷类型**: {template}",
        f"- **发现阶段**: {stage}",
        f"- **标签**: {tags}",
        f"- **提出时间**: {start_time}",
        f"- **更新时间**: {updated_at}",
    ]

    if description:
        lines.append("")
        lines.append("## 描述")
        lines.append(description)

    if root_cause:
        lines.append("")
        lines.append("## 问题产生原因")
        lines.append(root_cause)

    if regression:
        lines.append("")
        lines.append("## 问题回归建议")
        lines.append(regression)

    if self_test:
        lines.append("")
        lines.append("## 研发自测结论")
        lines.append(self_test)

    return "\n".join(lines)


# ============================================================
# 进度管理
# ============================================================

def _progress_file():
    return SKILL_DIR / f".import_progress_{PROJECT_KEY}.json"


def load_progress() -> dict:
    pf = _progress_file()
    if pf.exists():
        with open(pf) as f:
            return json.load(f)
    return {"imported_ids": [], "total_pages": 0, "current_page": 0}


def save_progress(progress: dict):
    progress["last_update"] = datetime.now().isoformat()
    with open(_progress_file(), "w") as f:
        json.dump(progress, f)


# ============================================================
# 主流程
# ============================================================

def fetch_and_import_all(start_offset: int = 0, max_bugs: int = 0):
    """使用 MQL LIMIT offset,count 分页获取所有 Bug 并写入 Markdown 文件"""
    print(f"SELECT 字段: {SELECT_STR}")
    print(f"project_key: {PROJECT_KEY}")
    print()

    imported_ids = set(load_progress().get("imported_ids", []))
    total_imported = 0
    offset = start_offset

    while True:
        page_num = offset // PAGE_SIZE + 1
        print(f"获取第 {page_num} 页 (offset={offset})...", end=" ")

        args = {
            "project_key": PROJECT_KEY,
            "mql": f"SELECT {SELECT_STR} FROM {PROJECT_KEY}.issue LIMIT {offset}, {PAGE_SIZE}",
        }

        result = mcp_call("search_by_mql", args)
        if not result:
            print("失败")
            break

        total_count = result.get("list", [{}])[0].get("count", 0)

        bugs = parse_mql_bugs(result)
        if not bugs:
            print("无数据，结束")
            break

        # 过滤已导入
        new_bugs = [b for b in bugs if b["work_item_id"] not in imported_ids]
        print(f"{len(new_bugs)} 新 / {len(bugs)} 本页 (总数 {total_count})")

        for bug in new_bugs:
            if max_bugs and total_imported >= max_bugs:
                break
            bid = bug["work_item_id"]
            md_content = bug_to_markdown(bug)
            file_path = BUGS_DIR / f"{bid}.md"
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            imported_ids.add(bid)
            total_imported += 1

        save_progress({
            "imported_ids": list(imported_ids),
            "current_offset": offset,
            "total_count": total_count,
        })

        if max_bugs and total_imported >= max_bugs:
            print(f"已达到限制 {max_bugs} 条，停止")
            break

        # 检查是否有更多页
        if len(bugs) < PAGE_SIZE:
            print("已到最后一页")
            break

        offset += PAGE_SIZE
        time.sleep(CONFIG.get("rate_limit_delay", 0.5))

    print(f"\n导入完成: 本批新增 {total_imported} 条，累计 {len(imported_ids)} 条")
    print(f"文件目录: {BUGS_DIR}")
    return total_imported


def register_with_openviking():
    """将 Bug 文件注册到 OpenViking"""
    import subprocess

    # 确保目录存在
    subprocess.run(
        ["ov", "mkdir", "/resources/feishu-bugs", "--description", "飞书项目缺陷数据"],
        capture_output=True, text=True, timeout=10,
    )

    md_files = sorted(BUGS_DIR.glob("*.md"))
    total = len(md_files)
    if total == 0:
        print("没有找到 .md 文件")
        return

    print(f"将 {total} 个 Bug 文件注册到 OpenViking...")

    registered = 0
    for i, filepath in enumerate(md_files):
        bug_id = filepath.stem  # 文件名（不含扩展名）作为 Bug ID
        try:
            result = subprocess.run(
                ["ov", "add-resource", "--to", f"/resources/feishu-bugs/{bug_id}", str(filepath)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                registered += 1
        except Exception as e:
            pass  # 单个失败不中断

        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{total} ({registered} 成功)")

    print(f"注册完成: {registered}/{total}")
    print("等待后台处理完成...")
    subprocess.run(["ov", "wait", "--timeout", "120"], capture_output=True, timeout=130)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="飞书 Bug 批量导入 OpenViking (MCP)")
    parser.add_argument("--all", action="store_true", help="全量导入所有 Bug")
    parser.add_argument("--recent", type=int, default=0, help="只导入最近 N 条")
    parser.add_argument("--resume", action="store_true", help="断点续传")
    parser.add_argument("--register-only", action="store_true", help="只注册已有文件到 OpenViking")
    parser.add_argument("--start", type=int, default=0, help="从第 N 条开始(offset)")
    parser.add_argument("--project", type=str, default=None, help="项目标识 (如 sw_team, axr)，覆盖 config.json")
    args = parser.parse_args()

    # 支持 --project 参数和环境变量覆盖
    global PROJECT_KEY
    if args.project:
        PROJECT_KEY = args.project
    elif os.environ.get("FEISHU_PROJECT_KEY"):
        PROJECT_KEY = os.environ["FEISHU_PROJECT_KEY"]

    if args.register_only:
        register_with_openviking()
        return

    if not args.all and not args.recent and not args.resume:
        parser.print_help()
        print("\n示例:")
        print("  python3 import_to_openviking.py --all                        # 全量导入")
        print("  python3 import_to_openviking.py --project axr --all          # axr 项目全量导入")
        print("  python3 import_to_openviking.py --recent 200                 # 导入最近 200 条")
        print("  python3 import_to_openviking.py --resume                     # 断点续传")
        print("  python3 import_to_openviking.py --register-only              # 只注册文件")
        return

    if not MCP_TOKEN:
        print("错误: MCP User Token 未配置，请检查 config.json 中的 mcp_user_token")
        sys.exit(1)

    print("=" * 60)
    print("飞书 Bug → OpenViking 批量导入 (MCP)")
    print("=" * 60)

    start_offset = args.start
    if args.resume:
        prog = load_progress()
        start_offset = prog.get("current_offset", 0)
        print(f"断点续传: 从 offset={start_offset} 继续")

    max_bugs = args.recent if args.recent else 0
    fetch_and_import_all(start_offset=start_offset, max_bugs=max_bugs)

    # 注册到 OpenViking
    print("\n" + "=" * 60)
    print("注册到 OpenViking...")
    print("=" * 60)
    register_with_openviking()

    print("\n全部完成！测试搜索:")
    print('  ov find "USB 连接异常" -u /feishu-bugs')


if __name__ == "__main__":
    main()
