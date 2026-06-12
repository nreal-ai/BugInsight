#!/usr/bin/env python3
"""
版本-仓库关联解析器

功能：
1. 从飞书群聊构建卡片中解析 PILOT/NRSDK 版本号
2. 通过版本号/构建时间在 nrsdkrepo git 历史中定位对应 manifest 快照
3. 解析 manifest 提取各子仓库的 commit SHA 或 branch
4. 建立版本号 → 仓库 commit 的完整映射

使用场景：
- Bug 报告中提到 PILOT/NRSDK 版本号，通过此工具找到该版本对应的所有子仓库 commit
- 辅助 bug-analyzer 精确定位到正确的代码版本进行分析
"""

import subprocess
import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class VersionRepoMapper:
    """版本号到仓库 commit 的映射器"""

    def __init__(self, nrsdkrepo_path: Optional[Path] = None):
        self.nrsdkrepo_path = nrsdkrepo_path or Path(__file__).parent.parent / "repositories" / "clones" / "nrsdkrepo"
        self._commits_cache = None

    def get_all_commits(self) -> List[Dict]:
        """获取 nrsdkrepo 的所有 commit 历史"""
        if self._commits_cache is not None:
            return self._commits_cache

        if not (self.nrsdkrepo_path / ".git").exists():
            raise FileNotFoundError(f"nrsdkrepo not found at {self.nrsdkrepo_path}")

        result = subprocess.run(
            ['git', 'log', '--all', '--format=%H|%h|%ai|%s', '--date=iso'],
            capture_output=True, text=True, timeout=60,
            cwd=self.nrsdkrepo_path
        )

        commits = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split('|', 3)
            if len(parts) >= 4:
                commits.append({
                    'hash': parts[0],
                    'short_hash': parts[1],
                    'date_str': parts[2],
                    'message': parts[3],
                    'date': self._parse_git_date(parts[2])
                })

        commits.sort(key=lambda c: c['date'], reverse=True)
        self._commits_cache = commits
        return commits

    def _parse_git_date(self, date_str: str) -> datetime:
        """解析 git 日期格式：2026-05-21 14:46:12 +0800"""
        date_part = ' '.join(date_str.split()[:2])
        return datetime.strptime(date_part, '%Y-%m-%d %H:%M:%S')

    def find_commits_by_version(self, version: str) -> List[Dict]:
        """通过版本号精确查找 commit"""
        commits = self.get_all_commits()
        return [c for c in commits if version in c['message']]

    def find_commits_by_time(self, build_time: str, tolerance_minutes: int = 120) -> List[Dict]:
        """
        通过构建时间查找对应时间点的 commit
        
        Args:
            build_time: 格式如 20260521095835 或 2026-05-21 09:58:35
            tolerance_minutes: 时间容差（分钟），默认 2 小时
        """
        # 解析构建时间
        if len(build_time) == 14 and build_time.isdigit():
            build_dt = datetime.strptime(build_time, '%Y%m%d%H%M%S')
        else:
            build_dt = datetime.strptime(build_time, '%Y-%m-%d %H:%M:%S')

        commits = self.get_all_commits()
        results = []
        tolerance = timedelta(minutes=tolerance_minutes)

        for commit in commits:
            # 找构建时间之前（或附近）的 commit
            time_diff = commit['date'] - build_dt
            if -tolerance <= time_diff <= tolerance:
                results.append({
                    **commit,
                    'time_diff_minutes': time_diff.total_seconds() / 60
                })

        results.sort(key=lambda c: abs(c['time_diff_minutes']))
        return results

    def get_manifest_at_commit(self, commit_hash: str, manifest_name: str) -> Optional[Dict]:
        """获取指定 commit 时某个 manifest 文件的内容并解析"""
        result = subprocess.run(
            ['git', 'show', f'{commit_hash}:{manifest_name}'],
            capture_output=True, text=True, timeout=10,
            cwd=self.nrsdkrepo_path
        )

        if result.returncode != 0:
            return None

        return self._parse_manifest_xml(result.stdout, manifest_name)

    def _parse_manifest_xml(self, xml_content: str, manifest_name: str) -> Dict:
        """解析 manifest XML，提取项目信息"""
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError:
            return {'error': 'XML parse error', 'name': manifest_name}

        # 获取默认 remote 和 revision
        default_elem = root.find('default')
        default_remote = default_elem.get('remote', 'github') if default_elem else 'github'
        default_revision = default_elem.get('revision', 'main') if default_elem else 'main'

        # 获取 remote 信息
        remotes = {}
        for remote_elem in root.findall('remote'):
            name = remote_elem.get('name')
            if name:
                remotes[name] = {
                    'fetch': remote_elem.get('fetch', ''),
                    'alias': remote_elem.get('alias', name)
                }

        # 获取所有 project
        projects = []
        for project_elem in root.findall('project'):
            name = project_elem.get('name', '')
            path = project_elem.get('path', name)
            revision = project_elem.get('revision', default_revision)
            clone_depth = project_elem.get('clone-depth', '')

            # 解析远程 URL
            remote_name = project_elem.get('remote', default_remote)
            remote_info = remotes.get(remote_name, {})
            fetch_url = remote_info.get('fetch', '')

            # 生成完整 URL
            if fetch_url:
                full_url = f"{fetch_url}/{name}"
            else:
                full_url = name

            projects.append({
                'name': name,
                'path': path,
                'revision': revision,  # commit SHA、branch 或 tag
                'remote': remote_name,
                'clone_depth': clone_depth,
                'fetch_url': fetch_url,
                'full_url': full_url
            })

        return {
            'name': manifest_name,
            'default_remote': default_remote,
            'default_revision': default_revision,
            'projects': projects,
            'remotes': remotes
        }

    def resolve_version_to_repos(self, version: str, build_time: str, project_name: str) -> Dict:
        """
        解析版本号到仓库 commit 的完整映射

        Args:
            version: PILOT 或 NRSDK 版本号
            build_time: 构建时间戳
            project_name: 项目名称（如 xrlinux, android）

        Returns:
            包含版本信息、commit 和仓库映射的字典
        """
        result = {
            'version': version,
            'build_time': build_time,
            'project_name': project_name,
            'status': 'unknown',
            'commits': [],
            'manifests': []
        }

        # 1. 尝试通过版本号精确匹配
        version_commits = self.find_commits_by_version(version)
        if version_commits:
            result['status'] = 'version_matched'
            result['commits'] = version_commits[:5]

            # 获取这些 commit 对应的 manifest
            for commit in version_commits[:3]:
                manifest_name = self._find_manifest_for_project(project_name)
                if manifest_name:
                    manifest_data = self.get_manifest_at_commit(commit['hash'], manifest_name)
                    if manifest_data:
                        manifest_data['commit'] = commit
                        result['manifests'].append(manifest_data)
        else:
            # 2. 通过时间匹配（容差 2 小时）
            time_commits = self.find_commits_by_time(build_time, tolerance_minutes=120)
            if time_commits:
                result['status'] = 'time_matched'
                result['commits'] = time_commits[:5]

                # 获取时间匹配 commit 对应的 manifest
                for commit in time_commits[:3]:
                    manifest_name = self._find_manifest_for_project(project_name)
                    if manifest_name:
                        manifest_data = self.get_manifest_at_commit(commit['hash'], manifest_name)
                        if manifest_data:
                            manifest_data['commit'] = commit
                            result['manifests'].append(manifest_data)
            else:
                result['status'] = 'no_match'

        return result

    def _find_manifest_for_project(self, project_name: str) -> Optional[str]:
        """根据项目名称找到对应的 manifest 文件"""
        manifest_map = {
            'xrlinux': ['xrlinux.xml', 'default_xrlinux.xml'],
            'android': ['android.xml', 'default.xml'],
            'windows': ['windows.xml'],
            'macos': ['macos.xml'],
            'linux': ['linux.xml'],
            'ios': ['ios.xml'],
            'release': ['release.xml'],
            'release_xrlinux': ['release_xrlinux.xml'],
            'temp': ['temp.xml'],
            'test': ['test.xml'],
            'hotfix': ['hotfix.xml'],
            'xrlinux_google': ['xrlinux_google.xml'],
            'xrlinux_aura_test': ['xrlinux_aura_test.xml'],
            'xrlinux_test': ['xrlinux_test.xml'],
            'release_xrlinux_glory': ['release_xrlinux_glory.xml'],
            'release_xrlinux_myglassses': ['release_xrlinux_myglassses.xml'],
            'release_helen_temp': ['release_helen_temp.xml'],
            'release_mac': ['release_mac.xml'],
            'release_win': ['release_win.xml'],
            'sightful_win': ['sightful_win.xml'],
        }

        if project_name in manifest_map:
            for manifest in manifest_map[project_name]:
                # 检查 manifest 是否存在（通过 git ls-tree）
                result = subprocess.run(
                    ['git', 'ls-tree', 'HEAD', manifest],
                    capture_output=True, text=True, timeout=5,
                    cwd=self.nrsdkrepo_path
                )
                if result.returncode == 0 and manifest in result.stdout:
                    return manifest
        return None

    def get_current_manifest(self, manifest_name: str) -> Optional[Dict]:
        """获取当前 HEAD 的 manifest 内容"""
        result = subprocess.run(
            ['git', 'show', f'HEAD:{manifest_name}'],
            capture_output=True, text=True, timeout=10,
            cwd=self.nrsdkrepo_path
        )

        if result.returncode != 0:
            return None

        return self._parse_manifest_xml(result.stdout, manifest_name)


