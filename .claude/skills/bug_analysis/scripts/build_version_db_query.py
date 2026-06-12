#!/usr/bin/env python3
"""
飞书构建版本数据库查询器

集成到 bug-analyzer 管道：
- 从 bug 描述/日志/评论中提取 PILOT/NRSDK 版本号
- 查询本地 build_version_index.json 获取该版本对应的所有子仓库 commit
- 将版本-仓库映射信息注入到 LLM prompt，辅助精确定位代码
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# 数据库路径
DB_DIR = Path(__file__).parent.parent / "data"
DB_FILE = DB_DIR / "build_version_index.json"


class BuildVersionDB:
    """飞书构建版本数据库查询器"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_FILE
        self._db = None

    def _load_db(self) -> Dict:
        """懒加载数据库"""
        if self._db is not None:
            return self._db

        if not self.db_path.exists():
            print(f"[version_db] 数据库不存在: {self.db_path}")
            self._db = {}
            return self._db

        with open(self.db_path, 'r', encoding='utf-8') as f:
            self._db = json.load(f)

        print(f"[version_db] 加载数据库: {len(self._db)} 条记录")
        return self._db

    def lookup_version(self, version: str, project_name: str = None) -> Optional[Dict]:
        """
        通过版本号查询构建记录

        Args:
            version: PILOT 或 NRSDK 版本号，如 1.9.0.20260521095835
            project_name: 项目名称（可选），如 xrlinux, android

        Returns:
            匹配的记录，包含版本信息、commit hash、项目列表等
        """
        db = self._load_db()
        if not db:
            return None

        # 精确匹配
        if project_name:
            key = f"{version}_{project_name}"
            if key in db:
                return db[key]

        # 模糊匹配：遍历所有 key
        matches = []
        for key, record in db.items():
            if record.get('version') == version:
                matches.append(record)

        if matches:
            # 优先返回 project_name 匹配的
            if project_name:
                for m in matches:
                    if m.get('project_name') == project_name:
                        return m
            return matches[0]

        # === 找不到时，自动从飞书拉取最新一轮消息 ===
        new_records = self._fetch_latest_from_feishu()
        if new_records > 0:
            # 重新加载数据库后再次查询
            self._db = None  # 清除缓存
            db = self._load_db()
            print(f"[version_db] 刷新后再次查询版本 {version}")
            
            # 再次尝试匹配
            if project_name:
                key = f"{version}_{project_name}"
                if key in db:
                    return db[key]
            for key, record in db.items():
                if record.get('version') == version:
                    return record

        return None

    def lookup_by_project_time(self, project_name: str, build_time: str) -> Optional[Dict]:
        """
        通过项目名+构建时间查询

        Args:
            project_name: 项目名
            build_time: 构建时间戳 (14 位数字)

        Returns:
            最近的匹配记录
        """
        db = self._load_db()
        if not db:
            return None

        # 找到该项目在时间上最接近的记录
        best_match = None
        best_diff = float('inf')

        for key, record in db.items():
            if record.get('project_name') != project_name:
                continue
            if not record.get('build_time'):
                continue

            try:
                db_time = int(record['build_time'])
                query_time = int(build_time)
                diff = abs(db_time - query_time)
                if diff < best_diff:
                    best_diff = diff
                    best_match = record
            except (ValueError, TypeError):
                continue

        return best_match

    def _fetch_latest_from_feishu(self) -> int:
        """
        从飞书群聊拉取最新一轮消息，解析新增构建记录，更新数据库。
        
        Returns:
            新增的记录数
        """
        import requests as req
        from pathlib import Path as P
        import subprocess as sp
        import xml.etree.ElementTree as ET
        
        # 从环境变量读取飞书凭证
        app_id = os.environ.get('FEISHU_APP_ID', '')
        app_secret = os.environ.get('FEISHU_APP_SECRET', '')
        if not app_id or not app_secret:
            print("[version_db] 缺少 FEISHU_APP_ID/FEISHU_APP_SECRET 环境变量，无法拉取飞书消息")
            return 0
        
        # 获取 token
        try:
            token_resp = req.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=10
            )
            access_token = token_resp.json()['tenant_access_token']
        except Exception as e:
            print(f"[version_db] 获取 token 失败: {e}")
            return 0
        
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        chat_id = os.getenv("FEISHU_CHAT_ID")
        if not chat_id:
            print("[version_db] 警告: 未设置 FEISHU_CHAT_ID，无法自动拉取飞书群聊构建记录")
            return 0
        
        # 拉取最新消息（第一页就是最新的 50 条）
        try:
            resp = req.get(
                "https://open.feishu.cn/open-apis/im/v1/messages",
                headers=headers,
                params={"container_id_type": "chat", "container_id": chat_id, "page_size": "50"},
                timeout=15
            )
            data = resp.json()
            items = data.get('data', {}).get('items', [])
        except Exception as e:
            print(f"[version_db] 拉取消息失败: {e}")
            return 0
        
        # 解析构建记录
        def extract_text(elements):
            texts = []
            for elem_list in elements:
                if isinstance(elem_list, list):
                    for item in elem_list:
                        if isinstance(item, dict) and item.get('text'):
                            texts.append(item['text'])
            return '\n'.join(texts).strip()
        
        def parse_record(item):
            body = item.get('body', {})
            content = json.loads(body.get('content', '{}'))
            elements = content.get('elements', [])
            text = extract_text(elements)
            
            project_match = re.search(r'项目名称[：:]\s*\n?\s*(\S+)', text)
            project_name = project_match.group(1) if project_match else ''
            if not project_name:
                return None
            
            version = ''
            version_type = ''
            
            # 格式1: 标准卡片 - NRSDK Version / PILOT Version
            nrsdk_match = re.search(r'NRSDK Version[：:]\s*([^\n]+)', text)
            if nrsdk_match:
                version = nrsdk_match.group(1).strip()
                version_type = 'NRSDK'
            pilot_match = re.search(r'PILOT Version[：:]\s*([^\n]+)', text)
            if pilot_match:
                version = pilot_match.group(1).strip()
                version_type = 'PILOT'
            
            # 格式2: Jenkins 构建通知 - 从 AAR 行提取版本
            # 例: > AAR: com.xreal.nrsdk:[...]:2.1.2.202310191657-android-[...]-SNAPSHOT
            if not version:
                aar_match = re.search(r'AAR[：:]\s*[^\n]*:(\d+\.\d+\.\d+\.\d{12,14})', text)
                if aar_match:
                    version = aar_match.group(1).strip()
                    version_type = 'NRSDK'
            
            build_time = ''
            if version:
                time_match = re.search(r'(\d{14})', version)
                if time_match:
                    build_time = time_match.group(1)
                else:
                    # 12 位时间戳也接受（补 00）
                    time_match = re.search(r'(\d{12})', version)
                    if time_match:
                        build_time = time_match.group(1) + '00'
            
            return {
                'project_name': project_name,
                'version': version,
                'version_type': version_type,
                'build_time': build_time
            }
        
        # 加载现有数据库
        db = self._load_db()
        existing_keys = set(db.keys())
        
        new_count = 0
        interactive_msgs = [m for m in items if m.get('msg_type') == 'interactive']
        
        for item in interactive_msgs:
            record = parse_record(item)
            if not record or not record['version'] or not record['build_time']:
                continue
            
            key = f"{record['version']}_{record['project_name']}"
            if key in existing_keys:
                continue
            
            # 查找对应 commit
            commit_hash = None
            manifest_name = None
            projects_list = []
            
            try:
                # 查找 commit
                nrsdkrepo = DB_DIR.parent / "repositories" / "clones" / "nrsdkrepo"
                if (nrsdkrepo / ".git").exists():
                    result = sp.run(
                        ['git', 'log', '--all', '--format=%H|%ai|%s', '--date=iso'],
                        capture_output=True, text=True, timeout=30, cwd=nrsdkrepo
                    )
                    build_dt = datetime.strptime(record['build_time'], '%Y%m%d%H%M%S')
                    
                    for line in result.stdout.strip().split('\n'):
                        if not line:
                            continue
                        parts = line.split('|', 2)
                        if len(parts) >= 3:
                            date_str = ' '.join(parts[1].split()[:2])
                            commit_dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                            if commit_dt <= build_dt:
                                commit_hash = parts[0][:8]
                                
                                # 查找 manifest
                                manifest_map = {
                                    'xrlinux': ['xrlinux.xml'],
                                    'android': ['android.xml'],
                                    'windows': ['windows.xml'],
                                    'macos': ['macos.xml'],
                                    'linux': ['linux.xml'],
                                    'release': ['release.xml'],
                                    'release_xrlinux': ['release_xrlinux.xml'],
                                    'xrlinux_google': ['xrlinux_google.xml'],
                                    'xrlinux_aura_test': ['xrlinux_aura_test.xml'],
                                }
                                candidates = manifest_map.get(record['project_name'], 
                                    [f"{record['project_name']}.xml"])
                                
                                for mf in candidates:
                                    r = sp.run(
                                        ['git', 'ls-tree', commit_hash, mf],
                                        capture_output=True, text=True, timeout=5, cwd=nrsdkrepo
                                    )
                                    if r.returncode == 0 and mf in r.stdout:
                                        manifest_name = mf
                                        
                                        # 解析 manifest
                                        r2 = sp.run(
                                            ['git', 'show', f'{commit_hash}:{mf}'],
                                            capture_output=True, text=True, timeout=10, cwd=nrsdkrepo
                                        )
                                        if r2.returncode == 0:
                                            try:
                                                root = ET.fromstring(r2.stdout)
                                                default_elem = root.find('default')
                                                default_rev = default_elem.get('revision', 'main') if default_elem else 'main'
                                                for proj_elem in root.findall('project'):
                                                    pname = proj_elem.get('name', '')
                                                    ppath = proj_elem.get('path', pname)
                                                    prev = proj_elem.get('revision', default_rev)
                                                    is_sha = len(prev) == 40 and all(c in '0123456789abcdef' for c in prev.lower())
                                                    projects_list.append({
                                                        'name': pname, 'path': ppath,
                                                        'revision': prev[:8] if is_sha else prev,
                                                        'is_commit': is_sha
                                                    })
                                            except ET.ParseError:
                                                pass
                                        break
                                break
                
                db[key] = {
                    'version': record['version'],
                    'build_time': record['build_time'],
                    'project_name': record['project_name'],
                    'match_type': 'version_matched',
                    'commit_hash': commit_hash,
                    'manifest_name': manifest_name,
                    'projects': projects_list,
                    'auto_fetched': True
                }
                new_count += 1
            except Exception as e:
                print(f"[version_db] 处理新记录异常: {e}")
                continue
        
        if new_count > 0:
            db_file = DB_DIR / "build_version_index.json"
            with open(db_file, 'w', encoding='utf-8') as f:
                json.dump(db, f, ensure_ascii=False, indent=2)
            print(f"[version_db] 从飞书拉取并新增 {new_count} 条记录")
        
        return new_count

    def extract_versions_from_text(self, text: str) -> List[Tuple[str, str]]:
        """
        从文本中提取版本号

        Returns:
            List of (version, version_type) tuples
        """
        results = []

        # PILOT Version: 1.9.0.20260521095835
        pilot_matches = re.findall(r'PILOT\s+Version[：:]\s*([\d.]+)', text, re.I)
        for v in pilot_matches:
            results.append((v, 'PILOT'))

        # NRSDK Version: 3.1.2.20260521120646
        nrsdk_matches = re.findall(r'NRSDK\s+Version[：:]\s*([\d.]+)', text, re.I)
        for v in nrsdk_matches:
            results.append((v, 'NRSDK'))

        # 通用版本号模式：x.x.x.yyyyMMddHHmmss
        # 如 1.9.0.20260521095835
        version_matches = re.findall(r'\b(\d+\.\d+\.\d+\.\d{14})\b', text)
        for v in version_matches:
            if not any(v == existing[0] for existing in results):
                results.append((v, 'unknown'))

        return results

    def extract_project_from_text(self, text: str) -> Optional[str]:
        """
        从文本中提取可能的项目名称
        匹配常见的飞书项目名称
        """
        # 常见项目名模式
        project_patterns = [
            r'xrlinux_aura_test', r'xrlinux_google', r'xrlinux_test',
            r'release_xrlinux_glory', r'release_xrlinux_myglassses',
            r'release_helen_temp', r'release_xrlinux',
            r'release_mac', r'release_win', r'sightful_win',
            r'hotfix_xrlinux', r'hotfix_archive',
            r'evapro', r'nio', r'xrsdk',
        ]

        for pattern in project_patterns:
            if re.search(pattern, text, re.I):
                return pattern

        # 单字项目名
        single_projects = ['xrlinux', 'android', 'windows', 'macos', 'linux', 'ios',
                          'temp', 'test', 'hotfix', 'release']
        for proj in single_projects:
            # 匹配 "项目: xrlinux" 或 "xrlinux 构建" 等模式
            if re.search(rf'(?:项目|build|构建)[：:\s]+{proj}\b', text, re.I):
                return proj

        return None

    def extract_timestamp_from_text(self, text: str) -> Optional[str]:
        """
        从文本中提取时间戳，用于模糊匹配
        支持格式：
        - 2026-05-21 14:30:00
        - 20260521143000
        - 2026/05/21 14:30
        """
        # 完整时间戳：2026-05-21 14:30:00
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})', text)
        if match:
            return f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}{match.group(5)}{match.group(6)}"

        # 14 位纯数字时间戳
        match = re.search(r'\b(\d{14})\b', text)
        if match:
            try:
                datetime.strptime(match.group(1), '%Y%m%d%H%M%S')
                return match.group(1)
            except ValueError:
                pass

        # 短日期时间：2026/05/21 14:30
        match = re.search(r'(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})', text)
        if match:
            return f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}{match.group(5)}00"

        return None

    def fuzzy_match(self, text: str, known_project: str = None) -> Dict:
        """
        模糊匹配：当文本中没有精确版本号时，
        通过时间戳+项目名近似定位到最近的构建记录

        Args:
            text: bug 描述、日志或评论文本
            known_project: 已知的项目名称

        Returns:
            {
                'match_type': 'fuzzy_time' | 'fuzzy_project' | 'none',
                'record': {...} or None,
                'reason': '...'
            }
        """
        # 1. 尝试从文本提取项目名
        project = known_project or self.extract_project_from_text(text)

        # 2. 尝试从文本提取时间戳
        timestamp = self.extract_timestamp_from_text(text)

        if project and timestamp:
            record = self.lookup_by_project_time(project, timestamp)
            if record:
                return {
                    'match_type': 'fuzzy_time_project',
                    'record': record,
                    'reason': f'通过项目 "{project}" + 时间 "{timestamp}" 近似匹配'
                }

        if timestamp and not project:
            # 只有时间，没有项目
            # 搜索该时间点附近的所有构建记录
            db = self._load_db()
            closest = None
            closest_diff = float('inf')

            for key, record in db.items():
                if not record.get('build_time'):
                    continue
                try:
                    diff = abs(int(record['build_time']) - int(timestamp))
                    if diff < closest_diff:
                        closest_diff = diff
                        closest = record
                except (ValueError, TypeError):
                    continue

            if closest and closest_diff < 8640000:  # 容差 1 天
                return {
                    'match_type': 'fuzzy_time_only',
                    'record': closest,
                    'reason': f'通过时间 "{timestamp}" 近似匹配（最近构建: {closest.get("project_name")}）'
                }

        if project and not timestamp:
            # 只有项目名，返回该项目最新的构建
            db = self._load_db()
            latest = None
            latest_time = 0

            for key, record in db.items():
                if record.get('project_name') != project:
                    continue
                bt = record.get('build_time', '')
                if bt and bt.isdigit():
                    time_val = int(bt)
                    if time_val > latest_time:
                        latest_time = time_val
                        latest = record

            if latest:
                return {
                    'match_type': 'fuzzy_project_only',
                    'record': latest,
                    'reason': f'项目 "{project}" 最新的构建（{latest.get("version")}）'
                }

        return {
            'match_type': 'none',
            'record': None,
            'reason': '无法从文本中提取足够信息进行模糊匹配'
        }

    def query_for_bug(self, bug_description: str = "", log_content: str = "",
                     comments: List[Dict] = None, known_project: str = None) -> Dict:
        """
        为 bug 分析查询版本-仓库映射

        Args:
            bug_description: bug 标题/描述
            log_content: 日志内容
            comments: 评论列表
            known_project: 已知的项目名称

        Returns:
            {
                'versions_found': [...],
                'repo_mappings': [...],
                'summary': '...'
            }
        """
        # 合并所有文本提取版本号
        all_text_parts = []
        if bug_description:
            all_text_parts.append(bug_description)
        if log_content:
            # 只取前 10000 字符用于版本提取
            all_text_parts.append(log_content[:10000])
        if comments:
            for c in comments:
                if isinstance(c, dict):
                    all_text_parts.append(str(c.get('content', '')))
                else:
                    all_text_parts.append(str(c))

        all_text = '\n'.join(all_text_parts)

        # 提取版本号
        versions = self.extract_versions_from_text(all_text)

        result = {
            'versions_found': [],
            'repo_mappings': [],
            'summary': ''
        }

        if not versions:
            # 没有版本号，尝试模糊匹配
            fuzzy_result = self.fuzzy_match(all_text, known_project)
            if fuzzy_result['record']:
                record = fuzzy_result['record']
                result['versions_found'].append({
                    'version': record.get('version', ''),
                    'version_type': 'unknown',
                    'found': True,
                    'match_type': fuzzy_result['match_type'],
                    'project_name': record.get('project_name', ''),
                    'build_time': record.get('build_time', ''),
                    'commit_hash': record.get('commit_hash', ''),
                    'manifest_name': record.get('manifest_name', ''),
                    'repo_count': len(record.get('projects', [])),
                    'projects': record.get('projects', [])
                })
                result['repo_mappings'].append({
                    'version': record.get('version', ''),
                    'project': record.get('project_name', ''),
                    'commit': record.get('commit_hash', ''),
                    'manifest': record.get('manifest_name', ''),
                    'repos': record.get('projects', [])
                })
                result['summary'] = f'通过模糊匹配找到 1 个版本: {fuzzy_result["reason"]}'
            else:
                result['summary'] = f'未从 bug 数据中检测到版本号，且模糊匹配失败: {fuzzy_result["reason"]}'
            return result

        # 去重：同一版本号只查询一次
        seen_versions = set()
        unique_versions = []
        for v in versions:
            if v[0] not in seen_versions:
                seen_versions.add(v[0])
                unique_versions.append(v)

        # 查询每个版本（去重后）
        for version, version_type in unique_versions:
            record = self.lookup_version(version, known_project)

            version_info = {
                'version': version,
                'version_type': version_type,
                'found': record is not None
            }

            if record:
                version_info['project_name'] = record.get('project_name', '')
                version_info['build_time'] = record.get('build_time', '')
                version_info['match_type'] = record.get('match_type', '')
                version_info['commit_hash'] = record.get('commit_hash', '')
                version_info['manifest_name'] = record.get('manifest_name', '')
                version_info['repo_count'] = len(record.get('projects', []))
                version_info['projects'] = record.get('projects', [])

                result['repo_mappings'].append({
                    'version': version,
                    'project': record.get('project_name', ''),
                    'commit': record.get('commit_hash', ''),
                    'manifest': record.get('manifest_name', ''),
                    'repos': record.get('projects', [])
                })

            result['versions_found'].append(version_info)

        # 如果没有找到任何版本，尝试模糊匹配
        if not result['versions_found']:
            fuzzy_result = self.fuzzy_match(all_text, known_project)
            if fuzzy_result['record']:
                record = fuzzy_result['record']
                result['versions_found'].append({
                    'version': record.get('version', ''),
                    'version_type': 'unknown',
                    'found': True,
                    'match_type': fuzzy_result['match_type'],
                    'project_name': record.get('project_name', ''),
                    'build_time': record.get('build_time', ''),
                    'commit_hash': record.get('commit_hash', ''),
                    'manifest_name': record.get('manifest_name', ''),
                    'repo_count': len(record.get('projects', [])),
                    'projects': record.get('projects', [])
                })
                result['repo_mappings'].append({
                    'version': record.get('version', ''),
                    'project': record.get('project_name', ''),
                    'commit': record.get('commit_hash', ''),
                    'manifest': record.get('manifest_name', ''),
                    'repos': record.get('projects', [])
                })
                result['summary'] = f'通过模糊匹配找到 1 个版本: {fuzzy_result["reason"]}'
            else:
                result['summary'] = f'未找到版本信息且模糊匹配失败: {fuzzy_result["reason"]}'

        # 生成摘要
        found_count = sum(1 for v in result['versions_found'] if v['found'])
        total_count = len(result['versions_found'])
        result['summary'] = f'检测到 {total_count} 个版本号，其中 {found_count} 个找到对应的仓库映射'

        return result


