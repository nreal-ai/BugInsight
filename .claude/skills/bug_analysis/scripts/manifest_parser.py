#!/usr/bin/env python3
"""
Manifest Parser - 从 repo manifest XML 文件解析仓库列表。

支持的 manifest 格式:
- xrlinux*.xml (眼镜端仓库清单)
- android.xml (主机端仓库清单)
- release.xrl (眼镜端发布版本清单)

Manifest 格式示例 (repo manifest / Google repo style):
    <manifest>
      <remote name="origin" fetch="https://github.com/nreal-ai/" />
      <project name="dove" path="dove" remote="origin" revision="main" />
      <project name="leopard" path="leopard" remote="origin" revision="main" />
    </manifest>

用法:
    from manifest_parser import parse_manifest, get_platform_repos
    repos = get_platform_repos('glasses')  # 从 xrlinux*.xml 解析
    repos = get_platform_repos('host')     # 从 android.xml 解析
"""

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Manifest 文件搜索路径 (按优先级排序)
MANIFEST_SEARCH_PATHS = {
    'glasses': [
        # xrlinux 相关 manifest
        "xrlinux.xml",
        "xrlinux_main.xml",
        "xrlinux_manifest.xml",
        "xrlinux-release.xml",
        "release.xrl",
        "manifest_xrlinux.xml",
    ],
    'host': [
        # Android 相关 manifest
        "android.xml",
        "android_manifest.xml",
        "manifest_android.xml",
        "release.xml",
        "default.xml",  # repo 默认 manifest
    ],
}

# 如果找不到 XML，使用硬编码的默认仓库列表作为 fallback
DEFAULT_REPOS = {
    'glasses': [
        'nrealUtil',
        'heron',
        'xr_codec',
        'nrsdkrepo',
        'dove',
        'leopard',
        'sparrow',
        'framework',
    ],
    'host': [
        'project',
        'framework',
        'dove',
        'leopard',
        'sparrow',
    ],
}


def find_manifest_file(platform: str, search_roots: List[str] = None) -> Optional[str]:
    """
    在搜索路径中查找 manifest 文件。
    
    Args:
        platform: 'glasses' 或 'host'
        search_roots: 要搜索的根目录列表 (默认: 当前工作目录 + skill 根目录)
    
    Returns:
        str: manifest 文件的绝对路径，如果找不到则返回 None
    """
    if search_roots is None:
        search_roots = [
            os.getcwd(),
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            os.path.expanduser("~/.openviking/workspace/feishu-bugs"),
        ]
    
    candidate_names = MANIFEST_SEARCH_PATHS.get(platform, [])
    
    for root in search_roots:
        if not os.path.isdir(root):
            continue
        # 直接搜索目录
        for name in candidate_names:
            filepath = os.path.join(root, name)
            if os.path.isfile(filepath):
                return filepath
        
        # 递归搜索子目录 (限制深度为2)
        for name in candidate_names:
            for dirpath, dirnames, files in os.walk(root, topdown=True):
                # 限制深度
                depth = dirpath.replace(root, '').count(os.sep)
                if depth > 2:
                    dirnames[:] = []
                    continue
                if name in files:
                    return os.path.join(dirpath, name)
    
    return None


def parse_manifest(filepath: str) -> List[Dict[str, str]]:
    """
    解析 repo manifest XML 文件，提取仓库列表。
    
    Args:
        filepath: manifest XML 文件路径
    
    Returns:
        list: 每个元素包含 {name, path, remote, revision, url}
    """
    repos = []
    
    if not os.path.isfile(filepath):
        return repos
    
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        
        # 提取 remote 信息
        remotes = {}
        for remote_elem in root.findall('.//remote'):
            name = remote_elem.get('name', '')
            fetch_url = remote_elem.get('fetch', '')
            if name:
                remotes[name] = fetch_url
        
        # 提取 default remote
        default_remote = ""
        default_revision = "main"
        default_elem = root.find('default')
        if default_elem is not None:
            default_remote = default_elem.get('remote', '')
            default_revision = default_elem.get('revision', 'main')
        
        # 提取 project 元素
        for project_elem in root.findall('.//project'):
            name = project_elem.get('name', '')
            path = project_elem.get('path', name)
            remote = project_elem.get('remote', default_remote)
            revision = project_elem.get('revision', default_revision)
            
            # 构建完整 URL
            fetch_url = remotes.get(remote, '')
            full_url = f"{fetch_url}/{name}" if fetch_url and not fetch_url.endswith('/') else f"{fetch_url}{name}"
            
            if name:
                repos.append({
                    'name': name,
                    'path': path,
                    'remote': remote,
                    'revision': revision,
                    'url': full_url,
                    'source': filepath,
                })
    
    except ET.ParseError as e:
        # XML 解析失败，尝试用正则提取
        repos = _parse_manifest_regex(filepath)
    except Exception as e:
        print(f"[manifest-parser] Failed to parse {filepath}: {e}")
    
    return repos


