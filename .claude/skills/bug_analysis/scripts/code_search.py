#!/usr/bin/env python3
"""
代码搜索模块 - 在 NReal 代码仓库中搜索相关代码
支持: project, framework, leopard, dove, sparrow

优化 (2026-04-14): 使用 ripgrep (rg) 替代 Python os.walk，搜索性能提升 10-100x。
优化 (2026-04-28): 使用 repository manager 自动 git clone（首次使用时 shallow clone）。
"""

import os
import re
import sys
import subprocess
from typing import List, Dict, Optional
from pathlib import Path


class CodeSearcher:
    """在 NReal 代码仓库中搜索代码"""

    # 代码仓库根目录 — 使用项目根目录下的 nreal-code/
    # 由 nreal-code skill 统一管理，避免重复克隆
    @staticmethod
    def _find_nreal_code_root():
        """向上查找项目根目录下的 nreal-code/（必须包含至少一个 nreal-* git 仓库）"""
        current = os.path.dirname(os.path.abspath(__file__))
        for _ in range(8):  # 最多向上 8 层
            candidate = os.path.join(current, "nreal-code")
            if os.path.isdir(candidate):
                # 验证这是实际的代码目录（包含 nreal-* 仓库），而非 skill 定义目录
                for entry in os.listdir(candidate):
                    if entry.startswith("nreal-") and os.path.isdir(os.path.join(candidate, entry, ".git")):
                        return candidate
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        # Fallback: 旧的 repositories/clones/ 路径
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "repositories", "clones")

    CODE_ROOT = _find_nreal_code_root.__func__()  # class-level init

    # 仓库映射 — bug_analysis 配置名 → nreal-code/ 中的实际目录名
    # nreal-code/ 使用 nreal-* 前缀（除 nrealUtil 外）
    REPOS = {
        "project": "nreal-project",
        "framework": "nreal-framework",
        "leopard": "nreal-leopard",
        "dove": "nreal-dove",
        "sparrow": "nreal-sparrow",
        "heron": "nreal-heron",
        "nrealUtil": "nrealUtil",
        "xr_codec": "nreal-xr_codec",
        "nrsdkrepo": "nreal-nrsdkrepo",
    }

    # 所有仓库已在 nreal-code/ 中，无需 fallback clone
    _FALLBACK_CLONE_REPOS = set()

    # 文件扩展名过滤 — 转成 rg --glob 格式
    CODE_EXTENSIONS = {'.h', '.hpp', '.cc', '.cpp', '.c', '.py', '.java', '.kt', '.gradle', '.cmake', '.sh'}

    # rg 排除目录
    RG_IGNORE_DIRS = ['.git', 'build', 'external', 'node_modules', '__pycache__']

    def __init__(self, code_root: str = None):
        self.code_root = code_root or self.CODE_ROOT
        self._use_rg = self._check_rg_available()

    @staticmethod
    def _check_rg_available() -> bool:
        """检查 rg 是否可用"""
        try:
            subprocess.run(['rg', '--version'], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _get_repo_path(self, repo: str) -> str:
        """获取仓库路径 — nreal-code/ 优先，fallback 到 auto-clone"""
        repo_name = self.REPOS.get(repo, repo)
        repo_path = os.path.join(self.code_root, repo_name)

        if os.path.isdir(repo_path):
            return repo_path

        # 对于不在 nreal-code/ 中的仓库（xr_codec, nrsdkrepo），fallback 到 repositories/clones/
        if repo in self._FALLBACK_CLONE_REPOS:
            fallback_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         "repositories", "clones")
            fallback_path = os.path.join(fallback_root, repo_name)
            if os.path.isdir(fallback_path):
                return fallback_path

        # 尝试自动克隆
        return self._auto_clone(repo, repo_path)

    def _auto_clone(self, repo: str, repo_path: str) -> str:
        """首次使用时自动 shallow clone 代码仓库"""
        try:
            # Ensure skill root is in sys.path so 'repositories.manager' can be found
            skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if skill_root not in sys.path:
                sys.path.insert(0, skill_root)

            from repositories.manager import ensure_repo
            result = ensure_repo(repo)
            if result:
                return str(result)
        except Exception:
            pass

        # Fallback: 尝试从 config 获取 URL 并克隆
        return self._clone_fallback(repo, repo_path)
    
    def _clone_fallback(self, repo: str, repo_path: str) -> str:
        """从 repositories/config.yaml 读取 URL 并克隆"""
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "repositories", "config.yaml")
        if not os.path.exists(config_path):
            return repo_path
        
        import yaml
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}
        
        repos = config.get("repositories", {})
        repo_config = repos.get(repo)
        if not repo_config:
            return repo_path
        
        url_template = repo_config.get("url_template", "")
        
        # SSH URLs don't need token substitution
        if url_template.startswith("ssh://") or url_template.startswith("git@"):
            url = url_template
        else:
            token = os.getenv("GITHUB_TOKEN", "")
            user = os.getenv("GITHUB_USER", "")
            url = url_template.replace("{GITHUB_TOKEN}", token).replace("{GITHUB_USER}", user)
            url = url.replace("{token}", token).replace("***", token)
        
        branch = repo_config.get("branch", "main")
        depth = config.get("clone", {}).get("depth", 1)
        timeout = config.get("clone", {}).get("timeout", 120)
        is_ssh = url.startswith("ssh://") or url.startswith("git@")
        
        safe_url = url.split("@")[-1] if "@" in url else url
        print(f"[code-search] Cloning {repo} from {safe_url} (depth={depth}, {'ssh' if is_ssh else 'https'})...")
        
        os.makedirs(os.path.dirname(repo_path), exist_ok=True)
        
        try:
            clone_env = os.environ.copy()
            clone_env["GIT_TERMINAL_PROMPT"] = "0"
            
            if is_ssh:
                cmd = ["git", "clone", "--depth", str(depth), "--single-branch", url, repo_path]
                # Gerrit repos may have non-standard default branches; follow remote HEAD.
                clone_env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
            else:
                cmd = ["git", "clone", "--depth", str(depth), "--branch", branch, "--single-branch", url, repo_path]
            
            subprocess.run(
                cmd,
                capture_output=True, text=True, timeout=timeout, env=clone_env
            )
            if os.path.isdir(os.path.join(repo_path, ".git")):
                print(f"[code-search] Cloned {repo} successfully")
        except Exception as e:
            print(f"[code-search] Clone failed for {repo}: {e}")
        
        return repo_path

    def _build_rg_globs(self, extensions) -> List[str]:
        """构建 rg --glob 参数"""
        return [f'*{ext}' for ext in extensions]

    def _build_rg_type_filter(self, extensions) -> List[str]:
        """构建 rg --type 或 --glob 过滤"""
        # rg 没有内置的类型覆盖所有我们需要的扩展，用 --glob
        globs = []
        for ext in extensions:
            globs.extend([f'!*{ext}', f'*{ext}'])  # 这种方式不行，换用 --include
        # 直接用 --glob 'PATTERN' 包含指定扩展名
        return [f'glob:*{ext}' for ext in extensions]

    def _run_rg(self, repo_path: str, query: str, extensions: set, max_matches_per_file: int = 3, case_sensitive: bool = False) -> List[Dict]:
        """使用 ripgrep 搜索代码
        
        Args:
            repo_path: 搜索根目录
            query: 搜索关键词
            extensions: 文件扩展名集合
            max_matches_per_file: 每文件最多匹配数
            case_sensitive: 是否大小写敏感
        
        Returns:
            搜索结果列表
        """
        results = []
        
        # 构建 rg 命令
        cmd = ['rg', '--json', '--no-heading', '--line-number', '--with-filename']
        
        # 大小写：默认智能 (auto)，如需强制忽略用 --ignore-case
        if not case_sensitive:
            cmd.append('--ignore-case')
        
        # 文件过滤：只搜索指定扩展名
        for ext in extensions:
            cmd.extend(['--glob', f'*{ext}'])
        
        # 排除目录
        for d in self.RG_IGNORE_DIRS:
            cmd.extend(['--glob', f'!{d}/**'])
        
        # 每文件最大匹配数
        cmd.extend(['--max-count', str(max_matches_per_file * 10)])  # 留余量，后面截断
        
        cmd.extend([query, repo_path])
        
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if proc.returncode not in (0, 1):  # 0=匹配, 1=无匹配, 2+=错误
                return results
            
            # 解析 JSON 输出
            file_matches = {}
            for line in proc.stdout.strip().split('\n'):
                if not line:
                    continue
                try:
                    import json
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                if record.get('type') != 'match':
                    continue
                
                data = record['data']
                filepath = data['path']['text']
                rel_path = os.path.relpath(filepath, repo_path)
                
                line_num = data['line_number']
                line_text = data.get('lines', {}).get('text', '').strip()
                
                if rel_path not in file_matches:
                    file_matches[rel_path] = []
                
                if len(file_matches[rel_path]) < max_matches_per_file:
                    # 提取上下文：用 rg 额外调用获取前后3行
                    context = self._get_context(filepath, line_num)
                    file_matches[rel_path].append({
                        "line_num": line_num,
                        "content": line_text,
                        "context": context,
                    })
            
            for rel_path, matches in file_matches.items():
                if matches:
                    results.append({
                        "repo": os.path.basename(repo_path),
                        "file": rel_path,
                        "matches": matches,
                    })
        
        except (subprocess.TimeoutExpired, Exception):
            pass
        
        return results

    def _get_context(self, filepath: str, line_num: int, context_lines: int = 3) -> str:
        """获取匹配行的上下文"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                all_lines = f.readlines()
            
            start = max(0, line_num - 1 - context_lines)
            end = min(len(all_lines), line_num - 1 + context_lines + 1)
            return ''.join(all_lines[start:end])
        except Exception:
            return ''

    def _run_grep_fallback(self, repo_path: str, query: str, extensions: set, max_matches_per_file: int = 3) -> List[Dict]:
        """使用 grep 作为 rg 不可用时的 fallback"""
        results = []
        
        # 构建 find + grep 管道
        # find repo_path -type f \( -name '*.h' -o -name '*.cpp' ... \) -not -path '*/.git/*' ...
        find_cmd = ['find', repo_path, '-type', 'f']
        
        # 扩展名过滤
        ext_args = []
        for ext in extensions:
            ext_args.extend(['-name', f'*{ext}', '-o'])
        if ext_args:
            ext_args.pop()  # 移除最后一个 -o
            find_cmd.extend(['(', *ext_args, ')'])
        
        # 排除目录
        for d in self.RG_IGNORE_DIRS:
            find_cmd.extend(['-not', '-path', f'*/{d}/*'])
        
        try:
            # 先找文件列表
            find_proc = subprocess.run(
                find_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            files = [f.strip() for f in find_proc.stdout.strip().split('\n') if f.strip()]
            query_lower = query.lower()
            
            for filepath in files:
                rel_path = os.path.relpath(filepath, repo_path)
                try:
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                    
                    matches = []
                    for i, line in enumerate(lines, 1):
                        if query_lower in line.lower():
                            start = max(0, i - 4)
                            end = min(len(lines), i + 3)
                            context = ''.join(lines[start:end])
                            
                            matches.append({
                                "line_num": i,
                                "content": line.strip(),
                                "context": context,
                            })
                            if len(matches) >= max_matches_per_file:
                                break
                    
                    if matches:
                        results.append({
                            "repo": os.path.basename(repo_path),
                            "file": rel_path,
                            "matches": matches,
                        })
                except Exception:
                    continue
        
        except (subprocess.TimeoutExpired, Exception):
            pass
        
        return results

    def search_code(
        self,
        query: str,
        repos: List[str] = None,
        extensions: set = None,
        max_results: int = 20
    ) -> List[Dict]:
        """
        搜索代码 (使用 rg 加速)

        Args:
            query: 搜索关键词(函数名、类名、错误消息等)
            repos: 要搜索的仓库列表(默认全部)
            extensions: 文件扩展名集合(默认所有代码文件)
            max_results: 最大结果数

        Returns:
            list: 搜索结果列表
        """
        if repos is None:
            repos = list(self.REPOS.keys())

        if extensions is None:
            extensions = self.CODE_EXTENSIONS

        results = []

        for repo in repos:
            repo_path = self._get_repo_path(repo)

            if not os.path.isdir(repo_path):
                continue

            # 使用 rg 或 grep 搜索
            if self._use_rg:
                repo_results = self._run_rg(repo_path, query, extensions, max_matches_per_file=3)
            else:
                repo_results = self._run_grep_fallback(repo_path, query, extensions, max_matches_per_file=3)

            results.extend(repo_results)

            if len(results) >= max_results:
                results = results[:max_results]
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
        搜索错误码定义 (使用 rg 加速)

        Args:
            error_code: 错误码(如 ERR_FAILURE, -1)
            repos: 仓库列表

        Returns:
            list: 错误码定义
        """
        if repos is None:
            repos = list(self.REPOS.keys())

        results = []
        c_extensions = {'.h', '.hpp', '.cc', '.cpp', '.py'}

        for repo in repos:
            repo_path = self._get_repo_path(repo)

            if not os.path.isdir(repo_path):
                continue

            # 用 rg 搜索错误码，使用正则模式
            escaped = re.escape(error_code)
            pattern = f'(enum.*Retcode.*{escaped}|#define\\s+{escaped}|{escaped}\\s*=|\"{escaped}\")'
            
            if self._use_rg:
                repo_results = self._run_rg(repo_path, pattern, c_extensions, max_matches_per_file=5, case_sensitive=False)
            else:
                # grep fallback: 用 -E 支持扩展正则
                repo_results = self._run_grep_fallback(repo_path, error_code, c_extensions, max_matches_per_file=5)

            results.extend(repo_results)

        return results


def main():
    """测试入口"""
    searcher = CodeSearcher()

    print(f"搜索引擎: {'ripgrep (rg)' if searcher._use_rg else 'grep (fallback)'}")

    print("\n=== 搜索函数定义 ===")
    results = searcher.search_function("DOVE_LOG_ERROR", repos=["dove"])
    for r in results[:5]:
        print(f"[{r['repo']}] {r['file']}")
        for m in r['matches'][:2]:
            print(f"  L{m['line_num']}: {m['content'][:80]}")

    print("\n=== 搜索代码 ===")
    results = searcher.search_code("NullPointerException", repos=["dove"], max_results=10)
    for r in results[:5]:
        print(f"[{r['repo']}] {r['file']}")
        for m in r['matches'][:2]:
            print(f"  L{m['line_num']}: {m['content'][:60]}")


if __name__ == "__main__":
    main()
