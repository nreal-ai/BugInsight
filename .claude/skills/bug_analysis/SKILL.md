---
name: bug_analysis
description: >
  Feishu Project bug analysis pipeline - fetch bugs from multiple projects,
  download attachments, search source code, analyze root causes with LLM,
  and post analysis reports as Feishu comments.
  Supports manual single-bug analysis and cron-based automated batch analysis.
  Triggers on: 飞书缺陷分析, analyze bug, 缺陷根因分析, 定时分析缺陷, feishu bug,
  setup bug cron, 设置缺陷自动分析, 启动自动分析, 停止自动分析, 飞书bug, 追加评论, 删除评论.
---

# Bug Analysis Skill (Claude Code)

端到端飞书项目缺陷分析流水线。支持手动分析单个 Bug 和定时自动批量分析。

## 前置条件

运行前验证环境变量：

```bash
echo $BUG_INSIGHT_FEISHU_MCP_TOKEN  # MCP Token（必需）
echo $BUG_INSIGHT_FEISHU_PLUGIN_ID  # Plugin ID（必需）
echo $BUG_INSIGHT_FEISHU_PLUGIN_SECRET  # Plugin Secret（必需）
echo $BUG_INSIGHT_FEISHU_USER_KEY  # User Key（必需）
echo $LLM_API_KEY  # LLM API Key（LLM 分析必需）
echo $GITHUB_TOKEN  # GitHub Token（代码搜索必需）
```

所有脚本位于：`.claude/skills/bug_analysis/scripts/`

---

## Mode 1: 手动分析单个 Bug

**触发**：用户说"分析飞书缺陷 7007264060" 或 "analyze bug 7007264060 in sw_team"

### 快速流程（Python 脚本）

当 LLM_API_KEY 等环境变量配置完整时，优先使用 Python 脚本：

1. **获取 Bug 基本信息**（可选，用于展示给用户）：
   ```
   mcp__FeishuProjectMcp__get_workitem_brief(project_key="sw_team", work_item_id="7007264060")
   ```

2. **运行分析**（核心步骤）：
   ```bash
   cd .claude/skills/bug_analysis/scripts && BUG_ANALYZER_MAX_ROUNDS=2 python3 bug_analyzer.py feishu <bug_id> --llm --project <project_key>
   ```
   脚本自动完成：附件下载 → 平台检测 → 代码搜索 → LLM 根因分析 → 生成报告 JSON

3. **回写评论**（可选，询问用户是否需要）：
   ```
   mcp__FeishuProjectMcp__add_comment(work_item_id="<bug_id>", project_key="sw_team", content="<分析报告>")
   ```

4. **更新 AI 分析字段**（评论成功后执行）：
   ```
   mcp__FeishuProjectMcp__update_field(
       project_key="<project_key>",
       work_item_id="<bug_id>",
       fields=[{"field_key": "field_d381f5", "field_value": "[{\"option_id\": \"x5q6hij1t\"}]"}]
   )
   ```

5. **读取分析结果**：
   ```bash
   cat /tmp/bug_<bug_id>_analysis_*.json
   ```

### 快速流程（MCP 手动分析）

当环境变量不完整（缺少 LLM_API_KEY 等）时，Claude 将直接通过 MCP 工具 + 代码搜索执行手动分析：

1. **获取 Bug 详情**：`get_workitem_brief(fields=["_all"])`
2. **下载附件并提取日志**
3. **搜索 nreal-code/ 相关代码**
4. **编译分析报告并写入评论**
5. **更新 AI 分析字段为 SDK**：`update_field(project_key, work_item_id, field_d381f5, SDK)`

### 评论格式要求（必需）

无论使用哪种分析方式，写入飞书评论时**必须**遵循以下格式：

- 标题必须为：`## 🔍 AI分析结论 (by Claude Code + {模型名称})`
- 模型名称从 `config.yaml` 的 `llm.model` 读取（如 `deepseek-v4-pro`）
- 置信度在核心结论表格中一行展示：`**置信度** | **94% 🟢 高**`，不单独开章节
  - 百分比制（0-100%），综合为各维度加权平均取整
  - 等级：🟢 高 (≥80%) | 🟡 中 (60%-79%) | 🔴 低 (<60%)
- 必须包含「证据链」章节（直接证据 / 间接证据 / 辅助证据）
- 末尾标注：`> ⚠️ 此分析来源于 AI（Claude Code + {模型名称}），仅供参考。`
- **分析完成后，必须在控制台输出缺陷链接**：`https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}`

