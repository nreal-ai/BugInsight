# Bug Analysis Pipeline Architecture

Verified from code review of `scripts/analyzer.py` and `scripts/bug_analyzer.py` on 2026-06-08.

## Data Flow

```
Feishu Bug (Direct API + MCP)
    ├── bug_info (title, description, status)
    ├── comments (list_workitem_comments)
    └── attachments (multi_attachment field)
           │
           ▼
┌─────────────────────────────────────────────┐
│  bug_analyzer.py::cmd_feishu               │
│  1. _find_feishu_bug() → cache lookup      │
│  2. analyze_feishu_bug() → classify comments│
│  3. _fetch_live_feishu_data() → refresh    │
│  4. download_bug_attachments() → logs      │
│  5. full_analysis(log+comments+bug_desc)   │
│  6. llm_analyze(result, force=True)        │
└─────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│  analyzer.py::full_analysis()              │
│  - detect_platform(glasses/host)           │
│  - extract_versions()                      │
│  - BuildVersionDB.query_for_bug()          │
│  - BspVersionDB.query_for_bug()            │
│  - analyze_log() if log_content present    │
│  - infer_root_cause()                      │
│  - find_similar_bugs()                     │
└─────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│  analyzer.py::llm_analyze()                │
│  - multi-round (1-3 rounds)                │
│  - _build_llm_prompt() → inject all data   │
│  - LLM call (qwen3.6-plus)                 │
│  - termination: max_rounds/confidence/ENOUGH_INFO │
└─────────────────────────────────────────────┘
```

## Comment Classification (bug_analyzer.py::analyze_feishu_bug)

Technical evidence patterns:
- `SIG\w+`, `Segmentation fault`, `Bus error`, `Aborted`
- `Exception`, `NullPointerException`, `IllegalArgumentException`
- `FATAL`, `CRASH`, `tombstone`, `backtrace`, `stacktrace`
- `0x[0-9a-fA-F]{8,}` (memory addresses)
- `at \w+\.\w+\.java:\d+` (Java stack frames)
- `Error\s*[:=]`
- `memory\s*(leak|不足|溢出|kill|OOM)`
- `log`, `日志`, `logcat`, `adb`

Noise patterns (filtered out):
- Pure markdown image: `!\[.*?\]\(.*?\)`
- Pure URL to image files

## Prompt Sections (injection order)

1. 平台检测 (glasses/host + 目标仓库 + 日志类型)
2. 版本信息 (Dove/NRSDK/BSP/HMD)
3. 飞书构建版本-仓库映射 (from build_version_db)
4. BSP 固件版本信息
5. Bug 基本信息 (title/description/status, 300 chars each)
6. 关键词
7. 错误摘要 (error_count/warning_count/fatal_count + summary flags)
8. 置信度细分
9. 数据覆盖 (attachment download stats)
10. 附件状态
11. 错误码 (600 chars budget)
12. 主要错误 (2000 chars budget, priority scoring)
13. 警告信息 (800 chars budget)
14. 堆栈跟踪 (3000 chars budget)
15. 已有推断 (rule engine result)
16. Native Crash 检测 (1200 chars budget)
17. 时序问题检测 (600 chars budget)
18. Shader/渲染错误检测 (600 chars budget)
19. 飞书已知信息 (attachment list)
20. 飞书结论性信息 (solution comments, max 3)
21. 代码上下文 (4000 chars budget, max 12 files)
22. 飞书技术证据 (1000 chars budget, max 10 entries)
23. 下载的日志文件内容 (200K chars budget, Top 10 files)
24. 历史相似缺陷 (2000 chars budget, max 5 bugs)

## Cron Limitation

`cron_auto_analyze.py` only detects NEW OPEN bugs (ID comparison against cache).
It does NOT:
- Detect new comments on existing bugs
- Re-analyze bugs that already have [AI分析] comments
- Pick up developer replies to existing bugs

For existing bugs with new comments: run `bug_analyzer.py --llm <bug_id>` manually.
