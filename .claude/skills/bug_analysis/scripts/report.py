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
import re
from datetime import datetime
from typing import Dict, Optional, List


def _build_source_lookup(result: Dict) -> Dict:
    """Build a lookup map from evidence content to its original source location.

    Returns a dict mapping content fingerprints to source info:
    {'comments': [...], 'description': str, 'log_files': [...],
     'log_contents': {filename: full_content, ...},
     'archive_trees': {archive_name: tree_string, ...}}
    """
    lookup = {"comments": [], "description": "", "log_files": [],
              "log_contents": {}, "archive_trees": {}}

    # Index comments with their metadata
    feishu = result.get("feishu_evidence", {})
    for ev in feishu.get("technical_evidence", []):
        lookup["comments"].append({
            "index": ev.get("index", ""),
            "created_at": ev.get("created_at", ""),
            "content": ev.get("content", ""),
        })

    # Bug description
    bug_info = result.get("bug_info", {})
    lookup["description"] = bug_info.get("description", "")

    # Log file names
    for att in feishu.get("log_attachments", []):
        if att.get("available"):
            lookup["log_files"].append(att["name"])

    # Log contents for context snippet extraction
    log_contents = result.get("downloaded_log_contents", {})
    if isinstance(log_contents, dict):
        lookup["log_contents"] = log_contents

    # Archive trees for display
    download_result = feishu.get("download_result")
    if download_result and isinstance(download_result, dict):
        lookup["archive_trees"] = download_result.get("archive_trees", {})

    return lookup


def _extract_context_snippet(filename: str, line_number, log_contents: Dict, context_lines: int = 5) -> str:
    """Extract a context snippet showing nearby lines around a referenced line number.

    Supports cross-file context resolution: if the target line contains a stack trace
    or file reference pointing to another file in log_contents, also extracts context
    from that related file.

    Returns a formatted string or empty string if not found.
    """
    if not line_number or not log_contents:
        return ""

    # Try to find the file content
    content = None
    matched_key = None

    # Direct match
    if filename in log_contents:
        content = log_contents[filename]
        matched_key = filename
    else:
        # Fuzzy match: try to find the filename as a suffix in any key
        for key, val in log_contents.items():
            if key.endswith(filename) or filename.endswith(key):
                content = val
                matched_key = key
                break
        # Also try just the basename
        if content is None:
            base = filename.split("/")[-1] if "/" in filename else filename
            for key, val in log_contents.items():
                if key.endswith(base) or base in key:
                    content = val
                    matched_key = key
                    break

    if content is None:
        return ""

    lines = content.split("\n")
    try:
        # line_number might be int or str
        target = int(str(line_number).strip())
    except (ValueError, TypeError):
        return ""

    if target < 1 or target > len(lines):
        return ""

    # Calculate context range (1-indexed line_number to 0-indexed array)
    start = max(0, target - context_lines - 1)
    end = min(len(lines), target + context_lines)

    # Only show context if there are actual surrounding lines
    total_context = end - start
    if total_context <= 3:
        return ""

    snippet_lines = []
    header = f"--- 附近日志 ({matched_key}) ---"
    snippet_lines.append(header)
    for i in range(start, end):
        line_num = i + 1  # Convert back to 1-indexed
        marker = ">>>" if line_num == target else "   "
        line_text = lines[i].rstrip()
        # Truncate very long lines
        if len(line_text) > 200:
            line_text = line_text[:200] + "..."
        snippet_lines.append(f"{marker} L{line_num}: {line_text}")

    # === 跨文件上下文关联（新增）===
    # 检查目标行是否包含对其他文件的引用（堆栈跟踪、文件路径等）
    target_idx = target - 1  # 0-indexed
    if 0 <= target_idx < len(lines):
        target_line = lines[target_idx]
        cross_refs = _resolve_cross_file_refs(target_line, log_contents, matched_key)
        if cross_refs:
            snippet_lines.append("")
            snippet_lines.append("--- 关联文件上下文（跨文件追踪）---")
            for ref_file, ref_line_num, ref_reason in cross_refs:
                ref_content = log_contents.get(ref_file)
                if ref_content:
                    ref_lines = ref_content.split("\n")
                    ref_start = max(0, ref_line_num - context_lines - 1)
                    ref_end = min(len(ref_lines), ref_line_num + context_lines)
                    snippet_lines.append(f"  ↳ {ref_file} (原因: {ref_reason})")
                    for j in range(ref_start, ref_end):
                        rn = j + 1
                        marker = ">>>" if rn == ref_line_num else "   "
                        rt = ref_lines[j].rstrip()
                        if len(rt) > 150:
                            rt = rt[:150] + "..."
                        snippet_lines.append(f"  {marker} L{rn}: {rt}")

    return "\n".join(snippet_lines)


