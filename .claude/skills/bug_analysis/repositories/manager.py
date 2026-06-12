#!/usr/bin/env python3
"""
Repository manager for bug-analyzer.
Auto-clones git repositories on first use with shallow clone (depth=1).
"""

import os
import subprocess
import yaml
from pathlib import Path
from typing import Dict, Optional

# GITHUB_TOKEN and GITHUB_USER are read from os.environ at usage time.
# Set via Claude Code settings.json or shell export.

REPO_CONFIG_FILE = Path(__file__).parent.parent / "repositories" / "config.yaml"
REPO_DIR = Path(__file__).parent.parent / "repositories" / "clones"


def load_repo_config() -> dict:
    """Load repository configuration from repositories/config.yaml."""
    if not REPO_CONFIG_FILE.exists():
        return {"repositories": {}}
    with open(REPO_CONFIG_FILE, 'r') as f:
        return yaml.safe_load(f) or {}


def get_repo_url(repo_name: str, config: dict = None) -> Optional[str]:
    """Resolve the actual git URL for a repository, substituting the token from env."""
    if config is None:
        config = load_repo_config()
    
    repos = config.get("repositories", {})
    repo_config = repos.get(repo_name)
    if not repo_config:
        return None
    
    url_template = repo_config.get("url_template", "")
    
    # SSH URLs (gerrit, etc.) don't need token substitution
    if url_template.startswith("ssh://") or url_template.startswith("git@"):
        return url_template
    
    token = os.getenv("GITHUB_TOKEN", "")
    user = os.getenv("GITHUB_USER", "")
    url = url_template.replace("{GITHUB_TOKEN}", token).replace("{GITHUB_USER}", user)
    # Backward compat: replace {token} and *** too
    url = url.replace("{token}", token).replace("***", token)
    return url


def get_repo_path(repo_name: str) -> Path:
    """Get the local clone path for a repository."""
    return REPO_DIR / repo_name


# In-memory cache: repo_name -> Path or None
_REPO_CACHE: Dict[str, Optional[Path]] = {}


def ensure_repo(repo_name: str, config: dict = None) -> Optional[Path]:
    """Ensure a repository is cloned locally. Returns the path if successful."""
    # Check in-memory cache first
    if repo_name in _REPO_CACHE:
        return _REPO_CACHE[repo_name]

    repo_path = get_repo_path(repo_name)

    if repo_path.exists() and (repo_path / ".git").exists():
        _REPO_CACHE[repo_name] = repo_path
        return repo_path
    
    url = get_repo_url(repo_name, config)
    if not url:
        print(f"[repo-manager] Repository '{repo_name}' not found in config")
        return None
    
    depth = 1
    branch = "main"
    timeout = 120
    
    if config is None:
        config = load_repo_config()
    
    repo_config = config.get("repositories", {}).get(repo_name, {})
    clone_config = config.get("clone", {})
    
    depth = clone_config.get("depth", 1)
    branch = repo_config.get("branch", "main")
    timeout = clone_config.get("timeout", 120)
    is_ssh = url.startswith("ssh://") or url.startswith("git@")
    
    print(f"[repo-manager] Cloning {repo_name} (depth={depth}, branch={branch}, {'ssh' if is_ssh else 'https'})...")
    
    try:
        # Use a sanitized URL for logging (hide token)
        safe_url = url.split("@")[-1] if "@" in url else url
        
        if is_ssh:
            # SSH clone: use SSH agent, follow remote HEAD
            cmd = [
                "git", "clone",
                "--depth", str(depth),
                "--single-branch",
                url, str(repo_path)
            ]
            # Gerrit repositories may have non-standard default branches;
            # don't force --branch, follow remote HEAD instead.
        else:
            # HTTPS clone: disable credential helper (macOS osxkeychain overrides URL-embedded tokens)
            cmd = [
                "git", "-c", "credential.helper=",
                "clone",
                "--depth", str(depth),
                "--branch", branch,
                "--single-branch",
                url, str(repo_path)
            ]
        
        clone_env = os.environ.copy()
        clone_env["GIT_TERMINAL_PROMPT"] = "0"
        if is_ssh:
            # For SSH, allow git-askpass to fail silently (use SSH agent)
            clone_env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
        clone_env["GIT_ASKPASS"] = "echo"
        
        subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=clone_env
        )
        
        if repo_path.exists() and (repo_path / ".git").exists():
            print(f"[repo-manager] Successfully cloned {repo_name}")
            _REPO_CACHE[repo_name] = repo_path
            return repo_path
        else:
            print(f"[repo-manager] Failed to clone {repo_name}")
            _REPO_CACHE[repo_name] = None
            return None

    except subprocess.TimeoutExpired:
        print(f"[repo-manager] Clone timed out for {repo_name} after {timeout}s")
        _REPO_CACHE[repo_name] = None
        return None
    except Exception as e:
        print(f"[repo-manager] Clone error for {repo_name}: {e}")
        _REPO_CACHE[repo_name] = None
        return None


def list_repos() -> Dict[str, str]:
    """List all configured repositories and their status."""
    config = load_repo_config()
    result = {}
    
    for name in config.get("repositories", {}).keys():
        path = get_repo_path(name)
        exists = path.exists() and (path / ".git").exists()
        result[name] = {
            "path": str(path),
            "exists": exists,
            "url": get_repo_url(name, config)
        }
    
    return result


if __name__ == "__main__":
    print("=== Repository Status ===")
    repos = list_repos()
    for name, info in repos.items():
        status = "cloned" if info["exists"] else "not cloned"
        print(f"  {name}: {status}")
        print(f"    path: {info['path']}")
        print(f"    url: {info['url'].split('@')[-1]}")
        print()
