#!/usr/bin/env python3
"""
飞书单个缺陷详情获取脚本
功能：获取单个缺陷的完整信息（详情、评论、附件）

用法:
    python3 fetch_single_bug.py <缺陷ID>
    python3 fetch_single_bug.py 6945539575
    python3 fetch_single_bug.py 6945539575 --comments --attachments
"""

import subprocess
import json
import os
import sys
import time
import requests
from pathlib import Path
from datetime import datetime

# mcporter 配置: ~/.mcporter/mcporter.json

# 添加配置模块路径（脚本在 references/, 配置在 scripts/）
_local_config_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(_local_config_dir))
try:
    from config import get_feishu_config, get_output_config
except ImportError:
    get_feishu_config = None
    get_output_config = None

# 从配置加载飞书参数
if get_feishu_config:
    _fs_cfg = get_feishu_config()
else:
    _fs_cfg = {
        "project_key": "<FEISHU_PROJECT_KEY>",
        "plugin_secret": "<FEISHU_PLUGIN_SECRET>",
        "plugin_id": "<FEISHU_PLUGIN_ID>",
        "user_key": "<FEISHU_USER_KEY>"
    }

PROJECT_KEY = _fs_cfg.get("project_key", "")
PLUGIN_ID = _fs_cfg.get("plugin_id", "")
PLUGIN_SECRET = _fs_cfg.get("plugin_secret", "")
USER_KEY = _fs_cfg.get("user_key", "")

# 从配置加载输出目录
_output_cfg = get_output_config() if get_output_config else {}
OUTPUT_DIR = os.path.expanduser(_output_cfg.get("base_dir", "~/.openviking/workspace/feishu-bugs"))


def get_plugin_token():
    """获取Plugin Token"""
    url = "https://project.feishu.cn/open_api/authen/plugin_token"
    data = {
        "plugin_id": PLUGIN_ID,
        "plugin_secret": PLUGIN_SECRET,
        "type": 0
    }
    try:
        result = requests.post(url, json=data, timeout=10)
        return result.json().get('data', {}).get('token')
    except Exception as e:
        print(f"获取 token 失败: {e}")
        return None


def mcporter_call(tool, args):
    """调用mcporter MCP命令。work_item_id 必须是字符串格式。"""
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


def get_single_bug_detail(bug_id):
    """获取单个缺陷详情"""
    print(f"获取缺陷 {bug_id} 的详情...")
    
    result = mcporter_call("get_workitem_brief", {
        "project_key": PROJECT_KEY,
        "work_item_id": bug_id
    })
    
    if result:
        return result
    return None


def get_single_bug_comments(bug_id):
    """获取单个缺陷的评论"""
    print(f"获取缺陷 {bug_id} 的评论...")
    
    for attempt in range(3):
        result = mcporter_call("list_workitem_comments", {
            "project_key": PROJECT_KEY,
            "work_item_id": bug_id,
            "page_num": 1
        })
        
        if result and result.get('comments'):
            return result['comments']
        
        if attempt < 2:
            time.sleep(0.5)
    
    return []


def get_single_bug_attachments(bug_id):
    """获取单个缺陷的附件信息（通过 Direct API）
    
    返回: (attachments_list, raw_bug_data) 
    raw_bug_data 是 Direct API 返回的扁平格式缺陷数据, 可用于报告解析
    """
    print(f"获取缺陷 {bug_id} 的附件...")
    
    token = get_plugin_token()
    if not token:
        print("  无法获取 token")
        return [], None
    
    url = f"https://project.feishu.cn/open_api/{PROJECT_KEY}/work_item/issue/query"
    headers = {
        "x-plugin-token": token,
        "x-user-key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    data = {
        "work_item_ids": [int(bug_id)]
    }
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        items = resp.json().get('data', [])
        
        if items:
            item = items[0]
            # 附件在 fields 数组中的 multi_attachment 字段
            attachments = []
            for field in item.get('fields', []):
                if field.get('field_key') == 'multi_attachment':
                    attachments = field.get('field_value', [])
                    break
            
            # 格式化附件信息
            formatted = []
            for att in attachments:
                formatted.append({
                    'name': att.get('name', ''),
                    'type': att.get('type', ''),
                    'size': att.get('size', 0),
                    'url': att.get('download_url', ''),
                    'token': att.get('token', '')
                })
            
            return formatted, item
    except Exception as e:
        print(f"  获取附件失败: {e}")
    
    return [], None


