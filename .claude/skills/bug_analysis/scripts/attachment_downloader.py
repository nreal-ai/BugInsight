#!/usr/bin/env python3
"""
飞书附件下载模块 — 集成到 bug_analyzer.py
从飞书 Direct API 下载缺陷附件（日志文件），供分析器使用。
"""
import json
import os
import re
import requests
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_feishu_credentials():
    """从 config.py 获取飞书凭据"""
    try:
        import sys
        _dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, _dir)
        from config import get_feishu_config
        cfg = get_feishu_config()
        return {
            'project_key': cfg.get('project_key', ''),
            'plugin_secret': cfg.get('plugin_secret', ''),
            'plugin_id': cfg.get('plugin_id', ''),
            'user_key': cfg.get('user_key', ''),
        }
    except Exception:
        return {
            'project_key': os.getenv('BUG_INSIGHT_FEISHU_PROJECT_KEY', '') or os.getenv('FEISHU_PROJECT_KEY', ''),
            'plugin_secret': os.getenv('BUG_INSIGHT_FEISHU_PLUGIN_SECRET', '') or os.getenv('FEISHU_PLUGIN_SECRET', ''),
            'plugin_id': os.getenv('BUG_INSIGHT_FEISHU_PLUGIN_ID', '') or os.getenv('FEISHU_PLUGIN_ID', ''),
            'user_key': os.getenv('BUG_INSIGHT_FEISHU_USER_KEY', '') or os.getenv('FEISHU_USER_KEY', ''),
        }


def get_plugin_token(project_key: str, plugin_id: str, plugin_secret: str) -> Optional[str]:
    """获取飞书 Plugin Token"""
    url = "https://project.feishu.cn/open_api/authen/plugin_token"
    data = {
        "plugin_id": plugin_id,
        "plugin_secret": plugin_secret,
        "type": 0
    }
    try:
        resp = requests.post(url, json=data, timeout=10)
        return resp.json().get('data', {}).get('token')
    except Exception:
        return None


def get_attachment_uuids(project_key: str, user_key: str, token: str, work_item_id: str) -> List[Dict]:
    """获取缺陷的附件 UUID 列表（仅日志文件）"""
    url = f"https://project.feishu.cn/open_api/{project_key}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": user_key,
        "Content-Type": "application/json"
    }
    data = {
        "work_item_ids": [int(work_item_id)],
        "get_all_properties": True
    }
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=30)
        items = resp.json().get('data', [])
        if not items:
            return []
        item = items[0]
        files = []
        seen = set()
        # 检查 multi_file 字段
        for field in item.get('fields', []):
            field_type = field.get('field_type_key', '')
            if field_type == 'multi_file':
                for f in field.get('field_value', []):
                    # 飞书 API 可能返回 JSON 字符串而不是字典
                    if isinstance(f, str):
                        try:
                            f = json.loads(f)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    if not isinstance(f, dict):
                        continue
                    uid = f.get('uid', '')
                    if uid and uid not in seen:
                        seen.add(uid)
                        files.append({
                            'uuid': uid,
                            'name': f.get('name', ''),
                            'size': f.get('size', ''),
                            'type': f.get('type', ''),
                            'url': f.get('url', ''),  # Direct API 返回的 v1/tos URL
                        })
        # 检查 multi_attachment 字段
        for field in item.get('fields', []):
            if field.get('field_key', '') == 'multi_attachment':
                for f in field.get('field_value', []):
                    # 飞书 API 可能返回 JSON 字符串而不是字典
                    if isinstance(f, str):
                        try:
                            f = json.loads(f)
                        except (json.JSONDecodeError, TypeError):
                            continue
                    if not isinstance(f, dict):
                        continue
                    uid = f.get('uid', '')
                    if uid and uid not in seen:
                        seen.add(uid)
                        files.append({
                            'uuid': uid,
                            'name': f.get('name', ''),
                            'size': f.get('size', ''),
                            'type': f.get('type', ''),
                            'url': f.get('url', ''),  # Direct API 返回的 v1/tos URL
                        })
        return files
    except Exception:
        return []


def get_work_item_type_key(project_key: str, user_key: str, token: str, work_item_id: str) -> str:
    """获取工作项类型 key"""
    url = f"https://project.feishu.cn/open_api/{project_key}/work_item/issue/query"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": user_key,
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
            return items[0].get('work_item_type_key', 'issue')
    except Exception:
        pass
    return 'issue'


def _get_download_url_via_mcporter(project_key: str, work_item_id: str,
                                   file_url: str) -> Optional[Dict]:
    """通过 MCP JSON-RPC get_download_url 获取签名下载 URL（替代 mcporter CLI）。

    返回 {'download_url': str, 'sign': str} 或 None
    """
    try:
        from mcp_client import mcp_get_download_url
        return mcp_get_download_url(project_key, str(work_item_id), file_url)
    except Exception:
        pass
    return None