def _resolve_cross_file_refs(line_text: str, log_contents: Dict, current_file: str) -> List[tuple]:
    """从单行日志中解析对其他文件的引用，返回 [(ref_file, ref_line_num, reason), ...]。

    支持的引用格式:
    - Java 堆栈: at com.xxx.ClassName.java:123
    - Native 堆栈: #00 pc 00012345 /system/lib/xxx.so
    - 通用路径: /path/to/file.py:456
    - 相对路径: src/utils/helper.py:78
    """
    refs = []
    seen = set()

    # Java/Kotlin stack traces: ClassName.java:123
    for m in re.finditer(r'([\w]+\.java|[\w]+\.kt|[\w]+\.scala):(\d+)', line_text):
        ref_name = m.group(1)
        ref_line = int(m.group(2))
        # 尝试在 log_contents 中匹配
        for key in log_contents:
            if key.endswith(ref_name) and key != current_file:
                ref_key = (key, ref_line)
                if ref_key not in seen:
                    seen.add(ref_key)
                    refs.append((key, ref_line, f"Java堆栈引用 {ref_name}:{ref_line}"))

    # Native stack traces: /path/to/xxx.so+offset or /path/to/xxx.so
    for m in re.finditer(r'(/[\w./_-]+(?:\.so|\.dylib|\.dll))(\+0x[\w]+)?', line_text):
        ref_path = m.group(1)
        for key in log_contents:
            if key.endswith(ref_path.split('/')[-1]) and key != current_file:
                ref_key = (key, 1)
                if ref_key not in seen:
                    seen.add(ref_key)
                    refs.append((key, 1, f"Native库引用 {ref_path}"))

    # Generic file:line references (e.g., helper.py:456, config.yaml:10)
    for m in re.finditer(r'([\w_-]+\.(py|c|cpp|h|hpp|rs|go|js|ts|xml|json|yaml|yml|txt|cfg|conf|ini|log)):?(\d+)?', line_text):
        ref_name = m.group(1)
        ref_line = int(m.group(3)) if m.group(3) else 1
        if ref_line < 1:
            ref_line = 1
        for key in log_contents:
            if key.endswith(ref_name) and key != current_file:
                ref_key = (key, ref_line)
                if ref_key not in seen:
                    seen.add(ref_key)
                    refs.append((key, ref_line, f"文件引用 {ref_name}:{ref_line}"))

    return refs[:3]  # 最多返回3个跨文件引用


def _resolve_source(content: str, source_file, line_number, source_lookup: Dict) -> tuple:
    """Resolve original source location for evidence when source_file is None.

    Returns (resolved_source, resolved_line) — one of:
    - (filename, line_number) if from a log file
    - ('评论#N', '') if from a developer comment
    - ('缺陷描述', '') if from the bug description
    """
    if source_file and line_number:
        return source_file, line_number

    if not content:
        return source_file or "未知", line_number or ""

    content_trunc = content[:100].strip()

    # === Step 1: Extract explicit comment references from content ===
    # Content may contain "评论#5:" or "评论#6:" as inline references
    ref_match = re.search(r'评论#(\d+)', content)
    if ref_match:
        return f"评论#{ref_match.group(1)}", ""

    # === Step 2: Match against indexed comments using key-phrase matching ===
    # (content may be truncated/fragmented from line-by-line parsing)
    for cm in source_lookup.get("comments", []):
        cm_content = cm.get("content", "").strip()
        if not cm_content:
            continue
        # Bidirectional substring matching with flexible overlap
        # 1. Evidence content is contained in comment
        if content_trunc and content_trunc in cm_content:
            return f"评论#{cm.get('index', '')}", ""
        # 2. Comment is contained in evidence content
        if len(cm_content) > 20 and cm_content[:100] in content_trunc:
            return f"评论#{cm.get('index', '')}", ""
        # 3. Key phrase match: take first 30 chars of evidence, see if they appear in comment
        key_phrase = content_trunc[:30].strip()
        if len(key_phrase) >= 10 and key_phrase in cm_content:
            return f"评论#{cm.get('index', '')}", ""
        # 4. Fuzzy: check if a meaningful chunk (5+ words or 20+ chars) appears
        if len(key_phrase) >= 20:
            for window_size in [25, 20, 15]:
                window = key_phrase[:window_size].strip()
                if len(window) >= 10 and window in cm_content:
                    return f"评论#{cm.get('index', '')}", ""

    # === Step 3: Match against bug description ===
    desc = source_lookup.get("description", "").strip()
    if desc:
        desc_trunc = desc[:100]
        if content_trunc and content_trunc in desc_trunc:
            return "缺陷描述", ""
        if desc_trunc and desc_trunc in content_trunc:
            return "缺陷描述", ""

    # === Step 4: Fallback ===
    if source_file:
        return source_file, line_number or ""

    return "未知", ""


