#!/usr/bin/env python3
"""
飞书缺陷数据获取完整脚本
功能：
1. 获取缺陷列表 (MCP)
2. 获取缺陷详情 (MCP)
3. 获取评论 (MCP)
4. 获取附件信息 (Direct API)
5. 导入到 OpenViking 知识库

用法:
    python3 fetch_bugs_full.py [options]
    
Options:
    --list-only      只获取缺陷列表
    --details-only   只获取详情
    --comments-only   只获取评论
    --attachments-only 只获取附件信息
    --all           获取全部数据 (默认)
    --import        获取后导入到 OpenViking
    --session NAME  指定 OpenViking session 名称 (默认: feishu-bugs)
"""

import subprocess
import json
import os
import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# mcporter 配置路径: ~/.mcporter/mcporter.json (MCPORTER_CONFIG_PATH 环境变量被 mcporter 忽略)
from pathlib import Path

# 添加配置模块路径（脚本在 references/, 配置在 scripts/）
_local_config_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_local_config_dir))
try:
    from config import get_openviking_config, get_feishu_config, get_output_config, print_config_check
except ImportError:
    get_openviking_config = None
    get_feishu_config = None
    get_output_config = None
    print_config_check = None

# OpenViking 导入 - 使用 HTTP API
import requests

# 从配置加载 OpenViking
if get_openviking_config:
    _ov_cfg = get_openviking_config()
else:
    _ov_cfg = {
        "api_base": "http://127.0.0.1:1933",
        "api_key": "<OV_API_KEY>",
        "account": "root",
        "user": "user001"
    }

OV_BASE_URL = _ov_cfg.get("api_base", "http://127.0.0.1:1933")
OV_API_KEY = _ov_cfg.get("api_key", "")
OV_ACCOUNT = _ov_cfg.get("account", "root")
OV_USER = _ov_cfg.get("user", "user001")

def ov_headers():
    return {
        "X-API-Key": OV_API_KEY,
        "X-OpenViking-Account": OV_ACCOUNT,
        "X-OpenViking-User": OV_USER,
        "Content-Type": "application/json"
    }

def create_ov_session():
    """创建 OpenViking session"""
    resp = requests.post(f"{OV_BASE_URL}/api/v1/sessions", headers=ov_headers(), timeout=30)
    if resp.json().get('result'):
        return resp.json()['result']['session_id']
    return None

def add_ov_message(session_id, content):
    """添加消息到 session"""
    url = f"{OV_BASE_URL}/api/v1/sessions/{session_id}/messages"
    resp = requests.post(url, headers=ov_headers(), json={"role": "user", "content": content}, timeout=30)
    return resp.json().get('result', {}).get('message_count', 0) > 0

def wait_ov_processed(timeout=300):
    """等待处理完成"""
    url = f"{OV_BASE_URL}/api/v1/system/wait"
    resp = requests.post(url, headers=ov_headers(), json={"timeout": timeout}, timeout=timeout+10)
    return resp.json().get('result')

# 从配置加载飞书参数
if get_feishu_config:
    _fs_cfg = get_feishu_config()
else:
    _fs_cfg = {
        "project_key": "<BUG_INSIGHT_FEISHU_PROJECT_KEY>",
        "plugin_secret": "<BUG_INSIGHT_FEISHU_PLUGIN_SECRET>",
        "plugin_id": "<BUG_INSIGHT_FEISHU_PLUGIN_ID>",
        "user_key": "<BUG_INSIGHT_FEISHU_USER_KEY>"
    }

PROJECT_KEY = _fs_cfg.get("project_key", "")
PLUGIN_ID = _fs_cfg.get("plugin_id", "")
PLUGIN_SECRET = _fs_cfg.get("plugin_secret", "")
USER_KEY = _fs_cfg.get("user_key", "")

# 从配置加载输出目录
_output_cfg = get_output_config() if 'get_output_config' in dir() else {}
OUTPUT_DIR = _output_cfg.get("base_dir", os.path.expanduser("~/.openviking/workspace/feishu-bugs"))
BATCH_DIR = os.path.join(OUTPUT_DIR, 'batch')
BUGS_LIST_FILE = f"{BATCH_DIR}/bugs_index.json"
BUGS_DETAILS_FILE = f"{BATCH_DIR}/bugs_full_all.json"
BUGS_WITH_COMMENTS_FILE = f"{BATCH_DIR}/bugs_with_comments.json"
ALL_BUG_IDS_FILE = "/tmp/all_bug_ids.txt"

