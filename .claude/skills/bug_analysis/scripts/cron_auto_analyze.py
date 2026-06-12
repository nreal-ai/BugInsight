#!/usr/bin/env python3
"""
飞书缺陷自动分析 & 评论定时任务脚本

工作流程：
1. 通过 MCP JSON-RPC 分页查询所有 OPEN 缺陷（OFFSET 分页，解决 mcporter 前50条限制）
2. 对比本地索引找出新增 OPEN 缺陷
3. 逐个运行 bug_analyzer.py --llm 进行深度分析
4. 将分析结果以 [AI分析] 标记评论到飞书
5. 生成汇总报告

用法: python3 cron_auto_analyze.py
"""

import json
import os
import subprocess
import sys
import time
import glob
import requests
from datetime import datetime
from pathlib import Path

# Redirect stdout to output file so cron Agent always reads fresh data
OUTPUT_PATH = "/tmp/cron_auto_analyze_last_output.txt"
_tee_file = open(OUTPUT_PATH, "w", encoding="utf-8")
_orig_stdout = sys.stdout

class TeeOutput:
    def write(self, text):
        _tee_file.write(text)
        _orig_stdout.write(text)
    def flush(self):
        try:
            _tee_file.flush()
        except (ValueError, IOError):
            pass
        try:
            _orig_stdout.flush()
        except (ValueError, IOError):
            pass
        try:
            os.fsync(_tee_file.fileno())
        except (OSError, ValueError):
            pass

sys.stdout = TeeOutput()

# Config
BUG_ANALYZER_DIR = str(Path(__file__).parent)  # script-relative (no hardcoded paths)
CACHE_PATH = os.path.expanduser("~/.openviking/workspace/feishu-bugs/.bug_index_cache.json")
PROJECT_KEYS = ["sw_team", "676e7fecad8e9de8735fa89f"]
ANALYSIS_TIMEOUT = 900  # 15 min per bug — enough for LLM (2 rounds × ~300s) + API calls
MAX_ATTACHMENTS_THRESHOLD = 15  # skip analysis for bugs with more attachments (too slow)
SKIP_ZIP_THRESHOLD = 3  # bugs with 3+ zip files are skipped entirely
RETRY_FILE = "/tmp/.cron_auto_analyze_retry.json"  # track failed bugs for retry
MAX_RETRY_ATTEMPTS = 2  # max retries per bug before giving up
COMMENT_TIMEOUT = 30
FETCH_TIMEOUT = 60
FETCH_PAGE_SIZE = 50  # bugs per page when paginating
FETCH_MAX_PAGES = 30  # safety limit (50 × 30 = 1500 bugs max per project)
ANALYSIS_OUTPUT_DIR = "/tmp"
OV_API_BASE = "http://127.0.0.1:1933"
OV_HEADERS = {"X-OpenViking-Account": "default", "X-OpenViking-User": "admin"}
OV_IMPORT_DIR = os.path.expanduser("~/.openviking/workspace/feishu-bugs/ov_import_closed/")
OV_ARCHIVED_PATH = os.path.expanduser("~/.openviking/workspace/feishu-bugs/.ov_archived.json")
# Cron run log: append each run's summary for history
CRON_RUN_LOG = "/tmp/cron_auto_analyze_history.log"


# Import MCP client functions (replaces mcporter CLI)
from mcp_client import get_mcp_token, get_plugin_token, get_user_key, mcp_call, mcp_add_comment, parse_moql_field_list


def fetch_bugs(project_key):
    """Fetch ALL bugs from Feishu Project via MCP JSON-RPC with OFFSET pagination.

    Replaces the old mcporter CLI approach which was limited to the first 50 results.
    Uses MQL SELECT with LIMIT/OFFSET to paginate through all bugs in the project.
    """
    pk_ref = f"`{project_key}`" if project_key != "sw_team" else project_key
    all_bugs = []
    offset = 0

    for page in range(FETCH_MAX_PAGES):
        mql = f"SELECT work_item_id, name, work_item_status FROM {pk_ref}.issue LIMIT {FETCH_PAGE_SIZE} OFFSET {offset}"

        result, err = mcp_call(
            "search_by_mql",
            {"project_key": project_key, "mql": mql},
            timeout=FETCH_TIMEOUT,
        )

        if err:
            print(f"  [WARN] MCP call failed at offset {offset}: {err}")
            break

        items = result.get("data", {}).get("1", [])
        if not items:
            break

        for item in items:
            fields = _parse_moql_field_list(item.get("moql_field_list", []))
            fields["_project_key"] = project_key
            all_bugs.append(fields)

        if len(items) < FETCH_PAGE_SIZE:
            # Last page
            break

        offset += FETCH_PAGE_SIZE
        time.sleep(0.3)  # rate limit between pages

    print(f"    [分页] 共 {offset // FETCH_PAGE_SIZE + 1} 页, 获取 {len(all_bugs)} 条")
    return all_bugs


