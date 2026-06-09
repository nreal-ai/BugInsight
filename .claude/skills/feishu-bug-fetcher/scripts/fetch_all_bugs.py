#!/usr/bin/env python3
"""
飞书缺陷全量数据获取脚本
使用 mcporter MQL OFFSET 分页获取所有 ID，然后通过 Direct API 批量获取详情。
"""

import json
import os
import sys
import time
import requests
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Config
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib
import config as feishu_config
importlib.reload(feishu_config)

fs_cfg = feishu_config.get_feishu_config()
ov_cfg = feishu_config.get_openviking_config()
output_cfg = feishu_config.get_output_config()

PROJECT_KEY = fs_cfg['project_key']
PLUGIN_ID = fs_cfg['plugin_id']
PLUGIN_SECRET = fs_cfg['plugin_secret']
USER_KEY = fs_cfg['user_key']

OUTPUT_DIR = os.path.expanduser(output_cfg.get("base_dir", "~/.openviking/workspace/feishu-bugs"))
BATCH_DIR = os.path.join(OUTPUT_DIR, 'batch')
os.makedirs(BATCH_DIR, exist_ok=True)

OV_BASE = ov_cfg.get("api_base", "http://127.0.0.1:1933")
OV_HEADERS = {
    "X-API-Key": ov_cfg.get("api_key", ""),
    "X-OpenViking-Account": ov_cfg.get("account", "default"),
    "X-OpenViking-User": ov_cfg.get("user", "xreal"),
    "Content-Type": "application/json"
}


def get_plugin_token():
    """获取 Plugin Token"""
    url = "https://project.feishu.cn/open_api/authen/plugin_token"
    data = {"plugin_id": PLUGIN_ID, "plugin_secret": PLUGIN_SECRET, "type": 0}
    resp = requests.post(url, json=data, timeout=10)
    return resp.json().get('data', {}).get('token')


def fetch_all_bug_ids():
    """通过 mcporter MQL OFFSET 分页获取所有缺陷 ID"""
    print("=== 步骤1: 获取所有缺陷 ID ===")
    
    # 先用 LIMIT 1 获取总数
    result = subprocess.run(
        ["mcporter", "call", "meego", "search_by_mql", "--args",
         json.dumps({"project_key": PROJECT_KEY, "mql": "SELECT work_item_id, name FROM sw_team.issue LIMIT 1"})],
        capture_output=True, text=True, timeout=30
    )
    data = json.loads(result.stdout)
    total = data.get('list', [{}])[0].get('count', 0)
    print(f"  缺陷总数: {total}")
    
    all_bugs = []
    batch_size = 50
    
    for offset in range(0, total, batch_size):
        result = subprocess.run(
            ["mcporter", "call", "meego", "search_by_mql", "--args",
             json.dumps({"project_key": PROJECT_KEY, 
                        "mql": f"SELECT work_item_id, name FROM sw_team.issue LIMIT {batch_size} OFFSET {offset}"})],
            capture_output=True, text=True, timeout=30
        )
        try:
            data = json.loads(result.stdout)
            items = data.get('data', {}).get('1', [])
            for item in items:
                bug_info = {'id': '', 'name': '', 'status': ''}
                for field in item.get('moql_field_list', []):
                    key = field.get('key')
                    val = field.get('value', {})
                    if key == 'work_item_id':
                        bug_info['id'] = str(val.get('long_value') or val.get('text_value') or val)
                    elif key == 'name':
                        bug_info['name'] = val.get('text_value', '')
                if bug_info['id']:
                    all_bugs.append(bug_info)
            
            print(f"  进度: {offset + len(items)}/{total}")
        except Exception as e:
            print(f"  批次 offset={offset} 失败: {e}")
        
        time.sleep(0.3)  # 避免 rate limit
    
    print(f"  共获取 {len(all_bugs)} 个缺陷 ID")
    return all_bugs