def download_comment_attachment(file_url: str, project_key: str,
                                 work_item_id: str, output_path: str) -> bool:
    """下载评论中的附件文件。

    流程:
    1. 通过 mcporter get_download_url 获取签名 URL
    2. 用 X-Meego-File-Sign header 下载文件

    Args:
        file_url: 评论中的文件 URL (来自 file_url 字段或 content 中的下载链接)
        project_key: 飞书项目 key
        work_item_id: 缺陷 ID
        output_path: 输出文件路径

    Returns:
        是否下载成功
    """
    try:
        url_info = _get_download_url_via_mcporter(project_key, work_item_id, file_url)
        if not url_info:
            print(f"    ⚠ 无法获取评论附件下载 URL: {os.path.basename(output_path)}")
            return False

        download_url = url_info['download_url']
        sign = url_info['sign']

        resp = requests.get(download_url, headers={"X-Meego-File-Sign": sign},
                            timeout=120, stream=True)
        content_type = resp.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            try:
                error_data = resp.json()
                print(f"    ⚠ {os.path.basename(output_path)}: 错误响应 {error_data}")
            except Exception:
                print(f"    ⚠ {os.path.basename(output_path)}: JSON 错误响应")
            return False

        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"    ✗ 评论附件下载失败 {os.path.basename(output_path)}: {e}")
        return False


def extract_file_urls_from_comments(comments: List[Dict]) -> List[Dict]:
    """从评论列表中提取可下载的文件 URL。

    两个来源:
    1. comment['file_url'] 字段
    2. comment['content'] 中嵌入的 ![](https://project.feishu.cn/goapi/...) URL

    返回: [{'file_url': str, 'comment_index': int, 'comment_id': str, 'created_at': str}]
    """
    results = []
    seen_urls = set()
    url_pattern = re.compile(
        r"https://project\.feishu\.cn/goapi/(?:v5/platform/file/stream/download|v1/tos/file/meego-business/checklist)/[^\s)\"'\]]+"
    )

    for idx, comment in enumerate(comments):
        # 来源 A: file_url 字段
        file_url = comment.get('file_url', '').strip()
        if file_url and file_url not in seen_urls:
            seen_urls.add(file_url)
            results.append({
                'file_url': file_url,
                'source': 'file_url_field',
                'comment_index': idx,
                'comment_id': comment.get('comment_id', ''),
                'created_at': comment.get('created_at', ''),
                'creator': comment.get('creator', ''),
            })

        # 来源 B: content 中嵌入的 URL
        content = comment.get('content', '')
        if content:
            urls = url_pattern.findall(content)
            for url in urls:
                # Clean URL: remove trailing punctuation that may have been captured
                url = url.rstrip(')。')
                if url not in seen_urls:
                    seen_urls.add(url)
                    results.append({
                        'file_url': url,
                        'source': 'content_embedded',
                        'comment_index': idx,
                        'comment_id': comment.get('comment_id', ''),
                        'created_at': comment.get('created_at', ''),
                        'creator': comment.get('creator', ''),
                    })

    return results


def download_file(project_key: str, user_key: str, token: str,
                  work_item_type_key: str, work_item_id: str,
                  file_uuid: str, output_path: str) -> bool:
    """下载单个附件文件"""
    url = f"https://project.feishu.cn/open_api/{project_key}/work_item/{work_item_type_key}/{work_item_id}/file/download"
    headers = {
        "X-Plugin-Token": token,
        "X-User-Key": user_key,
    }
    data = {"uuid": file_uuid}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=120, stream=True)
        content_type = resp.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            error_data = resp.json()
            print(f"    ⚠ {os.path.basename(output_path)}: 错误响应 {error_data}")
            return False
        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"    ✗ 下载失败 {os.path.basename(output_path)}: {e}")
        return False


def is_log_file(filename: str) -> bool:
    """判断是否为日志文件"""
    ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ''
    # 排除已知二进制/归档/图片/多媒体格式
    excluded_extensions = {'apk', 'ipa', 'exe', 'bin', 'dat', 'so', 'dll', 'dylib',
                           'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'ico',
                           'mp4', 'mp3', 'wav', 'avi', 'mkv',
                           # 压缩格式由 is_archive_file 单独处理
                           'zip', 'tar', 'gz', 'tgz', 'bz2', 'tbz2', 'xz', 'txz',
                           '7z', 'rar', 'jar', 'war'}
    if ext in excluded_extensions:
        return False
    log_extensions = {'log', 'txt', 'html', 'json', 'xml', 'csv'}
    log_patterns = ['log', 'logcat', 'trace', 'dump', 'crash', 'tombstone']
    if ext in log_extensions:
        return True
    name_lower = filename.lower()
    return any(p in name_lower for p in log_patterns)