def _build_evidence_chain(result: Dict) -> Dict:
    """Build structured evidence chain from analysis result.

    Classifies evidence into three tiers:
    - direct: Strongest evidence — crash signatures, fatal errors, explicit error messages
      that directly point to the root cause
    - indirect: Supporting evidence — warnings, timing issues, shader errors that
      corroborate but don't independently prove the root cause
    - auxiliary: Contextual evidence — developer comments, log attachments, similar bugs

    Each evidence item includes: type, content, source_file, line_number, relevance
    (explanation of why this supports the conclusion).

    SOURCE RESOLUTION (2026-04-30):
    When source_file is None (e.g., evidence comes from comments or bug description),
    this function resolves the original source using:
    - feishu_evidence.technical_evidence (has comment index)
    - bug_info.description / bug_info.comments
    - Content text matching against known sources
    """
    root_cause = result.get("root_cause", "未知")
    log_analysis = result.get("log_analysis", {})
    confidence = result.get("confidence", {})

    # Pre-build source lookup maps for comment-based evidence resolution
    source_lookup = _build_source_lookup(result)

    chain = {"direct": [], "indirect": [], "auxiliary": []}

    # === Direct Evidence ===

    # 1. Native Crash (strongest direct evidence)
    nc = log_analysis.get("native_crash", {})
    if nc.get("has_native_crash"):
        ci = nc.get("crash_info", {})
        ev = {
            "type": "Native Crash",
            "content": ci.get("description", f"{ci.get('crash_type', '')} - {ci.get('signal_name', ci.get('signal', ''))}"),
            "source_file": ci.get("source_file"),
            "line_number": ci.get("line_number"),
            "relevance": "崩溃信号直接指向根因",
            "severity": "critical"
        }
        nc_errors = nc.get("errors", [])
        if nc_errors:
            ev["details"] = f"{len(nc_errors)} 个崩溃错误"
        chain["direct"].append(ev)

    # 2. Crash signature
    crash_sig = log_analysis.get("crash_signature")
    if crash_sig:
        chain["direct"].append({
            "type": "崩溃签名",
            "content": crash_sig.get("description", crash_sig.get("keyword", "")),
            "relevance": "崩溃特征与根因匹配",
            "severity": "critical"
        })

    # 3. Fatal errors (only from actual log files, not developer comments)
    fatal_errors = [e for e in log_analysis.get("errors", []) if e.get("type") == "FATAL"]
    if fatal_errors:
        for err in fatal_errors[:3]:
            sf = err.get("source_file")
            ln = err.get("line_number")
            content = err.get("content", "")
            # Only include if it comes from an actual log file (has source_file + line_number)
            # or contains technical crash signatures (not conversational text)
            is_log_entry = bool(sf and ln)
            has_tech_signature = bool(re.search(r'(SIG\w+|0x[0-9a-f]+|Segmentation|exception|abort|FORTIFY|FATAL\s+EXCEPTION|backtrace)', content, re.I))
            if is_log_entry or has_tech_signature:
                chain["direct"].append({
                    "type": "FATAL 错误",
                    "content": content[:200],
                    "source_file": sf,
                    "line_number": ln,
                    "relevance": "致命错误直接支持根因判断",
                    "severity": "critical"
                })

    # 4. Key error keywords (SIGSEGV, SIGABRT, FORTIFY, ANR, etc.)
    error_keywords = []
    for err in log_analysis.get("errors", [])[:10]:
        content = err.get("content", "")
        sf = err.get("source_file")
        ln = err.get("line_number")
        for kw in ["SIGSEGV", "SIGABRT", "SIGBUS", "FORTIFY", "ANR", "process died", "segmentation fault",
                    "NullPointerException", "OutOfMemoryError", "FATAL EXCEPTION"]:
            if kw.lower() in content.lower():
                error_keywords.append({
                    "type": f"关键错误 ({kw})",
                    "content": content[:200],
                    "source_file": sf,
                    "line_number": ln,
                    "relevance": f"关键词 '{kw}' 与根因直接相关",
                    "severity": "high"
                })
                break
    chain["direct"].extend(error_keywords[:5])

    # === Indirect Evidence ===

    # 1. Timing issues
    ti = log_analysis.get("timing_issues", {})
    if ti.get("has_timing_issue"):
        for issue in ti.get("issues", [])[:3]:
            chain["indirect"].append({
                "type": f"时序问题 ({issue.get('type', '')})",
                "content": issue.get("context", "")[:200],
                "timestamp": issue.get("timestamp"),
                "source_file": issue.get("source_file"),
                "line_number": issue.get("line_number"),
                "relevance": "时序异常为根因提供佐证",
                "severity": issue.get("severity", "medium")
            })

    # 2. Shader/render errors
    se = log_analysis.get("shader_errors", {})
    if se.get("has_shader_error"):
        for err in se.get("errors", [])[:3]:
            chain["indirect"].append({
                "type": f"渲染错误 ({err.get('type', '')})",
                "content": err.get("context", err.get("description", ""))[:200],
                "source_file": err.get("source_file"),
                "line_number": err.get("line_number"),
                "relevance": "渲染错误与显示相关根因相符",
                "severity": err.get("severity", "medium")
            })

    # 3. Non-fatal errors
    non_fatal = [e for e in log_analysis.get("errors", []) if e.get("type") != "FATAL"]
    for err in non_fatal[:5]:
        sf = err.get("source_file")
        ln = err.get("line_number")
        chain["indirect"].append({
            "type": f"ERROR ({err.get('type', '')})",
            "content": err.get("content", "")[:180],
            "source_file": sf,
            "line_number": ln,
            "relevance": "错误日志支持根因推断",
            "severity": "medium"
        })

    # 4. Warnings
    for warn in log_analysis.get("warnings", [])[:3]:
        sf = warn.get("source_file")
        ln = warn.get("line_number")
        chain["indirect"].append({
            "type": "WARNING",
            "content": warn.get("content", "")[:150],
            "source_file": sf,
            "line_number": ln,
            "relevance": "警告信息提供辅助线索",
            "severity": "low"
        })

    # === Auxiliary Evidence ===

    # 1. Feishu developer comments
    feishu = result.get("feishu_evidence", {})
    tech_evidence = feishu.get("technical_evidence", [])
    if tech_evidence:
        for ev in tech_evidence[:5]:
            chain["auxiliary"].append({
                "type": f"开发者评论 (#{ev['index']})",
                "content": ev.get("content", "")[:200],
                "created_at": ev.get("created_at"),
                "relevance": "开发者分析提供专业判断",
                "severity": "info"
            })

    # 2. Log attachments
    attachments = feishu.get("log_attachments", [])
    download_info = feishu.get("download_result")
    if attachments:
        log_names = [a["name"] for a in attachments if a.get("available")][:5]
        if log_names:
            chain["auxiliary"].append({
                "type": "日志附件",
                "content": ", ".join(log_names),
                "relevance": f"下载 {download_info['downloaded_count']}/{download_info['total_found']} 个附件，包含实际日志内容",
                "severity": "info"
            })

    # 3. Similar bugs
    similar = result.get("similar_bugs", [])
    if similar:
        top = similar[0]
        chain["auxiliary"].append({
            "type": "相似缺陷",
            "content": f"{top.get('title', '')} (相似度: {top.get('score', 0):.2f})",
            "relevance": f"{len(similar)} 个历史相似缺陷可交叉验证",
            "severity": "info"
        })

    # === Source Resolution for all evidence items ===
    # Resolve source_file/line_number for evidence that lacks direct file attribution
    _resolve_all_sources(chain, source_lookup)

    return chain


