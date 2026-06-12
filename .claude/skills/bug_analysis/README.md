# 飞书缺陷自动分析工具 (Feishu Bug Analysis Pipeline)

## 项目简介

飞书 Project 缺陷分析流水线。支持手动分析单个 Bug 和 Cron 定时自动批量分析。通过 MCP 工具直接与飞书交互，下载日志附件，搜索 nreal-code 代码仓库，进行根因分析，并在飞书缺陷评论中提交 AI 分析报告。

## 核心功能

- **手动分析**: 说"分析飞书缺陷 XXX"，Claude 直接通过 MCP 工具完成全流程
- **自动分析**: Cron 定时触发，Claude MCP 原生执行，零 Python 脚本依赖
- **附件智能下载**: 自动下载日志、crash dump 等附件，智能解压 ZIP
- **多仓库代码搜索**: 根据项目（AXR/SW Team）自动限定代码仓库范围
- **飞书评论自动提交**: 分析结论按规范格式写入飞书缺陷评论
- **已分析跳过**: 通过 `by Claude Code` 标记自动跳过已分析 Bug

## 架构概览

```
Claude Code CronCreate (每 10 分钟)
    │
    ▼
MCP 原生自动分析流程
    ├── search_by_mql              # MQL 查询 30 天内未解决 Bug
    ├── list_workitem_comments     # 检查是否已有 AI 分析评论
    ├── get_workitem_brief         # 获取缺陷详情和附件
    ├── get_download_url + curl    # 下载并解压附件
    ├── nreal-code/ 代码搜索       # 跨仓库定位相关代码
    ├── Claude 根因分析            # LLM 推理 + 代码关联
    └── add_comment                # 写入飞书分析评论
```

## 使用方法

### 手动分析单个 Bug

在 Claude Code 中说：`分析飞书缺陷 <ID或链接>`

### 启动自动分析

说"开始自动分析" 或 "启动自动分析"。Claude 会：
1. 更新 memory 配置切换为 auto 模式
2. 创建 Cron 定时任务（每 10 分钟）

### 停止自动分析

说"停止自动分析"。

### 运行 Python 脚本分析（需完整环境变量）

```bash
cd .claude/skills/bug_analysis/scripts/
python3 bug_analyzer.py feishu <缺陷ID> --llm
```

## 目录结构

```
bug_analysis/
├── SKILL.md                      # Claude Code 技能定义文档
├── README.md
├── config.yaml                   # 项目配置（敏感信息通过环境变量加载）
├── .gitignore
├── scripts/                      # Python 脚本（辅助工具）
│   ├── bug_analyzer.py           # 缺陷分析入口
│   ├── analyzer.py               # LLM 分析核心引擎
│   ├── config.py                 # 配置加载模块
│   ├── code_search.py            # 跨仓库代码搜索
│   ├── attachment_downloader.py  # 附件下载
│   ├── platform_detector.py      # 平台检测
│   ├── report.py                 # 报告生成
│   ├── fetch_all_bugs.py         # 全量缺陷获取
│   └── mcp_client.py             # MCP JSON-RPC 共享客户端
├── references/                   # 参考文档
└── .gitignore
```

## 许可

内部使用，请勿外传。