def filter_open_bugs(bugs):
    """Filter bugs that are in OPEN status.
    Handles both flat format (work_item_status: "OPEN") and nested format."""
    open_bugs = []
    for bug in bugs:
        status = bug.get("work_item_status", "")
        # Handle nested dict format (old style)
        if isinstance(status, dict):
            status = status.get("state_key", "") or bug.get("sub_stage", "")
        if status == "OPEN":
            open_bugs.append(bug)
    return open_bugs


def get_closed_bug_ids(open_bugs):
    """Find bugs that were OPEN in cache but are actually CLOSED now.

    Uses a two-step verification to avoid false positives from MQL pagination
    race conditions (newly created bugs may be missed in OFFSET pagination):
    1. If cached OPEN but NOT in current OPEN list -> candidate
    2. Verify actual status via Direct API before declaring closed

    Args:
        open_bugs: list of currently OPEN bugs from API
    Returns:
        (closed_ids, uploaded_set)
    """
    if not os.path.exists(CACHE_PATH):
        return [], []

    try:
        with open(CACHE_PATH) as f:
            cache = json.load(f)
        cache_index = cache.get("index", {})
    except (json.JSONDecodeError, KeyError):
        return [], []

    # Current OPEN bug IDs from API
    current_open_ids = set(str(bug.get("work_item_id", "")) for bug in open_bugs if bug.get("work_item_id"))

    # Track uploaded bugs (avoid duplicate uploads)
    uploaded = set()
    if os.path.exists(OV_ARCHIVED_PATH):
        try:
            with open(OV_ARCHIVED_PATH) as f:
                uploaded = set(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass

    # Step 1: Find candidates - OPEN in cache but NOT in current OPEN list
    candidates = []
    for wid, entry in cache_index.items():
        if not wid:
            continue
        cached_status = entry.get("status", "")
        if cached_status == "OPEN" and wid not in current_open_ids and wid not in uploaded:
            candidates.append(wid)

    if not candidates:
        return [], list(uploaded)

    # Step 2: Verify actual status via Direct API before declaring closed
    # This prevents false positives from MQL OFFSET pagination race conditions
    print(f"  [Closed] 发现 {len(candidates)} 个疑似关闭的缺陷，验证实际状态...")
    closed_ids = []
    false_positives = []

    # Batch verify via Direct API (max 50 per batch)
    for batch_start in range(0, len(candidates), 50):
        batch = candidates[batch_start:batch_start + 50]
        token = get_plugin_token()
        if not token:
            # Can't verify, skip all to be safe
            print(f"  [WARN] 无法获取 plugin token，跳过 {len(batch)} 个疑似关闭缺陷的验证")
            false_positives.extend(batch)
            continue

        headers = {
            "x-plugin-token": token,
            "x-user-key": get_user_key(),
            "Content-Type": "application/json",
        }
        # Determine project for each candidate
        batch_by_project = {}
        for wid in batch:
            entry = cache_index.get(wid, {})
            pk = entry.get("_project_key", PROJECT_KEYS[0])
            batch_by_project.setdefault(pk, []).append(int(wid))

        verified_closed = []
        for pk, ids in batch_by_project.items():
            pk_ref = pk if pk == "sw_team" else f"`{pk}`"
            try:
                resp = requests.post(
                    f"https://project.feishu.cn/open_api/{pk}/work_item/issue/query",
                    json={"work_item_ids": ids},
                    headers=headers,
                    timeout=30,
                )
                data = resp.json().get("data", [])
                found_ids = set(str(bug.get("id", "")) for bug in data)

                for wid in [str(i) for i in ids]:
                    if wid in found_ids:
                        # Bug found in Direct API - check its actual status
                        bug_data = [b for b in data if str(b.get("id", "")) == wid]
                        if bug_data:
                            bug = bug_data[0]
                            status = bug.get("work_item_status", {})
                            if isinstance(status, dict):
                                actual_status = status.get("state_key", "")
                            else:
                                actual_status = str(status)
                            if actual_status == "OPEN":
                                false_positives.append(wid)
                                print(f"    [误判] {wid} 实际状态仍为 OPEN，不是关闭缺陷")
                            else:
                                verified_closed.append(wid)
                        else:
                            verified_closed.append(wid)
                    else:
                        # Bug NOT found in Direct API -> likely truly closed/deleted
                        verified_closed.append(wid)
            except Exception as e:
                print(f"  [WARN] Direct API 验证失败 (project={pk}): {e}")
                false_positives.extend([str(i) for i in ids])

        closed_ids.extend(verified_closed)

    if false_positives:
        print(f"  [Closed] 排除 {len(false_positives)} 个误判（实际仍为 OPEN）")
    if closed_ids:
        print(f"  [Closed] 确认 {len(closed_ids)} 个真正关闭的缺陷")

    return sorted(closed_ids), list(uploaded)


def fetch_bug_full_info(bug_id, project_key=None):
    """Fetch full bug details: name, status, description, comments."""
    if project_key is None:
        project_key = PROJECT_KEYS[0]
    # Get name and status from cache
    name = f"Bug {bug_id}"
    status = "Unknown"
    desc = ""

    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                cache = json.load(f)
            entry = cache.get("index", {}).get(bug_id, {})
            name = entry.get("name", name)
            status = entry.get("status", status)
            desc = entry.get("desc_lower", "") or entry.get("description", "")
        except (json.JSONDecodeError, KeyError):
            pass

    # Fetch comments via MCP JSON-RPC
    comments = []
    result, err = mcp_call(
        "list_workitem_comments",
        {"work_item_id": bug_id, "project_key": project_key, "page_num": 1, "page_size": 50},
        timeout=COMMENT_TIMEOUT,
    )
    if result:
        comments = result.get("comments", [])

    return {
        "bug_id": bug_id,
        "name": name,
        "status": status,
        "description": desc,
        "comments": comments
    }


def build_closed_bug_markdown(bug_info):
    """Build a Markdown document for a closed bug to import into OpenViking."""
    bug_id = bug_info["bug_id"]
    name = bug_info["name"]
    status = bug_info["status"]
    desc = bug_info["description"]
    comments = bug_info["comments"]

    md = f"# 缺陷 #{bug_id}: {name}\n\n"
    md += f"**状态**: {status}\n"
    md += f"**缺陷ID**: {bug_id}\n\n"

    if desc:
        md += f"## 缺陷描述\n\n{desc}\n\n"

    if comments:
        md += f"## 评论 ({len(comments)} 条)\n\n"
        for i, c in enumerate(comments, 1):
            content = c.get("content", "").strip()
            if not content:
                continue
            creator = c.get("creator_name", c.get("creator", "未知"))
            created_at = c.get("created_at", "")
            md += f"**评论 {i}** ({created_at}) — {creator}:\n{content}\n\n"

    return md


def _track_archived_bug(bug_id):
    """Ensure a bug_id is tracked in .ov_archived.json to prevent duplicate imports."""
    existing = set()
    if os.path.exists(OV_ARCHIVED_PATH):
        try:
            with open(OV_ARCHIVED_PATH) as f:
                existing = set(json.load(f))
        except (json.JSONDecodeError, IOError):
            pass
    if str(bug_id) not in existing:
        existing.add(str(bug_id))
        with open(OV_ARCHIVED_PATH, "w") as f:
            json.dump(sorted(existing), f)


def upload_to_openviking(filename, markdown_content):
    """Upload a Markdown file to OpenViking for vectorization.
    Uses HTTP API first, falls back to ov CLI. Always saves markdown locally."""
    # Ensure import directory exists
    os.makedirs(OV_IMPORT_DIR, exist_ok=True)
    filepath = os.path.join(OV_IMPORT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    # Method 1: Try HTTP API
    try:
        resp = requests.get(f"{OV_API_BASE}/health", timeout=5)
        if resp.status_code == 200:
            # Upload via temp_upload + add_resource
            with open(filepath, "rb") as f:
                files = {"file": (filename, f, "text/markdown")}
                resp = requests.post(
                    f"{OV_API_BASE}/api/v1/resources/temp_upload",
                    headers=OV_HEADERS,
                    files=files,
                    timeout=120
                )
            resp.raise_for_status()
            temp_file_id = resp.json()["result"]["temp_file_id"]

            resp2 = requests.post(
                f"{OV_API_BASE}/api/v1/resources",
                headers=OV_HEADERS,
                json={
                    "temp_file_id": temp_file_id,
                    "reason": f"Closed bug import: {filename}",
                    "instruction": "Extract all content for semantic search.",
                    "wait": False
                },
                timeout=30
            )
            resp2.raise_for_status()
            return True, "HTTP API uploaded"
    except Exception as e:
        pass  # Fall through to CLI

    # Method 2: Try ov CLI command
    try:
        ov_bin = os.path.expanduser("~/.openviking/venv/bin/ov")
        cmd = f'{ov_bin} add-resource "{filepath}" --parent "default:/feishu-bugs/closed" --account default --user admin --reason "Closed bug import" --compact 2>&1'
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120,
                               cwd=os.path.expanduser("~/.openviking"))
        if result.returncode == 0:
            return True, f"CLI uploaded: {result.stdout.strip()[:100]}"
        else:
            return False, f"CLI failed: {result.stderr.strip()[:200] or result.stdout.strip()[:200]}"
    except Exception as e:
        return False, f"Markdown saved locally but OpenViking unavailable: {str(e)[:100]}"


def process_closed_bugs(closed_ids):
    """Process closed bugs: fetch info, generate markdown, upload to OpenViking."""
    if not closed_ids:
        print("\n没有新关闭的缺陷需要导入 OpenViking")
        return []

    print(f"\n[Closed] 发现 {len(closed_ids)} 个新关闭的缺陷，准备导入 OpenViking...")

    results = []
    for i, bug_id in enumerate(closed_ids):
        print(f"  [{i+1}/{len(closed_ids)}] 处理 Bug {bug_id}...")

        # Skip if already imported (check markdown file exists)
        md_path = os.path.join(OV_IMPORT_DIR, f"closed_bug_{bug_id}.md")
        if os.path.exists(md_path):
            print(f"    [SKIP] Markdown 文件已存在，跳过重复导入")
            results.append({"bug_id": bug_id, "status": "already_imported", "name": ""})
            # Also ensure it's tracked in archived list
            _track_archived_bug(bug_id)
            continue

        # Fetch full info
        bug_info = fetch_bug_full_info(bug_id)
        name = bug_info["name"]
        status = bug_info["status"]
        print(f"    名称: {name[:60]}")
        print(f"    状态: {status}")
        print(f"    评论: {len(bug_info['comments'])} 条")

        # Build markdown
        md_content = build_closed_bug_markdown(bug_info)
        filename = f"closed_bug_{bug_id}.md"
        print(f"    Markdown: {len(md_content)} 字符")

        # Upload to OpenViking
        ok, msg = upload_to_openviking(filename, md_content)
        if ok:
            print(f"    [OK] 已上传到 OpenViking: {msg}")
            results.append({"bug_id": bug_id, "status": "uploaded", "name": name[:60]})
            _track_archived_bug(bug_id)
        else:
            print(f"    [WARN] OpenViking 上传失败: {msg}")
            results.append({"bug_id": bug_id, "status": "ov_failed", "name": name[:60], "reason": msg})

        # Rate limit
        if i < len(closed_ids) - 1:
            time.sleep(3)

    # Update archived tracking
    uploaded_ids = [r["bug_id"] for r in results if r["status"] == "uploaded"]
    if uploaded_ids:
        existing = set()
        if os.path.exists(OV_ARCHIVED_PATH):
            try:
                with open(OV_ARCHIVED_PATH) as f:
                    existing = set(json.load(f))
            except (json.JSONDecodeError, IOError):
                pass
        existing.update(uploaded_ids)
        with open(OV_ARCHIVED_PATH, "w") as f:
            json.dump(sorted(existing), f)

    return results


def _load_retry_list():
    """Load the retry list from disk."""
    if not os.path.exists(RETRY_FILE):
        return {}
    try:
        with open(RETRY_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_retry_list(retry_list):
    """Save the retry list to disk."""
    with open(RETRY_FILE, "w") as f:
        json.dump(retry_list, f, ensure_ascii=False)


def _should_retry(bug_id):
    """Check if a bug should be retried. Returns (should_retry, attempts_so_far)."""
    retry_list = _load_retry_list()
    entry = retry_list.get(str(bug_id))
    if entry is None:
        return False, 0
    attempts = entry.get("attempts", 0)
    if attempts >= MAX_RETRY_ATTEMPTS:
        return False, attempts
    return True, attempts


def _record_retry_failure(bug_id):
    """Record that a bug analysis failed and should be retried next run."""
    retry_list = _load_retry_list()
    bug_key = str(bug_id)
    if bug_key not in retry_list:
        retry_list[bug_key] = {"attempts": 0, "first_failure": datetime.now().isoformat()}
    retry_list[bug_key]["attempts"] += 1
    retry_list[bug_key]["last_failure"] = datetime.now().isoformat()
    _save_retry_list(retry_list)


def _remove_from_retry(bug_id):
    """Remove a bug from the retry list (analysis succeeded)."""
    retry_list = _load_retry_list()
    bug_key = str(bug_id)
    if bug_key in retry_list:
        del retry_list[bug_key]
        _save_retry_list(retry_list)


def check_attachment_count(bug_id, project_key):
    """Check how many attachments a bug has. Returns (count, has_large_zip)."""
    result, err = mcp_call(
        "list_workitem_comments",
        {"work_item_id": bug_id, "project_key": project_key, "page_num": 1, "page_size": 50},
        timeout=COMMENT_TIMEOUT,
    )
    if err or not result:
        return 0, False
    try:
        comments = result.get("comments", [])
        total_attachments = 0
        has_zip = False
        for c in comments:
            attachments = c.get("attachments", [])
            total_attachments += len(attachments)
            for a in attachments:
                filename = a.get("file_name", "").lower()
                if filename.endswith(".zip"):
                    has_zip = True
        return total_attachments, has_zip
    except (json.JSONDecodeError, KeyError):
        return 0, False


def get_new_bug_ids(open_bugs):
    """Compare with cache to find new OPEN bug IDs."""
    open_ids = set(str(bug.get("work_item_id", "")) for bug in open_bugs)

    existing_ids = set()
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH) as f:
                cache = json.load(f)
            existing_ids = set(cache.get("index", {}).keys())
        except (json.JSONDecodeError, KeyError):
            pass

    new_ids = sorted(open_ids - existing_ids)
    return new_ids


def update_status_cache(bugs):
    """Update cache with current status of ALL fetched bugs so we can detect status changes."""
    if not os.path.exists(CACHE_PATH):
        return

    try:
        with open(CACHE_PATH) as f:
            cache = json.load(f)
    except (json.JSONDecodeError, KeyError):
        return

    cache_index = cache.get("index", {})
    updated = 0
    added = 0

    for bug in bugs:
        wid = str(bug.get("work_item_id", ""))
        if not wid:
            continue

        new_status = bug.get("work_item_status", "")
        if isinstance(new_status, dict):
            new_status = new_status.get("state_key", "")

        if wid in cache_index:
            old_status = cache_index[wid].get("status", "")
            if old_status != new_status:
                cache_index[wid]["status"] = new_status
                updated += 1
        else:
            # Add new bug to cache with ALL required fields (bug_analyzer.py needs these)
            cache_index[wid] = {
                "name": bug.get("name", f"Bug {wid}"),
                "status": new_status,
                "desc_lower": "",
                "description": "",
                "search_text": "",
                "comments": [],
                "attachments": {},
            }
            added += 1

    if updated > 0 or added > 0:
        cache["index"] = cache_index
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, ensure_ascii=False)
        if updated > 0:
            print(f"    缓存状态更新: {updated} 个缺陷状态变化")
        if added > 0:
            print(f"    缓存新增: {added} 个缺陷")


