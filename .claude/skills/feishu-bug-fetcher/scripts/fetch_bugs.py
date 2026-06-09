#!/usr/bin/env python3
"""
飞书缺陷数据获取工具 - Claude Code 适配版

认证方式（按优先级）：
1. MCP User Token + meego MCP 服务器（推荐，已在 ~/.claude.json 中配置）
2. Plugin Token + Direct API（仅评论获取，因为 issue/query 需要更高权限）

用法:
    python3 scripts/fetch_bugs.py --list                    # 获取缺陷列表（最新50条）
    python3 scripts/fetch_bugs.py --details 123,456,789     # 获取指定ID详情
    python3 scripts/fetch_bugs.py --recent 50               # 获取最新50条
    python3 scripts/fetch_bugs.py --single 6970632429       # 获取单个缺陷（详情+评论+附件）
    python3 scripts/fetch_bugs.py --all                     # 全量获取（列表+详情+评论+附件）
    python3 scripts/fetch_bugs.py --config                  # 检查配置状态
"""

import json
import os
import sys
import time
import requests
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

# ============================================================
# 配置
# ============================================================

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent  # scripts 的上一级才是技能根目录
CONFIG_FILE = SKILL_DIR / "config.json"
DATA_DIR = Path(os.path.expanduser("~/.openviking/workspace/feishu-bugs"))
BATCH_DIR = DATA_DIR / "batch"
SINGLE_DIR = DATA_DIR / "single"

MCP_URL = "https://project.feishu.cn/mcp_server/v1"

# 默认配置
DEFAULT_CONFIG = {
    "project_key": "sw_team",
    "mcp_user_token": "",
    "output_dir": str(DATA_DIR),
    "concurrency": {"max_workers": 5, "rate_limit": 10},
    "fetch": {"page_size": 50, "max_retries": 3, "retry_interval": 2, "request_timeout": 30},
    "incremental": {"enabled": True, "checkpoint_file": ".fetch_progress.json"},
}

# 加载配置
def load_config() -> Dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                user_cfg = json.load(f)
            _deep_merge(config, user_cfg)
        except Exception as e:
            print(f"Warning: config.json 加载失败: {e}")
    # 环境变量覆盖
    for env_key, cfg_key in [
        ("BUG_INSIGHT_FEISHU_PROJECT_KEY", "project_key"),
        ("BUG_INSIGHT_FEISHU_MCP_TOKEN", "mcp_user_token"),
        ("BUG_INSIGHT_FEISHU_PLUGIN_ID", "plugin_id"),
        ("BUG_INSIGHT_FEISHU_PLUGIN_SECRET", "plugin_secret"),
        ("BUG_INSIGHT_FEISHU_USER_KEY", "user_key"),
    ]:
        val = os.getenv(env_key)
        if val:
            config[cfg_key] = val
    return config

