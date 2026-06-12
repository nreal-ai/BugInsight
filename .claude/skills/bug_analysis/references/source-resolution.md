# 证据链来源解析系统 (Source Resolution)

## 概述

bug-analyzer 报告的证据链中，每条证据必须标注其原始数据来源，确保分析结论可追溯。

## 四级来源解析

### Level 1: 日志文件（最高精度）
- 格式: `` `filename:line_number` ``
- 示例: `` `pilot.log:14230` ``
- 来源: `smart_extract_log_content` 输出的 `# FILE: filename | LINE: N | ` 前缀
- 解析: `analyzer.py` 中 `_parse_line_prefix()` 方法提取

### Level 2: 内联评论引用
- 格式: `` `评论#N` ``
- 正则: `评论#(\d+)`
- 场景: 当分析器处理组合文本时，评论编号被嵌入到文本行中

### Level 3: 内容匹配（双向子串 + 关键短语）
- 用于证据内容与原始评论/描述的映射
- 匹配策略：
  1. 证据内容包含在评论中
  2. 评论前100字符包含在证据中
  3. 取证据前30字符作为关键短语，在评论中查找
  4. 模糊窗口：依次尝试25/20/15字符的滑动窗口

### Level 4: 缺陷描述
- 格式: `` `缺陷描述` ``
- 当证据内容匹配 bug 原始描述时标记

### Fallback
- 格式: `` `未知` ``
- 所有解析失败时的默认值

## 实现位置

- `report.py::_build_source_lookup()` — 构建评论索引和描述索引
- `report.py::_resolve_source()` — 四级解析核心逻辑
- `report.py::_resolve_all_sources()` — 对所有证据项批量解析
- `report.py::_format_evidence_item()` — 格式化显示

## 示例

有日志文件的 bug:
```
1. 🔴 **Native Crash** `pilot.log:14230`
   - SIGABRT in thread "nrsdk-handler"
```

仅有评论的 bug:
```
1. 🟡 **时序问题 (timeout)** `评论#5`
   - 手机端3dof会从眼镜端获取在线标定参数，获取失败就会主动signal 6退出
```

混合场景:
```
1. 🔴 **FATAL 错误** `pilot.log:8721`
2. 🟡 **时序问题** `评论#6`
3. ⚪ **开发者评论** `评论#1` [1752484184000]
```