def _resolve_all_sources(chain: Dict, source_lookup: Dict):
    """Resolve source_file and line_number for all evidence items lacking file attribution."""
    for tier in ("direct", "indirect", "auxiliary"):
        for ev in chain.get(tier, []):
            sf = ev.get("source_file")
            ln = ev.get("line_number")
            content = ev.get("content", "")
            if sf is None and not ln:
                resolved_sf, resolved_ln = _resolve_source(content, None, None, source_lookup)
                ev["source_file"] = resolved_sf
                ev["line_number"] = resolved_ln


def _strip_md(text: str) -> str:
    """Remove markdown formatting from text (for clean table cells)."""
    text = text.replace('**', '').replace('*', '')
    text = text.replace('`', '').replace('~~', '')
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # links
    return text.strip()


def _format_evidence_item(ev: Dict, index: int, tier: str, source_lookup: Dict = None) -> str:
    """Format a single evidence item for the report."""
    tier_icons = {"direct": "🔴", "indirect": "🟡", "auxiliary": "⚪"}
    icon = tier_icons.get(tier, "⚪")

    # Build location reference
    loc_parts = []
    sf = ev.get("source_file")
    ln = ev.get("line_number")

    if sf and ln and sf not in ("未知", "评论", "缺陷描述"):
        loc_parts.append(f"`{sf}:{ln}`")
    elif sf and sf not in ("未知", "评论", "缺陷描述"):
        loc_parts.append(f"`{sf}`")
    elif sf and sf.startswith("评论#"):
        loc_parts.append(f"`{sf}`")
    elif sf and sf == "缺陷描述":
        loc_parts.append("`缺陷描述`")
    elif ev.get("line"):
        loc_parts.append(f"L{ev['line']}")

    if ev.get("timestamp"):
        loc_parts.append(f"[{ev['timestamp']}]")
    elif ev.get("created_at"):
        loc_parts.append(f"[{ev['created_at']}]")

    loc_str = " ".join(loc_parts)
    if loc_str:
        loc_str = f" {loc_str}"

    # Build content with optional details
    content = ev.get("content", "")
    if ev.get("details"):
        content += f" ({ev['details']})"

    relevance = ev.get("relevance", "")

    lines = [f"{index}. {icon} **{ev['type']}**{loc_str}\n   - {content}\n   - 关联: {relevance}"]

    # Add context snippet for file-based evidence
    if sf and ln and sf not in ("未知", "评论", "缺陷描述"):
        log_contents = source_lookup.get("log_contents", {}) if source_lookup else {}
        snippet = _extract_context_snippet(sf, ln, log_contents)
        if snippet:
            lines.append(f"   ```\n   {snippet}\n   ```")

    return "\n".join(lines)


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
            "shader_detection": "Shader 检测",
            "native_crash_detection": "Native Crash 检测",
            "timing_detection": "时序检测",
            "similarity_match": "相似匹配",
            "root_cause_certainty": "根因确定性"
        }
        for dim, name in dim_names.items():
            val = details.get(dim, 0)
            bar = "█" * int(val * 5) + "░" * (5 - int(val * 5))
            lines.append(f"- {name}: {bar} {val:.0%}")
        if details.get("time_concentrated"):
            lines.append("- 时间集中度: ✅ 错误集中爆发")
        if details.get("log_source_score", 0) > 0:
            lines.append(f"- 日志源可信度: +{details['log_source_score']:.0%}")
    
    return "\n".join(lines)


