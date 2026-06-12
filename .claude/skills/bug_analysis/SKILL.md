---
name: bug_analysis
description: >
  Feishu Project bug analysis pipeline - fetch bugs from multiple projects,
  download attachments, search source code, analyze root causes with LLM,
  and post analysis reports as Feishu comments.
  Supports manual single-bug analysis and cron-based automated batch analysis.
  Triggers on: 飞书缺陷分析, analyze bug, 缺陷根因分析, 定时分析缺陷, feishu bug,
  setup bug cron, 设置缺陷自动分析.
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

### 快速流程

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
   评论以 `[AI分析]` 前缀标记。

4. **读取分析结果**：
   ```bash
   cat /tmp/bug_<bug_id>_analysis_*.json
   ```

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
- **代码仓库**：统一使用项目根目录 `nreal-code/`（由 nreal-code skill 管理），共 11 个仓库全覆盖
- **输出目录**：分析结果默认写入 `/tmp/`，缓存写入 `~/.openviking/workspace/feishu-bugs/`
- **Skill 目录**：不要在 skill 目录下写入运行时数据（缓存、JSON 报告等）