def get_bug_info(bugs, bug_id):
    """Get bug name/title from fetched data"""
    for bug in bugs:
        if str(bug.get("work_item_id", "")) == str(bug_id):
            return bug.get("name", f"Bug {bug_id}")
    return f"Bug {bug_id}"


def check_ai_comment(bug_id, project_key):
    """Check if bug already has an [AI分析] comment"""
    result, err = mcp_call(
        "list_workitem_comments",
        {"work_item_id": bug_id, "project_key": project_key, "page_num": 1},
        timeout=COMMENT_TIMEOUT,
    )

    if err or not result:
        print(f"  [WARN] Failed to check comments for {bug_id}")
        return False  # proceed anyway

    try:
        comments = result.get("comments", [])
        for c in comments:
            content = c.get("content", "")
            if "[AI分析]" in content:
                return True
    except (json.JSONDecodeError, KeyError):
        pass

    return False


def fetch_new_bug_details(bug_id, project_key):
    """Fetch a single bug's details via MQL and add to cache so bug_analyzer.py can find it."""
    pk_ref = f"`{project_key}`" if project_key != "sw_team" else project_key
    mql = f"SELECT work_item_id, name, work_item_status, description FROM {pk_ref}.issue WHERE work_item_id = {bug_id} LIMIT 1"

    result, err = mcp_call(
        "search_by_mql",
        {"project_key": project_key, "mql": mql},
        timeout=FETCH_TIMEOUT,
    )
    if err or not result:
        print(f"  [WARN] Failed to fetch details for bug {bug_id}: {err or 'empty response'}")
        return False

    try:
        bug_data = result.get("data", {}).get("1", [])
        if not bug_data:
            print(f"  [WARN] No data returned for bug {bug_id}")
            return False

        fields = _parse_moql_field_list(bug_data[0].get("moql_field_list", []))

        # Add to cache
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH) as f:
                cache = json.load(f)
        else:
            cache = {"index": {}}

        cache_index = cache.get("index", {})
        cache_index[bug_id] = {
            "name": fields.get("name", f"Bug {bug_id}"),
            "status": fields.get("work_item_status", "OPEN"),
            "desc_lower": fields.get("description", "").lower() if fields.get("description") else "",
            "description": fields.get("description", ""),
            "search_text": "",
            "comments": [],
            "attachments": {},
        }
        cache["index"] = cache_index
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, ensure_ascii=False)

        print(f"  [CACHE] Bug {bug_id} details cached: {fields.get('name', '')[:60]}")
        return True
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  [WARN] Failed to parse bug details for {bug_id}: {e}")
        return False