完整格式规范见 `references/report-format.md`。

### 自定义参数

- 指定项目：`python3 bug_analyzer.py feishu <bug_id> --project <project_key> --llm`
- 只做规则分析（不用 LLM）：去掉 `--llm` 参数
- 查看报告：`python3 report.py <bug_id>`

---

## Mode 2: 自动分析（Cron + MCP 原生）

**触发**：用户说"开始自动分析"、"启动自动分析"、"设置缺陷自动分析"

### 设置流程

1. 更新 `bug-auto-analyzer-config` memory 将 mode 设为 `auto`
2. 创建 Cron 任务（每 10 分钟，Claude MCP 原生执行）：
   ```
   CronCreate(
     cron: "3,13,23,33,43,53 * * * *",
     prompt: "执行自动分析扫描...",
     recurring: true
   )
   ```
3. Cron 触发时 Claude 直接用 MCP 工具执行全流程：MQL 查询 → 附件下载 → 代码搜索 → 分析 → 写评论 → **更新 AI分析 字段为 SDK** → **控制台输出缺陷链接**

### 停止

1. `CronDelete` 删除 Cron
2. memory 切回 `manual`

### 跳过规则

- 评论含 `AI分析结论 (by Claude Code` → 已分析过，跳过
- **注意**：其他 AI 工具（如"AI分析 自动缺陷分析报告"）分析过的不算已分析，仍需本工具分析
- 已在 `analyzed_bugs` 列表中 → 跳过
- 附件 >15 个或 ZIP ≥3 个 → 跳过
- **分析时禁止参考其他 AI 工具的评论**

### 限制

- **Session 内有效**：Cron 在 Claude Code session 结束时会停止
- **仅检测未解决 Bug**：状态 OPEN/IN PROGRESS/REOPENED
- **30 天内**：只扫描最近 30 天创建的 Bug

---

## 可用 MCP 工具（Claude Code 内置）

分析过程中可直接使用这些 Feishu MCP 工具：

| 工具 | 用途 |
|------|------|
| `mcp__FeishuProjectMcp__search_by_mql` | MQL 查询缺陷列表 |
| `mcp__FeishuProjectMcp__get_workitem_brief` | 获取缺陷详情 |
| `mcp__FeishuProjectMcp__list_workitem_comments` | 获取评论 |
| `mcp__FeishuProjectMcp__add_comment` | 添加评论 |
| `mcp__FeishuProjectMcp__get_download_url` | 获取附件签名下载 URL |
| `mcp__FeishuProjectMcp__search_user_info` | 查询用户信息 |
| `mcp__FeishuProjectMcp__update_field` | 更新工作项字段值 |

## Python 脚本速查

| 脚本 | 功能 | 用法 |
|------|------|------|
| `bug_analyzer.py` | 单 Bug 分析入口 | `python3 bug_analyzer.py feishu <id> --llm` |
| `analyzer.py` | LLM 分析核心引擎 | 由 bug_analyzer 调用 |
| `attachment_downloader.py` | 附件下载+日志提取 | 由 bug_analyzer 调用 |
| `code_search.py` | 跨仓库代码搜索（使用 nreal-code/） | 由 analyzer 调用 |
| `platform_detector.py` | 平台检测(glasses/host) | 由 analyzer 调用 |
| `report.py` | Markdown 报告生成 | `python3 report.py <bug_id>` |
| `fetch_all_bugs.py` | 全量缺陷数据抓取 | `python3 fetch_all_bugs.py` |
| `mcp_client.py` | MCP JSON-RPC 共享客户端 | 被其他脚本导入 |

## 关键参考文档

- `references/pipeline-architecture.md` — 完整分析流水线架构
- `references/source-resolution.md` — 代码版本-仓库映射策略
- `references/platform-detection-repo-filtering.md` — 平台检测与仓库过滤
- `references/report-format.md` — 分析报告格式规范
- `references/false-closed-bug-pitfall.md` — 误判关闭缺陷的处理
- `references/mql-blind-spot-fix.md` — MQL 分页盲区修复
- `references/llm-config-pitfalls.md` — LLM 配置常见问题
- `references/direct-api-json-issue.md` — Direct API 调用的 JSON 问题
- `references/comment-attachment-download.md` — 评论附件下载方案
- `references/changelog.md` — 变更记录

