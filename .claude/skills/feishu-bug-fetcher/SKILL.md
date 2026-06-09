---
name: feishu-bug-fetcher
description: |
  飞书项目缺陷数据获取工具。用于批量获取飞书项目(Feishu Project)的缺陷(bug)数据，包括：
  - 缺陷列表、缺陷详情（含完整字段）、评论数据、附件信息
  - 通过 MCP 向缺陷追加/删除评论（含 @人员、图片、附件格式规范）

  激活条件：用户提及"飞书缺陷"、"飞书bug"、"feishu project bug"、"获取缺陷"、"bug抓取"、"追加评论"、"删除评论"等。
---

## 架构概述

本工具提供两种飞书缺陷数据获取方式：

| 方式 | 适用场景 | 认证 |
|------|---------|------|
| **FeishuProjectMcp 工具调用** | Claude Code 内直接使用 MCP 工具 | MCP_USER_TOKEN（已配置） |
| **Direct API（fetch_bugs.py）** | 批量获取、脚本化操作 | Plugin ID + Plugin Secret + User Key |

## 项目列表

> **重要**：用户可能涉及多个飞书项目，查询 bug 时必须同时搜索所有项目，除非用户明确指定。

| 项目标识 (project_key) | 项目名称 | 说明 |
|------------------------|---------|------|
| `sw_team` | SW Team | 软件团队缺陷（主） |
| `axr` | AXR | XR 相关缺陷 |

**注意**：新项目接入时应补充到此列表。

**Claude Code 环境已配置**：`FeishuProjectMcp` MCP 服务器已在 `~/.claude.json` 中配置，包含 `MCP_USER_TOKEN`，可以直接调用以下工具：

- `search_by_mql` — MQL 查询工作项
- `get_workitem_brief` — 获取单个工作项概况
- `list_workitem_comments` — 获取评论列表
- `add_comment` — 追加评论
- `upload_file` — 上传文件
- `search_user_info` — 搜索用户信息
- `list_workitem_field_config` — 获取字段配置
- `transition_state` — 流转状态
- `update_field` — 更新字段
- 更多工具见 `mcp__FeishuProjectMcp__*`

## 常见查询场景

> **前置步骤**：查询涉及具体用户时，需先用 `search_user_info` 获取用户的 `user_key`。

### 场景一：当前分配给我的 bug

查询当前负责人是某用户的缺陷：

```sql
-- 对每个项目执行（替换 {project} 为 sw_team / axr）
SELECT `work_item_id`, `name`, `work_item_status`, `start_time`, `priority`
FROM `{project}`.`issue`
WHERE array_contains(`current_status_operator`, '<id:{user_key}>')
ORDER BY `start_time` DESC LIMIT N
```

### 场景二：我曾参与但已转出的 bug

**思路**：查出用户是报告人或仍在全部人员列表中、但当前负责人不是自己的缺陷。这两类覆盖了大部分"曾经经手"的场景。

```sql
-- 对每个项目执行（替换 {project} 为 sw_team / axr）
SELECT `work_item_id`, `name`, `work_item_status`, `start_time`, `current_status_operator`
FROM `{project}`.`issue`
WHERE array_contains(all_participate_persons(), '<id:{user_key}>')
  AND not array_contains(`current_status_operator`, '<id:{user_key}>')
ORDER BY `start_time` DESC LIMIT N
```

**局限性**：`all_participate_persons()` 仅包含**当前**参与人员。如果用户曾短暂担任经办人、之后完全移除（例如：被设为经办人 → 把自己移除 → 添加他人），则该用户会从全部人员列表中消失，此查询无法命中。此时只能通过 `get_workitem_op_record` 逐 bug 查操作记录来确认。

### 场景三：我报告（创建）的 bug

查询报告人是某用户的缺陷：

```sql
-- 对每个项目执行（替换 {project}）
SELECT `work_item_id`, `name`, `work_item_status`, `start_time`
FROM `{project}`.`issue`
WHERE `__报告人` = '<id:{user_key}>'
ORDER BY `start_time` DESC LIMIT N
```

### 场景四：查"曾经经手但完全脱离"的 bug（兜底方案）

**适用情况**：用户短暂担任经办人后把自己完全移除（如 6985016259），此时 `all_participate_persons()` 不再包含该用户，场景二查不到。

**分两步走**：

**第一步**：MQL 圈定候选范围（缩小时间窗口控制数量）。