# ============ 工具函数 ============

def get_plugin_token():
    """获取Plugin Token"""
    url = "https://project.feishu.cn/open_api/authen/plugin_token"
    data = {
        "plugin_id": PLUGIN_ID,
        "plugin_secret": PLUGIN_SECRET,
        "type": 0
    }
    result = requests.post(url, json=data, timeout=10)
    return result.json().get('data', {}).get('token')

def mcporter_call(tool, args):
    """调用mcporter MCP命令。
    注意: get_workitem_brief 和 list_workitem_comments 的 work_item_id 必须是字符串格式。"""
    # mcporter 的 work_item_id 参数必须是字符串, 否则报 Invalid Param
    if tool in ('get_workitem_brief', 'list_workitem_comments') and 'work_item_id' in args:
        args['work_item_id'] = str(args['work_item_id'])
    cmd = ["mcporter", "call", "meego", tool, "--args", json.dumps(args)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.stdout.strip():
            return json.loads(result.stdout)
    except Exception as e:
        print(f"  MCP调用失败: {e}")
    return None

def get_bug_list_mcp(limit=5000):
    """获取缺陷列表"""
    print("获取缺陷列表...")
    result = mcporter_call("search_by_mql", {
        "project_key": PROJECT_KEY,
        "mql": f"SELECT work_item_id, name, work_item_status FROM sw_team.issue LIMIT {limit}"
    })
    if not result:
        print("  获取失败，尝试直接API...")
        return get_bug_list_api()
    
    bugs = []
    if 'data' in result:
        for group_data in result['data'].values():
            for item in group_data:
                bug_info = {'id': None, 'name': '', 'status': ''}
                for field in item.get('moql_field_list', []):
                    key = field.get('key')
                    if key == 'work_item_id':
                        bug_info['id'] = str(field['value']['long_value'])
                    elif key == 'name':
                        bug_info['name'] = field['value'].get('text_value', '')
                    elif key == 'work_item_status':
                        bug_info['status'] = field['value'].get('label', '')
                if bug_info['id']:
                    bugs.append(bug_info)
    
    print(f"  获取到 {len(bugs)} 个缺陷")
    return bugs

def get_bug_list_api():
    """通过直接API获取缺陷列表"""
    token = get_plugin_token()
    if not token:
        print("  无法获取token")
        return []
    
    url = f"https://project.feishu.cn/open_api/{PROJECT_KEY}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    # 简单查询
    data = {"limit": 5000}
    result = requests.post(url, json=data, headers=headers, timeout=30)
    items = result.json().get('data', [])
    
    bugs = []
    for item in items:
        bugs.append({
            'id': str(item.get('id')),
            'name': item.get('name', ''),
            'status': item.get('status', {}).get('status_name', '')
        })
    
    print(f"  API获取到 {len(bugs)} 个缺陷")
    return bugs

def get_bug_details(bug_ids, workers=10):
    """批量获取缺陷详情"""
    print(f"获取缺陷详情 (共{len(bug_ids)}个)...")
    
    def get_single_detail(bug_id):
        detail = mcporter_call("get_workitem_brief", {
            "project_key": PROJECT_KEY,
            "work_item_id": bug_id
        })
        if detail:
            return {'id': bug_id, 'detail': detail}
        return None
    
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(get_single_detail, bid): bid for bid in bug_ids}
        for i, future in enumerate(as_completed(futures)):
            result = future.result()
            if result:
                results.append(result)
            if (i + 1) % 100 == 0:
                print(f"  进度: {i+1}/{len(bug_ids)}")
    
    print(f"  获取到 {len(results)} 个详情")
    return results

def get_bug_comments_mcp(bug_ids, workers=5):
    """批量获取评论 (MCP)"""
    print(f"获取缺陷评论 (共{len(bug_ids)}个)...")
    
    bugs_with_comments = []
    
    def check_comments(bug_id):
        for attempt in range(3):
            result = mcporter_call("list_workitem_comments", {
                "project_key": PROJECT_KEY,
                "work_item_id": bug_id,
                "page_num": 1
            })
            if result and result.get('comments'):
                return {'work_item_id': bug_id, 'comments': result['comments']}
            time.sleep(0.5)
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
            if processed % 100 == 0:
                elapsed = (time.time() - start_time) / 60
                print(f"  进度: {processed}/{len(bug_ids)}, 有评论: {len(bugs_with_comments)}, 耗时: {elapsed:.1f}分钟")
    
    elapsed = (time.time() - start_time) / 60
    print(f"  获取到 {len(bugs_with_comments)} 个有评论的缺陷, 耗时: {elapsed:.1f}分钟")
    return bugs_with_comments