def run_analysis(bug_id):
    """Run bug_analyzer.py --llm for a single bug with watchdog monitoring.
    Uses Popen + process group isolation to reliably kill stuck processes.
    Monitors for zombie/hung processes and kills them before full timeout.
    Sets BUG_ANALYZER_MAX_ROUNDS=2 to limit LLM rounds (prevents 3×300s timeout)."""
    python_exe = sys.executable  # current Python interpreter (no hardcoded paths)
    cmd = f"BUG_ANALYZER_MAX_ROUNDS=2 {python_exe} bug_analyzer.py feishu {bug_id} --llm"
    print(f"  [ANALYZING] {cmd}")

    # Start process in isolated process group
    import signal
    proc = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        cwd=BUG_ANALYZER_DIR, start_new_session=True
    )
    pgid = os.getpgid(proc.pid)

    # Watchdog: poll for output file creation and process health
    pattern = os.path.join(ANALYSIS_OUTPUT_DIR, f"bug_{bug_id}_analysis_*.json")
    start_time = time.time()
    last_file_mtime = None
    no_progress_checks = 0
    stuck_since = None

    while True:
        elapsed = time.time() - start_time

        # Check if process is still running
        if proc.poll() is not None:
            break

        # Hard timeout: kill if exceeded
        if elapsed > ANALYSIS_TIMEOUT:
            print(f"  [TIMEOUT] Analysis exceeded {ANALYSIS_TIMEOUT}s, killing...")
            _kill_process_group(pgid, proc)
            return None, False

        # Check if output file exists and is being updated
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
        if files:
            current_mtime = os.path.getmtime(files[0])
            if current_mtime != last_file_mtime:
                last_file_mtime = current_mtime
                no_progress_checks = 0
                stuck_since = None
            else:
                no_progress_checks += 1
                # Track how long since last progress
                if stuck_since is None:
                    stuck_since = elapsed
                idle_time = elapsed - stuck_since
                # If no file update for 600s after initial 120s, likely stuck in LLM call
                # (Increased from 500s to 600s: LLM streaming can be slow under rate limiting,
                # and some bugs legitimately need extra time for evidence gathering)
                if idle_time > 600 and elapsed > 120:
                    print(f"  [WATCHDOG] No progress for {idle_time:.0f}s (total {elapsed:.0f}s). Killing stuck process...")
                    _kill_process_group(pgid, proc)
                    return None, False

        time.sleep(10)  # Check every 10 seconds

    # Process completed
    stdout, stderr = proc.communicate()
    code = proc.returncode

    # Parse and return the analysis result
    return _parse_analysis_result(bug_id)


