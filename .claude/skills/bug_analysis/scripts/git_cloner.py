#!/usr/bin/env python3
"""
临时 Git 克隆模块 - 按需克隆仓库到临时目录，用完自动清理
支持: GitHub, GitLab, Bitbucket 等，支持 token 认证
"""

import os
import re
import subprocess
import tempfile
import shutil
from typing import List, Dict, Optional
from pathlib import Path


class GitCloner:
    """按需克隆 Git 仓库到临时目录"""

    # Token 占位符
    TOKEN_PLACEHOLDER = '{token}'

    # 默认深度克隆深度
    DEFAULT_DEPTH = 1

    def __init__(self, temp_dir: str = None):
        self.temp_dir = temp_dir or tempfile.mkdtemp(prefix='git_clones_')
        os.makedirs(self.temp_dir, exist_ok=True)
        self.cloned: Dict[str, str] = {}  # name -> local_path

    def clone_repo(
        self,
        url: str,
        name: str = None,
        depth: int = None,
        branch: str = None,
        token: str = None,
    ) -> Optional[str]:
        """
        克隆单个仓库到临时目录

        Args:
            url: Git URL，支持 {token} 占位符
            name: 仓库别名（用于本地目录名），默认从 URL 提取
            depth: 克隆深度，默认 1（浅克隆）
            branch: 分支名，默认主分支
            token: 认证 token，自动替换 {token} 占位符

        Returns:
            克隆后的本地目录路径，失败返回 None
        """
        if url in self.cloned:
            return self.cloned[url]

        # 解析 token
        if token and self.TOKEN_PLACEHOLDER in url:
            actual_url = url.replace(self.TOKEN_PLACEHOLDER, token)
        else:
            actual_url = url

        # 提取仓库名
        if not name:
            name = self._extract_repo_name(url)

        clone_path = os.path.join(self.temp_dir, name)
        os.makedirs(clone_path, exist_ok=True)

        # 构建 git clone 命令
        cmd = ['git', 'clone']
        if depth:
            cmd.extend(['--depth', str(depth)])
        elif self.DEFAULT_DEPTH:
            cmd.extend(['--depth', str(self.DEFAULT_DEPTH)])
        if branch:
            cmd.extend(['--branch', branch])
        cmd.extend([actual_url, clone_path])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                self.cloned[url] = clone_path
                return clone_path
            else:
                return None
        except Exception:
            return None

    def clone_multiple(
        self,
        repos: List[Dict],
        token: str = None,
    ) -> Dict[str, str]:
        """
        批量克隆多个仓库

        Args:
            repos: 仓库列表，每项含 url, name(可选), branch(可选), depth(可选)
            token: 认证 token

        Returns:
            {name: local_path} 字典
        """
        results = {}
        for repo in repos:
            url = repo['url']
            name = repo.get('name')
            depth = repo.get('depth')
            branch = repo.get('branch')
            path = self.clone_repo(url, name, depth, branch, token)
            if path:
                results[name or url] = path
        return results

    def get_repo_paths(self) -> Dict[str, str]:
        """返回所有已克隆仓库的路径 {name: path}"""
        return dict(self.cloned)

    def cleanup(self):
        """清理所有克隆的仓库"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            try:
                shutil.rmtree(self.temp_dir)
            except Exception:
                pass
        self.cloned.clear()

    @staticmethod
    def _extract_repo_name(url: str) -> str:
        """从 Git URL 提取仓库名"""
        # 处理 SSH (gerrit 风格): ssh://user@host:port/path/to/repo
        if url.startswith('ssh://'):
            # ssh://zyshan@gerrit.nreal.ai:29418/ars45/code
            path_part = url.split('/', 3)[-1] if '/' in url.split('://', 1)[1] else ''
            if path_part:
                return path_part.replace('/', '-')
        # 处理 SSH: git@github.com:user/repo.git
        if url.startswith('git@'):
            match = re.search(r':([^/]+)/([^/]+?)(?:\.git)?$', url)
            if match:
                return f"{match.group(1)}-{match.group(2)}"
        # 处理 HTTPS: https://github.com/user/repo.git
        match = re.search(r'/([^/]+)/([^/]+?)(?:\.git)?$', url)
        if match:
            return f"{match.group(1)}-{match.group(2)}"
        # Fallback: 用 URL 的 hash
        import hashlib
        return f"repo-{hashlib.md5(url.encode()).hexdigest()[:8]}"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cleanup()