def get_attachments_info(bug_ids, token=None):
    """获取附件信息 (Direct API)"""
    print(f"获取附件信息 (共{len(bug_ids)}个)...")
    
    if not token:
        token = get_plugin_token()
    
    if not token:
        print("  无法获取token")
        return []
    
    url = f"https://project.feishu.cn/open_api/{PROJECT_KEY}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    results = []
    # 每次查询50个 (Direct API 限制: work_item_ids 超过 50 返回空)
    for i in range(0, len(bug_ids), 50):
        batch = bug_ids[i:i+50]
        data = {"work_item_ids": [int(bid) for bid in batch], "get_all_properties": True}
        try:
            resp = requests.post(url, json=data, headers=headers, timeout=30)
            items = resp.json().get('data', [])
            for item in items:
                # 从 fields 中获取 multi_attachment
                attachments = []
                fields = item.get('fields', [])
                for field in fields:
                    if field.get('field_key') == 'multi_attachment':
                        attachments = field.get('field_value', [])
                        break
                
                if attachments:
                    results.append({
                        'id': str(item.get('id')),
                        'attachments': attachments
                    })
        except Exception as e:
            print(f"  批次 {i//100 + 1} 失败: {e}")
        time.sleep(0.2)
    
    print(f"  获取到 {len(results)} 个有附件的缺陷")
    return results