def _parse_manifest_regex(filepath: str) -> List[Dict[str, str]]:
    """
    使用正则表达式解析 manifest 文件 (XML 解析失败时的 fallback)。
    """
    repos = []
    
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # 匹配 <project name="..." path="..." revision="..." />
        project_pattern = re.compile(
            r'<project\s+[^>]*?name="([^"]+)"'
            r'(?:\s+path="([^"]+)")?'
            r'(?:\s+remote="([^"]+)")?'
            r'(?:\s+revision="([^"]+)")?'
            r'[^>]*?>',
            re.DOTALL
        )
        
        # 提取 remote 信息
        remote_pattern = re.compile(
            r'<remote\s+[^>]*?name="([^"]+)"'
            r'(?:\s+fetch="([^"]+)")?'
            r'[^>]*?>',
            re.DOTALL
        )
        
        remotes = {}
        for m in remote_pattern.finditer(content):
            name, fetch = m.group(1), m.group(2) or ""
            remotes[name] = fetch
        
        for m in project_pattern.finditer(content):
            name = m.group(1)
            path = m.group(2) or name
            remote = m.group(3) or ""
            revision = m.group(4) or "main"
            
            fetch_url = remotes.get(remote, "")
            full_url = f"{fetch_url}/{name}" if fetch_url else ""
            
            repos.append({
                'name': name,
                'path': path,
                'remote': remote,
                'revision': revision,
                'url': full_url,
                'source': filepath,
            })
    
    except Exception as e:
        print(f"[manifest-parser] Regex parse failed for {filepath}: {e}")
    
    return repos


def get_platform_repos(
    platform: str,
    search_roots: List[str] = None,
    use_fallback: bool = True
) -> List[Dict[str, str]]:
    """
    获取平台对应的仓库列表。
    
    查找顺序:
    1. 搜索 manifest XML 文件并解析
    2. 如果找不到，使用硬编码的默认列表 (仅当 use_fallback=True)
    
    Args:
        platform: 'glasses' 或 'host'
        search_roots: 搜索根目录列表
        use_fallback: 如果找不到 manifest 是否使用默认列表
    
    Returns:
        list: 仓库列表，每项含 {name, path, revision, url, source}
    """
    if platform not in ('glasses', 'host'):
        return []
    
    # 1. 尝试找到并解析 manifest 文件
    manifest_path = find_manifest_file(platform, search_roots)
    if manifest_path:
        repos = parse_manifest(manifest_path)
        if repos:
            return repos
    
    # 2. Fallback: 使用默认列表
    if use_fallback:
        default_names = DEFAULT_REPOS.get(platform, [])
        return [
            {
                'name': name,
                'path': name,
                'remote': 'origin',
                'revision': 'main',
                'url': f"https://github.com/nreal-ai/{name}.git",
                'source': 'fallback (no manifest found)',
            }
            for name in default_names
        ]
    
    return []


def get_repo_names_for_platform(
    platform: str,
    search_roots: List[str] = None
) -> List[str]:
    """
    获取平台对应的仓库名称列表 (简化版，只返回名称)。
    
    Args:
        platform: 'glasses' 或 'host'
        search_roots: 搜索根目录列表
    
    Returns:
        list: 仓库名称列表
    """
    repos = get_platform_repos(platform, search_roots)
    return [r['name'] for r in repos]


def detect_manifest_from_log(log_content: str) -> Optional[str]:
    """
    从日志内容中推断可能使用的 manifest 文件名。
    
    Args:
        log_content: 日志内容
    
    Returns:
        str: 推测的 manifest 文件名，或 None
    """
    if not log_content:
        return None
    
    content_lower = log_content.lower()
    
    # xrlinux 相关关键词
    xrlinux_keywords = ['xrlinux', 'rockchip', 'rk3568', 'rk3588', 'dove', 
                       'leopard', 'heron', 'nrsdk', 'xr_codec', 'nrealUtil']
    # android 相关关键词
    android_keywords = ['logcat', 'android.os', 'android.app', 'ActivityManager',
                       'ANR', 'com.nreal', 'com.android', 'gradle']
    
    xrlinux_score = sum(1 for kw in xrlinux_keywords if kw.lower() in content_lower)
    android_score = sum(1 for kw in android_keywords if kw.lower() in content_lower)
    
    if xrlinux_score > android_score:
        return 'xrlinux.xml'
    elif android_score > xrlinux_score:
        return 'android.xml'
    else:
        return None


if __name__ == "__main__":
    import json
    
    print("=== Glasses platform repos ===")
    glasses_repos = get_platform_repos('glasses')
    for repo in glasses_repos:
        print(f"  {repo['name']} (from: {repo['source']})")
    
    print("\n=== Host platform repos ===")
    host_repos = get_platform_repos('host')
    for repo in host_repos:
        print(f"  {repo['name']} (from: {repo['source']})")
    
    print("\n=== Manifest detection test ===")
    test_log = """
[2024-01-01] kernel: dove_display initialized
signal 11 in libdove.so
    """
    detected = detect_manifest_from_log(test_log)
    print(f"Detected manifest: {detected}")
