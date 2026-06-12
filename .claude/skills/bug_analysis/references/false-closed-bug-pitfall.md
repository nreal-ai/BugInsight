# False-Closed Bug Detection Pitfall

## Symptom
An OPEN bug appears as `closed_bug_{id}.md` in `~/.openviking/workspace/feishu-bugs/ov_import_closed/` and its ID is in `.ov_archived.json`. The bug is never analyzed despite being OPEN.

## Root Cause

`Cron 自动分析任务` line 189-227, `get_closed_bug_ids()`:

```python
# Bugs that were OPEN in cache but are no longer in current OPEN list = closed
for wid, entry in cache_index.items():
    if cached_status == "OPEN" and wid not in current_open_ids and wid not in uploaded:
        closed_ids.append(wid)
```

The logic assumes: "if cached OPEN bug is NOT in current MQL results → it must be closed". This is WRONG because:

1. **MQL OFFSET pagination misses newly created bugs** during pagination sweeps
2. **Temporary state transitions** (e.g., bug briefly set to IN PROGRESS during query time)
3. **`work_item_status` parsing issues** (`key_label_value_list` may parse differently than expected)

## Why It's Permanent

Once a bug is flagged as closed:
1. Its Markdown is uploaded to OpenViking
2. Its ID is added to `.ov_archived.json` (547 entries as of 2026-06-09)
3. `get_closed_bug_ids()` checks `wid not in uploaded` — so it's never flagged again
4. The bug is **permanently invisible** to all future cron runs

## Evidence (Bug 7009022969)

- Created: ~2026-06-08
- Status: OPEN (confirmed via Direct API)
- Comments: 10 technical discussion comments
- Attachments: 2 zip files (682KB + 3MB)
- `closed_bug_7009022969.md` created: 2026-06-06 01:31:56
- In `.ov_archived.json`: YES
- In cache with status OPEN: YES (status was OPEN, but not returned by MQL query on 06-06 run)

## Recovery

```bash
# 1. Remove from archived set
python3 -c "
import json
path = '~/.openviking/workspace/feishu-bugs/.ov_archived.json'
with open(path) as f: ids = set(json.load(f))
ids.discard('7009022969')
with open(path, 'w') as f: json.dump(sorted(ids), f, indent=2)
"

# 2. Remove the false closed markdown
rm ~/.openviking/workspace/feishu-bugs/ov_import_closed/closed_bug_7009022969.md

# 3. Next cron run will pick it up as a new OPEN bug
```

## Fix (Implemented 2026-06-09)

`get_closed_bug_ids()` now uses a **two-step verification** mechanism:

1. **Step 1 (fast)**: Find candidates — cached OPEN bugs not in current MQL OPEN list (unchanged)
2. **Step 2 (verify)**: Batch-verify candidates via Direct API `issue/query` before declaring closed
   - Fetches actual bug details using `POST /open_api/{pk}/work_item/issue/query`
   - Checks `work_item_status.state_key` for real status
   - Only marks as closed if status != "OPEN" or bug not found at all
   - Prints `[误判]` warnings for false positives

### New helper functions

- `_get_plugin_token()`: Gets short-lived plugin token (2hr validity) via `POST /open_api/authen/plugin_token`
- `_get_user_key()`: Reads `FEISHU_USER_KEY` from `~/.hermes/.env`

### Key implementation details

```python
# Step 2 verification via Direct API
resp = requests.post(
    f"https://project.feishu.cn/open_api/{pk}/work_item/issue/query",
    json={"work_item_ids": [int(wid) for wid in batch]},
    headers={"x-plugin-token": token, "x-user-key": user_key},
    timeout=30,
)
for bug in resp.json().get("data", []):
    status = bug.get("work_item_status", {})
    actual_status = status.get("state_key", "") if isinstance(status, dict) else str(status)
    if actual_status == "OPEN":
        false_positives.append(wid)  # Still open, not closed!
```

Verification uses batched Direct API calls (max 50 IDs per request), grouped by `project_key`. If token is unavailable, candidates are treated as false positives (safe default — never close a bug we can't verify).