def is_archive_file(filename: str) -> bool:
    """判断是否为压缩/归档文件（需要解压后读取）"""
    name_lower = filename.lower()
    # 双扩展名: .tar.gz, .tar.bz2, .tar.xz, .tgz, .tbz2, .txz
    tar_extensions = ('.tar.gz', '.tar.bz2', '.tar.xz', '.tgz', '.tbz2', '.txz')
    if any(name_lower.endswith(e) for e in tar_extensions):
        return True
    # 单扩展名: .zip, .gz, .7z, .rar
    ext = name_lower.rsplit('.', 1)[-1] if '.' in filename else ''
    return ext in {'zip', 'gz', '7z', 'rar'}


def _build_archive_tree(extract_dir: str, archive_name: str) -> str:
    """Build a directory tree string for extracted archive contents.

    Returns a tree-formatted string like:
    archive.zip/
    ├── logs/
    │   ├── crash.log
    │   └── system.log
    └── config.txt
    """
    if not os.path.isdir(extract_dir):
        return ""

    tree_lines = [f"📦 {archive_name}/"]
    for root, dirs, files in os.walk(extract_dir):
        # Sort for deterministic output
        dirs.sort()
        files.sort()
        rel = os.path.relpath(root, extract_dir)
        prefix = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        depth = 0 if rel == "." else len(rel.split(os.sep))
        indent = "│   " * (depth - 1) + ("├── " if depth > 0 else "")
        for d in dirs:
            tree_lines.append(f"    {'│   ' * (depth - 1) if depth > 0 else ''}{'├── ' if depth > 0 else ''}{d}/")
        for i, f in enumerate(files):
            is_last = (i == len(files) - 1) and not dirs
            connector = "└── " if is_last else "├── "
            if depth == 0:
                # Root-level files
                tree_lines.insert(-len([f for d in dirs for _ in [1]]) - (1 if files else 0) + 1,
                                  f"    {connector}{f}" if not any(l.endswith("/" + f) for l in tree_lines) else "")
            else:
                sub_indent = "│   " * (depth - 1) + "    "
                tree_lines.append(f"{sub_indent}{connector}{f}")

    # Simplify: just use a flat listing if tree is too complex
    all_files = []
    for root, dirs, files in os.walk(extract_dir):
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(root, f), extract_dir)
            all_files.append(rel.replace(os.sep, "/"))

    if not all_files:
        return f"📦 {archive_name}/ (空)"

    # Build a clean tree with directories
    lines = [f"📦 {archive_name}/"]
    tree_nodes = {}
    for fpath in all_files:
        parts = fpath.split("/")
        node = tree_nodes
        for part in parts[:-1]:
            if part not in node:
                node[part] = {"__files__": []}
            node = node[part]
            if "__files__" not in node:
                node["__files__"] = []
        node.setdefault("__files__", []).append(parts[-1])

    def _render_tree(node, prefix="", is_last=True):
        items = sorted(k for k in node.keys() if k != "__files__")
        files = sorted(node.get("__files__", []))
        result = []
        all_items = items + ["__files__"] if files else items
        for idx, item in enumerate(all_items):
            is_last_item = (idx == len(all_items) - 1)
            connector = "└── " if is_last_item else "├── "
            if item == "__files__":
                for fi, fname in enumerate(files):
                    f_last = (fi == len(files) - 1) and is_last_item
                    f_connector = "└── " if f_last else "├── "
                    result.append(f"{prefix}{f_connector}{fname}")
            else:
                result.append(f"{prefix}{connector}{item}/")
                child_prefix = prefix + ("    " if is_last_item else "│   ")
                result.extend(_render_tree(node[item], child_prefix, is_last_item))
        return result

    lines.extend(_render_tree(tree_nodes))
    return "\n".join(lines)


