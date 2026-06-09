#!/usr/bin/env python3
"""
飞书工作项附件下载脚本
功能：下载工作项的指定附件

API 说明:
- 请求方式: POST
- 请求地址: /open_api/:project_key/work_item/:work_item_type_key/:work_item_id/file/download
- 需要先获取 multi_file 字段的 uuid

用法:
    # 下载单个文件（通过 uid）
    python3 download_attachment.py <缺陷ID> <uuid>
    
    # 下载指定工作项的所有附件
    python3 download_attachment.py <缺陷ID> --all
    
    # 列出可下载的附件
    python3 download_attachment.py <缺陷ID> --list
    
    # 指定输出目录
    python3 download_attachment.py <缺陷ID> --all -o /path/to/output
"""

import subprocess
import json
import os
import sys
import time
import requests
from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse
from tqdm import tqdm

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
OUTPUT_DIR = _output_cfg.get("base_dir", "/home/xreal/.openviking/workspace/feishu-bugs")


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


def get_work_item_type_key(project_key, work_item_id):
    """获取工作项的类型 key"""
    # 通过 MCP 获取工作项详情，里面包含类型信息
    token = get_plugin_token()
    if not token:
        return None
    
    url = f"https://project.feishu.cn/open_api/{project_key}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    data = {
        "work_item_ids": [int(work_item_id)],
        "get_all_properties": True
    }
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        items = resp.json().get('data', [])
        
        if items:
            # 从返回数据中获取工作项类型 key
            # 飞书项目返回的数据包含 work_item_type_key 字段
            return items[0].get('work_item_type_key')
    except Exception as e:
        print(f"获取工作项类型失败: {e}")
    
    return None


def get_multi_file_uuids(project_key, work_item_id):
    """获取工作项的 multi_file 字段的 uuid 列表"""
    token = get_plugin_token()
    if not token:
        print("无法获取 token")
        return []
    
    url = f"https://project.feishu.cn/open_api/{project_key}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
        "Content-Type": "application/json"
    }
    
    # 需要添加 get_all_properties: true 才能获取附件信息
    data = {
        "work_item_ids": [int(work_item_id)],
        "get_all_properties": True
    }
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        items = resp.json().get('data', [])
        
        if items:
            item = items[0]
            # 获取附件类型字段 (multi_file)
            multi_file_fields = []
            seen_uuids = set()  # 全局去重
            
            # 遍历字段找到包含附件的字段
            fields = item.get('fields', [])
            for field in fields:
                field_type = field.get('field_type_key', '')
                if field_type == 'multi_file':
                    field_name = field.get('field_alias', field.get('field_key', ''))
                    field_value = field.get('field_value', [])
                    
                    for f in field_value:
                        uid = f.get('uid', '')
                        if uid and uid not in seen_uuids:
                            seen_uuids.add(uid)
                            multi_file_fields.append({
                                'uuid': uid,
                                'name': f.get('name', ''),
                                'size': f.get('size', ''),
                                'type': f.get('type', ''),
                                'field_name': field_name
                            })
            
            # 也检查 multi_attachment 字段
            for field in fields:
                field_key = field.get('field_key', '')
                if field_key == 'multi_attachment':
                    field_value = field.get('field_value', [])
                    for f in field_value:
                        uid = f.get('uid', '')
                        if uid and uid not in seen_uuids:
                            seen_uuids.add(uid)
                            multi_file_fields.append({
                                'uuid': uid,
                                'name': f.get('name', ''),
                                'size': f.get('size', ''),
                                'type': f.get('type', ''),
                                'field_name': 'attachment'
                            })
            
            return multi_file_fields
            
    except Exception as e:
        print(f"获取附件信息失败: {e}")
    
    return []


