#!/usr/bin/env python3
"""Manage nreal-code reference repositories (update, view git log)."""

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent.parent  # .claude/skills/code-fetcher -> project root
CODE_DIR = BASE / "nreal-code"

REPOS = {
    "dove": {"dir": "nreal-dove", "remote": "git@github.com:nreal-ai/dove.git", "branch": "develop"},
    "ferrit": {"dir": "nreal-ferrit", "remote": "git@github.com:nreal-ai/ferrit.git", "branch": "develop", "submodules": True},
    "framework": {"dir": "nreal-framework", "remote": "git@github.com:nreal-ai/framework.git", "branch": "develop"},
    "leopard": {"dir": "nreal-leopard", "remote": "git@github.com:nreal-ai/leopard.git", "branch": "develop"},
    "ov580_driver": {"dir": "nreal-ov580_driver", "remote": "git@github.com:nreal-ai/ov580_driver.git", "branch": "develop", "submodules": True},
    "project": {"dir": "nreal-project", "remote": "git@github.com:nreal-ai/project.git", "branch": "develop"},
    "sparrow": {"dir": "nreal-sparrow", "remote": "git@github.com:nreal-ai/sparrow.git", "branch": "develop"},
    "heron": {"dir": "nreal-heron", "remote": "git@github.com:nreal-ai/heron.git", "branch": "develop"},
    "xr_codec": {"dir": "nreal-xr_codec", "remote": "git@github.com:nreal-ai/xr_codec.git", "branch": "develop"},
    "nrsdkrepo": {"dir": "nreal-nrsdkrepo", "remote": "git@github.com:nreal-ai/nrsdkrepo.git", "branch": "master"},
    "util": {"dir": "nrealUtil", "remote": "git@github.com:nreal-ai/nrealUtil.git", "branch": "develop"},
}


def get_repo_path(name: str) -> Path:
    return CODE_DIR / REPOS[name]["dir"]


def parse_targets(args: list[str]) -> list[str]:
    targets = []
    for arg in args:
        arg = arg.lower()
        if arg == "all":
            return list(REPOS.keys())
        if arg in REPOS:
            targets.append(arg)
        else:
            print(f"[?] 未知仓库: {arg}，可用: {', '.join(REPOS.keys())}")
            sys.exit(1)
    return targets


def ensure_branch(branch: str) -> bool:
    """Switch to target branch if not already on it."""
    try:
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if current == branch:
            return True
        exists = subprocess.run(
            ["git", "branch", "--list", branch],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if exists:
            subprocess.run(["git", "checkout", branch], capture_output=True, text=True, timeout=10)
        else:
            subprocess.run(
                ["git", "checkout", "-b", branch, f"origin/{branch}"],
                capture_output=True, text=True, timeout=10,
            )
        return True
    except Exception:
        return False


def update_repo(name: str, info: dict) -> str:
    repo_path = get_repo_path(name)
    if not repo_path.exists():
        try:
            os.chdir(CODE_DIR)
            subprocess.run(
                ["git", "clone", "--branch", info["branch"], info["remote"], info["dir"]],
                capture_output=True, text=True, timeout=300,
            )
            os.chdir(repo_path)
            if info.get("submodules"):
                subprocess.run(
                    ["git", "submodule", "update", "--init", "--recursive"],
                    capture_output=True, text=True, timeout=300,
                )
            return f"[✓] {name:<12} {info['branch']:<10} clone 完成"
        except Exception as e:
            return f"[✗] {name:<12} {info['branch']:<10} clone 失败: {e}"

    try:
        os.chdir(repo_path)
        if not ensure_branch(info["branch"]):
            return f"[✗] {name:<12} {info['branch']:<10} 切换分支失败"

        result = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            err = result.stderr.strip().split("\n")[-1]
            return f"[✗] {name:<12} {info['branch']:<10} pull 失败: {err}"

        if info.get("submodules"):
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive"],
                capture_output=True, text=True, timeout=300,
            )

        if "Already up to date" in result.stdout:
            return f"[✓] {name:<12} {info['branch']:<10} 已是最新"
        lines = result.stdout.strip().split("\n")
        changed = [l for l in lines if "file" in l and "changed" in l]
        summary = changed[0].strip() if changed else "有更新"
        return f"[✓] {name:<12} {info['branch']:<10} {summary}"
    except subprocess.TimeoutExpired:
        return f"[✗] {name:<12} {info['branch']:<10} 超时"
    except Exception as e:
        return f"[✗] {name:<12} {info['branch']:<10} {e}"


def log_repo(name: str, count: int = 5) -> str:
    """Show recent commits with author and date."""
    repo_path = get_repo_path(name)
    if not repo_path.exists():
        return f"[✗] {name:<12} 仓库不存在"

    try:
        result = subprocess.run(
            ["git", "log", f"--format=%h | %an | %ai | %s", f"-{count}"],
            capture_output=True, text=True, timeout=10, cwd=repo_path,
        )
        if result.returncode != 0:
            return f"[✗] {name:<12} git log 失败: {result.stderr.strip()}"

        lines = result.stdout.strip().split("\n")
        header = f"{'提交':<10} | {'作者':<20} | {'时间':<22} | 说明"
        separator = "-" * len(header)
        return f"{name} 最近 {min(count, len(lines))} 次提交:\n\n{header}\n{separator}\n" + "\n".join(lines)
    except subprocess.TimeoutExpired:
        return f"[✗] {name:<12} 超时"
    except Exception as e:
        return f"[✗] {name:<12} {e}"


def cmd_update(args):
    targets = parse_targets(args) if args else list(REPOS.keys())
    print(f"更新 {len(targets)} 个仓库:\n")
    for name in targets:
        print(update_repo(name, REPOS[name]))
    print()


def cmd_log(args):
    if not args:
        print("用法: python3 update_code.py log <仓库别名> [数量]")
        print(f"可用仓库: {', '.join(REPOS.keys())}")
        sys.exit(1)

    name = args[0].lower()
    if name not in REPOS:
        print(f"[?] 未知仓库: {name}，可用: {', '.join(REPOS.keys())}")
        sys.exit(1)

    count = int(args[1]) if len(args) > 1 else 5
    print(log_repo(name, count))
    print()


def main():
    args = sys.argv[1:]
    if not args:
        # 向后兼容：默认执行 update all
        cmd_update(["all"])
        return

    command = args[0].lower()
    if command == "update":
        cmd_update(args[1:])
    elif command == "log":
        cmd_log(args[1:])
    else:
        # 向后兼容：把第一个参数当作仓库别名
        cmd_update(args)


if __name__ == "__main__":
    main()