def fetch_details_direct_api(bug_ids, token):
    """通过 Direct API 批量获取缺陷详情"""
    url = f"https://project.feishu.cn/open_api/{PROJECT_KEY}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    all_details = []
    batch_size = 50
    
    for i in range(0, len(bug_ids), batch_size):
        batch_ids = bug_ids[i:i+batch_size]
        # work_item_ids 必须是整数数组
        data = {
            "work_item_ids": [int(bid) for bid in batch_ids],
            "get_all_properties": True
        }
        
        try:
            resp = requests.post(url, json=data, headers=headers, timeout=30)
            items = resp.json().get('data', [])
            for item in items:
                all_details.append({
                    'id': str(item.get('id')),
                    'detail': {
                        'work_item_attribute': {
                            'work_item_id': item.get('id'),
                            'work_item_name': item.get('name', ''),
                            'work_item_status': {
                                'key': item.get('work_item_status', {}).get('state_key', ''),
                                'name': item.get('work_item_status', {}).get('state_key', ''),
                                'sub_stage': item.get('sub_stage', ''),
                                **item.get('work_item_status', {})
                            },
                            'description': item.get('description', ''),
                            'owned_project': item.get('project_key', PROJECT_KEY),
                            'fields': item.get('fields', [])
                        }
                    }
                })
            print(f"  详情进度: {min(i+batch_size, len(bug_ids))}/{len(bug_ids)}")
        except Exception as e:
            print(f"  批次 {i//batch_size + 1} 失败: {e}")
        
        time.sleep(0.2)
    
    return all_details


def fetch_comments_via_mcporter(bug_ids, workers=5):
    """批量获取评论 (MCP)"""
    print(f"获取缺陷评论 (共{len(bug_ids)}个)...")
    
    bugs_with_comments = []
    
    def check_comments(bug_id):
        cmd = ["mcporter", "call", "meego", "list_workitem_comments", "--args",
               json.dumps({"project_key": PROJECT_KEY, "work_item_id": str(bug_id), "page_num": 1})]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.stdout.strip():
                data = json.loads(result.stdout)
                if data.get('comments'):
                    return {'work_item_id': bug_id, 'comments': data['comments']}
        except:
            pass
        return None
    
    start_time = time.time()
    processed = 0
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(check_comments, bid): bid for bid in bug_ids}
        for future in as_completed(futures):
            result = future.result()
            if result:
                bugs_with_comments.append(result)
            processed += 1
            if processed % 200 == 0:
                elapsed = (time.time() - start_time) / 60
                print(f"  进度: {processed}/{len(bug_ids)}, 有评论: {len(bugs_with_comments)}, 耗时: {elapsed:.1f}分钟")
    
    elapsed = (time.time() - start_time) / 60
    print(f"  获取到 {len(bugs_with_comments)} 个有评论的缺陷, 耗时: {elapsed:.1f}分钟")
    return bugs_with_comments


def fetch_attachments_via_api(bug_ids, token):
    """获取附件信息 (Direct API)"""
    print(f"获取附件信息 (共{len(bug_ids)}个)...")
    
    url = f"https://project.feishu.cn/open_api/{PROJECT_KEY}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    results = []
    batch_size = 50
    
    for i in range(0, len(bug_ids), batch_size):
        batch = bug_ids[i:i+batch_size]
        data = {"work_item_ids": [int(bid) for bid in batch], "get_all_properties": True}
        try:
            resp = requests.post(url, json=data, headers=headers, timeout=30)
            items = resp.json().get('data', [])
            for item in items:
                attachments = []
                for field in item.get('fields', []):
                    if field.get('field_key') == 'multi_attachment':
                        attachments = field.get('field_value', [])
                        break
                
                if attachments:
                    results.append({
                        'id': str(item.get('id')),
                        'attachments': attachments
                    })
        except Exception as e:
            print(f"  批次 {i//batch_size + 1} 失败: {e}")
        time.sleep(0.2)
    
    print(f"  获取到 {len(results)} 个有附件的缺陷")
    return results


