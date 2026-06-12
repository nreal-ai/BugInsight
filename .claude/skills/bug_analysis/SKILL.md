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
   cd .claude/skills/bug_analysis/scripts && BUG_ANALYZER_MAX_ROUNDS=2 python3 bug_analyzer.py feishu <bug_id> --llm
   ```
   脚本自动完成：附件下载 → 平台检测 → 代码搜索 → LLM 根因分析 → 生成报告 JSON

3. **回写评论**（可选，询问用户是否需要）：
   ```
   mcp__FeishuProjectMcp__add_comment(work_item_id="<bug_id>", project_key="sw_team", content="<分析报告>")
   ```

4. **读取分析结果**：
   ```bash
   cat /tmp/bug_<bug_id>_analysis_*.json
   ```

### 快速流程（MCP 手动分析）

当环境变量不完整（缺少 LLM_API_KEY 等）时，Claude 将直接通过 MCP 工具 + 代码搜索执行手动分析：

1. **获取 Bug 详情**：`get_workitem_brief(fields=["_all"])`
2. **下载附件并提取日志**
3. **搜索 nreal-code/ 相关代码**
4. **编译分析报告并写入评论**

### 评论格式要求（必需）

无论使用哪种分析方式，写入飞书评论时**必须**遵循以下格式：

- 标题必须为：`## 🔍 AI分析结论 (by Claude Code + {模型名称})`
- 模型名称从 `config.yaml` 的 `llm.model` 读取（如 `deepseek-v4-pro`）
- **必须包含置信度评估章节**（评分制，非星级制），格式：
  ```markdown
  ### 置信度评估
  
  | 维度 | 得分 | 说明 |
  |------|------|------|
  | 日志完整性 | x.xx/0.10 | 日志覆盖情况说明 |
  | 错误明确性 | x.xx/0.10 | 错误码/异常指向性说明 |
  | 根因确定性 | x.xx/0.15 | 代码定位精度说明 |
  
  **综合：🟢/🟡/🔴 x.xx（高/中/低置信度）**
  ```
  - 综合得分 = 三维度之和，满分 0.35
  - 等级：🟢 高 (≥0.30) | 🟡 中 (0.20-0.29) | 🔴 低 (<0.20)
  - **禁止使用**星级（⭐⭐⭐⭐⭐）或百分比替代
- 必须包含「证据链」章节（直接证据 / 间接证据 / 辅助证据）
- 末尾标注：`> ⚠️ 此分析来源于 AI（Claude Code + {模型名称}），仅供参考。`

完整格式规范见 `references/report-format.md`。

### 自定义参数

- 指定项目：`python3 bug_analyzer.py feishu <bug_id> --project <project_key> --llm`
- 只做规则分析（不用 LLM）：去掉 `--llm` 参数
- 查看报告：`python3 report.py <bug_id>`

---

## Mode 2: 定时自动分析（Cron）

**触发**：用户说"设置缺陷自动分析"、"setup bug cron"、"每 N 小时自动分析飞书新缺陷"

### 设置流程

1. **确认项目列表**：`scripts/cron_auto_analyze.py` 中 `PROJECT_KEYS` 默认为 `["sw_team", "676e7fecad8e9de8735fa89f"]`。如需修改告知用户。

2. **创建 Cron 任务**：使用 Claude Code `CronCreate` 工具：
   ```
   CronCreate(
     cron: "7 */2 * * *",  // 每 2 小时，分钟避开 0/30 高峰
     prompt: "Run bug_analysis cron: cd /Users/apple/WorkSpace/HermesBugInsight/.claude/skills/bug_analysis/scripts && python3 cron_auto_analyze.py. After completion, read /tmp/cron_auto_analyze_last_output.txt and summarize: how many new bugs found, analyzed, comments posted, errors.",
     recurring: true
   )
   ```
   根据用户要求的频率调整 cron 表达式。`durable: true` 可让任务跨 session 持久化。

3. **查看历史运行记录**：
   ```bash
   tail -100 /tmp/cron_auto_analyze_history.log
   ```

### Cron 工作流程

```
cron_auto_analyze.py 执行：
  1. MCP JSON-RPC 分页查询全部 OPEN 缺陷
  2. 对比本地缓存找新增 Bug
  3. 跳过已有 [AI分析] 评论的 Bug
  4. 跳过附件过多（>15）或 ZIP 过多（≥3）的 Bug
  5. 对每个新 Bug：运行 bug_analyzer.py --llm（带 900s 超时 + 600s 卡死检测）
  6. 通过 MCP JSON-RPC 回写 [AI分析] 评论
  7. 检测已关闭 Bug → 导入 OpenViking 向量库
  8. 写汇总到 /tmp/cron_auto_analyze_last_output.txt
```

### Cron 限制

- **仅检测新增 OPEN Bug**：不检测已有 Bug 的新评论
- **Session 内有效**：CronCreate 任务在 Claude Code session 结束时会停止（除非 `durable: true` 可用）
- **重试机制**：失败的 Bug 自动加入重试队列（最多 2 次），下次运行时自动重试

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

## Python 脚本速查

| 脚本 | 功能 | 用法 |
|------|------|------|
| `bug_analyzer.py` | 单 Bug 分析入口 | `python3 bug_analyzer.py feishu <id> --llm` |
| `cron_auto_analyze.py` | 定时自动批量分析 | `python3 cron_auto_analyze.py` |
| `analyzer.py` | LLM 分析核心引擎 | 由 bug_analyzer 调用 |
| `attachment_downloader.py` | 附件下载+日志提取 | 由 bug_analyzer 调用 |
| `code_search.py` | 跨仓库代码搜索（使用 nreal-code/） | 由 analyzer 调用 |
| `platform_detector.py` | 平台检测(glasses/host) | 由 analyzer 调用 |
| `report.py` | Markdown 报告生成 | `python3 report.py <bug_id>` |
| `fetch_all_bugs.py` | 全量缺陷数据抓取 | `python3 fetch_all_bugs.py` |
| `mcp_client.py` | MCP JSON-RPC 共享客户端 | 被其他脚本导入 |

## 关键参考文档

- `references/pipeline-architecture.md` — 完整分析流水线架构
- `references/cron-pipeline.md` — Cron 定时任务详解
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
4. **分析**：下载附件 → 搜索代码 → LLM 根因 → 写评论
5. **记录**：追加到 `analyzed_bugs`，更新 `last_scan_time`

### 分析结论格式

- 标题：`## 🔍 AI分析结论 (by Claude Code + {模型})`
- 附带缺陷链接
- 注明显具体日志文件
- 包含置信度评估
- 末尾：`> ⚠️ 此分析来源于 AI（Claude Code + {模型}），仅供参考。`
- 禁止 @ 任何人，不重复缺陷概要信息
