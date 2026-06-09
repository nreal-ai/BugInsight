#!/usr/bin/env python3
"""
相似缺陷检测模块
支持两种模式:
1. OpenViking 语义搜索 (ov find)
2. 本地 JSON 关键词匹配 (回退)
"""

import json
import os
import re
import subprocess
from typing import List, Dict, Optional
from pathlib import Path


class OpenVikingBugFinder:
    """基于 OpenViking 的语义相似缺陷搜索器"""

    def __init__(self, base_uri: str = "/resources/feishu-bugs"):
        self.base_uri = base_uri
        self.bugs_dir = Path.home() / ".openviking/data/viking/default/feishu-bugs"

    def _is_available(self) -> bool:
        try:
            result = subprocess.run(
                ["ov", "system", "health"],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0 and "true" in result.stdout.lower()
        except Exception:
            return False

    def find_similar(self, query: str, limit: int = 10) -> List[Dict]:
        """语义搜索相似缺陷"""
        if not self._is_available():
            return []

        try:
            result = subprocess.run(
                ["ov", "find", query, "-u", self.base_uri, "-n", str(limit * 3)],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return []

            return self._parse_results(result.stdout, limit)
        except Exception as e:
            print(f"OpenViking 搜索失败: {e}")
            return []

    def _parse_results(self, output: str, limit: int) -> List[Dict]:
        results = []
        for line in output.strip().split("\n"):
            if "context_type" in line or "viking://temp" in line or ".abstract" in line:
                continue

            parts = line.strip().split()
            uri = ""
            score = 0.0
            for part in parts:
                if part.startswith("viking://resources/feishu-bugs/"):
                    uri = part
                try:
                    score = float(part)
                except ValueError:
                    pass

            if not uri:
                continue

            uri_parts = uri.replace("viking://", "").split("/")
            bug_id = ""
            for i, p in enumerate(uri_parts):
                if p == "feishu-bugs" and i + 1 < len(uri_parts):
                    bug_id = uri_parts[i + 1]
                    break

            if not bug_id or not bug_id.isdigit():
                continue

            title, status = self._read_bug_title(bug_id)
            results.append({
                "id": bug_id,
                "title": title[:80],
                "score": round(score, 2),
                "status": status,
                "comments": [],
            })

        seen = set()
        unique_results = []
        for r in sorted(results, key=lambda x: x["score"], reverse=True):
            if r["id"] not in seen:
                seen.add(r["id"])
                unique_results.append(r)
                if len(unique_results) >= limit:
                    break
        return unique_results

    def _read_bug_title(self, bug_id: str) -> tuple:
        md_path = self.bugs_dir / f"{bug_id}.md"
        if not md_path.exists():
            return f"缺陷 {bug_id}", ""
        try:
            content = md_path.read_text(encoding="utf-8")
            title = ""
            status = ""
            for line in content.split("\n"):
                if line.startswith("# ") and not title:
                    title = line[2:].strip()
                elif line.startswith("- **状态**:") and not status:
                    status = line.replace("- **状态**:", "").strip()
                if title and status:
                    break
            return title or f"缺陷 {bug_id}", status
        except Exception:
            return f"缺陷 {bug_id}", ""


class SimilarBugFinder:
    """相似缺陷搜索器 - 本地 JSON 关键字模式 (回退方案)"""

    def __init__(self, data_path: str = None):
        """
        Args:
            data_path: 本地缺陷数据目录路径，默认搜索几个常见位置
        """
        if data_path:
            self.search_paths = [Path(data_path)]
        else:
            self.search_paths = [
                Path(__file__).parent.parent / "data" / "feishu-bugs" / "batch",
                Path.home() / ".openviking/workspace/feishu-bugs/batch",
            ]

        self.bug_files = [
            "bugs_index.json",
            "bugs_all_with_details.json",
            "bugs_full_all.json",
        ]
        self._bugs_cache = None

    def _load_bugs(self) -> List[Dict]:
        """加载本地缺陷数据"""
        if self._bugs_cache is not None:
            return self._bugs_cache

        for search_path in self.search_paths:
            if not search_path.exists():
                continue
            for bf in self.bug_files:
                bf_path = search_path / bf
                if not bf_path.exists():
                    continue
                try:
                    with open(bf_path, 'r', encoding='utf-8') as f:
                        bugs = json.load(f)
                    if isinstance(bugs, dict):
                        bugs = bugs.get('data', [])
                    self._bugs_cache = bugs
                    return bugs
                except Exception as e:
                    print(f"读取 {bf} 失败: {e}")
                    continue

        return []

    def _get_bug_detail(self, bug_id: str) -> Dict:
        """从本地存储获取缺陷详情"""
        bugs = self._load_bugs()
        for bug in bugs:
            if str(bug.get('id')) == str(bug_id):
                detail = {
                    "id": bug_id,
                    "title": bug.get('name', bug.get('title', '')),
                    "status": bug.get('status', ''),
                }
                detail_obj = bug.get('detail', {})
                if detail_obj:
                    detail["module"] = detail_obj.get('module', {}).get('name', '')
                    detail["function"] = detail_obj.get('function', {}).get('name', '')
                return detail
        return {"id": bug_id, "title": f"缺陷 {bug_id}"}

    def find_similar(self, query: str, limit: int = 10) -> List[Dict]:
        """
        查找相似缺陷

        Args:
            query: 问题描述（关键词）
            limit: 返回结果数量

        Returns:
            list: 相似缺陷列表
        """
        bugs = self._load_bugs()
        if not bugs:
            return []

        query_words = query.lower().split()
        results = []

        for bug in bugs:
            name = bug.get('name', bug.get('title', ''))
            if not name:
                continue

            name_lower = name.lower()
            # 任意一个关键词匹配
            if any(word in name_lower for word in query_words):
                detail = self._get_bug_detail(str(bug.get('id', '')))
                results.append({
                    "id": str(bug.get('id', '')),
                    "title": name[:80],
                    "score": 0.8,
                    "status": bug.get('status', ''),
                    "module": detail.get('module', ''),
                    "function": detail.get('function', ''),
                    "comments": [],
                })
                if len(results) >= limit:
                    break

        return results

    def find_by_keyword(self, keyword: str, limit: int = 10) -> List[Dict]:
        """按关键词查找相似缺陷"""
        return self.find_similar(query=keyword, limit=limit)


def main():
    """测试入口"""
    # 优先 OpenViking 语义搜索
    ov_finder = OpenVikingBugFinder()
    results = ov_finder.find_similar("画面黑屏", limit=5)
    if results:
        print("=== OpenViking 语义搜索结果 ===")
        for i, bug in enumerate(results, 1):
            print(f"{i}. [{bug.get('status', 'N/A')}] {bug['title'][:60]} (得分: {bug['score']:.2f})")
    else:
        # 回退到本地
        print("OpenViking 不可用，使用本地搜索")
        finder = SimilarBugFinder()
        results = finder.find_similar("画面黑屏", limit=5)
        for i, bug in enumerate(results, 1):
            print(f"{i}. [{bug.get('status', 'N/A')}] {bug['title']} (得分: {bug.get('score', '.2f')})")


if __name__ == "__main__":
    main()
