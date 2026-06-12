# 飞书缺陷自动分析工具 (Feishu Bug Analysis Pipeline)

## 项目简介

飞书 Project 缺陷自动分析流水线。定时巡检飞书项目中的新增缺陷，自动下载日志附件，关联代码仓库进行根因分析，并在飞书缺陷评论中自动提交 AI 分析报告。已关闭的缺陷会自动同步到 OpenViking 向量知识库。

## 核心功能

- **缺陷自动发现**: 通过飞书 MCP API 定时巡检新增 Bug
- **附件智能下载**: 自动下载日志、crash dump、图片等附件，智能解压 ZIP 并提取关键内容
- **多仓库代码搜索**: 根据缺陷平台（眼镜端/主机端）自动过滤相关代码仓库，定位对应版本的 commit
- **LLM 多轮根因分析**: 支持规则引擎 + LLM（deepseek-v4-pro）混合分析
- **飞书评论自动提交**: 分析结论自动写入飞书缺陷评论
- **定时任务**: Claude Code CronCreate 实现自动巡检（可自定义频率）
- **OpenViking 同步**: 已关闭缺陷自动导入向量知识库
- **失败重试机制**: 分析失败的 Bug 自动加入重试队列

## 架构概览

```
Claude Code CronCreate
    │
    ▼
cron job (定时触发)
    │
    ▼
scripts/cron_auto_analyze.py          # 定时任务主脚本
    ├── fetch_all_bugs()              # 获取全量缺陷列表（MCP JSON-RPC）
    ├── check_attachment_count()      # 附件数量预检查（跳过 ZIP 过多/附件过多的缺陷）
    ├── bug_analyzer.py --llm         # 调用分析引擎
    │   ├── analyzer.py               # LLM 分析核心引擎
    │   ├── code_search.py            # 代码搜索（跨仓库）
    │   ├── attachment_downloader.py  # 附件下载
    │   ├── platform_detector.py      # 平台检测（glasses vs host）
    │   ├── manifest_parser.py        # manifest 解析
    │   ├── git_cloner.py             # 代码仓库自动克隆
    │   ├── version_extractor.py      # 版本信息提取
    │   ├── build_version_db_query.py # 构建版本数据库查询
    │   ├── bsp_version_query.py      # BSP 版本查询
    │   ├── version_repo_mapper.py    # 版本-仓库映射
    │   └── report.py                 # 报告生成
    ├── mcp_client.mcp_add_comment()  # 提交飞书评论（MCP JSON-RPC）
    └── OpenViking import             # 导入已关闭缺陷
```

## 快速部署

### 前置条件

- Python 3.11+
- Claude Code（已安装并配置飞书 MCP Server）
- OpenViking（可选，用于向量库同步）

### 1. 配置凭证

通过 shell 环境变量设置（推荐在 Claude Code settings.json 中配置）：

```bash
# 飞书 MCP 凭证（必需）
export BUG_INSIGHT_FEISHU_MCP_TOKEN=m-xxxx
export BUG_INSIGHT_FEISHU_PLUGIN_ID=cli_xxxx
export BUG_INSIGHT_FEISHU_PLUGIN_SECRET=xxxx
export BUG_INSIGHT_FEISHU_USER_KEY=xxxx

# GitHub（用于克隆 nreal-ai/* 代码仓库）
export GITHUB_USER=你的GitHub用户名
export GITHUB_TOKEN=ghp_xxxx

# LLM 分析（用于 bug 根因分析推理）
export LLM_API_KEY=sk-xxxx
export LLM_BASE_URL=https://api.deepseek.com/v1

# 飞书群聊（可选，用于构建版本-仓库映射）
export FEISHU_APP_ID=app_xxxx
export FEISHU_APP_SECRET=xxxx
export FEISHU_CHAT_ID=oc_xxxx

# OpenViking（可选，向量知识库）
export OPENVIKING_API_KEY=xxxx
```

或运行交互式配置脚本（写入 skill 本地 .env）：
```bash
cd .claude/skills/bug_analysis/scripts && python3 setup.py
```

检查配置状态：
```bash
cd .claude/skills/bug_analysis/scripts && python3 setup.py --check
```

### 2. 启动定时任务

在 Claude Code 中说"设置缺陷自动分析"或"每 2 小时自动分析飞书新缺陷"，Skill 会自动调用 `CronCreate` 创建定时任务。也可手动创建：

