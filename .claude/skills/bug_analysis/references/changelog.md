---
name: changelog
description: bug-analyzer 历史修复记录与性能优化记录
---

## 已知问题与修复记录 (历史)

> 以下为已修复的历史问题，仅供审计参考。活跃陷阱见下方"关键陷阱"章节。

| 问题 | 修复方案 | 日期 |
|------|----------|------|
| `enrich_cache.py` 缓存结构 key 不匹配 | `bugs_attachments.json` 使用 `id` 而非 `workItemId`，使用 `attachments` 而非 `files`；已修复字段映射 | 2026-04-29 |
| `enrich_cache.py` `work_id` 变量名拼写错误 | 两处 `work_id` 应为 `work_item_id`，已修复 | 2026-04-29 |
| `enrich_cache.py` mcporter 输出解析路径错误 | `list_workitem_comments` 返回 `{"comments": [...]}` 而非 `{"data": {"list": [...]}}`；已修复 | 2026-04-29 |
| `enrich_cache.py` 后台进程无输出 (Python 缓冲) | 添加 `sys.stdout.reconfigure(line_buffering=True)` + `flush=True` | 2026-04-29 |
| `similar_bugs.py` 中 `category` 变量未初始化导致 `UnboundLocalError` | 在 `_parse_bug_from_uri` 备用路径开头添加 `category = ""` 初始化 | 2026-04-27 |
| `llm_analyze` prompt 未注入飞书技术证据和下载日志全文 | 在 prompt 中新增「飞书技术证据」「事件时间线」「下载的日志文件内容」三个区块 | 2026-04-29 |
| `call_llm()` 非流式请求导致大 prompt 超时 | `call_llm()` 改为 `stream=True` + per-chunk timeout (15, 300)，max_tokens 4096→2048 | 2026-05-06 |
| `cmd_feishu` 仅依赖缓存数据 | 新增 `_fetch_live_feishu_data` 通过 Direct API 实时拉取评论和附件元数据 | 2026-04-29 |
| `attachment_downloader.py` 缺少实时数据获取能力 | 新增 `fetch_live_bug_data` 函数 | 2026-04-29 |
| 下载日志内容未传递到 LLM prompt | `cmd_feishu` 中注入 `result['downloaded_log_contents']` | 2026-04-29 |
| 日志文件按前 N 字符硬截断 | 新增 `smart_extract_log_content()` 基于严重性评分智能截取 ±40 行 | 2026-04-29 |
| 飞书附件下载后全量读取导致上下文溢出 | `download_bug_attachments` 自动调用 `smart_extract_log_content` | 2026-04-29 |
| LLM 分析为一次性单轮调用 | 升级为有限轮次多代理交互模式（观察员→调查员→裁判）| 2026-04-29 |
| `_build_llm_prompt` 未抽离为独立方法 | 新增 `_build_llm_prompt()` 支持 `round_num` 参数 | 2026-04-29 |
| `config.py` 缺少 `apply_env_refs` 函数和 `.env` 加载逻辑 | 补充 `apply_env_refs()` 和 `~/.hermes/.env` 加载 | 2026-04-27 |
| `analyzer.py` 缺少 `import tempfile` | 补充导入 | 2026-04-27 |
| `_build_bug_index` 数据路径错误 | 实际数据在 `~/.openviking/workspace/feishu-bugs` | 2026-04-27 |
| 嵌套 Feishu 数据格式不匹配 | `bugs_details_full.json` 的 ID 嵌套在 `work_item_attribute` 中 | 2026-04-27 |
| ANR检测漏报 | W/WARN 级别直接计入 warning_count + 全局 ANR 关键词扫描 | 2026-04-23 |
| 本地搜索中文查询失效 | 改用 `re.findall(r'[一-龥]+|[a-zA-Z0-9]+', query)` | 2026-04-27 |
| OpenViking 搜索端点错误 | 改为 `/api/v1/search/find` 并检查 `memories` 和 `resources` | 2026-04-27 |
| `_evaluate_log_source` 在 errors/warnings 为字符串列表时崩溃 | 改为 `e.get("content", "") if isinstance(e, dict) else str(e)` | 2026-04-28 |
| `cmd_feishu` 重复调用 `find_similar_bugs` | 已移除冗余调用 | 2026-04-28 |
| `cmd_feishu` 覆盖 `full_analysis` 的根因结果 | 已移除覆盖行 | 2026-04-28 |
| 缺陷描述为空但标题有关键词时根因推断失效 | 修复后当 desc 为空时 fallback 到 title | 2026-04-28 |
| JSON报告缺少 bug_id/title 字段 | 已在 `cmd_feishu` 中显式注入 | 2026-04-28 |
| macOS `credential.helper=osxkeychain` 覆盖 URL 中的 token | `manager.py` 使用 `git -c credential.helper=` 禁用钥匙串 | 2026-04-28 |
| `_fetch_live_feishu_data` 评论获取失败 | 端点改为 `/comments`（复数）；JSON 解析改为 `raw_decode()` | 2026-04-29 |
| 二进制附件(zip)被当作文本读取导致误报 | `is_log_file` 增加二进制扩展名排除列表 + null byte 检测 | 2026-04-29 |
| 压缩附件未自动解压 | `is_archive_file` 识别归档格式，下载后自动解压 | 2026-04-29 |
| `get_attachment_uuids` 解析 JSON 字符串附件失败 | 增加 `isinstance(f, str)` + `json.loads(f)` 解析 | 2026-04-30 |
| `cmd_feishu` 实时附件数据被 re-analyze 覆盖 | 合并时同步更新 `bug_info['attachments']` | 2026-04-30 |
| 证据链缺少归档文件上下文和目录树 | `extract_archive()` 返回目录树，`report.py` 附加上下文 | 2026-04-30 |
| 报告中的错误/警告/线索缺少文件溯源 | `smart_extract_log_content` 增加 `# FILE: | LINE: ` 前缀 | 2026-04-30 |
| `bug_analyzer.py` 中 `log_contents` 值类型不一致 | 增加 `isinstance(content, str)` 防御性检查 | 2026-04-30 |
| LLM调用失败 | 替换 subprocess/curl 为 requests | 2026-04-23 |
| 根因推断不完整 | 使用增强检测数据构建完整上下文 | 2026-04-23 |
| ROOT_CAUSE_RULES 根因太笼统 | 从10条扩展为24条，按具体场景→通用兜底排序 | 2026-04-28 |
| 数据文件路径失效 | _find_feishu_bug() 数据源优先级重构 | 2026-04-28 |
| similar_bugs.py._get_bug_detail() 只读取不存在的文件 | 增加 .bug_index_cache.json 和 bugs_index.json 为前两级数据源 | 2026-04-28 |
| 模型名称错误 (qwen3-coder-plus) | 全部更新为 qwen3.6-plus | 2026-04-28 |
| `_build_bug_index()` 缓存命中时返回 None | 调用方不应依赖返回值，无异常即成功 | 2026-04-28 |
| report.py 根因显示规则引擎结果而非 LLM 分析 | `generate_markdown_report` 新增 LLM 根因/解决方案提取 | 2026-04-30 |
| LLM timeout 过短 | `call_llm()` 改为 `stream=True` + SSE 解析 | 2026-05-06 |
| `similar_bugs.py` 方法名是 `find_by_keyword` 而非 `search_similar_bugs` | 已修正 | 2026-04-28 |
| `full_analysis()` 不接受 `git_urls` 参数 | 签名仅接受 `log_content` 和 `comments` | 2026-04-28 |
| `_find_feishu_bug_data` 函数不存在 | 使用 `BugAnalyzerCLI._find_feishu_bug(id)` 实例方法 | 2026-04-28 |
| `print_config_check()` 仅在配置缺失时输出 | 配置完整时返回 True 无 stdout | 2026-04-28 |
| ANR 检测不在顶层字段 | ANR 出现在 `errors` 列表中 (type='ANR') 或 `summary.has_crash` | 2026-04-28 |

## 性能优化记录

| 优化项 | 文件 | 效果 | 实现方式 |
|--------|------|------|----------|
| P0.1 代码搜索加速 | `code_search.py` | `os.walk` → `rg --json`，10-100x I/O 提升 | ripgrep 子进程调用 |
| P0.2 倒排索引 | `analyzer.py` | 4060 条缺陷搜索 0.23s → 0.002s | `_build_bug_index()` 延迟加载 |
| P0.3 单次扫描 | `analyzer.py` | 30+ 次全文扫描 → 1 次交替正则匹配 | 合并为大交替正则 `finditer` |
| P2 路径解耦 | 全部脚本 | `/tmp/bug_analyzer` → `tempfile.mkdtemp()` | 避免多实例并发冲突 |
| P1 报告增强 | `report.py` | TL;DR + Markdown + 三维度增强 | 三个 report 函数均已覆盖 |
## multi_attachment JSON 字符串问题

**日期**: 2026-05

飞书 Direct API 返回的 `multi_attachment` 字段中，`field_value` 的元素可能是 JSON 字符串而非字典对象。

**修复**: 在 `attachment_downloader.py` 中添加 `isinstance(f, str)` + `json.loads(f)` 类型检查。

