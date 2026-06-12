#!/usr/bin/env python3
"""
飞书缺陷自动分析定时任务

每2小时增量拉取缺陷，检测 OPEN 状态的缺陷，进行分析，将报告回写至评论。
评论带有 [AI 自动分析] 标识，已评论过的不会重复评论。
"""

import json
import os
import sys
import time
import subprocess
import requests
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set

# === 路径配置 ===
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))
from config import get_feishu_config, load_config

CACHE_PATH = Path.home() / ".openviking/workspace/feishu-bugs/.bug_index_cache.json"
REPORTED_PATH = Path.home() / ".openviking/workspace/feishu-bugs/.ai_reported.json"
LOG_DIR = Path.home() / ".openviking/workspace/feishu-bugs"

# === 飞书 API 配置 ===
fs_cfg = get_feishu_config()
PROJECT_KEY = fs_cfg['project_key']
PLUGIN_ID = fs_cfg['plugin_id']
PLUGIN_SECRET = fs_cfg['plugin_secret']
USER_KEY = fs_cfg['user_key']
API_BASE = f"https://project.feishu.cn/open_api/{PROJECT_KEY}"
TOKEN_URL = "https://project.feishu.cn/open_api/authen/plugin_token"
BATCH_SIZE = 50

# 429 重试配置
MAX_RETRIES = 5
RETRY_BASE_DELAY = 30  # 秒

AI_REPORT_PREFIX = "[AI 自动分析]"
AI_REPORT_TAG = f"{AI_REPORT_PREFIX} 自动分析报告"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def get_plugin_token() -> Optional[str]:
    """获取 plugin token"""
    resp = requests.post(TOKEN_URL, json={
        "plugin_id": PLUGIN_ID,
        "plugin_secret": PLUGIN_SECRET,
        "type": 0
    }, timeout=10)
    data = resp.json()
    return data.get('data', {}).get('token')


def api_headers() -> dict:
    return {
        "x-plugin-token": get_plugin_token(),
        "x-user-key": USER_KEY,
        "Content-Type": "application/json"
    }


def fetch_with_retry(method: str, url: str, **kwargs) -> Optional[requests.Response]:
    """带 429 重试的 HTTP 请求"""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, timeout=60, **kwargs)
            if resp.status_code == 429:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log(f"  429 限流，等待 {delay}s 后重试 ({attempt+1}/{MAX_RETRIES})")
                time.sleep(delay)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                log(f"  请求异常: {e}，等待 {delay}s 重试")
                time.sleep(delay)
            else:
                log(f"  请求失败: {e}")
    return None


def get_max_cache_id() -> int:
    """获取缓存中最大的 bug ID"""
    if not CACHE_PATH.exists():
        return 0
    try:
        cache = json.load(open(CACHE_PATH))
        index = cache.get("index", {})
        ids = [int(k) for k in index.keys() if k.isdigit()]
        return max(ids) if ids else 0
    except Exception:
        return 0


def probe_new_bugs(start_id: int, probe_count: int = 2000) -> List[dict]:
    """从 start_id 向上探测新缺陷"""
    new_bugs = []
    for i in range(0, probe_count, BATCH_SIZE):
        batch = list(range(start_id + i, start_id + min(i + BATCH_SIZE, probe_count)))
        resp = fetch_with_retry("POST", f"{API_BASE}/work_item/issue/query",
                                json={"work_item_ids": batch})
        if not resp:
            continue
        bugs = resp.json().get("data", [])
        new_bugs.extend(bugs)
        if i % (BATCH_SIZE * 3) == 0:
            log(f"  探测进度: ID {start_id + i} ~ {start_id + i + len(batch)}，找到 {len(new_bugs)} 个")
        time.sleep(0.3)
    return new_bugs


def check_status_changed(existing_ids: Set[str]) -> List[dict]:
    """检查已知缺陷中是否有状态变为 OPEN 的"""
    # 只检查最近 30 天创建的缺陷
    recent_ids = []
    for wid in existing_ids:
        if len(wid) >= 10 and int(wid) > 6900000000:  # 粗略过滤较新的 ID
            recent_ids.append(int(wid))
    if not recent_ids:
        return []
    
    open_bugs = []
    for i in range(0, len(recent_ids), BATCH_SIZE):
        batch = recent_ids[i:i+BATCH_SIZE]
        resp = fetch_with_retry("POST", f"{API_BASE}/work_item/issue/query",
                                json={"work_item_ids": batch})
        if not resp:
            continue
        for bug in resp.json().get("data", []):
            status = bug.get("work_item_status", {})
            if isinstance(status, dict) and status.get("state_key") == "OPEN":
                open_bugs.append(bug)
        time.sleep(0.3)
    return open_bugs


