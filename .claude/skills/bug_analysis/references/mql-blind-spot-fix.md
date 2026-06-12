# MQL Blind Spot Fix — MCP JSON-RPC Pagination

## Problem

`mcporter call meego search_by_mql` always returns only the first 50 results regardless of LIMIT. This creates a permanent blind spot in the cron incremental detection flow.

## Root Cause

1. Cache last refreshed at time T with 4270 bugs
2. New bug created between cache refreshes
3. Project has >50 OPEN bugs total
4. New bug never appears in MQL top-50
5. Cron compares top-50 IDs vs cache → bug invisible forever

**Evidence**: Bug 7006901369 (project `676e7fecad8e9de8735fa89f`), created 2026-06-03 11:43, was at offset=200 (5th page). All 12 consecutive cron runs reported 0 new bugs. Direct pagination confirmed 40 OPEN bugs across 13 pages (604 total).

## Solution

Replace mcporter CLI with direct MCP JSON-RPC calls + OFFSET pagination.

### Key Functions (added to Cron 自动分析任务)

```python
MCP_URL = "https://project.feishu.cn/mcp_server/v1"
FETCH_PAGE_SIZE = 50
FETCH_MAX_PAGES = 30  # safety: 1500 bugs max per project

def _get_mcp_token():
    """Read X-Mcp-Token from ~/.mcporter/mcporter.json"""
    mcporter_path = os.path.expanduser("~/.mcporter/mcporter.json")
    with open(mcporter_path) as f:
        mc_data = json.load(f)
    return mc_data["mcpServers"]["meego"]["headers"]["X-Mcp-Token"]

def _mcp_call(tool_name, arguments, timeout=30):
    """Call MCP server directly via JSON-RPC"""
    headers = {"X-Mcp-Token": _get_mcp_token(), "Content-Type": "application/json"}
    payload = {"jsonrpc": "2.0", "method": "tools/call", "id": 1,
               "params": {"name": tool_name, "arguments": arguments}}
    resp = requests.post(MCP_URL, headers=headers, json=payload, timeout=timeout)
    content_list = resp.json()["result"]["content"]
    return json.loads(content_list[0]["text"], parse_int=str), None
```

### Paginated fetch_bugs()

```python
def fetch_bugs(project_key):
    pk_ref = f"`{project_key}`" if project_key != "sw_team" else project_key
    all_bugs, offset = [], 0
    for page in range(FETCH_MAX_PAGES):
        mql = f"SELECT work_item_id, name, work_item_status FROM {pk_ref}.issue LIMIT {FETCH_PAGE_SIZE} OFFSET {offset}"
        result, err = _mcp_call("search_by_mql", {"project_key": project_key, "mql": mql}, timeout=60)
        items = result.get("data", {}).get("1", [])
        if not items:
            break
        for item in items:
            fields = _parse_moql_field_list(item.get("moql_field_list", []))
            fields["_project_key"] = project_key
            all_bugs.append(fields)
        if len(items) < FETCH_PAGE_SIZE:
            break
        offset += FETCH_PAGE_SIZE
        time.sleep(0.3)
    return all_bugs
```

### Also Updated: Comment Queries

The same MCP JSON-RPC approach replaces mcporter CLI for:
- `fetch_bug_full_info()` → `list_workitem_comments`
- `check_attachment_count()` → `list_workitem_comments`
- `check_ai_comment()` → `list_workitem_comments`
- `fetch_new_bug_details()` → `search_by_mql` (single bug)

**Why**: Removes shell escaping complexity (triple-escaped JSON strings in mcporter CLI args) and avoids mcporter subprocess overhead for each query.

### Performance Impact

- **Before**: ~2s per project (50 bugs via mcporter CLI)
- **After**: ~25s per project with 600+ bugs (13 pages × ~2s each)
- Trade-off: 23s more per run, but catches ALL bugs instead of just top-50
- Projects with fewer bugs (like `sw_team` ~100 bugs) only need 2-3 pages (~6s)

### MCP Token Reading

Token is read from `~/.mcporter/mcporter.json` (not from `.hermes/.env`). The static token in mcporter.json has ~2h validity. If it expires, the MCP calls return HTTP 401.

### OFFSET Support

Verified 2026-06-05: MQL `LIMIT N OFFSET M` works via direct MCP JSON-RPC. mcporter CLI strips/ignores OFFSET, which is why we must call the MCP server directly.