```sql
-- 对每个项目执行，时间窗口建议 30~60 天
SELECT `work_item_id`, `name`, `work_item_status`, `start_time`, `updated_by`
FROM `{project}`.`issue`
WHERE not array_contains(all_participate_persons(), '<id:{user_key}>')
  AND RELATIVE_DATETIME_BETWEEN(`start_time`, 'past', '30d')
ORDER BY `start_time` DESC LIMIT N
```

**第二步**：对候选 bug 逐条查操作记录，筛选角色/人员变更。

```
调用 MCP 工具 get_workitem_op_record：
  project_key: "{project}"
  work_item_id: "{bug_id}"
  op_record_module: ["role_and_user_mod"]
```

筛选条件：
- `operator` == 用户的 `user_key` → 用户亲自执行的变更
- `record_contents` 中 `add`/`delete` 数组包含用户的 `user_key` → 涉及用户本人的角色变更

**注意**：此方法本质是"MQL 预筛选 + 逐条验证"，候选越多越慢。建议只在用户明确指出某个 bug 可能遗漏时使用，而非日常查询。

## 飞书项目 URL 格式

缺陷详情页链接格式：

```
https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}
```

示例：`https://project.feishu.cn/axr/issue/detail/6985025776`

## 快速使用（Claude Code 内）

### 查询缺陷