def format_bug_report(bug_id, detail=None, comments=None, attachments=None, direct_api_bug=None):
    """格式化缺陷报告
    
    支持两种数据源格式:
    - mcporter 嵌套格式 (work_item_attribute, work_item_fields) — 详情来源
    - Direct API 扁平格式 (顶层 id, work_item_status.state_key, fields 数组) — 附件来源
    
    direct_api_bug: 可选, Direct API 返回的扁平格式缺陷数据, 用于补充状态/字段信息
    """
    report = {
        "id": bug_id,
        "fetch_time": datetime.now().isoformat(),
    }
    
    # 优先尝试从 Direct API 扁平格式解析（更简洁）
    if direct_api_bug:
        report['name'] = direct_api_bug.get('name', '')
        report['status'] = direct_api_bug.get('work_item_status', {}).get('state_key', '')
        report['sub_stage'] = direct_api_bug.get('sub_stage', '')
        
        # 从 fields 数组中提取字段
        for field in direct_api_bug.get('fields', []):
            fk = field.get('field_key', '')
            fv = field.get('field_value', None)
            if fk == 'priority' and fv:
                report['priority'] = fv[0].get('name', '') if isinstance(fv, list) else str(fv)
            elif fk == 'severity' and fv:
                report['severity'] = fv[0].get('name', '') if isinstance(fv, list) else str(fv)
            elif fk == 'issue_reporter' and fv:
                report['reporter'] = fv if isinstance(fv, list) else [fv]
        
        if direct_api_bug.get('created_at'):
            ts = direct_api_bug['created_at'] / 1000 if direct_api_bug['created_at'] > 1e12 else direct_api_bug['created_at']
            report['create_time'] = datetime.fromtimestamp(ts).isoformat()
        
        report['direct_api_data'] = direct_api_bug
        
    # 回退到 mcporter 嵌套格式解析
    elif detail:
        attr = detail.get('work_item_attribute', {})
        report['name'] = attr.get('work_item_name', '')
        report['status'] = attr.get('work_item_status', {}).get('name', '')
        report['create_time'] = attr.get('create_time', '')
        report['create_by'] = attr.get('create_by', {}).get('name', '')
        report['priority'] = attr.get('priority', {}).get('name', '')
        report['severity'] = attr.get('severity', {}).get('name', '')
        
        # 从字段中提取更多信息
        fields = detail.get('work_item_fields', [])
        for field in fields:
            field_name = field.get('field_name', '')
            if '功能' in field_name:
                report['功能模块'] = field.get('value', {}).get('name', '')
            elif '优先级' in field_name:
                report['优先级'] = field.get('value', {}).get('name', '')
            elif '严重程度' in field_name:
                report['严重程度'] = field.get('value', {}).get('name', '')
        
        report['detail'] = detail
    
    # 添加评论
    if comments:
        report['comments'] = comments
        report['comment_count'] = len(comments)
    
    # 添加附件
    if attachments:
        report['attachments'] = attachments
        report['attachment_count'] = len(attachments)
    
    return report


def save_report(bug_id, report):
    """保存报告到文件"""
    single_dir = os.path.join(OUTPUT_DIR, 'single')
    os.makedirs(single_dir, exist_ok=True)
    
    filename = f"{single_dir}/bug_{bug_id}_report.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n报告已保存: {filename}")
    return filename


def print_summary(report):
    """打印摘要"""
    print("\n" + "="*50)
    print("缺陷信息摘要")
    print("="*50)
    
    print(f"ID: {report.get('id')}")
    print(f"标题: {report.get('name', 'N/A')}")
    print(f"状态: {report.get('status', 'N/A')}")
    print(f"创建时间: {report.get('create_time', 'N/A')}")
    print(f"创建人: {report.get('create_by', 'N/A')}")
    
    if '功能模块' in report:
        print(f"功能模块: {report.get('功能模块')}")
    if '优先级' in report:
        print(f"优先级: {report.get('优先级')}")
    if '严重程度' in report:
        print(f"严重程度: {report.get('严重程度')}")
    
    comment_count = report.get('comment_count', 0)
    print(f"\n评论数: {comment_count}")
    if comment_count > 0 and 'comments' in report:
        print("最新评论:")
        for c in report['comments'][:3]:
            creator = c.get('creator', '未知')
            if isinstance(creator, str):
                # creator 是用户ID，不是对象
                creator = creator[:8] + '...' if len(creator) > 8 else creator
            elif isinstance(creator, dict):
                creator = creator.get('name', '未知')
            content = c.get('content', '')[:100]
            print(f"  - {creator}: {content}...")
    
    att_count = report.get('attachment_count', 0)
    print(f"\n附件数: {att_count}")
    if att_count > 0 and 'attachments' in report:
        print("附件列表:")
        for a in report['attachments']:
            print(f"  - {a.get('name')} ({a.get('type')}, {a.get('size')} bytes)")
    
    print("="*50)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='获取单个飞书缺陷详情')
    parser.add_argument('bug_id', help='缺陷ID')
    parser.add_argument('--no-detail', action='store_true', help='不获取详情')
    parser.add_argument('--no-comments', action='store_true', help='不获取评论')
    parser.add_argument('--no-attachments', action='store_true', help='不获取附件')
    parser.add_argument('--save', action='store_true', default=True, help='保存到文件 (默认开启)')
    parser.add_argument('--print', action='store_true', default=True, help='打印摘要 (默认开启)')
    
    args = parser.parse_args()
    
    bug_id = args.bug_id.strip()
    
    if not bug_id.isdigit():
        print(f"错误: 缺陷ID应该是数字, 收到: {bug_id}")
        sys.exit(1)
    
    print(f"开始获取缺陷 {bug_id} 的信息...")
    print(f"项目: {PROJECT_KEY}")
    print("-" * 40)
    
    # 获取各项数据
    detail = None
    direct_api_bug = None  # Direct API 返回的扁平格式数据
    if not args.no_detail:
        detail = get_single_bug_detail(bug_id)
        if detail:
            print(f"  ✓ 详情获取成功")
        else:
            print(f"  ✗ 详情获取失败")
    
    comments = None
    if not args.no_comments:
        comments = get_single_bug_comments(bug_id)
        print(f"  ✓ 评论获取成功 ({len(comments)} 条)")
    
    attachments = None
    if not args.no_attachments:
        attachments_result = get_single_bug_attachments(bug_id)
        # get_single_bug_attachments 返回 (attachments_list, raw_bug_data)
        if isinstance(attachments_result, tuple):
            attachments, direct_api_bug = attachments_result
        else:
            attachments = attachments_result
        print(f"  ✓ 附件获取成功 ({len(attachments)} 个)")
    
    # 构建报告
    report = format_bug_report(bug_id, detail, comments, attachments, direct_api_bug)
    
    # 打印摘要
    if args.print:
        print_summary(report)
    
    # 保存文件
    if args.save:
        save_report(bug_id, report)
    
    return report


if __name__ == "__main__":
    main()