def _deep_merge(base: Dict, override: Dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v

CONFIG = load_config()
PROJECT_KEY = CONFIG.get("project_key", "sw_team")
MCP_USER_TOKEN = CONFIG.get("mcp_user_token", "")
PLUGIN_ID = CONFIG.get("plugin_id", "")
PLUGIN_SECRET = CONFIG.get("plugin_secret", "")
USER_KEY = CONFIG.get("user_key", "")
PAGE_SIZE = CONFIG.get("fetch", {}).get("page_size", 50)
TIMEOUT = CONFIG.get("fetch", {}).get("request_timeout", 30)
MAX_RETRIES = CONFIG.get("fetch", {}).get("max_retries", 3)


# ============================================================
# MCP 工具调用（通过 MCP User Token）
# ============================================================

def mcp_call(tool_name: str, args: dict) -> Optional[Any]:
    """调用 meego MCP 工具"""
    if not MCP_USER_TOKEN:
        return None
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {"name": tool_name, "arguments": args},
    }
    headers = {"Content-Type": "application/json", "X-Mcp-Token": MCP_USER_TOKEN}

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(MCP_URL, headers=headers, json=payload, timeout=TIMEOUT)
            data = resp.json()
            content = data.get("result", {}).get("content", [])
            for c in content:
                t = c.get("text", "")
                if t.startswith("[") or t.startswith("{"):
                    try:
                        return json.loads(t)
                    except json.JSONDecodeError:
                        pass
                # 检查错误信息
                if "err_code" in t or "error" in t.lower():
                    try:
                        err = json.loads(t)
                        print(f"  MCP 错误 (attempt {attempt+1}): {err.get('err_msg', err.get('message', t[:100]))}")
                    except:
                        print(f"  MCP 错误 (attempt {attempt+1}): {t[:100]}")
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(CONFIG.get("fetch", {}).get("retry_interval", 2))
                        break
            return None
        except Exception as e:
            print(f"  请求异常 (attempt {attempt+1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(CONFIG.get("fetch", {}).get("retry_interval", 2))
    return None


# ============================================================
# Plugin Token（仅用于评论获取，issue/query 无权限）
# ============================================================

def get_plugin_token() -> Optional[str]:
    """获取 Plugin Token"""
    if not PLUGIN_ID or not PLUGIN_SECRET:
        return None
    try:
        resp = requests.post(
            "https://project.feishu.cn/open_api/authen/plugin_token",
            json={"plugin_id": PLUGIN_ID, "plugin_secret": PLUGIN_SECRET, "type": 0},
            timeout=10,
        )
        return resp.json().get("data", {}).get("token")
    except Exception as e:
        print(f"  获取 token 失败: {e}")
        return None


# ============================================================
# 数据获取
# ============================================================

def fetch_recent_bugs(limit: int = 50) -> List[Dict]:
    """通过 MCP search_by_mql 获取最新缺陷"""
    actual_limit = min(limit, PAGE_SIZE)  # MCP 固定返回前 50 条
    print(f"获取最新 {actual_limit} 条缺陷...")

    result = mcp_call("search_by_mql", {
        "project_key": PROJECT_KEY,
        "mql": f"SELECT work_item_id, name FROM {PROJECT_KEY}.issue LIMIT {actual_limit}",
    })

    bugs = []
    if not result:
        print("  MCP 调用失败")
        return bugs

    # 解析 search_by_mql 返回格式
    data_groups = result.get("data", {})
    for group_id, items in data_groups.items():
        for item in items:
            bug_info = {"id": "", "name": "", "status": "", "created_at": 0, "updated_at": 0}
            for field in item.get("moql_field_list", []):
                key = field.get("key")
                val = field.get("value", {})
                if key == "work_item_id":
                    bug_info["id"] = str(val.get("long_value") or val.get("text_value") or "")
                elif key == "name":
                    bug_info["name"] = val.get("text_value", "")
            if bug_info["id"]:
                bugs.append(bug_info)

    print(f"  获取到 {len(bugs)} 个缺陷")
    return bugs


def fetch_bug_details(bug_ids: List[str]) -> List[Dict]:
    """批量获取缺陷详情（通过 MCP get_workitem_brief）"""
    print(f"获取缺陷详情 (共 {len(bug_ids)} 个)...")
    results = []

    for i, bid in enumerate(bug_ids):
        detail = mcp_call("get_workitem_brief", {
            "project_key": PROJECT_KEY,
            "work_item_id": str(bid),
        })
        if detail:
            results.append(detail)
        if (i + 1) % 50 == 0:
            print(f"  进度: {i + 1}/{len(bug_ids)}")
        time.sleep(0.1)

    print(f"  获取到 {len(results)} 个详情")
    return results


def fetch_bug_comments(bug_id: str) -> List[Dict]:
    """获取单个缺陷的评论（通过 MCP）"""
    result = mcp_call("list_workitem_comments", {
        "project_key": PROJECT_KEY,
        "work_item_id": str(bug_id),
    })
    if result:
        return result.get("comments", [])
    return []


def fetch_single_bug_full(bug_id: str) -> Dict:
    """获取单个缺陷的完整信息（详情 + 评论 + 附件）"""
    print(f"获取缺陷 {bug_id} 的完整信息...")

    report = {"id": bug_id, "fetch_time": datetime.now().isoformat()}

    # 详情
    detail = mcp_call("get_workitem_brief", {
        "project_key": PROJECT_KEY,
        "work_item_id": str(bug_id),
    })
    if detail:
        report["detail"] = detail
        # MCP get_workitem_brief 返回格式: {"work_item_attribute": {...}}
        attrs = detail.get("work_item_attribute", {})
        report["name"] = attrs.get("work_item_name", "")
        status_info = attrs.get("work_item_status", {})
        report["status"] = status_info.get("key", status_info.get("state_key", ""))
        report["created_at"] = attrs.get("create_time", "")
        report["update_time"] = attrs.get("update_time", "")
        report["description"] = attrs.get("description", "")

        # 从 fields 提取（如果有的话）
        for field in attrs.get("work_item_fields", []):
            fk = field.get("field_key", "")
            fv = field.get("field_value")
            if fk == "priority" and fv:
                report["priority"] = fv[0].get("name", "") if isinstance(fv, list) else str(fv)
            elif fk == "severity" and fv:
                report["severity"] = fv[0].get("name", "") if isinstance(fv, list) else str(fv)
            elif fk == "issue_reporter" and fv:
                report["reporter"] = fv
            elif fk == "multi_attachment" and fv:
                report["attachments"] = fv

        # 从 role_members 提取经办人/测试leader等
        for role in attrs.get("role_members", []):
            members = role.get("members", [])
            if members:
                report.setdefault("roles", {})[role["name"]] = [m["name"] for m in members]

    # 评论
    comments = fetch_bug_comments(bug_id)
    if comments:
        report["comments"] = comments
        report["comment_count"] = len(comments)

    # 保存报告
    SINGLE_DIR.mkdir(parents=True, exist_ok=True)
    out_file = SINGLE_DIR / f"bug_{bug_id}_report.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  报告已保存: {out_file}")

    return report