def download_file(project_key, work_item_type_key, work_item_id, file_uuid, output_path):
    """
    下载单个文件
    
    API: POST /open_api/:project_key/work_item/:work_item_type_key/:work_item_id/file/download
    
    请求体:
    {
        "uuid": "xxx"
    }
    """
    token = get_plugin_token()
    if not token:
        print("无法获取 token")
        return False
    
    url = f"https://project.feishu.cn/open_api/{project_key}/work_item/{work_item_type_key}/{work_item_id}/file/download"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": USER_KEY,
    }
    
    data = {
        "uuid": file_uuid
    }
    
    try:
        print(f"下载文件到: {output_path}")
        print(f"URL: {url}")
        
        resp = requests.post(url, json=data, headers=headers, timeout=120, stream=True)
        
        # 检查响应
        content_type = resp.headers.get('Content-Type', '')
        
        if 'application/json' in content_type:
            # 可能是错误响应
            error_data = resp.json()
            print(f"错误响应: {error_data}")
            return False
        elif 'application/octet-stream' in content_type or 'application/pdf' in content_type or 'image' in content_type:
            # 二进制文件
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"✓ 下载成功: {output_path}")
            return True
        else:
            # 尝试作为二进制处理
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"✓ 下载成功: {output_path}")
            return True
            
    except Exception as e:
        print(f"下载失败: {e}")
        return False


def list_attachments(project_key, work_item_id):
    """列出工作项的所有可下载附件"""
    print(f"\n工作项 {work_item_id} 的附件列表:")
    print("=" * 60)
    
    files = get_multi_file_uuids(project_key, work_item_id)
    
    if not files:
        print("未找到附件")
        return []
    
    for i, f in enumerate(files, 1):
        size_str = format_size(f['size'])
        print(f"{i}. {f['name']}")
        print(f"   UUID: {f['uuid']}")
        print(f"   字段: {f['field_name']}")
        print(f"   大小: {size_str}")
        print(f"   类型: {f['type']}")
        print()
    
    print(f"共 {len(files)} 个文件")
    return files


def format_size(size):
    """格式化文件大小"""
    # 如果是字符串（如 "4.2MB"），直接返回
    if isinstance(size, str):
        return size
    
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def download_all_attachments(project_key, work_item_id, output_dir=None):
    """下载工作项的所有附件"""
    # 确保输出目录存在
    if output_dir is None:
        output_dir = os.path.join(OUTPUT_DIR, 'attachments', str(work_item_id))
    os.makedirs(output_dir, exist_ok=True)
    
    # 获取附件列表
    files = get_multi_file_uuids(project_key, work_item_id)
    
    if not files:
        print(f"工作项 {work_item_id} 没有附件")
        return []
    
    # 获取工作项类型 key
    work_item_type_key = get_work_item_type_key(project_key, work_item_id)
    if not work_item_type_key:
        print("无法获取工作项类型，使用 'issue' 作为默认值")
        work_item_type_key = "issue"
    
    print(f"\n开始下载 {len(files)} 个附件...")
    print(f"输出目录: {output_dir}")
    print("-" * 40)
    
    downloaded = []
    failed = []
    
    for f in files:
        # 清理文件名
        safe_name = f['name'].replace('/', '_').replace('\\', '_').replace(':', '_')
        output_path = os.path.join(output_dir, safe_name)
        
        # 处理重名文件
        counter = 1
        base_name = os.path.splitext(safe_name)[0]
        ext = os.path.splitext(safe_name)[1]
        while os.path.exists(output_path):
            output_path = os.path.join(output_dir, f"{base_name}_{counter}{ext}")
            counter += 1
        
        success = download_file(project_key, work_item_type_key, work_item_id, f['uuid'], output_path)
        
        if success:
            downloaded.append(output_path)
        else:
            failed.append(f['name'])
        
        time.sleep(0.3)  # 避免请求过快
    
    print("\n" + "=" * 40)
    print(f"下载完成: 成功 {len(downloaded)}, 失败 {len(failed)}")
    
    if failed:
        print(f"失败的文件: {', '.join(failed)}")
    