def save_bugs_list(bugs):
    """保存缺陷列表"""
    with open(BUGS_LIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(bugs, f, ensure_ascii=False, indent=2)
    print(f"已保存到: {BUGS_LIST_FILE}")

def save_bugs_details(details):
    """保存详情"""
    with open(BUGS_DETAILS_FILE, 'w', encoding='utf-8') as f:
        json.dump(details, f, ensure_ascii=False, indent=2)
    print(f"已保存到: {BUGS_DETAILS_FILE}")

def save_comments(comments):
    """保存评论"""
    with open(BUGS_WITH_COMMENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(comments, f, ensure_ascii=False, indent=2)
    print(f"已保存到: {BUGS_WITH_COMMENTS_FILE}")

# ============ OpenViking 导入 ============

def create_bug_text(bug):
    """将缺陷转换为可搜索的文本格式"""
    bug_id = str(bug.get('id', ''))
    name = bug.get('name', '')
    status = bug.get('status', '')
    
    # 附件信息
    attachments = bug.get('attachments', [])
    attachment_count = bug.get('attachment_count', 0)
    
    # 从标题提取关键信息
    # 功能分类
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
    
    # 复现概率
    reproduce = "未标注"
    if "必现" in name:
        reproduce = "必现"
    elif "偶现" in name:
        reproduce = "偶现"
    elif "高概率" in name:
        reproduce = "高概率"
    elif "低概率" in name:
        reproduce = "低概率"
    
    # 版本信息
    version = "未标注"
    for v in ["1.10.0", "1.9.0", "1.8.0", "1.7.0", "2.0.0", "A2.0.0", "SDK3.0.0"]:
        if v in name:
            version = v
            break
    
    # 构建可搜索文本
    text = f"""缺陷 ID: {bug_id}
标题: {name}
状态: {status}
功能分类: {category}
复现概率: {reproduce}
版本: {version}
附件数: {attachment_count}
"""
    
    if attachments:
        att_names = ", ".join([a.get('name', '')[:30] for a in attachments[:3]])
        text += f"\n附件: {att_names}"
    
    return text

def import_to_openviking(session_name="feishu-bugs"):
    """导入缺陷数据到 OpenViking (使用 HTTP API)"""
    print(f"开始导入到 OpenViking (session: {session_name})...")
    
    # 加载数据 - 优先使用 bugs_full_all.json
    bugs_file = f"{BATCH_DIR}/bugs_full_all.json"
    
    if not os.path.exists(bugs_file):
        bugs_file = f"{BATCH_DIR}/bugs_all_with_details.json"
    
    if not os.path.exists(bugs_file):
        print(f"错误: 文件不存在 {bugs_file}")
        return
    
    # 读取缺陷数据
    with open(bugs_file, 'r', encoding='utf-8') as f:
        bugs = json.load(f)
    
    print(f"加载了 {len(bugs)} 条缺陷")
    
    # 创建 session (session_name 暂时不支持，使用自动生成的 ID)
    session_id = create_ov_session()
    if not session_id:
        print("错误: 无法创建 session")
        return
    
    print(f"Session 创建成功: {session_id}")
    
    # 批量导入
    success = 0
    failed = 0
    
    for i, bug in enumerate(bugs):
        # 转换为文本
        text = create_bug_text(bug)
        
        try:
            if add_ov_message(session_id, text):
                success += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  导入失败 {bug.get('id')}: {e}")
        
        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{len(bugs)}, 成功: {success}, 失败: {failed}")
    
    # 等待处理完成
    print("等待向量化完成...")
    wait_ov_processed(timeout=300)
    
    print(f"\n导入完成！成功: {success}, 失败: {failed}")
    print(f"Session ID: {session_id}")
    print(f"查询示例: 使用 memory_search 搜索 '画面 显示 问题'")

# ============ 主函数 ============

def main():
    # 启动时检查配置
    if print_config_check:
        print_config_check()
    
    import argparse
    parser = argparse.ArgumentParser(description='飞书缺陷数据获取')
    parser.add_argument('--list-only', action='store_true', help='只获取缺陷列表')
    parser.add_argument('--details-only', action='store_true', help='只获取详情')
    parser.add_argument('--comments-only', action='store_true', help='只获取评论')
    parser.add_argument('--attachments-only', action='store_true', help='只获取附件信息')
    parser.add_argument('--all', action='store_true', help='获取全部数据')
    parser.add_argument('--import', dest='import_openviking', action='store_true', help='获取后导入到 OpenViking')
    parser.add_argument('--session', type=str, default='feishu-bugs', help='OpenViking session 名称')
    parser.add_argument('--workers', type=int, default=5, help='并发数')
    args = parser.parse_args()
    
    # 默认获取全部
    if not any([args.list_only, args.details_only, args.comments_only, args.attachments_only]):
        args.all = True
    
    os.makedirs(BATCH_DIR, exist_ok=True)
    
    # 1. 获取缺陷列表
    if args.list_only or args.all:
        bugs = get_bug_list_mcp()
        if bugs:
            save_bugs_list(bugs)
            # 保存ID列表供后续使用
            with open(ALL_BUG_IDS_FILE, 'w') as f:
                for b in bugs:
                    f.write(f"{b['id']}\n")
    
    # 读取已有列表
    bug_ids = []
    if os.path.exists(ALL_BUG_IDS_FILE):
        with open(ALL_BUG_IDS_FILE, 'r') as f:
            bug_ids = [line.strip() for line in f if line.strip()]
    elif os.path.exists(BUGS_LIST_FILE):
        with open(BUGS_LIST_FILE, 'r') as f:
            bugs = json.load(f)
            bug_ids = [b['id'] for b in bugs]
    
    if not bug_ids:
        print("没有缺陷ID，请先获取缺陷列表")
        return
    
    print(f"共有 {len(bug_ids)} 个缺陷")
    
    # 2. 获取详情
    if args.details_only or args.all:
        details = get_bug_details(bug_ids, workers=args.workers)
        save_bugs_details(details)
    
    # 3. 获取评论
    if args.comments_only or args.all:
        comments = get_bug_comments_mcp(bug_ids, workers=args.workers)
        save_comments(comments)
    
    # 4. 获取附件信息
    if args.attachments_only or args.all:
        token = get_plugin_token()
        attachments = get_attachments_info(bug_ids, token)
        # 保存附件信息
        att_file = f"{BATCH_DIR}/bugs_attachments.json"
        with open(att_file, 'w', encoding='utf-8') as f:
            json.dump(attachments, f, ensure_ascii=False, indent=2)
        print(f"已保存到: {att_file}")
    
    # 5. 导入到 OpenViking
    if args.import_openviking:
        import_to_openviking(session_name=args.session)
    else:
        print("\n完成!")

if __name__ == "__main__":
    main()