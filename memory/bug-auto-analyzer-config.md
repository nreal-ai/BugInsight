---
name: bug-auto-analyzer-config
description: 自动分析模式配置 - AXR 项目缺陷自动分析
metadata:
  type: project
---

# Bug Auto Analyzer 配置

## 项目信息
- **项目**: AXR
- **project_key**: 676e7fecad8e9de8735fa89f
- **缺陷类型**: issue

## 运行参数
- **max_per_batch**: 1
- **include_statuses**: OPEN, IN PROGRESS, REOPENED
- **priority_order**: P0, P1, P2, 待定
- **interval_minutes**: 0（连续模式），队列空后 10 分钟轮询

## 已分析 Bug 列表
- 6688923009
- 6688655472
- 6666654896
- 5979061618
- 6405205334

## 跳过规则
- 评论区已有 `分析来源于 AI` 或 `AI分析结论` 关键字 → 跳过
- bug 已在 analyzed_bugs 列表中 → 跳过

**Why:** 用户启动自动分析模式，需要持续扫描 AXR 项目中未分析的缺陷并自动分析。
**How to apply:** 每次分析前检查 analyzed_bugs 列表和评论区，只分析未处理过的缺陷。