def generate_tldr(result: Dict) -> str:
    """生成 TL;DR 摘要 (P1: 新增 native_crash/timing/shader 维度)"""
    log_analysis = result.get("log_analysis", {})
    root_cause = result.get("root_cause", "未知")

    fatal = log_analysis.get("fatal_count", 0)
    errors = log_analysis.get("error_count", 0)
    warnings = log_analysis.get("warning_count", 0)
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

    # P1 新增维度 — 兼容两种数据结构:
    # (1) log_analysis["summary"] 旧格式
    # (2) log_analysis["native_crash"/"shader_errors"/"timing_issues"] 新格式
    summary = log_analysis.get("summary", {})

    nc = log_analysis.get("native_crash", {})
    if summary.get("has_native_crash") or nc.get("has_native_crash"):
        ci = nc.get("crash_info", {})
        ct = summary.get("native_crash_types", ci.get("crash_type", "native"))
        parts.append(f"💣 Native 崩溃: {ct}")

    se = log_analysis.get("shader_errors", {})
    if summary.get("has_shader_error") or se.get("has_shader_error"):
        types = se.get("error_types", summary.get("shader_error_types", []))
        st = ", ".join(types[:3]) if types else "shader"
        parts.append(f"🎨 Shader 错误: {st}")

    ti = log_analysis.get("timing_issues", {})
    if summary.get("has_timing_issue") or ti.get("has_timing_issue"):
        tt = summary.get("timing_issue_count", ti.get("issue_count", len(ti.get("issues", []))))
        parts.append(f"⏱️ 时序问题: {tt} 个")

    if root_cause and "需要进一步分析" not in root_cause:
        parts.append(f"\n📍 根因: {root_cause}")

    return " | ".join(parts) if parts else "日志分析未发现明显问题"