def extract_archive(file_path: str, extract_dir: str) -> tuple:
    """解压归档文件，返回 (提取出的文件路径列表, 目录树字符串)"""
    extracted = []
    archive_tree = ""
    name_lower = os.path.basename(file_path).lower()

    try:
        # --- ZIP ---
        if name_lower.endswith('.zip'):
            import zipfile
            with zipfile.ZipFile(file_path, 'r') as zf:
                zf.extractall(extract_dir)
                for name in zf.namelist():
                    full = os.path.join(extract_dir, name)
                    if os.path.isfile(full):
                        extracted.append(full)

        # --- tar.* / tgz / tbz2 / txz ---
        elif any(name_lower.endswith(e) for e in ('.tar.gz', '.tar.bz2', '.tar.xz',
                                                   '.tgz', '.tbz2', '.txz', '.tar')):
            import tarfile
            with tarfile.open(file_path, 'r:*') as tf:
                tf.extractall(extract_dir)
                for m in tf.getmembers():
                    if m.isfile():
                        extracted.append(os.path.join(extract_dir, m.name))

        # --- standalone .gz ---
        elif name_lower.endswith('.gz'):
            import gzip
            import shutil
            out_path = file_path[:-3]  # remove .gz
            out_name = os.path.basename(out_path)
            with gzip.open(file_path, 'rb') as f_in, open(out_path, 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
            if os.path.isfile(out_path):
                extracted.append(out_path)

        # --- 7z / rar (require 7z command) ---
        elif name_lower.endswith(('.7z', '.rar')):
            import subprocess
            result = subprocess.run(['7z', 'x', f'-o{extract_dir}', file_path],
                                    capture_output=True, timeout=60)
            if result.returncode == 0:
                for root, dirs, files in os.walk(extract_dir):
                    for fn in files:
                        extracted.append(os.path.join(root, fn))
            else:
                print(f"    ⚠ 7z 解压失败: {result.stderr.decode(errors='replace')[:200]}")
                return extracted, archive_tree

    except Exception as e:
        print(f"    ⚠ 解压失败: {e}")

    # Build tree after extraction
    archive_tree = _build_archive_tree(extract_dir, os.path.basename(file_path))
    return extracted, archive_tree


# ========== 智能日志截取：只提取错误/崩溃附近的关键区域 ==========

# 严重性级别：用于定位出问题行
_SEVERITY_PATTERNS = [
    # (pattern, weight) — weight 越高越重要
    # FATAL / CRITICAL 级别
    (re.compile(r'^(?:.*\s)?(F|A)\s', re.MULTILINE), 10),
    (re.compile(r'\[F\]|\[A\]|\[FATAL\]|\[CRITICAL\]|\bFATAL\b', re.IGNORECASE), 10),
    # Android signal / crash 关键词
    (re.compile(r'\b(Signal \d+|SIGSEGV|SIGABRT|SIGBUS|SIGKILL|segmentation fault|panic)\b', re.IGNORECASE), 15),
    # 崩溃/中止关键词
    (re.compile(r'\b(crash|abort|force.*stop|process.*died|app.*crashed|system.*server.*died|'
                r'kill.*process|uncaught.*exception|Not Responding|ANR)\b', re.IGNORECASE), 12),
    # 堆栈/转储
    (re.compile(r'\b(Backtrace|Tombstone|traceback|Abort message|Build fingerprint|'\
                r'\*\*\* \*\*\* |pid:|tid:)\b'), 8),
    # FORTIFY 错误
    (re.compile(r'FORTIFY:'), 12),
    # Java crash
    (re.compile(r'(Caused by:|at \S+\.\w+\([^)]+\)\s*$)'), 6),
    # ERROR 级别
    (re.compile(r'^\S+\s+(E)\s+', re.MULTILINE), 3),
    (re.compile(r'\[E\]|\[ERROR\]'), 3),
    # Exception 类
    (re.compile(r'\b(NullPointerException|IllegalStateException|RuntimeException|'
                r'IndexOutOfBounds|MemoryError|OverflowError)\b'), 8),
]

# 上下文窗口（问题行前后各取 N 行）
_CONTEXT_WINDOW = 40
# 输出上限（每个文件）
_MAX_CHARS = 12000


def smart_extract_log_content(file_path: str, max_chars: int = _MAX_CHARS) -> str:
    """智能截取日志：提取错误/崩溃附近的关键上下文，而非简单截断头尾。

    策略:
    1. 逐行扫描，为每行计算"严重性得分"（命中关键模式 = 高分）
    2. 对有得分的行，扩展取前后 CONTEXT_WINDOW 行作为候选片段
    3. 按得分排序，按优先级合并重叠片段，直到达到 max_chars
    4. 若无任何错误行，退化为头+尾策略
    
    输出格式: 每行前加 "# FILE: filename | LINE: N | " 前缀，供分析器溯源

    Args:
        file_path: 日志文件路径
        max_chars: 最大输出字符数

    Returns:
        智能截取的日志文本（每行带 FILE/LINE 溯源前缀）
    """
    try:
        with open(file_path, 'rb') as f:
            header = f.read(1024)
        # Check for binary content: null bytes are a strong indicator
        if b'\x00' in header:
            return f"[二进制文件，跳过]"
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except Exception:
        return "[无法读取文件]"

    total_lines = len(lines)
    total_chars = sum(len(l) for l in lines)

    if total_chars <= max_chars:
        return ''.join(lines)

    # --- Step 1: 扫描所有行的严重性得分 ---
    line_scores = [0] * total_lines
    problem_lines = set()

    for i, line in enumerate(lines):
        for pattern, weight in _SEVERITY_PATTERNS:
            if pattern.search(line):
                line_scores[i] += weight
                problem_lines.add(i)

    if not problem_lines:
        # 无错误行 -> 头+尾策略
        return _head_tail_extract(''.join(lines), max_chars, total_lines, total_chars, file_path)

    # --- Step 2: 对每个问题行生成候选片段 ---
    candidates = []
    for idx in sorted(problem_lines):
        score = line_scores[idx]
        start = max(0, idx - _CONTEXT_WINDOW)
        end = min(total_lines, idx + _CONTEXT_WINDOW + 1)
        candidates.append((start, end, score))

    # --- Step 3: 按得分排序，合并重叠片段 ---
    candidates.sort(key=lambda x: -x[2])

    used_ranges = []
    selected_ranges = []

    for start, end, score in candidates:
        if score <= 0:
            continue

        # 检查重叠
        merged_start, merged_end = start, end
        overlap = False
        new_ranges = []
        for u_start, u_end in used_ranges:
            if merged_start <= u_end and merged_end >= u_start:
                merged_start = min(merged_start, u_start)
                merged_end = max(merged_end, u_end)
                overlap = True
            else:
                new_ranges.append((u_start, u_end))

        if overlap:
            used_ranges = new_ranges

        used_ranges.append((merged_start, merged_end))

        # 计算这个片段的字符数
        frag_chars = sum(len(lines[i]) for i in range(merged_start, merged_end))
        selected_ranges.append((merged_start, merged_end, frag_chars, score))

    # --- Step 4: 按优先级累积片段，直到 max_chars ---
    # 重新按得分排序 selected_ranges
    selected_ranges.sort(key=lambda x: -x[3])

    fragments = []
    remaining = max_chars

    for frag_start, frag_end, frag_chars, frag_score in selected_ranges:
        fragment = ''.join(lines[frag_start:frag_end])

        if frag_chars <= remaining:
            fragments.append((frag_start, frag_end, fragment, frag_score))
            remaining -= frag_chars
        else:
            # 截断这个片段
            half = remaining // 2
            fragment_truncated = fragment[:half] + '\n...[中间省略]...\n' + fragment[-half:]
            fragments.append((frag_start, frag_end, fragment_truncated, frag_score))
            remaining = 0
            break

    if not fragments:
        return _head_tail_extract(''.join(lines), max_chars, total_lines, total_chars, file_path)

    # --- Step 5: 按行号排序输出（保持时间顺序）---
    fragments.sort(key=lambda x: x[0])

    # 合并相邻片段（间隔 < 5 行的直接连接）
    merged_fragments = [fragments[0]]
    for frag in fragments[1:]:
        prev_start, prev_end = merged_fragments[-1][0], merged_fragments[-1][1]
        if frag[0] - prev_end < 5:
            # 直接合并
            new_end = max(prev_end, frag[1])
            new_fragment = ''.join(lines[merged_fragments[-1][0]:new_end])
            merged_fragments[-1] = (merged_fragments[-1][0], new_end, new_fragment, frag[3])
        else:
            # 添加分隔符
            new_fragment = merged_fragments[-1][2] + '\n\n--- [省略 %d 行] ---\n\n' % (frag[0] - prev_end) + frag[2]
            merged_fragments[-1] = (merged_fragments[-1][0], frag[1], new_fragment, merged_fragments[-1][3])

    # 添加 FILE/LINE 溯源前缀到每个合并后的片段
    fname = os.path.basename(file_path)
    tagged_fragments = []
    for frag_start, frag_end, frag_text, frag_score in merged_fragments:
        tagged_lines = []
        for ln_idx in range(frag_start, frag_end):
            if ln_idx < total_lines:
                orig_line = lines[ln_idx].rstrip('\n')
                tagged_lines.append(f"# FILE: {fname} | LINE: {ln_idx + 1} | {orig_line}")
        tagged_text = '\n'.join(tagged_lines)
        tagged_fragments.append((frag_start, frag_end, tagged_text, frag_score))

    result = '\n'.join(f[2] for f in tagged_fragments)

    # 添加元信息头部
    header = (
        f"[智能截取: 原始 {total_lines:,} 行 / {total_chars:,} 字符, "
        f"提取 {len(problem_lines)} 个错误行附近的上下文 -> {len(result):,} 字符]\n\n"
    )

    return header + result


def _head_tail_extract(content: str, max_chars: int, total_lines: int, total_chars: int, file_path: str = None) -> str:
    """无错误行时的退化策略：取头部摘要+尾部完整内容。"""
    if len(content) <= max_chars:
        return content

    head = max_chars // 4
    tail = max_chars - head
    header = f"[日志过大({total_lines:,} 行 / {total_chars:,} 字符)，未检测到明显错误模式，取头尾摘要]\n\n"
    lines = content.split('\n')
    
    fname = os.path.basename(file_path) if file_path else 'unknown'
    tagged_lines = []
    for i, line in enumerate(lines):
        tagged_lines.append(f"# FILE: {fname} | LINE: {i + 1} | {line.rstrip(chr(10))}")
    tagged_content = '\n'.join(tagged_lines)
    
    if len(tagged_content) <= max_chars:
        result = tagged_content
    else:
        head_chars = max_chars // 4
        tail_chars = max_chars - head_chars
        result = tagged_content[:head_chars] + '\n\n...[中间省略]...\n\n' + tagged_content[-tail_chars:]
    
    return header + result


def fetch_comment_urls_via_mcporter(bug_id: str, project_key: str) -> List[Dict]:
    """Use MCP JSON-RPC to fetch comments with full content (preserves ![](url)).

    The Direct API /comments endpoint strips image markdown to [图片], but MCP
    list_workitem_comments preserves the full content with embedded file URLs.
    Replaces old mcporter CLI approach.

    Returns formatted comments list suitable for extract_file_urls_from_comments.
    """
    try:
        from mcp_client import mcp_call
        result, err = mcp_call(
            "list_workitem_comments",
            {"project_key": project_key, "work_item_id": str(bug_id), "page_num": 1},
            timeout=30
        )
        if err or not result:
            return []

        raw_comments = result.get('comments', []) if isinstance(result, dict) else []

        formatted = []
        for c in raw_comments:
            content = c.get('content', '').strip()
            if not content:
                continue
            formatted.append({
                'content': content,
                'file_url': c.get('file_url', ''),
                'comment_id': c.get('comment_id', ''),
                'created_at': c.get('created_at', ''),
                'creator': c.get('creator', {}),
            })
        return formatted
    except Exception:
        return []


def fetch_live_bug_data(bug_id: str, project_key: str, plugin_token: str, user_key: str) -> Dict:
    """Fetch latest comments and attachment metadata from Feishu Direct API.
    
    Returns real-time data that may not be in the local cache.
    Includes file_url fields from comments for attachment download.
    """
    result = {
        'comments': [],
        'attachments': [],
        'error': None,
    }
    
    # --- Fetch comments ---
    comments_url = f"https://project.feishu.cn/open_api/{project_key}/work_item/issue/{bug_id}/comments"
    headers = {
        "X-Plugin-Token": plugin_token,
        "X-User-Key": user_key,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(comments_url, headers=headers, timeout=30)
        resp_text = resp.text.strip()
        # Feishu API may return concatenated JSON or trailing whitespace.
        # Use json.JSONDecoder().raw_decode() to parse the first JSON object
        # and safely ignore any trailing content.
        decoder = json.JSONDecoder()
        data, _ = decoder.raw_decode(resp_text)
        # API returns data as a list of comments directly (not nested in another dict)
        if isinstance(data, dict):
            raw_comments = data.get('data', [])
            if isinstance(raw_comments, dict):
                # Some APIs wrap comments in a nested structure
                raw_comments = raw_comments.get('list', raw_comments.get('comments', []))
        elif isinstance(data, list):
            raw_comments = data
        else:
            raw_comments = []
        for c in raw_comments:
            content = c.get('content', '').strip()
            if not content:
                continue
            result['comments'].append({
                'content': content,
                'created_at': c.get('created_at', ''),
                'author': c.get('author', c.get('author_name', '')),
                # Preserve file_url for comment attachment download
                'file_url': c.get('file_url', ''),
                'comment_id': c.get('comment_id', ''),
                'creator': c.get('creator', ''),
            })
    except json.JSONDecodeError as e:
        result['error'] = f"评论获取失败: JSON解析错误 {e}"
    except Exception as e:
        result['error'] = f"评论获取失败: {e}"
    
    # --- Fetch attachments via query API (already implemented in get_attachment_uuids) ---
    try:
        result['attachments'] = get_attachment_uuids(project_key, user_key, plugin_token, bug_id)
    except Exception:
        pass
    
    return result


def download_bug_attachments(bug_id: str, comments: List[Dict] = None) -> Dict:
    """
    下载指定缺陷的所有日志附件，包括正文附件和评论附件。

    Args:
        bug_id: 缺陷 ID
        comments: 评论列表（用于提取评论中的附件 URL）。
                  评论应包含 file_url 字段和 content 字段。
                  如果为 None，则只下载正文附件。

    返回:
    {
        'download_dir': str,        # 下载目录路径
        'downloaded': List[str],    # 成功下载的文件路径
        'failed': List[str],        # 失败的文件名
        'skipped': List[str],       # 跳过的非日志文件
        'log_contents': Dict[str, str],  # 文件名 -> 文件内容（文本）
        'total_found': int,         # 发现的附件总数（正文+评论）
        'comment_attachments': Dict, # 评论附件下载统计
    }
    """
    creds = get_feishu_credentials()
    
    result = {
        'download_dir': '',
        'downloaded': [],
        'failed': [],
        'skipped': [],
        'log_contents': {},
        'archive_trees': {},
        'total_found': 0,
        'comment_attachments': {
            'found': 0,
            'downloaded': 0,
            'failed': 0,
            'skipped': 0,
        },
    }
    
    # 检查凭据
    if not creds.get('project_key') or not creds.get('plugin_secret'):
        print("  ⚠ 飞书凭据未配置，跳过附件下载")
        print("  提示: 设置 FEISHU_PROJECT_KEY 和 FEISHU_PLUGIN_SECRET 环境变量")
        return result
    
    print(f"\n📥 正在下载缺陷 {bug_id} 的附件...")
    
    # 获取 token
    token = get_plugin_token(
        creds['project_key'], 
        creds['plugin_id'], 
        creds['plugin_secret']
    )
    if not token:
        print("  ✗ 无法获取飞书 Plugin Token")
        return result
    
    # === Phase 1: Download work item body attachments ===
    files = get_attachment_uuids(
        creds['project_key'], 
        creds['user_key'], 
        token, 
        bug_id
    )
    result['total_found'] = len(files)
    
    if files:
        print(f"  发现 {len(files)} 个正文附件")
    else:
        print("  未发现正文附件")
    
    # 创建下载目录
    download_dir = tempfile.mkdtemp(prefix=f"bug_{bug_id}_attachments_")
    result['download_dir'] = download_dir
    
    # Download body attachment files via MCP get_download_url
    for f in files:
        name = f['name']
        safe_name = re.sub(r'[\\/\\\\:]', '_', name)

        # Skip non-log, non-archive files
        if not is_log_file(name) and not is_archive_file(name):
            result['skipped'].append(name)
            print(f"  ⊘ 跳过非日志: {safe_name}")
            continue

        output_path = os.path.join(download_dir, safe_name)

        # Use MCP get_download_url if we have the attachment URL, fallback to Direct API
        file_url = f.get('url', '')
        if file_url:
            success = download_comment_attachment(
                file_url=file_url,
                project_key=creds['project_key'],
                work_item_id=str(bug_id),
                output_path=output_path,
            )
        else:
            # Fallback: use old Direct API method with uuid
            work_item_type_key = get_work_item_type_key(
                creds['project_key'], 
                creds['user_key'], 
                token, 
                bug_id
            )
            success = download_file(
                creds['project_key'],
                creds['user_key'],
                token,
                work_item_type_key,
                bug_id,
                f['uuid'],
                output_path
            )

        if success:
            result['downloaded'].append(output_path)

            # --- Handle archive files ---
            if is_archive_file(name):
                extract_dir = os.path.join(download_dir, safe_name + "_extracted")
                os.makedirs(extract_dir, exist_ok=True)
                extracted_files, archive_tree = extract_archive(output_path, extract_dir)
                print(f"  ✓ 已下载: {safe_name} (解压出 {len(extracted_files)} 个文件)")

                # Store archive tree for report display
                if archive_tree:
                    result['archive_trees'][safe_name] = archive_tree

                # Process extracted log files
                for ef in extracted_files:
                    ef_name = os.path.relpath(ef, extract_dir)
                    # Only process files that look like logs
                    if is_log_file(ef_name):
                        try:
                            content = smart_extract_log_content(ef)
                            if not isinstance(content, str):
                                print(f"    ⚠️ WARNING: {ef_name} returned {type(content).__name__} instead of str, skipping")
                                continue
                            result['log_contents'][f"{safe_name}/{ef_name}"] = content
                            print(f"    📄 {ef_name} (智能截取 {len(content):,} 字符)")
                        except Exception:
                            result['log_contents'][f"{safe_name}/{ef_name}"] = "[二进制文件，无法读取内容]"
                            print(f"    📄 {ef_name} (二进制)")
                    else:
                        print(f"    ⊘ {ef_name} (非日志，跳过)")
            else:
                # Regular log file
                try:
                    content = smart_extract_log_content(output_path)
                    result['log_contents'][safe_name] = content
                    print(f"  ✓ 已下载: {safe_name} (智能截取 {len(content):,} 字符)")
                except Exception:
                    result['log_contents'][safe_name] = "[二进制文件，无法读取内容]"
                    print(f"  ✓ 已下载: {safe_name} (二进制)")
        else:
            result['failed'].append(safe_name)
    
    # === Phase 2: Download comment attachments ===
    # First try the comments passed in (from Direct API or cache).
    # If they have no file URLs, fall back to mcporter which preserves full ![](url) content.
    all_comment_urls = []
    if comments:
        all_comment_urls = extract_file_urls_from_comments(comments)
    
    # If Direct API comments stripped the ![](url) to [图片], try mcporter
    if not all_comment_urls:
        mcporter_comments = fetch_comment_urls_via_mcporter(bug_id, creds['project_key'])
        if mcporter_comments:
            all_comment_urls = extract_file_urls_from_comments(mcporter_comments)
            if all_comment_urls:
                print(f"  ⚡ mcporter 评论数据源: 找到 {len(all_comment_urls)} 个附件 URL")
    
    result['comment_attachments']['found'] = len(all_comment_urls)
    
    if all_comment_urls:
        if not comments:
            print(f"\n📎 正在扫描评论附件...")
        print(f"  发现 {len(all_comment_urls)} 个评论附件 URL")
            
        for cf in all_comment_urls:
            file_url = cf['file_url']
            comment_idx = cf['comment_index']
            source = cf['source']
            
            # Determine filename from URL
            url_basename = os.path.basename(file_url.split('?')[0])
            # Use a descriptive name based on comment index and URL
            safe_comment_name = f"comment_{comment_idx}_{url_basename}"
            safe_comment_name = re.sub(r'[\\/\\\\:]', '_', safe_comment_name)
            output_path = os.path.join(download_dir, safe_comment_name)
            
            print(f"  📎 评论#{comment_idx} ({source}): {url_basename[:60]}")
            
            success = download_comment_attachment(
                file_url=file_url,
                project_key=creds['project_key'],
                work_item_id=str(bug_id),
                output_path=output_path,
            )
            
            if success:
                result['comment_attachments']['downloaded'] += 1
                result['downloaded'].append(output_path)
                
                # Process the downloaded comment attachment
                if is_archive_file(url_basename):
                    extract_dir = os.path.join(download_dir, safe_comment_name + "_extracted")
                    os.makedirs(extract_dir, exist_ok=True)
                    extracted_files, archive_tree = extract_archive(output_path, extract_dir)
                    print(f"    ✓ 解压出 {len(extracted_files)} 个文件")
                    if archive_tree:
                        result['archive_trees'][safe_comment_name] = archive_tree
                    for ef in extracted_files:
                        ef_name = os.path.relpath(ef, extract_dir)
                        if is_log_file(ef_name):
                            try:
                                content = smart_extract_log_content(ef)
                                if isinstance(content, str):
                                    result['log_contents'][f"comment_{comment_idx}/{ef_name}"] = content
                                    print(f"    📄 {ef_name} (智能截取 {len(content):,} 字符)")
                            except Exception:
                                pass
                elif is_log_file(url_basename):
                    try:
                        content = smart_extract_log_content(output_path)
                        if isinstance(content, str):
                            result['log_contents'][f"comment_{comment_idx}/{url_basename}"] = content
                            print(f"    📄 智能截取 {len(content):,} 字符")
                        else:
                            print(f"    📄 二进制文件")
                    except Exception:
                        pass
                else:
                    # Unknown type — try smart_extract anyway
                    try:
                        content = smart_extract_log_content(output_path)
                        if isinstance(content, str) and len(content) > 100:
                            result['log_contents'][f"comment_{comment_idx}/{url_basename}"] = content
                            print(f"    📄 提取 {len(content):,} 字符")
                    except Exception:
                        pass
            else:
                result['comment_attachments']['failed'] += 1
    else:
        print("  评论中未发现可下载附件")
    
    # 总结
    ca = result['comment_attachments']
    print(f"\n  📊 下载总结:")
    print(f"    正文附件: 成功 {len(result['downloaded']) - ca['downloaded']}, "
          f"失败 {len(result['failed'])}, "
          f"跳过 {len(result['skipped'])}")
    if comments:
        print(f"    评论附件: 发现 {ca['found']}, "
              f"成功 {ca['downloaded']}, "
              f"失败 {ca['failed']}")
    print(f"    总计: {len(result['downloaded'])} 个文件已下载, "
          f"{len(result['log_contents'])} 个日志已分析")
    
    return result
