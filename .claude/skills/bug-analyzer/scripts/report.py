#!/usr/bin/env python3
"""
报告生成器 - 生成 Bug 分析报告

改进 (2026-04-13):
- 增加置信度详情展示
- 增加 TL;DR 摘要
- 增加错误码和崩溃签名展示
- 增加相似缺陷展示
- 结构化输出优化
"""

import json
from datetime import datetime
from typing import Dict, Optional, List


def format_confidence(confidence: Dict) -> str:
    """格式化置信度信息"""
    if not confidence:
        return "置信度: 未知"
    
    score = confidence.get("score", 0)
    level = confidence.get("level", "🔴 低")
    details = confidence.get("details", {})
    
    lines = [f"**置信度**: {score:.0%} {level}"]
    
    if details:
        lines.append("\n各维度评分:")
        dim_names = {
            "log_completeness": "日志完整性",
            "stack_quality": "堆栈质量",
            "error_clarity": "错误明确性",
            "similarity_match": "相似匹配",
            "root_cause_certainty": "根因确定性"
        }
        for dim, name in dim_names.items():
            val = details.get(dim, 0)
            bar = "█" * int(val * 5) + "░" * (5 - int(val * 5))
            lines.append(f"- {name}: {bar} {val:.0%}")
    
    return "\n".join(lines)


def generate_tldr(result: Dict) -> str:
    """生成 TL;DR 摘要"""
    log_analysis = result.get("log_analysis", {})
    root_cause = result.get("root_cause", "未知")
    suggestion = result.get("suggestion", "无")
    
    # 错误类型
    fatal = log_analysis.get("fatal_count", 0)
    errors = log_analysis.get("error_count", 0)
    warnings = log_analysis.get("warning_count", 0)
    
    # 崩溃签名
    crash = log_analysis.get("crash_signature")
    
    parts = []
    
    if fatal > 0:
        parts.append(f"🚨 发生 {fatal} 次致命错误")
    if errors > 0:
        parts.append(f"❌ {errors} 个错误")
    if warnings > 0:
        parts.append(f"⚠️ {warnings} 个警告")
    if crash:
        sig = crash.get("signal", crash.get("keyword", ""))
        parts.append(f"💥 崩溃签名: {sig}")
    
    if root_cause and "需要进一步分析" not in root_cause:
        parts.append(f"\n📍 根因: {root_cause}")
    
    return " | ".join(parts) if parts else "日志分析未发现明显问题"