## 环境变量参考

| 变量 | 说明 | 必需 |
|------|------|------|
| `BUG_INSIGHT_FEISHU_MCP_TOKEN` | MCP Server Token | ✅ |
| `BUG_INSIGHT_FEISHU_PLUGIN_ID` | 飞书 Plugin ID | ✅ |
| `BUG_INSIGHT_FEISHU_PLUGIN_SECRET` | 飞书 Plugin Secret | ✅ |
| `BUG_INSIGHT_FEISHU_USER_KEY` | 飞书 User Key | ✅ |
| `LLM_API_KEY` | LLM API Key | ✅ (LLM 模式) |
| `LLM_BASE_URL` | LLM API 地址 | 可选 |
| `GITHUB_TOKEN` | GitHub Token（代码搜索） | 可选 |
| `GITHUB_USER` | GitHub 用户名 | 可选 |
| `FEISHU_PROJECT_KEY` | 默认飞书项目 Key | 可选 |
| `FEISHU_APP_ID` | 飞书 App ID（群聊消息） | 可选 |
| `FEISHU_APP_SECRET` | 飞书 App Secret | 可选 |
| `FEISHU_CHAT_ID` | 群聊 ID（构建记录） | 可选 |

## 注意事项

- **MCP Token**：优先读 `BUG_INSIGHT_FEISHU_MCP_TOKEN`，fallback 到 `~/.mcporter/mcporter.json`
- **评论 ID 精度**：19 位整数需 `parse_int=str` 处理（已在 `mcp_client.py` 中实现）
- **代码仓库**：统一使用项目根目录 `nreal-code/`（由 code-fetcher skill 管理），共 11 个仓库全覆盖
- **输出目录**：分析结果默认写入 `/tmp/`，缓存写入 `~/.openviking/workspace/feishu-bugs/`
- **Skill 目录**：不要在 skill 目录下写入运行时数据（缓存、JSON 报告等）

## 项目配置

> **重要**：涉及多个飞书项目，查询 bug 时必须同时搜索所有项目，除非用户明确指定。

| 项目标识 (project_key) | 项目名称 | 代码仓库 | 说明 |
|------------------------|---------|---------|------|
| `axr` | AXR | dove, ferrit, framework, heron, leopard, project, util, xr_codec, nrsdkrepo | XR 相关缺陷 |
| `sw_team` | SW Team | framework, project, ov580_driver, sparrow, util, xr_codec, nrsdkrepo | 软件团队缺陷（主） |

## 飞书项目 URL 格式

```
https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}
```

## MCP 快速使用

### 查询缺陷

通过 MCP 工具直接查询。**未指定项目时，必须对所有项目逐一查询并合并结果。**

```sql
-- 单项目查询 (替换 {project_key})
SELECT `work_item_id`, `name`, `work_item_status`, `start_time`, `priority`
FROM `{project_name}`.`issue`
WHERE `work_item_status` IN ({状态列表})
  AND RELATIVE_DATETIME_BETWEEN(`start_time`, 'past', '30d')
ORDER BY `start_time` DESC LIMIT 50
```

> 状态标签：AXR 用 `'OPEN', 'IN PROGRESS', 'REOPENED'`；SW Team 用 `'Open', 'In progress', 'Reopened'`。

### 获取详情/评论/追加

```
get_workitem_brief → 获取缺陷详情（需传 fields 参数查附件）
list_workitem_comments → 获取评论
add_comment → 追加评论（禁止 @ 分析无关人员）
```

### 删除评论

> MCP 无 `delete_comment` 工具，必须通过 Direct API。

```bash
python3 .claude/scripts/delete_comment.py <project_key> issue <work_item_id> <comment_id>
```
需要环境变量：`BUG_INSIGHT_FEISHU_PLUGIN_ID`、`BUG_INSIGHT_FEISHU_PLUGIN_SECRET`、`BUG_INSIGHT_FEISHU_USER_KEY`

## MCP 评论格式规范

**@人员**（先用 `search_user_info` 获取 lark_user_id。分析评论中禁止 @人员）：
```
@张三<!-- mention:{"id":"lark_user_id_xxx","cn_name":"张三","blockType":"AT_USER_BLOCK"} -->
```

**图片**（先用 `upload_file` 上传获取 file_token）：
```
![图片名称](图片URL)<!-- file_token -->
```

## 自动分析模式（MCP 原生）