def format_version_repo_prompt(query_result: Dict) -> str:
    """
    将版本-仓库查询结果格式化为 LLM prompt 片段

    Args:
        query_result: query_for_bug() 的返回值

    Returns:
        格式化的 prompt 字符串
    """
    lines = []

    if not query_result.get('versions_found'):
        return ''

    lines.append("## 飞书构建版本-仓库映射")
    lines.append(query_result.get('summary', ''))
    lines.append('')

    for mapping in query_result.get('repo_mappings', []):
        lines.append(f"### 版本: {mapping['version']} (项目: {mapping['project']})")
        lines.append(f"- 构建 commit: {mapping['commit']}")
        lines.append(f"- manifest: {mapping['manifest']}")
        lines.append(f"- 子仓库数: {len(mapping['repos'])}")
        lines.append('')

        # 列出关键仓库
        key_repos = ['nreal-ai/dove', 'nreal-ai/leopard', 'nreal-ai/framework',
                     'nreal-ai/heron', 'nreal-ai/project', 'nreal-ai/NRSDKPack']

        lines.append("| 仓库 | 路径 | 版本 |")
        lines.append("|------|------|------|")

        for repo in mapping['repos']:
            name = repo.get('name', '')
            # 只显示关键仓库，避免输出过多
            if name in key_repos or len(mapping['repos']) <= 30:
                revision = repo.get('revision', '')
                path = repo.get('path', '')
                is_commit = repo.get('is_commit', False)

                rev_display = f"{revision[:8]}..." if is_commit and len(revision) > 8 else revision
                lines.append(f"| {name} | {path} | {rev_display} |")

        lines.append('')

    return '\n'.join(lines)