def _kill_process_group(pgid, proc):
    """Kill a process group reliably."""
    import signal
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _parse_analysis_result(bug_id):
    """Parse the analysis result JSON file for a bug."""
    pattern = os.path.join(ANALYSIS_OUTPUT_DIR, f"bug_{bug_id}_analysis_*.json")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)

    if files:
        try:
            with open(files[0]) as f:
                analysis = json.load(f)
            # Check if LLM actually succeeded (not a 401 or auth error)
            llm_result = analysis.get("llm_analysis", "")
            if llm_result and ("HTTP 401" in llm_result or "Authentication Error" in llm_result or "403" in llm_result or "429" in llm_result or "throttling" in llm_result or len(llm_result.strip()) < 200 or "LLM 返回空内容" in llm_result or "LLM returned empty" in llm_result):
                print(f"  [LLM_ERROR] LLM analysis failed: {llm_result[:150]}")
                return analysis, False
            if llm_result and len(llm_result.strip()) >= 200:
                return analysis, True
            print(f"  [LLM_SKIP] No LLM analysis in output (non-LLM fallback)")
            return analysis, False
        except (json.JSONDecodeError, IOError):
            pass

    return None, False


def build_comment(analysis, bug_name):
    """Build a rich comment from the full LLM analysis output."""
    llm = analysis.get("llm_analysis", "")

    # Check if LLM actually produced analysis (not an error message)
    if not llm or len(llm.strip()) < 200 or "LLM 调用失败" in llm or "LLM call failed" in llm.lower() or "HTTP 401" in llm or "Authentication Error" in llm or "403" in llm or "429" in llm or "throttling" in llm or "LLM 返回空内容" in llm or "LLM returned empty" in llm:
        # Fallback: use simple summary
        root_cause = analysis.get("root_cause", "未知")
        confidence = analysis.get("confidence", {})
        score = confidence.get("score", 0) if isinstance(confidence, dict) else 0
        if score >= 0.8:
            level = "高"
        elif score >= 0.5:
            level = "中"
        else:
            level = "低"

        return (
            f"[AI分析] 自动缺陷分析报告\n\n"
            f"**缺陷**: {bug_name}\n"
            f"**根因**: {root_cause}\n"
            f"**置信度**: {score:.0%} ({level})\n\n"
            f"⚠️ LLM 深度分析不可用，以上为规则引擎分析结果。\n"
        )

    # Rich comment: use full LLM analysis
    confidence = analysis.get("confidence", {})
    score = confidence.get("score", 0) if isinstance(confidence, dict) else 0
    if score >= 0.8:
        level = "高"
    elif score >= 0.5:
        level = "中"
    else:
        level = "低"

    comment = (
        f"[AI分析] 自动缺陷分析报告\n\n"
        f"**缺陷**: {bug_name}\n"
        f"**置信度**: {score:.0%} ({level})\n\n"
        f"{llm}\n\n"
        f"---\n⚠️ 此为AI自动分析结果，仅供参考。"
    )

    return comment