def parse_feishu_build_record(card_text: str) -> Optional[Dict]:
    """
    从飞书卡片消息文本中解析构建记录

    Returns:
        {
            'project_name': 'xrlinux',
            'version': '1.9.0.20260521095835',
            'version_type': 'PILOT',
            'build_time': '20260521095835',
            'result': 'SUCCESS',
            'publisher': '尹慧',
            'build_url': 'https://...'
        }
    """
    text = card_text.strip()

    # 提取项目名称
    project_match = re.search(r'项目名称[：:]\s*\n?\s*(\S+)', text)
    if not project_match:
        return None
    project_name = project_match.group(1)

    # 提取发布者
    publisher_match = re.search(r'发布者[：:]\s*\n?\s*([^\n]+)', text)
    publisher = publisher_match.group(1).strip() if publisher_match else ''

    # 提取结果
    result_match = re.search(r'\b(SUCCESS|FAILURE)\b', text)
    result = result_match.group(1) if result_match else ''

    # 提取版本号
    version = ''
    version_type = ''

    nrsdk_match = re.search(r'NRSDK Version[：:]\s*([^\n]+)', text)
    if nrsdk_match:
        version = nrsdk_match.group(1).strip()
        version_type = 'NRSDK'

    pilot_match = re.search(r'PILOT Version[：:]\s*([^\n]+)', text)
    if pilot_match:
        version = pilot_match.group(1).strip()
        version_type = 'PILOT'

    # 提取构建 URL
    url_match = re.search(r'(https://jenkins-nrsdk\.xreal\.work[^\s]+)', text)
    build_url = url_match.group(1) if url_match else ''

    # 从版本号中提取时间戳
    build_time = ''
    if version:
        time_match = re.search(r'(\d{14})', version)
        if time_match:
            build_time = time_match.group(1)

    return {
        'project_name': project_name,
        'version': version,
        'version_type': version_type,
        'build_time': build_time,
        'result': result,
        'publisher': publisher,
        'build_url': build_url
    }


