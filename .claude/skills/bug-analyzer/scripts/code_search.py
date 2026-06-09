#!/usr/bin/env python3
"""
代码搜索模块 - 在 NReal 代码仓库中搜索相关代码
支持: project, framework, leopard, dove, sparrow
"""

import os
import re
from typing import List, Dict, Optional
from pathlib import Path


class CodeSearcher:
    """在 NReal 代码仓库中搜索代码"""

    # 代码仓库根目录 (相对于脚本目录)
    CODE_ROOT = str(Path(__file__).parent.parent.parent.parent / "nreal-code")

    # 仓库映射
    REPOS = {
        "project": "nreal-project",
        "framework": "nreal-framework",
        "leopard": "nreal-leopard",
        "dove": "nreal-dove",
        "sparrow": "nreal-sparrow"
    }

    # 文件扩展名过滤
    CODE_EXTENSIONS = {'.h', '.hpp', '.cc', '.cpp', '.c', '.py', '.java', '.kt', '.gradle', '.cmake', '.sh'}

    def __init__(self, code_root: str = None):
        self.code_root = code_root or self.CODE_ROOT

    def _get_repo_path(self, repo: str) -> str:
        """获取仓库路径"""
        return os.path.join(self.code_root, self.REPOS.get(repo, repo))

    def search_code(
        self,
        query: str,
        repos: List[str] = None,
        extensions: List[str] = None,
        max_results: int = 20
    ) -> List[Dict]:
        """
        搜索代码

        Args:
            query: 搜索关键词(函数名、类名、错误消息等)
            repos: 要搜索的仓库列表(默认全部)
            extensions: 文件扩展名过滤(默认所有代码文件)
            max_results: 最大结果数

        Returns:
            list: 搜索结果列表
        """
        if repos is None:
            repos = list(self.REPOS.keys())

        if extensions is None:
            extensions = self.CODE_EXTENSIONS

        results = []
        query_lower = query.lower()

        for repo in repos:
            repo_path = self._get_repo_path(repo)

            if not os.path.isdir(repo_path):
                continue

            # 递归搜索
            for root, dirs, files in os.walk(repo_path):
                # 跳过 .git 和 build 目录
                dirs[:] = [d for d in dirs if d not in ['.git', 'build', 'external', 'node_modules', '__pycache__']]

                for filename in files:
                    # 过滤扩展名
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in extensions:
                        continue

                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, repo_path)

                    # 简单文本搜索
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            lines = f.readlines()

                        matches = []
                        for i, line in enumerate(lines, 1):
                            if query_lower in line.lower():
                                # 提取上下文(前后3行)
                                start = max(0, i - 4)
                                end = min(len(lines), i + 3)
                                context = ''.join(lines[start:end])

                                matches.append({
                                    "line_num": i,
                                    "content": line.strip(),
                                    "context": context
                                })

                                if len(matches) >= 3:  # 每个文件最多3处匹配
                                    break

                        if matches:
                            results.append({
                                "repo": repo,
                                "file": rel_path,
                                "matches": matches
                            })
                            
                            if len(results) >= max_results:
                                break
                    
                    except Exception:
                        continue
                
                if len(results) >= max_results:
                    break
        
        return results

    def search_function(
        self,
        function_name: str,
        repos: List[str] = None
    ) -> List[Dict]:
        """
        搜索函数/宏定义

        Args:
            function_name: 函数名或宏名
            repos: 仓库列表

        Returns:
            list: 函数定义位置
        """
        # 使用通用搜索，更灵活
        return self.search_code(
            function_name,
            repos=repos,
            max_results=30
        )

    def search_error_code(
        self,
        error_code: str,
        repos: List[str] = None
    ) -> List[Dict]:
        """
        搜索错误码定义

        Args:
            error_code: 错误码(如 ERR_FAILURE, -1)
            repos: 仓库列表

        Returns:
            list: 错误码定义
        """
        results = []

        # 支持多种格式
        patterns = [
            f"enum.*Retcode.*{re.escape(error_code)}",
            f"#define\\s+{re.escape(error_code)}",
            f"{re.escape(error_code)}\\s*=",
            f'"{re.escape(error_code)}"'
        ]

        if repos is None:
            repos = list(self.REPOS.keys())

        for repo in repos:
            repo_path = self._get_repo_path(repo)

            if not os.path.isdir(repo_path):
                continue

            for root, dirs, files in os.walk(repo_path):
                dirs[:] = [d for d in dirs if d not in ['.git', 'build', 'external']]

                for filename in files:
                    ext = os.path.splitext(filename)[1].lower()
                    if ext not in {'.h', '.hpp', '.cc', '.cpp', '.py'}:
                        continue

                    filepath = os.path.join(root, filename)

                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                            lines = content.split('\n')

                        for i, line in enumerate(lines, 1):
                            for pattern in patterns:
                                if re.search(pattern, line, re.IGNORECASE):
                                    results.append({
                                        "repo": repo,
                                        "file": os.path.relpath(filepath, repo_path),
                                        "line": i,
                                        "content": line.strip()
                                    })
                                    break

                    except Exception:
                        continue

        return results


def main():
    """测试入口"""
    searcher = CodeSearcher()

    # 测试函数搜索
    print("=== 搜索函数定义 ===")
    results = searcher.search_function("DOVE_LOG_ERROR", repos=["dove"])
    for r in results[:5]:
        print(f"[{r['repo']}] {r['file']}:{r['line']}")
        print(f"  {r['definition'][:80]}")

    print("\n=== 搜索代码 ===")
    results = searcher.search_code("NullPointerException", repos=["dove"], max_results=10)
    for r in results[:5]:
        print(f"[{r['repo']}] {r['file']}")
        for m in r['matches'][:2]:
            print(f"  L{m['line_num']}: {m['content'][:60]}")


if __name__ == "__main__":
    main()