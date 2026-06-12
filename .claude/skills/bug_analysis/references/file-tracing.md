# 文件溯源功能 — 报告中每条线索标明来源文件和行号

## 设计目标
用户要求分析报告中的每条线索/证据必须指明：**原始文件在哪、具体行数、原文内容**。

## 数据流

```
attachment_downloader.py::smart_extract_log_content
  ↓ 每行添加 # FILE: filename | LINE: N | 前缀
  ↓
analyzer.py::analyze_log_enhanced
  ↓ 解析前缀，填充 source_file/line_number 到 errors/warnings/timing/native_crash/shader 条目
  ↓
report.py::generate_markdown_report
  ↓ 显示为 `filename:line_number` 格式
  ↓ _extract_context_snippet() 从 downloaded_log_contents 查找文件并返回 ±5 行上下文
```

## 实现细节

### 1. attachment_downloader.py

`smart_extract_log_content()` 输出的每个片段中，每行添加溯源前缀：
```
# FILE: 0217.log | LINE: 307422 | I/FORTIFY: pthread_mutex_destroy called on a destroyed mutex
```

`_head_tail_extract()` 也接受 `file_path` 参数，确保退化路径（无错误行时）也带前缀。

**归档目录树**: `extract_archive()` 返回 `(文件列表, 目录树字符串)` 元组。`_build_archive_tree()` 生成标准 tree 格式。目录树存入 `download_result['archive_trees'][archive_name]`，通过 `feishu_evidence.download_result.archive_trees` 传递到报告。

### 2. analyzer.py

三个新增辅助方法：
- `_parse_line_prefix(line)` — 从单行解析出 `{source_file, line_number, original_text}`
- `_extract_source_from_context(context_text)` — 从上下文文本的第一行提取溯源信息
- `_strip_file_prefix(text)` — 清除前缀，保持原文干净

`analyze_log_enhanced()` 中所有 error/warning/timing/native_crash/shader 条目收集时：
```python
pfx = self._parse_line_prefix(line)
errors.append({
    "line": f"L{pfx['line_number']}",
    "content": pfx.get("original_text", line[:120]),
    "source_file": pfx.get("source_file"),
    "line_number": pfx.get("line_number"),
    ...
})
```

### 3. report.py

**文件引用格式**: 统一为 `` `filename:line_number` ``

**上下文片段** (`_extract_context_snippet`):
- 对有 `source_file` + `line_number` 的证据项，自动附加附近 ±5 行日志
- 从 `result['downloaded_log_contents']` 中查找文件（支持直接匹配/后缀匹配/basename匹配）
- 目标行用 `>>>` 标记，上下文行用 `   ` 前缀
- 仅当周围行数 > 3 时才显示（避免冗余）

**归档目录树展示**:
- 报告"详细数据"区域新增"归档附件目录结构"章节
- 每个归档附件显示一个 code block 的 tree 格式目录树
- 帮助用户理解 zip 包内多文件的组织结构，方便定位日志路径

堆栈跟踪使用：
```
- # FILE: 0217.log | LINE: 307461 |原文
```

Native Crash 错误详情：
```
- 🔴 错误描述 `source_file:line_number`: 上下文原文
```

## 已知问题
- 评论数据（非日志文件）没有 FILE/LINE 前缀，溯源显示为 `LL1`, `LL2` 等逻辑行号
- 合并后的重叠片段，每行从原始 `lines` 数组重新标注，不重复