def format_repo_mapping_report(result: Dict) -> str:
    """格式化版本-仓库映射报告"""
    lines = []
    lines.append(f"{'=' * 60}")
    lines.append(f"版本: {result['version']}")
    lines.append(f"项目: {result['project_name']}")
    lines.append(f"构建时间: {result['build_time']}")
    lines.append(f"匹配状态: {result['status']}")
    lines.append(f"{'=' * 60}")

    if result['commits']:
        lines.append(f"\n找到的 commits ({len(result['commits'])}):")
        for i, commit in enumerate(result['commits'][:3], 1):
            lines.append(f"\n  Commit {i}:")
            lines.append(f"    Hash: {commit['short_hash']}")
            lines.append(f"    时间: {commit['date_str']}")
            lines.append(f"    消息: {commit['message']}")
            if 'time_diff_minutes' in commit:
                lines.append(f"    时间差: {commit['time_diff_minutes']:.1f} 分钟")

    for j, manifest in enumerate(result['manifests'], 1):
        lines.append(f"\n{'-' * 40}")
        lines.append(f"Manifest {j}: {manifest['name']}")
        lines.append(f"默认分支: {manifest.get('default_revision', 'N/A')}")
        lines.append(f"项目数: {len(manifest.get('projects', []))}")
        lines.append(f"{'-' * 40}")

        for project in manifest.get('projects', []):
            revision = project['revision']
            # 标记是 commit SHA 还是 branch
            if len(revision) == 40 and all(c in '0123456789abcdef' for c in revision.lower()):
                rev_display = f"{revision[:8]}... (commit SHA)"
            else:
                rev_display = f"{revision} (branch/tag)"

            lines.append(f"  {project['name']}:")
            lines.append(f"    路径: {project['path']}")
            lines.append(f"    revision: {rev_display}")
            lines.append(f"    URL: {project['full_url']}")

    return '\n'.join(lines)


def main():
    """主函数：演示版本-仓库关联功能"""
    mapper = VersionRepoMapper()

    # 示例：解析已知的构建记录
    example_records = [
        {
            'version': '1.9.0.20260509164744',
            'build_time': '20260509164744',
            'project_name': 'xrlinux_google',
            'version_type': 'PILOT'
        },
        {
            'version': '1.9.0.20260408153357',
            'build_time': '20260408153357',
            'project_name': 'xrlinux_aura_test',
            'version_type': 'PILOT'
        },
    ]

    for record in example_records:
        result = mapper.resolve_version_to_repos(
            version=record['version'],
            build_time=record['build_time'],
            project_name=record['project_name']
        )

        report = format_repo_mapping_report(result)
        print(report)
        print()


if __name__ == '__main__':
    main()