def main():
    """测试"""
    db = BuildVersionDB()

    # 测试 1：通过版本号查询
    print("测试 1: 版本号查询")
    record = db.lookup_version('1.9.0.20260509164744', 'xrlinux_google')
    if record:
        print(f"  找到: {record['project_name']} commit={record['commit_hash']}")
        print(f"  仓库数: {len(record.get('projects', []))}")
    else:
        print("  未找到")

    # 测试 2：从文本提取版本号
    print("\n测试 2: 文本版本提取")
    test_text = """
    Bug 描述: PILOT Version: 1.9.0.20260509164744 出现崩溃
    日志: NRSDK Version: 3.1.2.20260521120646 连接失败
    """
    versions = db.extract_versions_from_text(test_text)
    print(f"  提取到: {versions}")

    # 测试 3：完整的 bug 查询
    print("\n测试 3: 完整 bug 查询")
    result = db.query_for_bug(
        bug_description="PILOT Version: 1.9.0.20260408153357 在 xrlinux_aura_test 上崩溃",
        log_content="Dove Version: 1.9.0.20260408153357\nSIGSEGV at 0x00000000",
        known_project='xrlinux_aura_test'
    )
    print(f"  摘要: {result['summary']}")
    for m in result['repo_mappings']:
        repos = m.get('repos', [])
        print(f"  映射: {m['version']} → {m['project']} commit={m['commit']} repos={len(repos)}")

    # 测试 4：prompt 格式化
    print("\n测试 4: Prompt 格式化")
    result = db.query_for_bug(
        bug_description="PILOT Version: 1.9.0.20260408153357 崩溃",
        known_project='xrlinux_aura_test'
    )
    prompt = format_version_repo_prompt(result)
    print(prompt[:1000])


if __name__ == '__main__':
    main()
