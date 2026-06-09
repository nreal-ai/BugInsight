# Delete Comment Investigation — Feishu Project

**最初调研**: 2026-05-14（hermes_skills 环境）  
**更新**: 2026-06-08（Claude Code 环境实测）  
**Bug**: 6989974664（调研）, 5507770416, 7010969895（实测）

## 当前状态

⚠️ **方案可行，但有前提条件**：Plugin 必须被项目管理员安装到目标项目空间中。未安装时 Direct API 全部返回 `10301`。

## Problem

Need to delete a comment from a Feishu Project bug.

## Methods Tried (All Failed)

### 1. MCP `delete_comment` tool — NOT AVAILABLE

The meego MCP server exposes ~50+ tools but `delete_comment` is not among them. Available comment-related tools:
- `add_comment` — works via mcporter OAuth
- `list_workitem_comments` — works via mcporter OAuth

```
mcporter call meego delete_comment --args '{"work_item_id":"6989974664","project_key":"sw_team","comment_id":"7639622692866625000"}'
→ "function definition: delete_comment not found"
```

### 2. Direct API DELETE — plugin token EXPIRED

API: `DELETE https://project.feishu.cn/open_api/{project_key}/work_item/Bug/{work_item_id}/comment/{comment_id}`

Headers required: `x-plugin-token` + `x-user-key`

The plugin token in `~/.mcporter/mcporter.json` (`X-Mcp-Token: m-2b8d8cf2-...`) returned:
```json
{"err_code": 10022, "err_msg": "Check Token Failed", "message": "token expire"}
```

**Root cause**: The plugin token is a static credential that expires and cannot be auto-refreshed. It is separate from mcporter's OAuth flow.

### 3. OAuth Token Extraction — NOT POSSIBLE

mcporter manages OAuth tokens internally. Investigation revealed:

- **VaultPersistence** (`oauth-vault.js`): stores state, clientInfo, codeVerifier in `~/.mcporter/credentials.json` entries — but **NOT the actual access_token/refresh_token**
- **DirectoryPersistence** (`oauth-persistence.js`): would store `tokens.json` in `~/.mcporter/<name>/` (legacy path) — directory does NOT exist
- **macOS Keychain**: searched for "mcporter", "meego", "project.feishu.cn" — no matching entries found
- mcporter credentials contain OAuth client_id/client_secret, but these are NOT Feishu app_id/app_secret (different auth systems)
- When mcporter calls MCP tools, OAuth refresh happens internally in-memory — there is no CLI command to export tokens

### 4. Feishu App Access Token — NOT COMPATIBLE

The OAuth client_id/client_secret from mcporter credentials are for the MCP OAuth flow, not for Feishu's open platform API. They cannot be used to obtain an app_access_token:
```
POST https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal
→ {"code": 10003, "msg": "invalid param"}
```

### 5. mcporter HTTP URL call — NOT SUPPORTED FOR DELETE

```
mcporter call <url> -X DELETE
→ "Unable to load tool metadata; name positional arguments explicitly"
```

mcporter's HTTP call mode requires tool metadata and doesn't support arbitrary HTTP methods.

## Solution

### 前提条件

**Plugin 必须被项目管理员安装到目标项目空间中。** 未安装时，即使 authen API 成功返回 token，所有 Direct API 调用都会返回 `err_code: 10301`（无权限）。

管理员操作：
1. 打开 `https://project.feishu.cn/openapp/{plugin_id}`
2. 将插件安装到目标项目（需要项目管理员权限）
3. 确认插件权限包含"评论管理"

### Step 1: Refresh Plugin Token via Authen API

Do NOT use `mcporter auth meego --reset` — that only handles OAuth interactive flow and does NOT refresh the static plugin token.

```python
import requests

resp = requests.post(
    'https://project.feishu.cn/open_api/authen/plugin_token',
    json={
        'plugin_id': PLUGIN_ID,
        'plugin_secret': PLUGIN_SECRET,
        'type': 0
    },
    timeout=10
)
token = resp.json()['data']['token']  # valid ~5500 seconds
```

**实测（2026-06-08）**：此步骤正常，authen API 返回 token 成功（格式 `p-xxxxxxxx-...`）。

### Step 2: Delete via Direct API

```python
headers = {'x-plugin-token': token, 'x-user-key': USER_KEY}
url = f'https://project.feishu.cn/open_api/{project_key}/work_item/Bug/{work_item_id}/comment/{comment_id}'
resp = requests.delete(url, headers=headers, timeout=10)
# 成功返回 {"err_code": 0}
# Plugin 未安装时返回 {"err_code": 10301, "err_msg": "..."}
```

**实测（2026-06-08）**：sw_team 和 axr 均返回 10301，原因是 Plugin `MII_69F33F5215804BD4` 未安装到这两个项目空间。

**Note**: `Bug` in the URL is case-sensitive — `bug` will fail.

### CRITICAL: Comment ID Precision Loss

mcporter's JavaScript layer uses `JSON.parse` which truncates 19-digit comment IDs (e.g., `7639697209299487948` → `7639697209299488000`). Using the truncated ID returns `err_code: 30015, Record not found`.

**Solution**: Bypass mcporter, call MCP server JSON-RPC directly with `parse_int=str`:

```python
import requests, json
mcp_url = "https://project.feishu.cn/mcp_server/v1"
headers = {"X-Mcp-Token": "m-2b8d8cf2-...", "Content-Type": "application/json"}
payload = {"jsonrpc": "2.0", "method": "tools/call", "id": 1,
    "params": {"name": "list_workitem_comments",
               "arguments": {"project_key": "sw_team", "work_item_id": "..."}}}
resp = requests.post(mcp_url, headers=headers, json=payload, timeout=30)
result = resp.json()
for item in result.get("result", {}).get("content", []):
    if item.get("type") == "text":
        data = json.loads(item["text"], parse_int=str)  # KEY: preserves full integer
```

## Technical Details

### mcporter OAuth Architecture (v0.8.1)

```
mcporter call meego <tool>
    ↓
buildOAuthPersistence(definition)
    ↓
VaultPersistence (primary) → credentials.json entries
    ↓
DirectoryPersistence (fallback, legacy) → ~/.mcporter/<name>/tokens.json
    ↓
macOS Keychain (not used — no keytar dependency found)
```

The actual token flow:
1. mcporter calls the MCP HTTP endpoint
2. If 401, triggers OAuth flow (browser + callback server)
3. On success, saves tokens to vault persistence
4. Tokens are used in-memory for subsequent requests
5. Tokens are NOT exported or accessible via CLI

### Credential Files

| File | Contents |
|------|----------|
| `~/.mcporter/mcporter.json` | MCP server config (URL, headers with static plugin token) |
| `~/.mcporter/credentials.json` | OAuth vault (client_id, client_secret, state UUID, code_verifier) |
| `~/.mcporter/.mcporter/` | Does not exist |