通过 MCP 工具直接查询，无需额外配置。**未指定项目时，必须对[项目列表](#项目列表)中所有项目逐一查询并合并结果。**

```
# 单项目查询
调用 MCP 工具 search_by_mql：
  project_key: "sw_team"
  mql: "SELECT ... FROM sw_team.issue WHERE ..."

# 多项目查询（默认行为）
依次对 sw_team、axr 等所有已配置项目执行相同 MQL 查询（替换 FROM 子句中的项目名），
将结果合并后按时间排序返回。

### 获取单个缺陷详情

```
调用 MCP 工具 get_workitem_brief：
  project_key: "sw_team"
  work_item_id: "6970632429"
```

### 获取评论

```
调用 MCP 工具 list_workitem_comments：
  project_key: "sw_team"
  work_item_id: "6970632429"
```

### 追加评论

```
调用 MCP 工具 add_comment：
  work_item_id: "6970632429"
  content: "这是评论内容"
  project_key: "sw_team"
```

### 删除评论

> **MCP 工具 `delete_comment` 不存在。** 删除评论必须通过 Direct API。详见 `references/delete-comment-investigation.md`。
>
> ⚠️ **前提条件**：Plugin 必须被项目管理员安装到目标项目空间，否则所有 Direct API 返回 `10301`。

**两步方案**（Step 1 已验证可行，Step 2 需 plugin 已安装）：

**Step 1 — 刷新 Plugin Token**：
```python
import requests, os

resp = requests.post(
    'https://project.feishu.cn/open_api/authen/plugin_token',
    json={
        'plugin_id': os.environ['FEISHU_PLUGIN_ID'],
        'plugin_secret': os.environ['FEISHU_PLUGIN_SECRET'],
        'type': 0
    },
    timeout=10
)
token = resp.json()['data']['token']  # 有效期 ~5500 秒
```

**Step 2 — Direct API DELETE**：
```python
headers = {'x-plugin-token': token, 'x-user-key': os.environ['FEISHU_USER_KEY']}
url = f'https://project.feishu.cn/open_api/{project_key}/work_item/Bug/{work_item_id}/comment/{comment_id}'
resp = requests.delete(url, headers=headers, timeout=10)
# 成功返回 {"err_code": 0}
```

**关键陷阱**：

1. **评论 ID 精度丢失**（`references/comment-id-precision-loss.md`）：评论 ID 是 19 位整数（如 `7639697209299487948`），mcporter 的 JavaScript `JSON.parse` 会截断末位。必须**绕过 mcporter**，直接调用 MCP JSON-RPC 并设置 `parse_int=str`：

```python
import json
data = json.loads(raw_text, parse_int=str)  # 关键：保留完整整数
# comment_id 现在是字符串 '7639697209299487948'
```

2. **URL 大小写**：`Bug` 区分大小写，`bug` 会失败。

3. **不要用 `mcporter auth meego --reset`**：那只处理 OAuth 交互流程，不会刷新静态 plugin token。

## 批量获取脚本（fetch_bugs.py）

当需要批量处理大量缺陷时，使用 `scripts/fetch_bugs.py`。

### 前置配置

```bash
# 1. 复制配置模板
cd ~/.claude/skills/feishu-bug-fetcher
cp config-template.json config.json

# 2. 编辑 config.json，填入 plugin_id, plugin_secret, user_key
```

或通过环境变量：
```bash
export FEISHU_PROJECT_KEY=sw_team
export FEISHU_PLUGIN_ID=你的插件ID
export FEISHU_PLUGIN_SECRET=你的插件密钥
export FEISHU_USER_KEY=你的用户Key
```

### 用法

```bash
cd ~/.claude/skills/feishu-bug-fetcher

# 检查配置
python3 scripts/fetch_bugs.py --config

# 获取单个缺陷完整信息（详情+评论+附件）
python3 scripts/fetch_bugs.py --single 6970632429

# 获取指定 ID 的详情
python3 scripts/fetch_bugs.py --details 6970632429,6970632430,6970632431

# 获取最新 50 条缺陷
python3 scripts/fetch_bugs.py --recent 50

# 全量获取（列表+详情+评论+附件）
python3 scripts/fetch_bugs.py --all
```

### 输出文件

```
~/.openviking/workspace/feishu-bugs/
├── batch/
│   ├── bugs_index.json          # 缺陷列表
│   ├── bugs_full_all.json       # 缺陷详情（Direct API 格式）
│   ├── bugs_with_comments.json  # 评论数据
│   └── bugs_attachments.json    # 附件信息
└── single/
    └── bug_XXXXX_report.json    # 单个缺陷完整报告
```

## MCP 评论格式规范

通过 `add_comment` 追加评论时，content 支持以下格式：

**@人员**（先用 `search_user_info` 获取 lark_user_id）：
```
@张三<!-- mention:{"id":"lark_user_id_xxx","cn_name":"张三","blockType":"AT_USER_BLOCK"} -->
```

**图片**（先用 `upload_file` 上传获取 file_token）：
```
![图片名称](图片URL)<!-- file_token -->
```

**附件**：
```
[附件名称](附件URL)<!-- file_token -->
```

## Direct API 参考

当 MCP 工具不适用时，可直接调用飞书 Open API：

```python
import requests

# 1. 获取 Plugin Token（有效期 2 小时）
resp = requests.post(
    "https://project.feishu.cn/open_api/authen/plugin_token",
    json={"plugin_id": ID, "plugin_secret": SECRET, "type": 0},
)
token = resp.json()["data"]["token"]

headers = {"x-plugin-token": token, "x-user-key": USER_KEY}

# 2. 批量查询缺陷（每批最多 50 个 ID）
resp = requests.post(
    f"https://project.feishu.cn/open_api/{PROJECT_KEY}/work_item/issue/query",
    json={"work_item_ids": [6970632429, ...], "get_all_properties": True},
    headers=headers,
)
bugs = resp.json().get("data", [])

# 3. 解析返回数据
for bug in bugs:
    bug_id = bug["id"]                    # 整数
    name = bug.get("name", "")
    status = bug.get("work_item_status", {}).get("state_key", "")
    fields = bug.get("fields", [])        # 字段数组
    for f in fields:
        if f["field_key"] == "multi_attachment":
            attachments = f["field_value"]
```

**关键限制**：
- `work_item_ids` 每批最多 50 个，超过返回空数组
- ID 必须是整数，不能是字符串
- Header 区分大小写：`x-plugin-token`（全小写）
- Token 有效期 2 小时

## 配置变量

| 变量 | 说明 | 环境变量 | 默认值 |
|------|------|----------|--------|
| `project_key` | 飞书项目标识 | `FEISHU_PROJECT_KEY` | `sw_team` |
| `mcp_user_token` | MCP 用户 Token | `FEISHU_MCP_TOKEN` | - |
| `plugin_id` | 飞书插件 ID | `FEISHU_PLUGIN_ID` | - |
| `plugin_secret` | 飞书插件密钥 | `FEISHU_PLUGIN_SECRET` | - |
| `user_key` | 飞书用户 Key | `FEISHU_USER_KEY` | - |
| `output_dir` | 数据输出目录 | `OUTPUT_BASE_DIR` | `~/.openviking/workspace/feishu-bugs` |

## 历史参考

旧版脚本（依赖 mcporter CLI）保留在 `references/` 目录，仅供查阅。
新版架构使用 `scripts/fetch_bugs.py`，不再依赖 mcporter。

---
*更新: 2026-04-30*