def has_already_reported(bug_id: str) -> bool:
    """检查是否已经对该 bug 进行过 AI 分析评论"""
    if not REPORTED_PATH.exists():
        return False
    try:
        data = json.load(open(REPORTED_PATH))
        return str(bug_id) in data.get("reported_ids", set())
    except Exception:
        return False


def mark_reported(bug_id: str, report_summary: str = ""):
    """标记已报告"""
    data = {"reported_ids": [], "reports": {}}
    if REPORTED_PATH.exists():
        try:
            data = json.load(open(REPORTED_PATH))
        except Exception:
            pass
    
    if "reported_ids" not in data:
        data["reported_ids"] = []
    if "reports" not in data:
        data["reports"] = {}
    
    bug_id_str = str(bug_id)
    if bug_id_str not in data["reported_ids"]:
        data["reported_ids"].append(bug_id_str)
    
    data["reports"][bug_id_str] = {
        "reported_at": datetime.now().isoformat(),
        "summary": report_summary
    }
    
    # 只保留最近的 1000 条记录
    if len(data["reported_ids"]) > 1000:
        data["reported_ids"] = data["reported_ids"][-1000:]
    
    with open(REPORTED_PATH, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_bug_analysis(bug_id: str) -> Optional[Dict]:
    """运行 bug-analyzer 分析缺陷"""
    log(f"  开始分析 bug {bug_id}...")
    
    # 使用 bug_analyzer.py 的 feishu 命令
    analyzer_path = SCRIPTS_DIR / "bug_analyzer.py"
    cmd = [sys.executable, str(analyzer_path), "feishu", bug_id, "--llm"]
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=600,  # 10 分钟超时
            cwd=str(SCRIPTS_DIR)
        )
        
        output = result.stdout
        error = result.stderr
        
        if result.returncode != 0:
            log(f"  分析失败 (exit={result.returncode}): {error[:300]}")
            return None
        
        # 提取分析结果 - 查找报告部分
        analysis_result = {
            "bug_id": bug_id,
            "output": output[:5000],  # 截断输出
            "success": True
        }
        
        # 尝试提取关键信息
        for pattern_name, pattern in [
            ("root_cause", r"根因[分析]*[:：]\s*(.+?)\n"),
            ("confidence", r"置信度[：:]\s*(\d+\.?\d*)"),
            ("suggestion", r"建议[:：]\s*(.+?)(?:\n\n|\Z)")
        ]:
            match = re.search(pattern, output, re.DOTALL)
            if match:
                analysis_result[pattern_name] = match.group(1).strip()[:500]
        
        return analysis_result
        
    except subprocess.TimeoutExpired:
        log(f"  分析超时 (600s)")
        return None
    except Exception as e:
        log(f"  分析异常: {e}")
        return None


def format_ai_report(bug_info: dict, analysis_result: dict) -> str:
    """格式化 AI 分析报告为 Markdown"""
    bug_name = bug_info.get("name", "未知缺陷")
    bug_id = bug_info.get("id", "")
    
    report = f"""{AI_REPORT_TAG}

## 🤖 AI 自动分析报告

**缺陷 ID**: {bug_id}
**缺陷标题**: {bug_name}
**分析时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

### 📊 分析结果
"""
    
    # 添加分析摘要
    if analysis_result.get("output"):
        # 提取关键输出（去除前面的命令行输出）
        output = analysis_result["output"]
        # 找到报告开始位置
        report_start = output.find("=== 分析报告 ===")
        if report_start == -1:
            report_start = output.find("📊")
        if report_start == -1:
            report_start = 0
        
        # 取后面的内容
        report_content = output[report_start:report_start+3000]
        report += f"\n{report_content}\n"
    
    report += f"\n---\n"
    report += f"*⚠️ 本报告由 AI 自动生成，仅供参考。根因分析可能需要人工复核。*\n"
    
    return report


def post_comment(bug_id: str, content: str) -> bool:
    """通过 MCP JSON-RPC 向缺陷添加评论（替代 mcporter CLI）"""
    from mcp_client import mcp_add_comment

    # 截断过长的评论
    if len(content) > 15000:
        content = content[:14500] + "\n\n*（报告过长，已截断）*"

    try:
        success, detail = mcp_add_comment(PROJECT_KEY, str(bug_id), content)
        if success:
            log(f"  ✅ 评论回写成功 (id: {detail})")
            return True
        else:
            log(f"  ❌ 评论回写失败: {detail[:200]}")
            return False

    except Exception as e:
        log(f"  ❌ 评论回写异常: {e}")
        return False