> 不同于 Mode 2（Python 脚本），此模式通过 MCP + Cron 直接在 Claude Code 中实现，无需 Python 脚本。

### 配置来源

自动分析配置存储在 `bug-auto-analyzer-config` memory 中，包含：项目列表、扫描参数、已分析 bug 列表、轮转策略、模式状态。

### 启动

1. 更新 memory 将 `mode` 设为 `auto`
2. 创建 Cron（每 10 分钟，自续期）：
   ```
   CronCreate(cron: "*/10 * * * *", recurring: true, durable: true, prompt: "执行自动扫描...")
   ```
3. 立即首次扫描

Cron 限制：session-only 不会持久化；自续期绕过 7 天过期；另一台电脑需手动启动。

### 停止

1. `CronDelete` 删除
2. memory 切回 `manual`

### 扫描流程

每次 Cron 触发时，持续分析两个项目所有未分析 Bug 直到全部分析完毕。

1. **轮转**：`last_project` 决定下一个项目（AXR ↔ SW Team）
2. **查询**：MQL 查 30 天内未解决 Bug
3. **跳过**：评论含 `by Claude Code` 标记 或 已在 `analyzed_bugs` 列表
4. **分析**：下载附件 → 搜索代码 → LLM 根因 → 写评论 → **更新 AI分析 字段为 SDK**
5. **记录**：追加到 `analyzed_bugs`，更新 `last_scan_time`

### 日志分析铁律：全量异常扫描（最高优先级）

**分析任何日志文件时，必须先做全量异常关键词扫描，不能只搜特定模块或只关注显眼错误。** 这是最容易遗漏关键证据的环节。

#### 强制扫描规则

对日志目录下**每一个文本日志文件**，必须执行以下扫描：

```bash
grep -i -E 'error|fatal|fail' <日志文件>
```

| 关键词 | 说明 | 不区分大小写 |
|--------|------|:---:|
| `error` | 覆盖 `ERROR`、`Error`、`error`、`ERR` 等所有变体 | ✅ |
| `fatal` | 覆盖 `FATAL`、`Fatal`、`fatal` 等所有变体 | ✅ |
| `fail` | 覆盖 `FAIL`、`Fail`、`failed`、`failure` 等所有变体 | ✅ |

**扫描范围**：每一行文本，不仅仅是日志级别字段。`[ERROR] [Leopard] BaseBidiStreamReactor::OnReadDone` 这种带模块标签的错误、`failed to xxx` 这种描述性失败，都要命中。

#### 常见陷阱

- ❌ 只搜 `NRDpGetFrame`、`DpGetFrame` 等特定模块关键词 → 会漏掉其他模块的错误
- ❌ DP 管线 871 次重复错误太显眼，分析焦点被吸引过去，忽略了旁边 Leopard gRPC 的 1 条 ERROR
- ❌ 只关注数量多的错误，忽略只出现一两次但更致命的错误

#### 分析 checklist

1. **每个日志文件**执行 `grep -i -E 'error|fatal|fail'`
2. 按**模块**归类所有命中行（不要按数量排序，模块级错误重复度不同）
3. **时间戳对齐**：将不同日志文件的异常时间点对齐，找出关联
4. 任何命中的行都要纳入分析，**不能选择性忽略**

### 分析结论格式

- 标题：`## 🔍 AI分析结论 (by Claude Code + {模型})`
- 附带缺陷链接
- 注明显具体日志文件
- 包含置信度评估
- 末尾：`> ⚠️ 此分析来源于 AI（Claude Code + {模型}），仅供参考。`
- 禁止 @ 任何人，不重复缺陷概要信息

## AI 分析字段标记

分析完成后，自动将缺陷的「AI分析」字段设为「SDK」，用于标记该缺陷已被 AI 分析过。

- **字段 Key**：`field_d381f5`
- **字段类型**：multi-select
- **可选值**：SDK（option_id: `x5q6hij1t`）

### Python 脚本方式

```python
from mcp_client import mcp_update_field
mcp_update_field(project_key, bug_id, "field_d381f5", '[{"option_id": "x5q6hij1t"}]')
```

### MCP 原生方式（Cron 自动分析）

```
mcp__FeishuProjectMcp__update_field(
    project_key="axr",
    work_item_id="7006873847",
    fields=[{"field_key": "field_d381f5", "field_value": "[{\"option_id\": \"x5q6hij1t\"}]"}]
)
```