def import_to_openviking(details):
    """导入缺陷数据到 OpenViking"""
    print("\n=== 步骤4: 导入到 OpenViking ===")
    
    # Create session
    resp = requests.post(f"{OV_BASE}/api/v1/sessions", headers=OV_HEADERS, timeout=30)
    session_id = resp.json().get('result', {}).get('session_id')
    if not session_id:
        print("  错误: 无法创建 session")
        return
    
    print(f"  Session: {session_id}")
    
    # Build text for each bug
    messages = []
    for bug in details:
        attrs = bug.get('detail', {}).get('work_item_attribute', {})
        bug_id = str(bug.get('id', ''))
        name = attrs.get('work_item_name', '')
        status = attrs.get('work_item_status', '')
        if isinstance(status, dict):
            status = status.get('name', '')
        desc = attrs.get('description', '')
        
        # Categorize
        category = "未分类"
        if any(kw in name for kw in ["画面", "显示", "分辨率", "亮度", "UI", "屏幕", "白条", "黑屏", "花屏", "闪屏"]):
            category = "画面/显示"
        elif any(kw in name for kw in ["手势", "触控", "摇杆", "射线", "引导", "交互", "点击", "滑动"]):
            category = "手势/交互"
        elif any(kw in name for kw in ["连接", "USB", "拔插", "枚举", "配对", "识别", "断开"]):
            category = "连接问题"
        elif any(kw in name for kw in ["电源", "充电", "续航", "电池", "功耗", "省电"]):
            category = "电源管理"
        elif any(kw in name for kw in ["音频", "声音", "UAC", "音量", "扬声器", "耳机"]):
            category = "音频问题"
        elif any(kw in name for kw in ["视频", "播放", "锁屏", "解码"]):
            category = "视频播放"
        elif any(kw in name for kw in ["闪退", "崩溃", "死机", "重启", "异常"]):
            category = "稳定性"
        
        reproduce = "未标注"
        if "必现" in name: reproduce = "必现"
        elif "偶现" in name: reproduce = "偶现"
        elif "高概率" in name: reproduce = "高概率"
        elif "低概率" in name: reproduce = "低概率"
        
        text = f"缺陷 ID: {bug_id}\n标题: {name}\n状态: {status}\n功能分类: {category}\n复现概率: {reproduce}\n描述: {desc[:200] if desc else '无'}"
        messages.append(text)
    
    # Batch send
    success = 0
    failed = 0
    batch_size = 100
    
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i+batch_size]
        url = f"{OV_BASE}/api/v1/sessions/{session_id}/messages/batch"
        payload = [{"role": "user", "content": msg} for msg in batch]
        try:
            resp = requests.post(url, headers=OV_HEADERS, json=payload, timeout=60)
            if resp.status_code == 200:
                success += len(batch)
            else:
                failed += len(batch)
                print(f"  批次 {i//batch_size + 1} 失败: {resp.status_code}")
        except Exception as e:
            failed += len(batch)
            print(f"  批次 {i//batch_size + 1} 异常: {e}")
        
        if (i + len(batch)) % 500 == 0:
            print(f"  导入进度: {min(i+len(batch), len(messages))}/{len(messages)}")
        
        time.sleep(0.3)
    
    print(f"  等待向量化...")
    wait_url = f"{OV_BASE}/api/v1/system/wait"
    requests.post(wait_url, headers=OV_HEADERS, json={"timeout": 300}, timeout=310)
    
    print(f"  导入完成！成功: {success}, 失败: {failed}")
    return session_id


def main():
    print("=" * 60)
    print("飞书缺陷全量数据获取")
    print("=" * 60)
    
    # Step 1: Get all bug IDs
    all_bugs = fetch_all_bug_ids()
    bug_ids = [b['id'] for b in all_bugs]
    
    # Save index
    index_file = os.path.join(BATCH_DIR, 'bugs_index.json')
    with open(index_file, 'w', encoding='utf-8') as f:
        json.dump(all_bugs, f, ensure_ascii=False, indent=2)
    print(f"  已保存索引: {index_file} ({len(all_bugs)} 条)")
    
    # Step 2: Get full details via Direct API
    print("\n=== 步骤2: 获取缺陷详情 ===")
    token = get_plugin_token()
    if not token:
        print("  错误: 无法获取 token")
        return
    
    details = fetch_details_direct_api(bug_ids, token)
    details_file = os.path.join(BATCH_DIR, 'bugs_full_all.json')
    with open(details_file, 'w', encoding='utf-8') as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    print(f"  已保存详情: {details_file} ({len(details)} 条)")
    
    # Step 3: Get comments and attachments
    print("\n=== 步骤3: 获取评论和附件 ===")
    comments = fetch_comments_via_mcporter(bug_ids, workers=10)
    comments_file = os.path.join(BATCH_DIR, 'bugs_with_comments.json')
    with open(comments_file, 'w', encoding='utf-8') as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)
    print(f"  已保存评论: {comments_file} ({len(comments)} 条)")
    
    attachments = fetch_attachments_via_api(bug_ids, token)
    attachments_file = os.path.join(BATCH_DIR, 'bugs_attachments.json')
    with open(attachments_file, 'w', encoding='utf-8') as f:
        json.dump(attachments, f, ensure_ascii=False, indent=2)
    print(f"  已保存附件: {attachments_file} ({len(attachments)} 条)")
    
    # Step 4: Import to OpenViking
    session_id = import_to_openviking(details)
    
    print("\n" + "=" * 60)
    print(f"完成！共处理 {len(all_bugs)} 个缺陷")
    print(f"数据目录: {BATCH_DIR}")
    if session_id:
        print(f"OpenViking Session: {session_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