def update_cache(new_bugs: List[dict]):
    """更新缓存，添加新发现的缺陷"""
    if not new_bugs:
        return
    
    log("  更新缓存...")
    cache = {}
    if CACHE_PATH.exists():
        try:
            cache = json.load(open(CACHE_PATH))
        except Exception:
            pass
    
    if "index" not in cache:
        cache["index"] = {}
    
    for bug in new_bugs:
        bug_id = str(bug.get("id", ""))
        if bug_id:
            status = bug.get("work_item_status", {})
            state_key = ""
            if isinstance(status, dict):
                state_key = status.get("state_key", "")
            
            cache["index"][bug_id] = {
                "id": bug_id,
                "name": bug.get("name", ""),
                "status": state_key,
                "updated_at": datetime.now().isoformat()
            }
    
    cache["count"] = len(cache["index"])
    cache["enriched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    
    log(f"  缓存已更新: {len(cache['index'])} 个缺陷")


def main():
    log("=" * 60)
    log("飞书缺陷自动分析任务启动")
    log("=" * 60)
    
    # 1. 获取缓存中最大 ID
    max_id = get_max_cache_id()
    log(f"缓存最大 ID: {max_id}")
    
    # 2. 探测新缺陷（从 max_id + 1 开始）
    probe_count = min(5000, 2000)  # 每次探测 2000 个 ID
    log(f"探测新缺陷: ID {max_id + 1} ~ {max_id + probe_count}")
    new_bugs = probe_new_bugs(max_id + 1, probe_count)
    log(f"发现 {len(new_bugs)} 个新缺陷")
    
    # 3. 检查已知缺陷中状态变为 OPEN 的
    existing_ids = set()
    if CACHE_PATH.exists():
        try:
            cache = json.load(open(CACHE_PATH))
            existing_ids = set(cache.get("index", {}).keys())
        except Exception:
            pass
    
    log("检查已有缺陷的状态变化...")
    status_changed_open = check_status_changed(existing_ids)
    log(f"状态变为 OPEN 的缺陷: {len(status_changed_open)} 个")
    
    # 4. 合并所有需要分析的缺陷
    all_open_bugs = []
    seen_ids = set()
    
    for bug in new_bugs + status_changed_open:
        bug_id = str(bug.get("id", ""))
        if bug_id in seen_ids:
            continue
        seen_ids.add(bug_id)
        
        status = bug.get("work_item_status", {})
        state_key = ""
        if isinstance(status, dict):
            state_key = status.get("state_key", "")
        
        if state_key == "OPEN":
            all_open_bugs.append(bug)
    
    log(f"共 {len(all_open_bugs)} 个 OPEN 状态缺陷需要分析")
    
    # 5. 过滤已报告的缺陷
    to_analyze = []
    for bug in all_open_bugs:
        bug_id = str(bug.get("id", ""))
        if has_already_reported(bug_id):
            log(f"  跳过已报告的 bug {bug_id}")
        else:
            to_analyze.append(bug)
    
    log(f"待分析缺陷数: {len(to_analyze)}")
    
    if not to_analyze:
        log("没有需要分析的新缺陷")
        log("=" * 60)
        log("任务完成")
        return
    
    # 6. 逐个分析并回写报告
    analyzed_count = 0
    failed_count = 0
    
    for bug in to_analyze:
        bug_id = str(bug.get("id", ""))
        bug_name = bug.get("name", "")[:80]
        log(f"\n处理缺陷: {bug_id} - {bug_name}")
        
        # 运行分析
        analysis_result = run_bug_analysis(bug_id)
        
        if analysis_result:
            # 格式化报告
            report = format_ai_report(bug, analysis_result)
            
            # 回写评论
            if post_comment(bug_id, report):
                mark_reported(bug_id, bug_name)
                analyzed_count += 1
                # 评论后稍作等待，避免触发限流
                time.sleep(5)
            else:
                failed_count += 1
        else:
            log(f"  分析失败，跳过评论")
            failed_count += 1
        
        # 每个缺陷之间等待，避免触发限流
        time.sleep(10)
    
    # 7. 更新缓存
    update_cache(new_bugs)
    
    # 8. 输出统计
    log("\n" + "=" * 60)
    log(f"任务完成统计:")
    log(f"  新发现缺陷: {len(new_bugs)}")
    log(f"  状态变更: {len(status_changed_open)}")
    log(f"  分析成功: {analyzed_count}")
    log(f"  分析失败: {failed_count}")
    log("=" * 60)


if __name__ == "__main__":
    main()