def generate_markdown_report(analysis_result: Dict, bug_info = None) -> str:
    """生成精简分层 Markdown 分析报告

    三层结构：
    1. 核心结论 — 根因 + 置信度 + 3条关键证据
    2. 证据链 — 直接证据 → 间接证据 → 辅助证据
    3. 详细数据 — 完整错误列表、堆栈、相似缺陷等
    """
    if bug_info is not None and not isinstance(bug_info, dict):
        bug_info = {"id": str(bug_info), "source": "ID查询", "description": ""}

    log_analysis = analysis_result.get("log_analysis", {})
    
    # Use LLM root cause if available (overrides rule-engine root_cause)
    raw_root_cause = analysis_result.get("root_cause", "未知")
    llm_text = analysis_result.get("llm_analysis", "")
    if llm_text and isinstance(llm_text, str):
        import re
        # Pattern 1: heading + bold first line (e.g., ### 根因分析\n**DP Alt-Mode...**)
        m = re.search(r'###\s*根因分析.*?\n\*\*(.+?)\*\*[:：]?(.+?)(?=\n\*\*|\n\d+\.\s+\*\*|\n\n###|\n---)', llm_text, re.DOTALL)
        if m:
            title = _strip_md(m.group(1).strip())
            desc = _strip_md(m.group(2).strip().split('\n')[0])[:200]
            raw_root_cause = f"{title}: {desc}" if desc else title
        else:
            # Pattern 2: heading + numbered list items (e.g., 1. **xxx**：yyy)
            m2 = re.search(r'###\s*根因分析[^\n]*\n\n?\d+\.\s+\*\*(.+?)\*\*[:：](.+?)(?=\n\d+\.\s+\*\*|\n\n###|\n---)', llm_text, re.DOTALL)
            if m2:
                title = _strip_md(m2.group(1).strip())
                desc = _strip_md(m2.group(2).strip().split('\n')[0])[:200]
                raw_root_cause = f"{title}: {desc}" if desc else title
            else:
                # Pattern 3: fallback to first paragraph
                m3 = re.search(r'###\s*根因分析[^\n]*\n\n(.+?)(?=\n\n###|\Z)', llm_text, re.DOTALL)
                if m3:
                    raw_root_cause = _strip_md(m3.group(1).strip().split('\n')[0])[:300]
    root_cause = raw_root_cause
    
    suggestion = analysis_result.get("suggestion", "无")
    confidence = analysis_result.get("confidence", {})
    stack_traces = analysis_result.get("stack_traces", [])

    tldr = generate_tldr(analysis_result)
    evidence_chain = _build_evidence_chain(analysis_result)
    source_lookup = _build_source_lookup(analysis_result)

    report = f"""# 🐛 Bug 分析报告

## 核心结论

| 项目 | 内容 |
|------|------|
| 分析时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
| 缺陷ID | {bug_info.get('id', 'N/A') if bug_info else 'N/A'} |
| **根因** | **{root_cause}** |
| **置信度** | **{confidence.get('score', 0):.0%} {confidence.get('level', '🔴 低')}** |
"""
    if bug_info and bug_info.get('description'):
        report += f"| 问题描述 | {bug_info['description'][:200]} |\n"

    # 关键证据摘要（直接证据前3条）
    top_evidence = evidence_chain["direct"][:3]
    if top_evidence:
        report += "\n**关键证据：**\n"
        for i, ev in enumerate(top_evidence, 1):
            loc = ""
            if ev.get("source_file") and ev.get("line_number"):
                loc = f"`{ev['source_file']}:{ev['line_number']}` "
            report += f"{i}. 🔴 **{ev['type']}** {loc}— {ev['content'][:150]}\n"

    report += "\n---\n\n## 证据链\n\n"

    # 第一层：直接证据
    if evidence_chain["direct"]:
        report += "### 直接证据\n\n"
        for i, ev in enumerate(evidence_chain["direct"], 1):
            report += _format_evidence_item(ev, i, "direct", source_lookup) + "\n\n"

    # 第二层：间接证据
    if evidence_chain["indirect"]:
        report += "### 间接证据\n\n"
        for i, ev in enumerate(evidence_chain["indirect"], 1):
            report += _format_evidence_item(ev, i, "indirect", source_lookup) + "\n\n"

    # 第三层：辅助证据
    if evidence_chain["auxiliary"]:
        report += "### 辅助证据\n\n"
        for i, ev in enumerate(evidence_chain["auxiliary"], 1):
            report += _format_evidence_item(ev, i, "auxiliary", source_lookup) + "\n\n"

    # 建议方案 - use LLM suggestions if available
    report += "---\n\n## 解决方案\n\n"
    # Try to extract LLM's "建议措施" section
    llm_suggestion = None
    if llm_text and isinstance(llm_text, str):
        import re as _re
        # Try flexible patterns for suggestion extraction
        m_sug = _re.search(r'###\s*建议措施[^\n]*\n(.+?)(?=\n###|\Z)', llm_text, re.DOTALL)
        if m_sug:
            llm_suggestion = m_sug.group(1).strip()
        else:
            m_sug2 = _re.search(r'###\s*建议措施[^\n]*\n?\n?(.+)', llm_text, re.DOTALL)
            if m_sug2:
                llm_suggestion = m_sug2.group(1).strip()
    report += f"{llm_suggestion if llm_suggestion else suggestion}\n\n"

    # 第三层：详细数据（可折叠）
    report += "---\n\n<details>\n"
    report += "<summary>详细数据（展开）</summary>\n\n"

    # 错误统计
    report += "### 错误统计\n\n"
    report += f"- FATAL: {log_analysis.get('fatal_count', 0)} | "
    report += f"ERROR: {log_analysis.get('error_count', 0)} | "
    report += f"WARNING: {log_analysis.get('warning_count', 0)}\n\n"

    # 错误码
    error_codes = log_analysis.get("error_codes", [])
    if error_codes:
        codes = list({ec["code"] for ec in error_codes})[:10]
        report += f"**错误码**: {', '.join(f'`{c}`' for c in codes)}\n\n"

    # 堆栈跟踪
    all_traces = stack_traces or log_analysis.get("stack_traces", [])
    if all_traces:
        report += "### 堆栈跟踪\n\n"
        for i, trace in enumerate(all_traces[:3], 1):
            content = trace.get("content", str(trace))[:400] if isinstance(trace, dict) else str(trace)[:400]
            report += f"**Stack #{i}**\n```\n{content}\n```\n\n"

    # Native Crash 详情
    nc = log_analysis.get("native_crash", {})
    if nc.get("has_native_crash"):
        report += "### Native Crash\n\n"
        ci = nc.get("crash_info", {})
        report += f"- 类型: {ci.get('crash_type', 'N/A')} | 信号: {ci.get('signal_name', ci.get('signal', 'N/A'))}\n"
        if ci.get('source_file') and ci.get('line_number'):
            report += f"- 来源: `{ci['source_file']}:{ci['line_number']}`\n"
        if ci.get('module'):
            report += f"- 模块: {ci['module']}\n"
        if ci.get('context'):
            report += f"- 上下文: {ci['context'][:200]}\n"
        report += "\n"

    # Shader/时序
    se = log_analysis.get("shader_errors", {})
    if se.get("has_shader_error"):
        report += f"### Shader 错误: {', '.join(se.get('error_types', []))}\n\n"
    ti = log_analysis.get("timing_issues", {})
    if ti.get("has_timing_issue"):
        types = ', '.join(ti.get('issue_types', []))
        count = ti.get('issue_count', len(ti.get('issues', [])))
        report += f"### 时序问题 ({count}个): {types}\n\n"

    # 飞书证据
    feishu = analysis_result.get("feishu_evidence", {})
    if feishu:
        tech = feishu.get("technical_evidence", [])
        if tech:
            report += "### 技术线索（完整）\n\n"
            for ev in tech[:5]:
                time_str = f"[{ev['created_at']}]" if ev.get('created_at') else ""
                report += f"**#{ev['index']}** {time_str}\n```\n{ev['content'][:300]}\n```\n\n"

    # 归档目录树
    download_result = feishu.get("download_result")
    if download_result and download_result.get("archive_trees"):
        report += "### 归档附件目录结构\n\n"
        for archive_name, tree in download_result["archive_trees"].items():
            report += f"```\n{tree}\n```\n\n"
        report += "以上为解压后的文件结构，报告中的证据行号指向具体日志文件。\n\n"

    # 相似缺陷
    similar = analysis_result.get("similar_bugs", [])
    if similar:
        report += "### 相似缺陷\n\n"
        for bug in similar[:5]:
            report += f"- {'✅' if bug.get('score', 0) >= 0.7 else '🔶'} **{bug.get('title', 'N/A')[:40]}** (得分: {bug.get('score', 0):.2f}, 状态: {bug.get('status', 'N/A')})\n"
        report += "\n"

    report += "</details>\n\n"
    report += f"*报告生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n"

    return report


