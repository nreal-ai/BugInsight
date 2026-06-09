#!/usr/bin/env python3
"""增量刷新缺陷详情"""
import subprocess
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# mcporter 配置: ~/.mcporter/mcporter.json

# 添加配置模块路径（优先使用本地配置，备用 bug-analyzer）
_local_config_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_local_config_dir))
try:
    from config import get_openviking_config, get_feishu_config, get_output_config, print_config_check
except ImportError:
    get_openviking_config = None
    get_feishu_config = None
    get_output_config = None
    print_config_check = None
# 加载配置
_fs_cfg = get_feishu_config() if get_feishu_config else {}
PROJECT_KEY = _fs_cfg.get("project_key", "<BUG_INSIGHT_FEISHU_PROJECT_KEY>")

_ov_cfg = get_output_config() if get_output_config else {}
OUTPUT_DIR = _ov_cfg.get("base_dir", os.path.expanduser("~/.openviking/workspace/feishu-bugs"))

def mcporter_call(tool, args):
    """调用mcporter, work_item_id 必须是字符串。"""
    if 'work_item_id' in args:
        args['work_item_id'] = str(args['work_item_id'])
    cmd = ['mcporter', 'call', 'meego', tool, '--args', json.dumps(args)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env={**os.environ, 'HOME': os.environ['HOME']})
        if result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        print(f"  MCP错误: {e}")
    return None

def get_detail(bug_id):
    """获取单个缺陷详情"""
    detail = mcporter_call("get_workitem_brief", {
        "project_key": PROJECT_KEY,
        "work_item_id": bug_id
    })
    return {'id': bug_id, 'detail': detail}

# 读取现有数据 (优先 batch 子目录)
BATCH_DIR = os.path.join(OUTPUT_DIR, 'batch')
bugs_file = os.path.join(BATCH_DIR, 'bugs_full_all.json')
if not os.path.exists(bugs_file):
    bugs_file = os.path.join(BATCH_DIR, 'bugs_all_with_details.json')
if not os.path.exists(bugs_file):
    bugs_file = os.path.join(OUTPUT_DIR, 'bugs_all_with_details.json')
if not os.path.exists(bugs_file):
    bugs_file = os.path.join(OUTPUT_DIR, 'bugs_full_all.json')
with open(bugs_file) as f:
    bugs = json.load(f)

print(f'共有 {len(bugs)} 个缺陷，开始刷新详情...')

# 增量刷新前100个测试
test_bugs = bugs[:100]
results = []
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = {executor.submit(get_detail, b['id']): b for b in test_bugs}
    for i, future in enumerate(as_completed(futures)):
        r = future.result()
        results.append(r)
        print(f'  [{i+1}/{len(test_bugs)}] ID {r["id"]}: {"OK" if r["detail"] else "FAIL"}')

print(f'完成，获取了 {sum(1 for r in results if r["detail"])} 条详情')