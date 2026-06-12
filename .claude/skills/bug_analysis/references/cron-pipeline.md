# 飞书缺陷自动分析定时任务管道

## 脚本位置

`~/.hermes/skills/bug-analysis/feishu-bug-pipeline/scripts/cron_auto_analyze.py`

## 工作流程

1. 通过 MCP JSON-RPC 直接调用 `search_by_mql` + OFFSET 分页，获取项目**全部**缺陷列表
2. 解析 `moql_field_list` 嵌套格式，提取 `work_item_id`/`name`/`work_item_status`
3. 对比 `~/.openviking/workspace/feishu-bugs/.bug_index_cache.json` 找出新增 OPEN 缺陷
4. 逐个串行运行 `bug_analyzer.py feishu <ID> --llm`（每个超时 900s，前台等待）
5. 将 `llm_analysis` 完整内容注入飞书评论（非仅 root_cause 单行摘要）
6. 生成汇总报告

## MCP JSON-RPC 分页查询（2026-06-05 更新）

**核心变更**：废弃 mcporter CLI 调用，改用 MCP JSON-RPC 直接调用 + OFFSET 分页。

**原因**：mcporter `search_by_mql` 固定只返回前 50 条（LIMIT 无效），当项目有大量 OPEN 缺陷时，编号较小的缺陷会被永久遗漏（如 7006901369 在 604 条缺陷中排第 5 页，永远不会被前 50 条覆盖到）。

**实现**：
- `_get_mcp_token()`：从 `~/.mcporter/mcporter.json` 读取 `X-Mcp-Token`
- `_mcp_call()`：直接 POST 到 `https://project.feishu.cn/mcp_server/v1`，使用 JSON-RPC 格式
- `fetch_bugs()`：MQL `SELECT ... LIMIT 50 OFFSET N` 循环分页，直到返回数量 < 50
- 每页间隔 0.3s 限速，最多 30 页（1500 条上限）
- `_parse_moql_field_list()`：统一的 moql_field_list 解析函数，所有位置复用

## mcporter search_by_mql 数据格式陷阱

**重要**：返回的数据结构是 `moql_field_list` 嵌套格式，不是平铺字段。

```json
{
  "data": {
    "1": [
      {
        "moql_field_list": [
          {"key": "work_item_id", "value": {"long_value": 6983817306}},
          {"key": "name", "value": {"string_value": "缺陷标题..."}},
          {"key": "work_item_status", "value": {"key_label_value_list": [{"key": "OPEN", "label": "Open"}]}}
        ]
      }
    ]
  }
}
```

解析方法：
```python
bugs = []
for bug_raw in data.get("data", {}).get("1", []):
    fields = {}
    for field in bug_raw.get("moql_field_list", []):
        key = field.get("key")
        value_obj = field.get("value", {})
        if "long_value" in value_obj:
            fields[key] = str(value_obj["long_value"])
        elif "string_value" in value_obj:
            fields[key] = value_obj["string_value"]
        elif "key_label_value_list" in value_obj:
            klvl = value_obj["key_label_value_list"]
            if klvl:
                fields[key] = klvl[0].get("key", "")
        else:
            fields[key] = value_obj
    bugs.append(fields)
```

## 运行环境（重要）

**必须使用 hermes-agent venv 中的 Python**，绝不能用系统 `python3`。Cron 环境会解析到系统 Python 3.9，缺少 bug-analyzer 依赖，导致 `ModuleNotFoundError` 崩溃。

```python
PYTHON = "/Users/ai/.hermes/hermes-agent/venv/bin/python"
analyzer_script = os.path.join(BASE_DIR, "bug_analyzer.py")
# 使用 Popen + start_new_session=True 隔离进程组，超时后用 os.killpg 杀整个进程树
# 不要用 subprocess.run — 它只杀父进程，子进程（如挂起的 requests）会变 zombie
```

## 超时对齐（2026-06-03 更新）

**LLM 轮次限制**：cron 中通过环境变量 `BUG_ANALYZER_MAX_ROUNDS=2` 调用 bug_analyzer.py，将 LLM 分析从默认 3 轮限制为 2 轮。

原因：每轮 LLM 调用 `timeout=(15, 300)`，3 轮 × 300s = 900s，加上飞书 API 调用和附件下载，总时间很容易超过 `ANALYSIS_TIMEOUT=900s`（从 1200s 下调）。实测 bug 7007264060（仅 1 条评论、无附件）在 1200s 超时内仍被杀掉——卡在 LLM API 调用阶段，158s 未产生输出文件。

2 轮 × 300s + API 开销 ≈ 650-700s，在 900s 内可靠完成。