def generate_markdown_report(analysis_result: Dict, bug_info: Dict = None) -> str:
    """
    生成 Markdown 格式报告
    
    Args:
        analysis_result: 分析结果
        bug_info: Bug 基础信息
    
    Returns:
        str: Markdown 报告
    """
    log_analysis = analysis_result.get("log_analysis", {})
    root_cause = analysis_result.get("root_cause", "未知")
    suggestion = analysis_result.get("suggestion", "无")
    stack_traces = analysis_result.get("stack_traces", [])
    confidence = analysis_result.get("confidence", {})
    
    # TL;DR
    tldr = generate_tldr(analysis_result)
    
    report = f"""# 🐛 Bug 分析报告

## TL;DR
{tldr}

---

## 基本信息

| 项目 | 内容 |
|------|------|
| 分析时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| 缺陷ID | {bug_info.get('id', 'N/A') if bug_info else 'N/A'} |
| 来源 | {bug_info.get('source', '本地文件') if bug_info else '本地文件'} |

## 问题描述

{bug_info.get('description', '无') if bug_info else '用户提供了日志文件进行分析'}

---

## 📊 日志分析

### 错误统计

| 类型 | 数量 |
|------|------|
| 致命错误 (FATAL) | {log_analysis.get('fatal_count', 0)} |
| 错误 (ERROR) | {log_analysis.get('error_count', 0)} |
| 警告 (WARNING) | {log_analysis.get('warning_count', 0)} |

### 错误码

"""
    
    # 错误码
    error_codes = log_analysis.get("error_codes", [])
    if error_codes:
        unique_codes = list({ec["code"] for ec in error_codes})
        for code in unique_codes[:10]:
            report += f"- `{code}`\n"
    else:
        report += "无明确错误码\n"
    
    report += "\n### 关键错误\n\n"
    
    errors = log_analysis.get("errors", [])
    if errors:
        for err in errors[:8]:
            err_type = err.get("type", "E")
            emoji = "🚨" if err_type == "FATAL" else "❌"
            content = err.get("content", "")[:150]
            report += f"- {emoji} `{err.get('line', '?')}`: {content}\n"
    else:
        report += "- 未发现明显错误\n"
    
    report += "\n### 堆栈跟踪\n\n"
    
    # 堆栈
    if stack_traces:
        for i, trace in enumerate(stack_traces[:3], 1):
            if isinstance(trace, dict):
                content = trace.get("content", str(trace))[:400]
            else:
                content = str(trace)[:400]
            report += f"**Stack #{i}**\n```\n{content}\n```\n\n"
    else:
        # 从 log_analysis 获取
        log_stacks = log_analysis.get("stack_traces", [])
        if log_stacks:
            for i, st in enumerate(log_stacks[:3], 1):
                content = st.get("content", "")[:400]
                report += f"**Stack #{i}** ({st.get('lines', 0)} lines)\n```\n{content}\n```\n\n"
        else:
            report += "未发现堆栈跟踪\n"
    
    # 崩溃签名
    crash_sig = log_analysis.get("crash_signature")
    if crash_sig:
        report += f"### 💥 崩溃签名\n- **{crash_sig.get('description', crash_sig.get('keyword', 'Unknown'))}**\n"
        if crash_sig.get("fatal"):
            report += "- ⚠️ 这是一个致命崩溃\n"
        report += "\n"
    
    # 相似缺陷
    similar = analysis_result.get("similar_bugs", [])
    if similar:
        report += "### 🔍 相似缺陷\n\n"
        for bug in similar[:5]:
            score_emoji = "✅" if bug.get("score", 0) >= 0.7 else "🔶"
            report += f"- {score_emoji} **{bug.get('title', 'N/A')[:40]}** (得分: {bug.get('score', 0):.2f})\n"
            if bug.get("status"):
                report += f"  - 状态: {bug['status']}\n"
        report += "\n"
    
    report += f"""---

## 🔍 根因分析

**{root_cause}**

---

## 💡 建议解决方案

{suggestion}

---

## 📈 置信度评估

{format_confidence(confidence)}

---

*报告生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    
    return report


def generate_feishu_report(analysis_result: Dict, bug_info: Dict = None) -> str:
    """
    生成飞书格式报告（便于粘贴到飞书文档）
    
    Args:
        analysis_result: 分析结果
        bug_info: Bug 基础信息
    
    Returns:
        str: 飞书格式报告
    """
    log_analysis = analysis_result.get("log_analysis", {})
    root_cause = analysis_result.get("root_cause", "未知")
    suggestion = analysis_result.get("suggestion", "无")
    confidence = analysis_result.get("confidence", {})
    
    # TL;DR
    tldr = generate_tldr(analysis_result)
    
    # 错误码
    error_codes = log_analysis.get("error_codes", [])
    error_codes_str = ", ".join(list({ec["code"] for ec in error_codes})[:5]) or "无"
    
    # 相似缺陷
    similar = analysis_result.get("similar_bugs", [])
    similar_str = "\n".join([
        f"• {b.get('title', '')[:30]} (得分:{b.get('score', 0):.2f})" 
        for b in similar[:3]
    ]) or "无"
    
    report = f"""【Bug分析报告】

■ TL;DR
{tldr}

■ 基本信息
• 分析时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
• 缺陷ID：{bug_info.get('id', 'N/A') if bug_info else 'N/A'}
• 来源：{bug_info.get('source', '本地文件') if bug_info else '本地文件'}

■ 问题描述
{bug_info.get('description', '无') if bug_info else '用户提供了日志文件进行分析'}

■ 日志分析
• 致命错误：{log_analysis.get('fatal_count', 0)}
• 错误数量：{log_analysis.get('error_count', 0)}
• 警告数量：{log_analysis.get('warning_count', 0)}
• 错误码：{error_codes_str}

■ 相似缺陷
{similar_str}

■ 根因分析
{root_cause}

■ 建议解决方案
{suggestion}

■ 置信度
{confidence.get('score', 0):.0%} {confidence.get('level', '🔴 低')}
"""
    
    return report


def save_report(content: str, file_path: str = None) -> str:
    """
    保存报告到文件
    
    Args:
        content: 报告内容
        file_path: 文件路径
    
    Returns:
        str: 保存的路径
    """
    if file_path is None:
        file_path = f"/tmp/bug_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return file_path


if __name__ == "__main__":
    # 测试
    test_result = {
        "log_analysis": {
            "error_count": 2,
            "warning_count": 1,
            "errors": [
                {"line": 10, "content": "NullPointerException"},
                {"line": 15, "content": "Connection timeout"}
            ]
        },
        "root_cause": "可能原因: 空指针",
        "suggestion": "检查对象是否为空",
        "stack_traces": ["at com.example.Service.process..."]
    }
    
    report = generate_markdown_report(test_result, {"id": "BUG-001", "description": "服务崩溃"})
    print(report)