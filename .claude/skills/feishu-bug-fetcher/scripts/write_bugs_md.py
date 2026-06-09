#!/usr/bin/env python3
"""从 MCP search_by_mql 返回的 JSON 写入 Markdown 文件"""
import json, sys, os
from pathlib import Path

BUGS_DIR = Path(os.path.expanduser("~/.openviking/data/viking/default/feishu-bugs"))
BUGS_DIR.mkdir(parents=True, exist_ok=True)


def parse_field(field: dict) -> str:
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
    elif vtype == "text_value":
        return val.get("text_value", "") or ""
    return ""


def bug_to_markdown(bug: dict, project_key: str) -> str:
    bid = bug.get("work_item_id", "")
    name = bug.get("name", "")
    status = bug.get("work_item_status", "")
    severity = bug.get("severity", "")
    priority = bug.get("priority", "")
    module_name = bug.get("field_78151b", "")
    bug_class = bug.get("field_8218e6", "") or bug.get("bug_classification", "")
    business = bug.get("business", "")
    template = bug.get("template", "")
    stage = bug.get("issue_stage", "")
    start_time = bug.get("start_time", "")
    updated_at = bug.get("updated_at", "")
    self_test = bug.get("field_51dbab", "")
    regression = bug.get("field_9b0963", "")
    version = bug.get("field_897169", "")
    platform = bug.get("field_e4ca95", "")
    app = bug.get("field_9d6348", "")

    lines = [
        f"# {name}",
        "",
        f"- **Bug ID**: {bid}",
        f"- **项目**: {project_key}",
        f"- **状态**: {status}",
        f"- **严重程度**: {severity}",
        f"- **优先级**: {priority}",
        f"- **模块**: {module_name}",
        f"- **缺陷类型**: {bug_class}",
        f"- **业务线**: {business}",
        f"- **质量类型**: {template}",
        f"- **发现阶段**: {stage}",
        f"- **平台**: {platform}",
        f"- **应用**: {app}",
        f"- **版本号**: {version}",
        f"- **提出时间**: {start_time}",
        f"- **更新时间**: {updated_at}",
    ]
    if self_test:
        lines.append(f"- **研发自测结论**: {self_test}")
    if regression:
        lines.append("")
        lines.append("## 问题回归建议")
        lines.append(regression)
    return "\n".join(lines)


def process(data: dict, project_key: str):
    """处理 MQL 返回数据，写入 markdown"""
    written = 0
    if isinstance(data, list):
        items = data
    else:
        items = []
        for group_id, bugs in data.get("data", {}).items():
            for item in bugs:
                bug = {}
                for f in item.get("moql_field_list", []):
                    bug[f["key"]] = parse_field(f)
                items.append(bug)

    for bug in items:
        bid = bug.get("work_item_id")
        if not bid:
            continue
        md = bug_to_markdown(bug, project_key)
        filepath = BUGS_DIR / f"{bid}.md"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(md)
        written += 1
    print(f"写入 {written} 个 markdown 文件到 {BUGS_DIR}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 write_bugs_md.py <project_key> [json_file]")
        print("  或从 stdin: cat result.json | python3 write_bugs_md.py <project_key>")
        sys.exit(1)

    project_key = sys.argv[1]
    if len(sys.argv) >= 3:
        with open(sys.argv[2]) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)
    process(data, project_key)