- 脚本 `ANALYSIS_TIMEOUT` 设为 900s（单个 bug 分析上限 15 分钟）
- `run_analysis()` 注入 `BUG_ANALYZER_MAX_ROUNDS=2` 环境变量
- 不再跳过任何缺陷（含 zip 附件也参与分析）
- 每次运行结果追加到 `/tmp/cron_auto_analyze_history.log`，可用 `cat /tmp/cron_auto_analyze_history.log | tail -100` 查看历史

## 多项目轮询（2026-06-03 更新）

`cron_auto_analyze.py` 支持同时轮询多个飞书项目。配置在脚本顶部：

```python
PROJECT_KEYS = ["sw_team", "676e7fecad8e9de8735fa89f"]
```

**工作原理**：
1. 依次对每个 project_key 执行 `fetch_bugs` → `filter_open_bugs` → `get_new_bug_ids` → 缓存状态更新
2. 合并所有项目的新增缺陷为 `(bug_id, project_key)` 列表
3. 逐个分析、评论，每个 API 调用都传入对应的 `project_key`
4. 汇总输出按项目分组显示

**MQL 语法注意**：非字母数字的 project_key（如 `676e7fecad8e9de8735fa89f`）在 MQL 中必须用反引号包裹：
```sql
SELECT work_item_id, name FROM `676e7fecad8e9de8735fa89f`.issue LIMIT 50
```

字母数字的 project_key（如 `sw_team`）不需要反引号。

**缓存兼容**：所有项目共享同一个 `.bug_index_cache.json`，bug ID 不会冲突（不同项目的 ID 段不同）。

## 运行历史日志

`cron_auto_analyze.py` 每次运行完成后，会将汇总结果追加到 `/tmp/cron_auto_analyze_history.log`：

```
============================================================
Run: 2026-06-03 16:25:04
============================================================
[sw_team] 新增: 1 个
[676e7fecad8e9de8735fa89f] 新增: 0 个
总计新增: 1 个
  ❌ 7007264060 [sw_team]: 分析超时或失败
关闭缺陷: 无新关闭缺陷需要导入
```

包含每个项目的新增数量、已评论/已跳过/失败的缺陷列表（带项目标签），以及关闭缺陷导入 OpenViking 的结果。方便在 cron 交付失败（deliver=origin）时通过终端查询历史。

## 新缺陷预拉取机制（重要）

在调用 `bug_analyzer.py` 分析新发现的缺陷前，**必须先通过 MQL 获取完整详情并写入缓存**。否则分析器会因为缓存中缺少该 bug 的完整字段而抛出 `KeyError` 或直接报 "未找到缺陷"。

```python
def fetch_new_bug_details(bug_id):
    """通过 MQL 查询单个缺陷详情，补齐缓存所需字段。"""
    mql = f'AND(project_key=="{PROJECT_KEY}" AND work_item_id=="{bug_id}")'
    result = subprocess.run(
        ["mcporter", "call", "meego", "search_by_mql", "--args",
         json.dumps({"query": mql, "limit": 1}, ensure_ascii=False)],
        capture_output=True, text=True, timeout=120
    )
    # 解析 moql_field_list 格式，提取所有必要字段
    # 写入 cache: search_text, desc_lower, description, comments, attachments
```

## 缓存字段完整性防御

向 `.bug_index_cache.json` 写入新发现的缺陷时，**必须补齐所有必要字段**，使用空字符串占位：

```python
def _ensure_complete_fields(bug):
    bug.setdefault("search_text", "")
    bug.setdefault("desc_lower", "")
    bug.setdefault("description", "")
    bug.setdefault("comments", [])
    bug.setdefault("attachments", [])
    return bug
```

## 关闭缺陷自动检测与归档

### 检测逻辑

在更新缓存前执行：拉取当前所有 OPEN 缺陷 ID → 遍历缓存中状态为 OPEN 的条目 → 若不在当前 OPEN 集合中，判定为已关闭。

### 归档机制

已上传 OpenViking 的缺陷通过 `.ov_archived.json` 记录去重。关闭缺陷生成 Markdown 并通过 HTTP API 上传 OpenViking，失败回退到 `ov add-resource` CLI。临时文件保存到 `~/.openviking/workspace/feishu-bugs/ov_import_closed/`。

## Cron 任务架构选择（2026-05-09 更新）

**不要用 LLM 驱动的并行 cron 任务**。历史失败模式：
- 后台进程 (background) 在 session 结束后被 kill (exit 143)
- Unicode 安全过滤阻止含特殊字符的评论内容
- Session 超时 (300s) 在分析完成前结束
- 无法可靠等待后台进程完成

