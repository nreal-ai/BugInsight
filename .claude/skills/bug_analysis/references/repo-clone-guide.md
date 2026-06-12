# Repository Configuration & Clone Guide

## Config File

`repositories/config.yaml` defines all git repos for bug-analyzer code search.

### Token Placeholder

The file uses `***` as the token placeholder (NOT `{token}`). Both `manager.py` and `code_search.py` resolve it at runtime:

```python
# Correct pattern (used in manager.py get_repo_url and code_search.py _clone_fallback)
if url_template.startswith("ssh://") or url_template.startswith("git@"):
    url = url_template  # SSH URLs don't need token
else:
    token = os.getenv("GITHUB_TOKEN", "")
    url = url_template.replace("{token}", token)
    if "{token}" not in url_template:
        url = url_template.replace("***", token)
```

### Branch Defaults (Verified 2026-05-21)

| Repo | Branch | Auth |
|------|--------|------|
| project | master | HTTPS (GITHUB_TOKEN) |
| dove | master | HTTPS (GITHUB_TOKEN) |
| framework | main | HTTPS (GITHUB_TOKEN) |
| leopard | master | HTTPS (GITHUB_TOKEN) |
| sparrow | master | HTTPS (GITHUB_TOKEN) |
| nrealUtil | master | HTTPS (GITHUB_TOKEN) |
| heron | master | HTTPS (GITHUB_TOKEN) |
| xr_codec | master | HTTPS (GITHUB_TOKEN) |
| nrsdkrepo | master | HTTPS (GITHUB_TOKEN) |
| bsp | main | SSH (gerrit) |

### SSH Repository Support

BSP repo uses Gerrit SSH: `ssh://zyshan@gerrit.nreal.ai:29418/ars45/code`

**Clone logic** (manager.py ensure_repo):
- Detects `ssh://` or `git@` prefix
- Uses `GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"`
- Does NOT use `credential.helper=` or token embedding
- Requires local SSH key in ssh-agent

**Setup SSH auth**:
```bash
ssh-add ~/.ssh/id_rsa
ssh -T zyshan@gerrit.nreal.ai -p 29418
```

### GitCloner Name Extraction

`git_cloner.py` extracts repo names from URLs:
- `ssh://zyshan@gerrit.nreal.ai:29418/ars45/code` → `ars45-code`
- `https://.../nreal-ai/dove.git` → `nreal-ai-dove`
- `git@github.com:nreal-ai/framework.git` → `nreal-ai-framework`
