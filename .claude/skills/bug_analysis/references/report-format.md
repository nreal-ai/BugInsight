# 报告格式规范 — 三层精简分层结构 (2026-04-30)

## 设计原则

报告必须遵循「结论优先、证据分层、细节按需展开」原则。阅读者应该在 10 秒内理解根因，30 秒内看完关键证据。

## 三层结构

### 第一层：核心结论（必须一目了然）

```markdown
## 核心结论

| 项目 | 内容 |
|------|------|
| 缺陷ID | 5341333388 |
| **根因** | **Native 崩溃: Native 层崩溃** |
| **置信度** | **100% 🟢 高** |
| 问题描述 | ... |

**关键证据：**
1. 🔴 **Native Crash** — Native 层崩溃
2. 🔴 **崩溃签名** — SIGABRT - 程序异常终止
3. 🔴 **FATAL 错误** — `0217.log:307461` — FORTIFY: pthread_mutex_lock called on a destroyed mutex
```

规则：
- 最多 3 条关键证据
- 每条证据必须是日志行或崩溃签名，**不得包含开发者评论**
- 置信度格式：`{score:.0%} {level}`（如 `100% 🟢 高`），不要前缀「置信度:」

### 第二层：证据链（分类关联根因）

```markdown
## 证据链

### 直接证据
1. 🔴 **Native Crash** — 崩溃信号直接指向根因
2. 🔴 **崩溃签名** — SIGABRT，崩溃特征与根因匹配
3. 🔴 **FATAL 错误** — `0217.log:307462` — 致命错误直接支持根因判断

### 间接证据
1. 🟡 **时序问题 (timeout)** — `1459.log:1048` — 时序异常为根因提供佐证
2. 🟡 **渲染错误 (render_pipeline)** — `0217.log:307423` — 渲染错误与显示相关根因相符

### 辅助证据
1. ⚪ **开发者评论 (#3)** — 开发者分析提供专业判断
2. ⚪ **日志附件** — 下载 4/7 个附件，包含实际日志内容
3. ⚪ **相似缺陷** — 5 个历史相似缺陷可交叉验证
```

#### 直接证据规则

| 类型 | 纳入条件 |
|------|----------|
| Native Crash | `log_analysis['native_crashes']` 非空 |
| 崩溃签名 | `native_crash['signal']` 如 SIGABRT/SIGSEGV |
| FATAL 错误 | **必须同时满足**: 有 `source_file` + `line_number` **或** 包含技术签名正则 `(SIG\w+|0x[0-9a-f]+|Segmentation|FORTIFY|FATAL\s+EXCEPTION|backtrace)` |

**关键陷阱**: 开发者评论中常出现 "crash" 一词（如"看起来没有crash"），这些必须被排除。只有来自实际日志文件（有 FILE:LINE 前缀）或包含技术崩溃签名的内容才纳入直接证据。

#### 间接证据规则

| 类型 | 来源 |
|------|------|
| ERROR | `log_analysis['errors']` 中 type != 'FATAL' 的条目 |
| WARNING | `log_analysis['warnings']` 前 10 条 |
| 时序问题 | `log_analysis['timing_issues']` 前 5 条 |
| 渲染错误 | `log_analysis['shader_errors']` 前 5 条 |

#### 辅助证据规则

| 类型 | 来源 |
|------|------|
| 开发者评论 | `feishu_evidence` 中的技术线索和讨论评论 |
| 日志附件 | `downloaded_files` 统计信息 |
| 相似缺陷 | `similar_bugs` 列表 |

### 第三层：详细数据（折叠）

使用 `<details>` 标签包裹：
- 错误统计（FATAL/ERROR/WARNING 计数）
- 堆栈跟踪（带 FILE/LINE 前缀）
- Native Crash 详情
- 技术线索全文
- 相似缺陷完整列表

## 溯源格式

所有线索中的文件引用统一格式：`` `filename:line_number` ``
堆栈跟踪每行前缀：`# FILE: filename | LINE: N | original text`

### 上下文片段显示

对于有 `source_file` + `line_number` 的证据项，每条证据下方自动附加**附近日志上下文**（默认 ±5 行）：

```markdown
3. 🔴 **FATAL 错误** `pilot.log.2:1129`
   - Jan 25 08:09:50 XREAL[439]: [FATAL] [Ferrit] AdjustTabItemOrder failed!
   - 关联: 致命错误直接支持根因判断
   ```
   --- 附近日志 (log_backup.zip/log/log_62/pilot.log.2) ---
    L1124: Jan 25 08:10:09 XREAL[439]: DisplayClient I: init gdc0
    L1125: Jan 25 08:10:09 XREAL[439]: DisplayClient I: LVDS lowest power mode enabled
    L1126: Jan 25 08:10:09 XREAL[439]: DisplayClient I: resume gdc1
>>> L1127: Jan 25 08:10:09 XREAL[439]: DisplayClient I: set_gdc_lines64_param: applied...
    L1128: Jan 25 08:10:09 XREAL[439]: DisplayClient I: resume gdc1
    L1129: Jan 25 08:10:09 XREAL[439]: DisplayClient I: init gdc0: lines64_enable:0
   ```
```

- 目标行用 `>>>` 标记，上下文行用 `   ` 前缀
- 仅当周围行数 > 3 时才显示（避免冗余）
- 文件名来自 `downloaded_log_contents` 的键（如 `archive_name/relative/path`）

### 归档附件目录结构

对于 zip/tar 等归档附件，报告"详细数据"区域新增"归档附件目录结构"章节：

```markdown
### 归档附件目录结构

```
📦 log_backup_20251221_124720.zip/
└── log/
    ├── current_log_dir/
    │   ├── auth.log
    │   ├── mpp.log
    │   └── pilot.log
    └── log_62/
        ├── kernel.log
        └── pilot.log.2
```

以上为解压后的文件结构，报告中的证据行号指向具体日志文件。
```

- 使用标准 tree 格式，支持多层嵌套目录
- 每个归档附件一个 code block
- 帮助用户快速定位 zip 包内的日志路径

详见 `references/file-tracing.md`。

## 飞书格式同步

`generate_feishu_report()` 使用相同的三层结构，但移除 Markdown 特有的语法（如 `<details>`），改用飞书文档兼容的纯文本格式。