def add_comment(bug_id, content, project_key):
    """Add a comment to the bug via MCP JSON-RPC (replaces mcporter CLI)."""
    return mcp_add_comment(project_key, bug_id, content, timeout=COMMENT_TIMEOUT)


def append_run_log(summary_text):
    """Append a timestamped summary to the cron run history log."""
    try:
        with open(CRON_RUN_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*60}\n")
            f.write(summary_text + "\n")
    except Exception as e:
        print(f"  [WARN] Failed to append run log: {e}")


def main():
    print("=" * 60)
    print("飞书缺陷自动分析定时任务")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"项目: {', '.join(PROJECT_KEYS)}")
    print("=" * 60)

    # Collect bugs from all projects
    all_bugs = []
    all_open_bugs = []
    all_new_ids = []  # list of (bug_id, project_key)
    all_closed_ids = []
    all_closed_results = []

    for project_key in PROJECT_KEYS:
        print(f"\n[项目: {project_key}]")

        # Fetch bugs
        print(f"  获取缺陷列表...")
        bugs = fetch_bugs(project_key)
        print(f"    获取到 {len(bugs)} 个缺陷")
        all_bugs.extend(bugs)

        # Filter OPEN
        open_bugs = filter_open_bugs(bugs)
        print(f"    OPEN 状态: {len(open_bugs)} 个")
        all_open_bugs.extend(open_bugs)

        # Find new bugs for this project
        new_ids = get_new_bug_ids(open_bugs)
        print(f"    新增缺陷: {len(new_ids)} 个 - {new_ids}")
        for bid in new_ids:
            all_new_ids.append((bid, project_key))

        # Detect closed bugs
        print(f"  检查新关闭的缺陷...")
        closed_ids, uploaded_set = get_closed_bug_ids(open_bugs)
        if closed_ids:
            closed_results = process_closed_bugs(closed_ids)
            all_closed_ids.extend(closed_ids)
            all_closed_results.extend(closed_results)
        else:
            all_closed_results.extend([])

        # Update cache with current status
        update_status_cache(bugs)

    # Process each new bug (from all projects)
    results = []
    for i, (bug_id, project_key) in enumerate(all_new_ids):
        bug_name = get_bug_info(all_open_bugs, bug_id)
        print(f"\n[{i+1}/{len(all_new_ids)}] 处理 Bug {bug_id} [{project_key}]: {bug_name[:50]}...")

        # Pre-filter: check attachment count before spending time on analysis
        # Bugs with 3+ zip files are permanently skipped (will always timeout)
        att_count, has_large_zip = check_attachment_count(bug_id, project_key)
        zip_count = 0
        if has_large_zip:
            # Re-check for accurate zip count
            result, err = mcp_call(
                "list_workitem_comments",
                {"work_item_id": bug_id, "project_key": project_key, "page_num": 1, "page_size": 50},
                timeout=COMMENT_TIMEOUT,
            )
            if result:
                for c in result.get("comments", []):
                    for a in c.get("attachments", []):
                        if a.get("file_name", "").lower().endswith(".zip"):
                            zip_count += 1

        if zip_count >= SKIP_ZIP_THRESHOLD:
            print(f"  [SKIP] 附件过多 ({zip_count} 个ZIP, {att_count} 总附件)，永久跳过分析（必超时）")
            results.append({"bug_id": bug_id, "project_key": project_key, "status": "skipped", "reason": f"附件过多: {zip_count}ZIP/{att_count}附件"})
            continue

        if att_count > MAX_ATTACHMENTS_THRESHOLD:
            print(f"  [SKIP] 附件较多 ({att_count} 个)，跳过分析")
            results.append({"bug_id": bug_id, "project_key": project_key, "status": "skipped", "reason": f"附件过多: {att_count}个"})
            continue

        # Check if this is a retry of a previously failed bug
        is_retry, retry_attempts = _should_retry(bug_id)
        if is_retry:
            print(f"  [RETRY #{retry_attempts+1}] 重试之前失败的 bug...")
            # Increase timeout for retry attempts
            old_timeout = ANALYSIS_TIMEOUT
            globals()['ANALYSIS_TIMEOUT'] = min(ANALYSIS_TIMEOUT * (retry_attempts + 1), 1800)
            print(f"  [RETRY] 超时调整为 {globals()['ANALYSIS_TIMEOUT']}s")

        # Check for existing AI comment
        if check_ai_comment(bug_id, project_key):
            print(f"  [SKIP] 已有 [AI分析] 评论")
            _remove_from_retry(bug_id)  # already commented, remove from retry
            results.append({"bug_id": bug_id, "project_key": project_key, "status": "skipped", "reason": "已有AI评论"})
            continue

        # Fetch bug details and add to cache (bug_analyzer.py needs it in cache)
        if not fetch_new_bug_details(bug_id, project_key):
            print(f"  [FAIL] 无法获取缺陷详情")
            results.append({"bug_id": bug_id, "project_key": project_key, "status": "failed", "reason": "无法获取缺陷详情"})
            continue

        # Run LLM analysis
        analysis, success = run_analysis(bug_id)

        # Restore original timeout after retry
        if is_retry:
            globals()['ANALYSIS_TIMEOUT'] = old_timeout

        if not success:
            print(f"  [FAIL] 分析失败")
            _record_retry_failure(bug_id)
            results.append({"bug_id": bug_id, "project_key": project_key, "status": "failed", "reason": "分析超时或失败"})
            continue

        # Analysis succeeded — remove from retry list
        _remove_from_retry(bug_id)

        # Build comment with full LLM analysis
        comment = build_comment(analysis, bug_name)

        # Print brief summary for console output
        root_cause = analysis.get("root_cause", "未知")
        confidence = analysis.get("confidence", {})
        score = confidence.get("score", 0) if isinstance(confidence, dict) else 0
        llm = analysis.get("llm_analysis", "")
        has_llm = llm and "LLM 调用失败" not in llm and "HTTP 401" not in llm and "Authentication Error" not in llm and "429" not in llm and "throttling" not in llm
        print(f"  根因: {root_cause[:60]}")
        print(f"  置信度: {score:.0%} | LLM分析: {'是' if has_llm else '否(回退到规则引擎)'}")
        print(f"  评论长度: {len(comment)} 字符")

        # Add comment
        print(f"  正在添加评论...")
        comment_ok, comment_result = add_comment(bug_id, comment, project_key)

        if comment_ok:
            print(f"  [OK] 评论成功 (id: {comment_result})")
            results.append({
                "bug_id": bug_id,
                "project_key": project_key,
                "status": "commented",
                "comment_id": comment_result,
                "root_cause": root_cause[:60],
                "confidence": confidence
            })
        else:
            print(f"  [FAIL] 评论失败: {comment_result}")
            results.append({
                "bug_id": bug_id,
                "project_key": project_key,
                "status": "comment_failed",
                "reason": comment_result,
                "root_cause": root_cause[:60],
                "confidence": confidence
            })

        # Rate limit between bugs: increased to 10s to prevent LLM API rate limiting
        # Previous 2s was too short — 11 bugs in 2 hours caused LLM quota exhaustion,
        # resulting in watchdog kills after 500s idle time
        if i < len(all_new_ids) - 1:
            time.sleep(10)

    # Step 6: Summary
    print("\n" + "=" * 60)
    print("执行汇总")
    print("=" * 60)

    commented = [r for r in results if r["status"] == "commented"]
    skipped = [r for r in results if r["status"] == "skipped"]
    failed = [r for r in results if r["status"] in ("failed", "comment_failed")]

    for project_key in PROJECT_KEYS:
        pk_new = len([b for b in all_new_ids if b[1] == project_key])
        print(f"\n[{project_key}] 新增: {pk_new} 个")

    print(f"\n总计新增缺陷: {len(all_new_ids)} 个")
    print(f"已评论: {len(commented)} 个")
    for r in commented:
        print(f"  ✅ {r['bug_id']} [{r.get('project_key','?')}] | {r['root_cause']} | 置信度: {r['confidence']}")

    if skipped:
        print(f"已跳过: {len(skipped)} 个")
        for r in skipped:
            print(f"  ⏭ {r['bug_id']} [{r.get('project_key','?')}]")

    if failed:
        print(f"失败: {len(failed)} 个 (已加入重试列表，下次运行自动重试)")
        for r in failed:
            reason = r.get("reason", "未知")
            print(f"  ❌ {r['bug_id']} [{r.get('project_key','?')}]: {reason[:100]}")

    # Show retry queue
    retry_list = _load_retry_list()
    if retry_list:
        pending_retry = {bid: info for bid, info in retry_list.items() if info.get("attempts", 0) < MAX_RETRY_ATTEMPTS}
        exhausted = {bid: info for bid, info in retry_list.items() if info.get("attempts", 0) >= MAX_RETRY_ATTEMPTS}
        print(f"\n重试队列:")
        print(f"  待重试: {len(pending_retry)} 个")
        for bid, info in pending_retry.items():
            print(f"    🔁 {bid} (失败 {info['attempts']}/{MAX_RETRY_ATTEMPTS} 次, 首次: {info.get('first_failure','?')})")
        if exhausted:
            print(f"  已达上限: {len(exhausted)} 个 (不再重试)")
            for bid, info in exhausted.items():
                print(f"    ⛔ {bid} (失败 {info['attempts']} 次, 首次: {info.get('first_failure','?')})")

    if all_closed_results:
        print(f"\n关闭缺陷导入 OpenViking: {len(all_closed_results)} 个")
        for r in all_closed_results:
            if r["status"] == "uploaded":
                print(f"  ✅ {r['bug_id']} | {r['name'][:50]}")
            else:
                print(f"  ❌ {r['bug_id']}: {r.get('reason', 'Unknown')[:100]}")
    elif all_closed_ids:
        print(f"\n关闭缺陷: 找到 {len(all_closed_ids)} 个但 OpenViking 服务不可用")
    else:
        print(f"\n关闭缺陷: 无新关闭缺陷需要导入")

    print(f"\n结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Collect summary for log file
    summary_lines = []
    for project_key in PROJECT_KEYS:
        pk_new = len([b for b in all_new_ids if b[1] == project_key])
        summary_lines.append(f"[{project_key}] 新增: {pk_new} 个")
    summary_lines.append(f"总计新增: {len(all_new_ids)} 个")
    for r in commented:
        summary_lines.append(f"  ✅ {r['bug_id']} [{r.get('project_key','?')}] | {r['root_cause']} | 置信度: {r['confidence']}")
    for r in skipped:
        summary_lines.append(f"  ⏭ {r['bug_id']} [{r.get('project_key','?')}] - 已有AI评论")
    for r in failed:
        summary_lines.append(f"  ❌ {r['bug_id']} [{r.get('project_key','?')}]: {r.get('reason', '未知')[:100]}")
    # Retry queue summary
    retry_list = _load_retry_list()
    if retry_list:
        pending = sum(1 for info in retry_list.values() if info.get("attempts", 0) < MAX_RETRY_ATTEMPTS)
        exhausted = sum(1 for info in retry_list.values() if info.get("attempts", 0) >= MAX_RETRY_ATTEMPTS)
        summary_lines.append(f"  重试队列: {pending} 待重试, {exhausted} 已达上限")
    if all_closed_results:
        for r in all_closed_results:
            if r["status"] == "uploaded":
                summary_lines.append(f"  📦 [关闭] {r['bug_id']} | {r['name'][:50]}")
            else:
                summary_lines.append(f"  ❌ [关闭] {r['bug_id']}: {r.get('reason', 'Unknown')[:100]}")
    summary_text = "\n".join(summary_lines)
    append_run_log(summary_text)

    # Ensure output file is closed (stdout tee handles this)
    _tee_file.close()


if __name__ == "__main__":
    main()