**正确做法**：cron prompt 只负责调用一个独立 Python 脚本，脚本自己处理串行执行、超时控制和评论添加。

### Cron prompt 三步执行模式（2026-05-13 确认）

**问题**：从 2026-05-12 20:05 起，cron agent 只执行了 "启动后台进程" 就结束会话（仅 4 条消息），没有后续的 poll/wait/read_file。连续 10 次执行全部失效，`/tmp/cron_auto_analyze_last_output.txt` 为空（0 字节）。

**根因**：Cron agent 在收到 "Background process started" 响应后，误认为任务已完成，不再主动轮询或等待。

**确认有效的修复**：prompt 必须分为 3 个强制步骤，agent 才能正确执行：

```
步骤1: 启动脚本
  terminal: /Users/ai/.hermes/hermes-agent/venv/bin/python cron_auto_analyze.py
  参数: background=true, notify_on_complete=true
  工作目录: /Users/ai/.hermes/skills/bug-analysis/feishu-bug-pipeline/scripts

步骤2: 等待脚本完成（关键！必须执行此步）
  收到步骤1的 session_id 后，立即调用 process(action='wait', session_id=步骤1返回的session_id)
  这会阻塞直到脚本执行完毕。不要用 poll 轮询。

步骤3: 读取输出并回复
  wait 返回后，用 read_file 读取 /tmp/cron_auto_analyze_last_output.txt
  将文件内容作为最终回复。
```

**验证方法**：检查 cron session 的 tool call 序列。正常应包含 `terminal → process(wait) → read_file`。异常只有 `terminal` 一条。

**注意**：
- 工作目录通过 terminal 的 workdir 参数指定，不要用 `cd` 命令拼接
- 7200s 的 wait timeout 会被 clamp 到 600s，脚本通常 2-5 秒完成（无新增缺陷时）
- wait 被用户消息中断时（exit_code=130），脚本在后台仍正常完成

## 关键陷阱：background + wait 会被用户消息中断

实测发现：即使 cron prompt 明确要求 `terminal` 前台执行，cron agent 仍可能将脚本作为后台进程（`background=true`）启动，然后用 `process wait` 等待。

**问题**：当用户在 cron session 等待期间发送新消息时，`process wait` 会被中断（status: "interrupted"），note 显示 "User sent a new message -- wait interrupted"。同时 7200s 的 wait timeout 会被 clamp 到 600s 上限（错误信息: `Foreground timeout 7200s exceeds the maximum of 600s`）。Agent 随后自动回退到 `background=true` 模式，脚本在后台正常完成（exit_code=0）。

**后果**：前台执行失败但回退到 background 模式后脚本继续正常运行。如果 background + wait 正在等待中用户发送了新消息，wait 会被中断（exit_code=130），但脚本本身在后台不受影响。

**缓解措施**：
- 在 cron prompt 中明确指示：`使用 terminal 前台执行，不要用 background`
- 脚本自身应将 stdout/stderr 重定向到文件作为保险：`python script.py > /tmp/cron_output.log 2>&1`
- 用户可通过 `ps aux | grep cron_auto_analyze` 确认脚本是否仍在运行
- 分析结果文件（`/tmp/bug_*_analysis_*.json`）是最可靠的进度指标
- 由于 `deliver=origin` 无消息平台连接，cron 结果不会自动推送（`last_delivery_error: "no delivery target resolved for deliver=origin"`），需用户主动询问才能看到

## 评论 JSON 编码

使用 `json.dumps(args_dict, ensure_ascii=False)` 生成 mcporter add_comment 的参数，不要用手动字符串转义。手动转义容易出错且可能触发 Unicode 安全过滤。

```python
args_dict = {"work_item_id": bug_id, "project_key": PROJECT_KEY, "content": content}
args_json = json.dumps(args_dict, ensure_ascii=False)
cmd = f'mcporter call meego add_comment --args {json.dumps(args_json)}'
```

## bug-analyzer config.py 必备函数

`analyzer.py` 从 `config.py` 导入以下函数，缺一不可：
- `get_openviking_config()` — OpenViking API 配置
- `get_llm_config()` — LLM provider/model/endpoint 配置
- `get_code_repos()` — 代码仓库列表（用于代码搜索）
- `get_analysis_config()` — 分析参数（max_log_lines, timeout 等）
- `load_config()` — 配置加载（含 .env 解析）

## 评论质量

评论必须使用 `llm_analysis` 完整内容（通常 1500-2000 字符），包含：
- 核心根因与触发链路
- 影响范围（模块/功能/系统级）
- 复现概率与理由
- P0-P3 建议措施

当 LLM 调用失败时，fallback 为规则引擎 root_cause 摘要 + "LLM 深度分析不可用" 提示。