def download_batch_attachments(attachments_file, output_dir=None):
    """批量下载多个缺陷的附件"""
    if not os.path.exists(attachments_file):
        print(f"错误: 附件信息文件不存在: {attachments_file}")
        return []
    
    with open(attachments_file, 'r', encoding='utf-8') as f:
        attachments_data = json.load(f)
    
    if not attachments_data:
        print("没有附件数据")
        return []
    
    print(f"批量下载 {len(attachments_data)} 个缺陷的附件...")
    
    if output_dir is None:
        output_dir = os.path.join(OUTPUT_DIR, 'attachments')
    
    total_downloaded = 0
    total_failed = 0
    
    for item in tqdm(attachments_data, desc="下载进度"):
        bug_id = item.get('id', '')
        attachments = item.get('attachments', [])
        
        if not attachments:
            continue
        
        bug_output_dir = os.path.join(output_dir, str(bug_id))
        os.makedirs(bug_output_dir, exist_ok=True)
        
        # 获取工作项类型
        work_item_type_key = get_work_item_type_key(PROJECT_KEY, bug_id)
        if not work_item_type_key:
            work_item_type_key = "issue"
        
        for att in attachments:
            uuid = att.get('uid', '')
            name = att.get('name', '')
            
            if not uuid or not name:
                continue
            
            # 清理文件名
            safe_name = name.replace('/', '_').replace('\\', '_').replace(':', '_')
            output_path = os.path.join(bug_output_dir, safe_name)
            
            # 处理重名文件
            counter = 1
            base_name = os.path.splitext(safe_name)[0]
            ext = os.path.splitext(safe_name)[1]
            while os.path.exists(output_path):
                output_path = os.path.join(bug_output_dir, f"{base_name}_{counter}{ext}")
                counter += 1
            
            success = download_file(PROJECT_KEY, work_item_type_key, bug_id, uuid, output_path)
            
            if success:
                total_downloaded += 1
            else:
                total_failed += 1
            
            time.sleep(0.3)  # 避免请求过快
    
    print(f"\n批量下载完成: 成功 {total_downloaded}, 失败 {total_failed}")
    return []


def main():
    import argparse
    parser = argparse.ArgumentParser(description='下载飞书工作项附件')
    parser.add_argument('work_item_id', nargs='?', help='工作项ID (批量模式时可选)')
    parser.add_argument('file_uuid', nargs='?', help='文件UUID (可选)')
    parser.add_argument('--list', action='store_true', help='列出所有可下载的附件')
    parser.add_argument('--all', action='store_true', help='下载所有附件')
    parser.add_argument('--batch', action='store_true', help='批量模式: 从JSON文件下载多个缺陷的附件')
    parser.add_argument('-i', '--input', help='批量模式输入文件 (bugs_attachments.json)')
    parser.add_argument('-o', '--output', help='输出目录')
    parser.add_argument('-p', '--project-key', default=PROJECT_KEY, help='项目key')
    
    args = parser.parse_args()
    
    # 批量模式
    if args.batch:
        input_file = args.input
        if not input_file:
            # 默认使用 bugs_attachments.json
            input_file = os.path.join(OUTPUT_DIR, 'batch', 'bugs_attachments.json')
        
        output_dir = args.output
        download_batch_attachments(input_file, output_dir)
        return
    
    # 单个缺陷模式
    if not args.work_item_id:
        parser.print_help()
        return
    
    work_item_id = args.work_item_id.strip()
    project_key = args.project_key
    
    if not work_item_id.isdigit():
        print(f"错误: 工作项ID应该是数字, 收到: {work_item_id}")
        sys.exit(1)
    
    print(f"项目: {project_key}")
    print(f"工作项ID: {work_item_id}")
    
    # 列出附件
    if args.list:
        list_attachments(project_key, work_item_id)
        return
    
    # 下载单个文件
    if args.file_uuid:
        # 获取工作项类型 key
        work_item_type_key = get_work_item_type_key(project_key, work_item_id)
        if not work_item_type_key:
            work_item_type_key = "issue"
        
        # 确定输出路径
        if args.output:
            output_path = args.output
        else:
            output_dir = os.path.join(OUTPUT_DIR, 'attachments', str(work_item_id))
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, args.file_uuid[:8] + '_file')
        
        print(f"\n下载文件 UUID: {args.file_uuid}")
        success = download_file(project_key, work_item_type_key, work_item_id, args.file_uuid, output_path)
        
        if success:
            print(f"\n✓ 下载完成")
        else:
            print(f"\n✗ 下载失败")
            sys.exit(1)
        
        return
    
    # 下载所有附件
    if args.all:
        download_all_attachments(project_key, work_item_id, args.output)
        return
    
    # 无参数时显示帮助
    parser.print_help()
    print("\n示例:")
    print(f"  python3 {sys.argv[0]} {work_item_id} --list")
    print(f"  python3 {sys.argv[0]} {work_item_id} --all")
    print(f"  python3 {sys.argv[0]} {work_item_id} <uuid>")


if __name__ == "__main__":
    main()