def generate_feishu_report(analysis_result: Dict, bug_info: Dict = None) -> str:
    """生成飞书格式报告（精简分层版）"""
    log_analysis = analysis_result.get("log_analysis", {})
    root_cause = analysis_result.get("root_cause", "未知")
    suggestion = analysis_result.get("suggestion", "无")
    confidence = analysis_result.get("confidence", {})
    bug_id = bug_info.get('id', 'N/A') if bug_info else 'N/A'

    evidence_chain = _build_evidence_chain(analysis_result)

    top_evidence = evidence_chain["direct"][:3]
    ev_lines = ""
    for i, ev in enumerate(top_evidence, 1):
        loc = ""
        if ev.get("source_file") and ev.get("line_number"):
            loc = f" `{ev['source_file']}:{ev['line_number']}`"
        ev_lines += f"{i}. 🔴 {ev['type']}{loc}: {ev['content'][:120]}\n"

    nc = log_analysis.get('native_crash', {})
    se = log_analysis.get('shader_errors', {})
    ti = log_analysis.get('timing_issues', {})
    p1 = []
    if nc.get('has_native_crash'):
        ci = nc.get('crash_info', {})
        p1.append(f"• Native崩溃：{ci.get('crash_type', 'N/A')} ({ci.get('signal_name', ci.get('signal', ''))})")
    if se.get('has_shader_error'):
        p1.append(f"• Shader错误：{', '.join(se.get('error_types', [])[:2])}")
    if ti.get('has_timing_issue'):
        p1.append(f"• 时序问题：{ti.get('issue_count', len(ti.get('issues', [])))}个 ({', '.join(ti.get('issue_types', []))})")

    code_str = ", ".join(list({ec["code"] for ec in log_analysis.get("error_codes", [])})[:5]) or "无"

    report = f"""【Bug分析报告】

■ 核心结论
• 缺陷ID：{bug_id}
• 根因：{root_cause}
• 置信度：{confidence.get('score', 0):.0%} {confidence.get('level', '🔴 低')}
"""
    if bug_info and bug_info.get('description'):
        report += f"• 问题描述：{bug_info['description'][:150]}\n"
    if p1:
        report += "\n" + "\n".join(p1) + "\n"
    if ev_lines:
        report += f"\n关键证据：\n{ev_lines}"

    report += f"""
■ 证据链
"""
    if evidence_chain["direct"]:
        report += f"\n【直接证据 {len(evidence_chain['direct'])}条】\n"
        for ev in evidence_chain["direct"][:5]:
            loc = ""
            if ev.get("source_file") and ev.get("line_number"):
                loc = f" `{ev['source_file']}:{ev['line_number']}`"
            report += f"• 🔴 {ev['type']}{loc}: {ev['content'][:100]}\n"

    if evidence_chain["indirect"]:
        report += f"\n【间接证据 {len(evidence_chain['indirect'])}条】\n"
        for ev in evidence_chain["indirect"][:5]:
            loc = ""
            if ev.get("source_file") and ev.get("line_number"):
                loc = f" `{ev['source_file']}:{ev['line_number']}`"
            report += f"• 🟡 {ev['type']}{loc}: {ev['content'][:100]}\n"

    if evidence_chain["auxiliary"]:
        report += f"\n【辅助证据 {len(evidence_chain['auxiliary'])}条】\n"
        for ev in evidence_chain["auxiliary"][:3]:
            report += f"• ⚪ {ev['type']}: {ev['content'][:100]}\n"

    report += f"""
■ 日志统计
• 致命错误：{log_analysis.get('fatal_count', 0)}
• 错误：{log_analysis.get('error_count', 0)}
• 警告：{log_analysis.get('warning_count', 0)}
• 错误码：{code_str}

■ 解决方案
{suggestion}
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
        import tempfile
        file_path = f"{tempfile.gettempdir()}/bug_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    
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