```
CronCreate:
  cron: "7 */2 * * *"
  prompt: "Run bug_analysis cron: cd <skill_dir>/scripts && python3 cron_auto_analyze.py..."
  recurring: true
```

## 手动运行

### 运行完整分析流程

```bash
cd .claude/skills/bug_analysis/scripts/
python3 cron_auto_analyze.py
```

### 分析单个缺陷

```bash
cd .claude/skills/bug_analysis/scripts/
python3 bug_analyzer.py feishu <缺陷ID> --llm
```

### 获取全量缺陷数据

```bash
cd .claude/skills/bug_analysis/scripts/
python3 fetch_all_bugs.py
```

## 配置文件

### config.yaml

项目主配置文件，位于项目根目录。敏感值通过 `_env` 后缀从环境变量加载，**不要在此文件中存放明文凭证**。

```yaml
llm:
  api_base_env: LLM_BASE_URL    # 从环境变量 LLM_BASE_URL 读取
  model: "deepseek-v4-pro"
  api_key_env: LLM_API_KEY
  max_tokens: 4096
  temperature: 0.3

openviking:
  api_base: "http://127.0.0.1:1933"
  api_key_env: OPENVIKING_API_KEY

feishu:
  project_key_env: FEISHU_PROJECT_KEY
  mcp_key_env: FEISHU_MCP_TOKEN
  plugin_id_env: FEISHU_PLUGIN_ID
  plugin_secret_env: FEISHU_PLUGIN_SECRET
  user_key_env: FEISHU_USER_KEY

repositories/config.yaml — 代码仓库配置，定义各仓库的 Git URL 和分支。使用 `{GITHUB_USER}` 和 `{GITHUB_TOKEN}` 占位符，运行时从环境变量替换。

## 目录结构

```
ai_bug_analysis/
├── SKILL.md                      # Claude Code 技能定义文档
├── config.yaml                   # 项目配置（敏感信息通过环境变量加载）
├── .gitignore
├── scripts/                      # Python 脚本
│   ├── cron_auto_analyze.py      # 定时任务主脚本
│   ├── bug_analyzer.py           # 缺陷分析入口
│   ├── analyzer.py               # LLM 分析核心引擎
│   ├── config.py                 # 配置加载模块
│   ├── code_search.py            # 跨仓库代码搜索
│   ├── attachment_downloader.py  # 附件下载
│   ├── platform_detector.py      # 平台检测（glasses vs host）
│   ├── report.py                 # 报告生成
│   ├── git_cloner.py             # 代码仓库管理
│   ├── manifest_parser.py        # manifest 解析
│   ├── version_extractor.py      # 版本提取
│   ├── build_version_db_query.py # 构建版本查询
│   ├── bsp_version_query.py      # BSP 版本查询
│   ├── version_repo_mapper.py    # 版本-仓库映射
│   ├── fetch_all_bugs.py         # 全量缺陷获取
│   ├── auto_analyze.py           # 独立分析工具
│   ├── similar_bugs.py           # 相似缺陷查询
│   ├── mcp_client.py             # MCP JSON-RPC 共享客户端
│   ├── import_to_openviking_v2.py # OpenViking 导入
│   └── setup.py                  # 凭证配置脚本
├── repositories/
│   ├── config.yaml               # 代码仓库配置（Git URL、分支、平台分组）
│   ├── manager.py                # 仓库管理器（自动克隆、checkout）
│   └── clones/                   # 运行时自动克隆的代码仓库（不提交到 Git）
├── references/                   # 参考文档（架构说明、已知问题、最佳实践）
└── tests/                        # 测试套件
```

## 已知问题与注意事项

### LLM 分析超时

含大量日志附件（如 ZIP 压缩包含 68 个文件）的缺陷会导致超时。脚本会自动跳过 3+ ZIP 文件或 15+ 总附件的缺陷。

### 评论 ID 精度丢失

19 位大整数 comment_id 需要 `parse_int=str` 处理。已通过 MCP JSON-RPC 直接调用（`mcp_client.py`）解决，不再依赖 mcporter CLI。

### 误判关闭缺陷

MQL 分页可能导致缺陷状态误判。已使用两步验证：MQL 缓存 diff 识别候选 + Direct API `issue/query` 确认。

### Cron Session 限制

CronCreate 任务在 Claude Code session 结束时会停止（除非 `durable: true` 可用）。如需 7×24 持久化 cron，建议使用系统 crontab 或 launchd 作为备选方案。

## 许可

内部使用，请勿外传。