def fetch_all_bugs_full() -> Dict:
    """全量获取：列表 -> 详情 -> 评论 -> 附件"""
    print("=" * 50)
    print("飞书缺陷全量数据获取")
    print("=" * 50)

    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    # 步骤1: 获取缺陷列表（最新 50 条）
    print("\n=== 步骤1: 获取缺陷列表 ===")
    bugs_list = fetch_recent_bugs(50)
    bug_ids = [b["id"] for b in bugs_list]

    # 保存索引
    index_file = BATCH_DIR / "bugs_index.json"
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(bugs_list, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {index_file}")

    if not bug_ids:
        print("没有获取到缺陷 ID")
        return {}

    # 步骤2: 获取详情
    print("\n=== 步骤2: 获取缺陷详情 ===")
    details = fetch_bug_details(bug_ids)
    details_file = BATCH_DIR / "bugs_full_all.json"
    with open(details_file, "w", encoding="utf-8") as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    print(f"  已保存: {details_file}")

    # 步骤3: 获取评论和附件
    print("\n=== 步骤3: 提取评论和附件 ===")
    comments_list = []
    attachments_list = []
    for item in details:
        attrs = item.get("work_item_attribute", {}) if isinstance(item, dict) else {}
        bug_id = str(attrs.get("work_item_id", ""))

        # 附件
        for field in item.get("work_item", {}).get("work_item_fields", []):
            if field.get("field_key") == "multi_attachment":
                atts = field.get("field_value", [])
                if atts:
                    attachments_list.append({"id": bug_id, "attachments": atts})

        # 评论
        comments = fetch_bug_comments(bug_id)
        if comments:
            comments_list.append({"work_item_id": bug_id, "comments": comments})
        time.sleep(0.1)

    comments_file = BATCH_DIR / "bugs_with_comments.json"
    with open(comments_file, "w", encoding="utf-8") as f:
        json.dump(comments_list, f, ensure_ascii=False, indent=2)
    print(f"  评论已保存: {comments_file} ({len(comments_list)} 条)")

    attachments_file = BATCH_DIR / "bugs_attachments.json"
    with open(attachments_file, "w", encoding="utf-8") as f:
        json.dump(attachments_list, f, ensure_ascii=False, indent=2)
    print(f"  附件已保存: {attachments_file} ({len(attachments_list)} 条)")

    print("\n" + "=" * 50)
    print(f"完成！共处理 {len(bug_ids)} 个缺陷")
    print(f"数据目录: {BATCH_DIR}")
    return {
        "bugs": len(bugs_list),
        "details": len(details),
        "comments": len(comments_list),
        "attachments": len(attachments_list),
    }


# ============================================================
# 配置检查
# ============================================================

def check_config():
    """检查配置状态"""
    print("配置检查:")
    print(f"  project_key:      {PROJECT_KEY or '(未配置)'}")
    print(f"  mcp_user_token:   {'***' + MCP_USER_TOKEN[-8:] if MCP_USER_TOKEN else '(未配置)'}")
    print(f"  plugin_id:        {'***' + PLUGIN_ID[-8:] if PLUGIN_ID else '(未配置)'}")
    print(f"  plugin_secret:    {'[SET]' if PLUGIN_SECRET else '(未配置)'}")
    print(f"  user_key:         {USER_KEY or '(未配置)'}")
    print(f"  output_dir:       {DATA_DIR}")

    missing = []
    if not MCP_USER_TOKEN and not PLUGIN_ID:
        missing.append("mcp_user_token 或 plugin_id（至少需要一种认证方式）")
    if not USER_KEY:
        missing.append("user_key")

    if missing:
        print(f"\n  ⚠️  缺少以下配置: {', '.join(missing)}")
        print(f"  请编辑 {CONFIG_FILE} 或设置环境变量")
    else:
        print("\n  ✅ 配置完整")


# ============================================================
# 主函数
# ============================================================

def main():
    if "--config" in sys.argv:
        check_config()
        return

    if "--single" in sys.argv:
        idx = sys.argv.index("--single")
        if idx + 1 < len(sys.argv):
            bug_id = sys.argv[idx + 1].strip()
            fetch_single_bug_full(bug_id)
        else:
            print("用法: python3 scripts/fetch_bugs.py --single <缺陷ID>")
        return

    if "--details" in sys.argv:
        idx = sys.argv.index("--details")
        if idx + 1 < len(sys.argv):
            ids = [x.strip() for x in sys.argv[idx + 1].split(",") if x.strip()]
            details = fetch_bug_details(ids)
            details_file = SINGLE_DIR / "details_batch.json"
            SINGLE_DIR.mkdir(parents=True, exist_ok=True)
            with open(details_file, "w", encoding="utf-8") as f:
                json.dump(details, f, ensure_ascii=False, indent=2)
            print(f"已保存: {details_file}")
        return

    if "--recent" in sys.argv:
        idx = sys.argv.index("--recent")
        limit = 50
        if idx + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[idx + 1])
            except ValueError:
                pass
        fetch_recent_bugs(limit)
        return

    if "--all" in sys.argv:
        fetch_all_bugs_full()
        return

    if "--list" in sys.argv:
        fetch_recent_bugs(50)
        return

    # 默认: 显示帮助
    print(__doc__)
    check_config()


if __name__ == "__main__":
    main()
