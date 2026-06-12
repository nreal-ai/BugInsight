#!/usr/bin/env python3
"""
Bug Analyzer CLI - 缺陷分析命令行工具
用法:
    python3 bug_analyzer.py analyze <zip文件或日志路径>
    python3 bug_analyzer.py search <关键词>
    python3 bug_analyzer.py report <分析结果文件>
    python3 bug_analyzer.py feishu <缺陷ID或链接>
"""

import argparse
import json
import os
import re
import sys
import warnings
from datetime import datetime

# Suppress benign urllib3 NotOpenSSLWarning that pollutes stderr and breaks test exit codes
warnings.filterwarnings('ignore', category=DeprecationWarning, module='urllib3')
from typing import Dict, List, Optional

# 添加脚本目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from analyzer import BugAnalyzer
from code_search import CodeSearcher
from config import print_config_check
from pathlib import Path

# Configurable output directory
OUTPUT_DIR = Path(os.environ.get("BUG_ANALYZER_OUTPUT_DIR", "/tmp"))

def _ensure_output_dir():
    """确保输出目录存在"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class BugAnalyzerCLI:
    """Bug 分析器命令行工具"""

    def __init__(self):
        self.analyzer = BugAnalyzer()
        self.searcher = CodeSearcher()

    def cmd_analyze(self, args):
        """分析 ZIP 文件或日志"""
        path = args.path

        if not os.path.exists(path):
            print(f"错误: 文件不存在: {path}")
            return 1

        print(f"📂 正在分析: {path}")
        print("-" * 50)

        # 识别文件类型
        if path.endswith('.zip'):
            result = self.analyzer.analyze_zip(path)
        elif os.path.isfile(path):
            # 单个日志文件
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                log_content = f.read()
            # 调用 full_analysis 获取完整分析结果
            result = self.analyzer.full_analysis(log_content=log_content)
            
            # 如果启用 LLM 增强分析
            if args.llm:
                print("\n🤖 正在进行 LLM 增强分析...")
                llm_result = self.analyzer.llm_analyze(result, force=True)
                if llm_result:
                    # 将 LLM 结果合并到 result
                    result['llm_analysis'] = llm_result.get('result', '')
                    result['confidence'] = llm_result.get('confidence', {})
                    print(f"  LLM 分析完成 (置信度: {llm_result.get('confidence', {}).get('score', 0):.2f})")
        elif os.path.isdir(path):
            # 目录 - 查找所有日志文件
            result = self._analyze_directory(path)
        else:
            print("错误: 不支持的文件类型")
            return 1

        # 打印结果
        self._print_analysis_result(result)

        # 保存结果
        _ensure_output_dir()
        output_file = args.output or f"{OUTPUT_DIR}/bug_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n💾 结果已保存到: {output_file}")

        # 生成报告
        if args.report:
            from report import generate_markdown_report
            report = generate_markdown_report(result)
            report_file = output_file.replace('.json', '.md')
            with open(report_file, 'w', encoding='utf-8') as f:
                f.write(report)
            print(f"📝 报告已生成: {report_file}")

        return 0

    def _analyze_directory(self, dir_path: str) -> Dict:
        """分析目录中的所有日志文件"""
        all_log_contents = []
        log_files = []

        for root, dirs, files in os.walk(dir_path):
            for filename in files:
                if filename.endswith(('.log', '.txt', '.cat')) or 'log' in filename.lower():
                    filepath = os.path.join(root, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(100000)  # 每个文件限制 100KB
                            all_log_contents.append(content)
                            log_files.append(filename)
                    except Exception:
                        pass

        # 合并所有日志内容进行完整分析
        combined_log = "\n\n".join(all_log_contents) if all_log_contents else ""

        if combined_log.strip():
            result = self.analyzer.full_analysis(log_content=combined_log)
        else:
            result = {"log_analysis": {"errors": [], "warnings": [], "error_count": 0, "warning_count": 0},
                      "keywords": [], "root_cause": "无日志内容", "suggestion": "", "similar_bugs": []}

        result["files"] = log_files
        result["log_count"] = len(log_files)
        return result

    def _print_analysis_result(self, result: Dict):
        """打印分析结果"""
        log_analysis = result.get("log_analysis", {})
        errors = log_analysis.get("errors", [])
        warnings = log_analysis.get("warnings", [])

        print(f"\n📊 分析结果:")
        print(f"  • 错误: {log_analysis.get('error_count', 0)} 个")
        print(f"  • 警告: {log_analysis.get('warning_count', 0)} 个")

        if errors:
            print(f"\n🚨 关键错误 (前5条):")
            for err in errors[:5]:
                line = err.get("line", "?")
                content = err.get("content", "")[:80]
                print(f"  L{line}: {content}")

        if result.get("root_cause"):
            print(f"\n💡 根因分析: {result['root_cause']}")

        if result.get("suggestion"):
            print(f"\n🔧 建议: {result['suggestion']}")

        if result.get("similar_bugs"):
            print(f"\n🔍 相似缺陷: {len(result['similar_bugs'])} 个")
            for bug in result["similar_bugs"][:3]:
                print(f"  • [{bug.get('id', '')}] {bug.get('title', '')} (得分: {bug.get('score', 0):.2f})")

        # 显示 LLM 分析结果
        if result.get('llm_analysis'):
            print(f"\n🤖 LLM 增强分析:")
            llm_text = result['llm_analysis'][:1000]
            if len(result['llm_analysis']) > 1000:
                llm_text += "..."
            print(llm_text)
        
        if result.get('confidence'):
            conf = result['confidence']
            print(f"\n📈 置信度: {conf.get('score', 0):.2f} {conf.get('level', '')}")

    def cmd_search(self, args):
        """搜索相似缺陷"""
        query = args.query
        limit = args.limit

        print(f"🔍 搜索: {query}")
        print("-" * 50)

        # 搜索 OpenViking 知识库
        bugs = self.analyzer.find_similar_bugs(query, limit=limit)

        if bugs:
            print(f"\n找到 {len(bugs)} 个相似缺陷:\n")
            for i, bug in enumerate(bugs, 1):
                print(f"{i}. [{bug.get('id', '')}] {bug.get('title', '')}")
                print(f"   状态: {bug.get('status', 'UNKNOWN')} | 相似度: {bug.get('score', 0):.2f}")
                if bug.get('comments'):
                    c = bug['comments'][0]
                    preview = c.get('content', str(c))[:60] if isinstance(c, dict) else str(c)[:60]
                    print(f"   评论: {preview}...")
                print()
        else:
            print("未找到相似缺陷")

        # 代码搜索
        if args.code:
            print("\n" + "=" * 50)
            print("📚 代码搜索结果:\n")
            # 对多词查询，分词搜索（每个词单独搜索）
            all_results = []
            seen = set()
            for term in query.split():
                term_results = self.searcher.search_code(
                    term,
                    repos=args.repos or ["dove", "framework"],
                    max_results=10
                )
                for r in term_results:
                    key = f"{r['repo']}:{r['file']}"
                    if key not in seen:
                        seen.add(key)
                        all_results.append(r)

            if all_results:
                for r in all_results[:8]:
                    print(f"[{r['repo']}] {r['file']}")
                    for m in r['matches'][:2]:
                        print(f"  L{m['line_num']}: {m['content'][:60]}")
                    print()
            else:
                print("未找到相关代码")

        return 0

    def analyze_feishu_bug(self, bug_info: Dict) -> Dict:
        """Structured evidence extraction from Feishu bug comments and attachments.
        
        Separates technical evidence (logs, stack traces, error messages) from
        discussion text (opinions, image links, @mentions). Builds clean log_content
        for the analyzer with proper evidence tracking.
        """
        comments = bug_info.get('comments', [])
        attachments = bug_info.get('attachments', {})
        title = bug_info.get('title', '')
        desc = bug_info.get('description', '')
        
        # === Evidence containers ===
        technical_evidence = []    # Comments with technical content (stack traces, error codes, log snippets)
        discussion_points = []     # Discussion/opinion comments (not used for log analysis)
        log_attachments = []       # Log files found in attachments
        image_evidence = []        # Image references (for context, not for log parsing)
        timeline_events = []       # Chronological sequence of events
        
        # === Regex patterns for technical content detection ===
        tech_patterns = [
            re.compile(r'(SIG\w+|Segmentation fault|Bus error|Aborted)'),
            re.compile(r'(Exception|NullPointerException|IllegalArgumentException|IndexOutOfBoundsException|RuntimeException)', re.IGNORECASE),
            re.compile(r'(FATAL|CRASH|tombstone|backtrace|stacktrace|stack\s*trace)', re.IGNORECASE),
            re.compile(r'0x[0-9a-fA-F]{8,}'),  # Memory addresses
            re.compile(r'(at\s+[\w\.\$]+\.java:\d+|at\s+<\w+>)'),  # Java stack frames
            re.compile(r'(Error|error|ERROR)\s*[:=]'),
            re.compile(r'(memory|Memory|MEM)\s*(leak|不足|溢出|kill|OOM)', re.IGNORECASE),
            re.compile(r'(log|日志|logcat|adb)'),
        ]
        
        # === Noise patterns (to filter out) ===
        noise_patterns = [
            re.compile(r'^!\[.*?\]\(.*?\)$'),  # Pure markdown image
            re.compile(r'^https?://.*\.(png|jpg|jpeg|gif|webp)$', re.IGNORECASE),
        ]
        
        # === Process comments chronologically ===
        for i, comment in enumerate(comments):
            if isinstance(comment, dict):
                content = comment.get('content', '')
                created_at = comment.get('created_at', '')
            else:
                content = str(comment)
                created_at = ''
            
            if not content or not content.strip():
                continue
            
            # Check if it's pure noise (image link only)
            is_noise = any(p.match(content.strip()) for p in noise_patterns)
            if is_noise:
                # Extract image URL for context
                img_match = re.search(r'\]\((https?://[^\s)]+)\)', content)
                if img_match:
                    image_evidence.append({'url': img_match.group(1), 'source': f'comment_{i}'})
                continue
            
            # Clean content: remove markdown image syntax but keep surrounding text
            cleaned = re.sub(r'!\[.*?\]\(.*?\)', '', content).strip()
            cleaned = re.sub(r'<!--\s*mention:\{[^}]*\}\s*-->', '', cleaned).strip()
            cleaned = re.sub(r'<!--.*?-->', '', cleaned).strip()
            cleaned = re.sub(r'@\w+', '', cleaned).strip()  # Remove @mentions
            # Clean up multiple spaces
            cleaned = re.sub(r'\s+', ' ', cleaned).strip()
            
            if not cleaned:
                continue
            
            # Classify: technical vs discussion
            is_technical = any(p.search(cleaned) for p in tech_patterns)
            
            entry = {
                'index': i,
                'content': cleaned,
                'original': content,
                'created_at': created_at,
                'is_technical': is_technical,
            }
            
            if is_technical:
                technical_evidence.append(entry)
                timeline_events.append({
                    'time': created_at,
                    'type': 'technical',
                    'summary': cleaned[:100],
                })
            else:
                discussion_points.append(entry)
                # Still add to timeline for context
                if cleaned:
                    timeline_events.append({
                        'time': created_at,
                        'type': 'discussion',
                        'summary': cleaned[:80],
                    })
        
        # === Process attachments ===
        log_names = attachments.get('log_names', []) if attachments else []
        if log_names:
            for name in log_names:
                log_attachments.append({
                    'name': name,
                    'type': 'log',
                    'available': False,  # Not downloaded yet
                })
        
        # === Build clean log_content from technical evidence only ===
        log_parts = []
        
        # 1. Bug description (test report format)
        if desc:
            log_parts.append("=== 缺陷描述 ===")
            log_parts.append(desc)
            log_parts.append("")
        
        # 2. Technical evidence from comments (in order)
        if technical_evidence:
            log_parts.append("=== 评论中的技术线索 ===")
            for entry in technical_evidence:
                time_str = f"[{entry['created_at']}]" if entry['created_at'] else ""
                log_parts.append(f"{time_str} 评论#{entry['index']}:")
                log_parts.append(entry['content'])
                log_parts.append("")
        
        # 3. Attachment references
        if log_attachments:
            log_parts.append("=== 日志附件 ===")
            for att in log_attachments:
                log_parts.append(f"- {att['name']} ({att['type']}, {'available' if att['available'] else 'not downloaded'})")
            log_parts.append("")
        
        clean_log_content = "\n".join(log_parts).strip()
        
        # === Build analysis input dict ===
        analysis_input = {
            'bug_info': bug_info,
            'title': title,
            'description': desc,
            'clean_log_content': clean_log_content if clean_log_content else (desc or title),
            'technical_evidence': technical_evidence,
            'discussion_points': discussion_points,
            'log_attachments': log_attachments,
            'image_evidence': image_evidence,
            'timeline': timeline_events,
            'has_real_logs': any(a.get('available') for a in log_attachments),
            'has_technical_clues': len(technical_evidence) > 0,
        }
        
        return analysis_input

    def cmd_feishu(self, args):
        """从本地数据获取飞书缺陷并分析"""
        import time
        analysis_start_time = time.time()

        bug_input = args.input
        # 解析 ID：支持纯数字或 URL
        bug_id = bug_input.strip()
        if '/' in bug_id:
            # URL 提取最后一段数字
            parts = bug_id.rstrip('/').split('/')
            bug_id = parts[-1] if parts[-1].isdigit() else parts[-2]

        print(f"📋 正在获取飞书缺陷: {bug_id}")
        print("-" * 50)

        # 从本地数据查找
        bug_info = self._find_feishu_bug(bug_id)
        if not bug_info:
            print(f"错误: 未找到缺陷 {bug_id}")
            print("提示: 确保已运行 fetch_bugs_full.py 获取缺陷数据")
            return 1

        print(f"  ID: {bug_info['id']}")
        print(f"  标题: {bug_info.get('title', 'N/A')}")
        print(f"  状态: {bug_info.get('status', 'N/A')}")
        print(f"  评论数: {len(bug_info.get('comments', []))}")
        print(f"  附件: {bug_info.get('attachments', {})}")

        # === Step 1: Structured evidence extraction from cache ===
        analysis_input = self.analyze_feishu_bug(bug_info)
        
        # === Step 1.5: Real-time data refresh from Feishu Direct API ===
        live_data = self._fetch_live_feishu_data(bug_id)
        if live_data and not live_data.get('error'):
            # Merge live comments with cached ones (deduplicate, prefer live data)
            live_comments = live_data.get('comments', [])
            if live_comments:
                # Replace cache comments with live data for freshness
                bug_info['comments'] = live_comments
                print(f"  ⚡ 实时评论: {len(live_comments)} 条 (已替换缓存数据)")
            
            # Update attachment metadata if live data has more info
            live_attachments = live_data.get('attachments', [])
            if live_attachments:
                # Update bug_info attachments so re-analyze picks them up
                bug_info['attachments'] = {
                    'has_log': any(a['name'].lower().endswith(('.log', '.txt', '.zip', '.tar', '.gz')) for a in live_attachments),
                    'log_names': [a['name'] for a in live_attachments],
                    'has_image': any(a.get('type', '').startswith('image/') for a in live_attachments),
                    'image_count': sum(1 for a in live_attachments if a.get('type', '').startswith('image/')),
                    'total_files': len(live_attachments),
                }
                # Also merge into current analysis_input before re-analyze
                existing_names = set(a['name'] for a in analysis_input['log_attachments'])
                for att in live_attachments:
                    name = att.get('name', '')
                    if name and name not in existing_names:
                        analysis_input['log_attachments'].append({
                            'name': name,
                            'type': 'log' if name.lower().endswith(('.log', '.txt', '.html', '.json', '.xml', '.zip', '.tar', '.gz')) else 'attachment',
                            'available': False,
                        })
                        existing_names.add(name)
                print(f"  ⚡ 实时附件: {len(live_attachments)} 个 (已更新元数据)")
        elif live_data and live_data.get('error'):
            print(f"  ⚠ 实时数据获取: {live_data['error']}，使用缓存数据")
        
        # Re-analyze with fresh data
        analysis_input = self.analyze_feishu_bug(bug_info)
        
        print(f"\n  技术线索: {len(analysis_input['technical_evidence'])} 条")
        print(f"  讨论评论: {len(analysis_input['discussion_points'])} 条")
        print(f"  日志附件: {len(analysis_input['log_attachments'])} 个")
        print(f"  图片证据: {len(analysis_input['image_evidence'])} 个")

        # Collect Git URLs
        git_urls = args.git_url or []

        # === Step 2: Download actual log attachments from Feishu (body + comments) ===
        log_contents = {}
        download_result = None
        # Always attempt download if we have body attachments OR comments with potential file URLs
        has_body_attachments = bool(analysis_input['log_attachments'])
        has_comments = bool(bug_info.get('comments', []))
        if has_body_attachments or has_comments:
            try:
                from attachment_downloader import download_bug_attachments
                # Pass comments so comment attachments can also be downloaded
                download_result = download_bug_attachments(bug_id, comments=bug_info.get('comments', []))
                if download_result['log_contents']:
                    log_contents = download_result['log_contents']
                    # Update attachment availability status
                    downloaded_names = set()
                    for path in download_result['downloaded']:
                        downloaded_names.add(os.path.basename(path))
                    for att in analysis_input['log_attachments']:
                        if att['name'] in downloaded_names:
                            att['available'] = True
                elif download_result['total_found'] > 0 and not download_result['downloaded']:
                    print("  ⚠ 附件下载失败，将仅使用缓存中的评论线索进行分析")
            except ImportError:
                print("  ⚠ attachment_downloader 模块未找到，跳过附件下载")
            except Exception as e:
                print(f"  ⚠ 附件下载异常: {e}，将仅使用缓存中的评论线索进行分析")

        # === Step 3: Build comprehensive log content ===
        # Combine: cache comments + downloaded log file contents
        log_parts = []
        
        # 3a. Bug description
        desc = bug_info.get('description', '')
        if desc:
            log_parts.append("=== 缺陷描述 ===")
            log_parts.append(desc)
            log_parts.append("")
        
        # 3b. Comments with technical evidence (cleaned, noise-free)
        if analysis_input['technical_evidence']:
            log_parts.append("=== 评论中的技术线索 ===")
            for entry in analysis_input['technical_evidence']:
                time_str = f"[{entry['created_at']}]" if entry['created_at'] else ""
                log_parts.append(f"{time_str} 评论#{entry['index']}:")
                log_parts.append(entry['content'])
                log_parts.append("")
        
        # 3c. Downloaded log file contents
        if log_contents:
            log_parts.append("=== 日志附件内容 ===")
            for filename, content in log_contents.items():
                if isinstance(content, list):
                    # Safety net: prevent crash if downstream returns non-string
                    log_parts.append(f"\n--- {filename} --- [ERROR: unexpected type {type(content).__name__}]")
                    continue
                if content and content != "[二进制文件，无法读取内容]":
                    log_parts.append(f"\n--- {filename} ---")
                    log_parts.append(content)
                    log_parts.append("")
                else:
                    log_parts.append(f"\n--- {filename} --- [文件为空或二进制]")
            log_parts.append("")
        
        # 3d. Attachment references (status)
        if analysis_input['log_attachments']:
            log_parts.append("=== 日志附件状态 ===")
            for att in analysis_input['log_attachments']:
                status = "✓ 已下载分析" if att.get('available') else "⊘ 未下载"
                log_parts.append(f"- {att['name']} ({status})")
            log_parts.append("")
        
        clean_log = "\n".join(log_parts).strip()
        if not clean_log:
            clean_log = desc or bug_info.get('title', '')

        # === Step 4: Run analysis ===
        print(f"\n🔍 正在分析...")
        result = self.analyzer.full_analysis(
            log_content=clean_log if clean_log else None,
            bug_description=desc if desc else bug_info.get('title', ''),
            comments=bug_info.get('comments', [])
        )

        # Add structured evidence to result
        result['bug_id'] = bug_id
        result['title'] = bug_info.get('title', '')
        result['feishu_evidence'] = {
            'technical_evidence': analysis_input['technical_evidence'],
            'discussion_points': analysis_input['discussion_points'],
            'log_attachments': analysis_input['log_attachments'],
            'image_evidence': analysis_input['image_evidence'],
            'timeline': analysis_input['timeline'],
            'has_real_logs': analysis_input['has_real_logs'] or bool(log_contents),
            'has_technical_clues': analysis_input['has_technical_clues'],
            'download_result': {
                'total_found': download_result['total_found'] if download_result else 0,
                'downloaded_count': len(download_result['downloaded']) if download_result else 0,
                'failed_count': len(download_result['failed']) if download_result else 0,
                'skipped_count': len(download_result['skipped']) if download_result else 0,
                'archive_trees': download_result.get('archive_trees', {}) if download_result else {},
            } if download_result else None,
        }
        # Inject downloaded log contents for LLM prompt access
        result['downloaded_log_contents'] = log_contents
        result['bug_info'] = bug_info
        result['confidence'] = self.analyzer.evaluate_confidence(result)

        # If LLM analysis is enabled
        if args.llm:
            print("\n🤖 正在进行 LLM 增强分析...")
            llm_result = self.analyzer.llm_analyze(result, force=True)
            if llm_result:
                result['llm_analysis'] = llm_result.get('result', '')
                if llm_result.get('confidence'):
                    result['confidence'] = llm_result['confidence']
                print(f"  LLM 分析完成 (置信度: {result['confidence'].get('score', 0):.2f})")

        # Show results
        self._print_analysis_result(result)

        # Calculate analysis duration
        elapsed = time.time() - analysis_start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        result['analysis_duration_seconds'] = round(elapsed, 1)
        result['analysis_duration_str'] = f"{hours}小时{minutes}分{seconds}秒"
        print(f"\n⏱ 分析耗时: {result['analysis_duration_str']}")

        # Save
        _ensure_output_dir()
        output_file = f"{OUTPUT_DIR}/bug_{bug_id}_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n💾 结果已保存到: {output_file}")

        # Generate report
        report_file = output_file.replace('.json', '.md')
        from report import generate_markdown_report, save_report
        md = generate_markdown_report(result, bug_info)
        save_report(md, report_file)
        print(f"📝 报告已生成: {report_file}")

        return 0

    def cmd_report(self, args):
        """从 JSON 分析结果生成报告"""
        input_file = args.input
        if not os.path.exists(input_file):
            print(f"错误: 文件不存在: {input_file}")
            return 1

        print(f"📝 正在生成报告: {input_file}")
        print("-" * 50)

        with open(input_file, 'r', encoding='utf-8') as f:
            result = json.load(f)

        # 提取 bug_info（如果结果中有）
        bug_info = result.get('bug_info', {})

        if args.format == 'feishu':
            from report import generate_feishu_report
            report = generate_feishu_report(result, bug_info)
            ext = '.txt'
        else:
            from report import generate_markdown_report
            report = generate_markdown_report(result, bug_info)
            ext = '.md'

        output_file = args.output or input_file.replace('.json', ext)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(report)

        print(f"  格式: {args.format.upper()}")
        print(f"  输出: {output_file}")
        print(f"  大小: {os.path.getsize(output_file)} 字节")
        return 0

    def _fetch_live_feishu_data(self, bug_id: str) -> Optional[Dict]:
        """Fetch real-time comments and attachment metadata from Feishu Direct API."""
        try:
            from attachment_downloader import get_feishu_credentials, get_plugin_token, fetch_live_bug_data
            
            creds = get_feishu_credentials()
            if not creds.get('project_key') or not creds.get('plugin_secret'):
                return None
            
            token = get_plugin_token(creds['project_key'], creds['plugin_id'], creds['plugin_secret'])
            if not token:
                return {'error': '无法获取 Plugin Token', 'comments': [], 'attachments': []}
            
            return fetch_live_bug_data(bug_id, creds['project_key'], token, creds['user_key'])
        except ImportError:
            return None
        except Exception as e:
            return {'error': str(e), 'comments': [], 'attachments': []}

    def _find_feishu_bug(self, bug_id: str) -> Optional[Dict]:
        """从本地数据查找飞书缺陷

        数据源优先级（从最优到最差）：
        1. .bug_index_cache.json（4060 条，含完整描述和搜索文本）
        2. bugs_index.json（4060 条，仅 id/name/status）
        3. bugs_all_with_details.json / bugs_full_all.json 等（如果存在）
        """
        # 输入验证：拒绝空或无效的 bug_id
        cleaned_id = bug_id.strip()
        if not cleaned_id:
            return None
        if not cleaned_id.isdigit():
            return None
        # 1. 优先从 .bug_index_cache.json 查找（最可靠，含完整描述）
        cache_path = os.path.expanduser("~/.openviking/workspace/feishu-bugs/.bug_index_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                idx = cache.get('index', {})
                if cleaned_id in idx:
                    entry = idx[cleaned_id]
                    return {
                        'id': cleaned_id,
                        'title': entry.get('name', ''),
                        'status': entry.get('status', ''),
                        'description': entry.get('desc_lower', ''),
                        'log_content': entry.get('search_text', ''),
                        'comments': entry.get('comments', []),
                        'attachments': entry.get('attachments', {}),
                        'source': '飞书项目(cache)',
                    }
            except Exception as e:
                print(f"  读取缓存失败: {e}")

        # 2. 从 bugs_index.json 查找（4060 条，仅基本信息）
        index_path = os.path.expanduser("~/.openviking/workspace/feishu-bugs/batch/bugs_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, 'r', encoding='utf-8') as f:
                    bugs = json.load(f)
                for b in bugs:
                    if str(b.get('id', '')) == cleaned_id:
                        return {
                            'id': cleaned_id,
                            'title': b.get('name', ''),
                            'status': b.get('status', ''),
                            'description': '',
                            'log_content': '',
                            'comments': [],
                            'source': '飞书项目(index)',
                        }
            except Exception as e:
                print(f"  读取索引失败: {e}")

        # 3. 备用：从旧格式完整数据文件查找（如果存在）
        data_paths = [
            os.path.expanduser("~/.openviking/workspace/feishu-bugs/batch/bugs_full_all.json"),
            os.path.expanduser("~/.openviking/workspace/feishu-bugs/bugs_all_with_details.json"),
            os.path.expanduser("~/.openviking/workspace/feishu-bugs/bugs_full_details.json"),
            os.path.expanduser("~/.openviking/workspace/feishu-bugs/batch/bugs_details_full.json"),
        ]

        for path in data_paths:
            if not os.path.exists(path):
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    bugs = json.load(f)

                for b in bugs:
                    bid = str(b.get('id', ''))
                    if bid == cleaned_id:
                        return self._parse_bug_detail(b)

                # 尝试从 detail 里的 work_item_id 匹配
                for b in bugs:
                    detail = b.get('detail', {})
                    attrs = detail.get('work_item_attribute', {})
                    wid = str(attrs.get('work_item_id', ''))
                    if wid == cleaned_id:
                        return self._parse_bug_detail(b)
            except Exception as e:
                print(f"  读取 {path} 失败: {e}")
                continue

        return None

    def _parse_bug_detail(self, bug: Dict) -> Dict:
        """解析缺陷详情为统一格式"""
        bid = bug.get('id', '')
        detail = bug.get('detail', {})
        attrs = detail.get('work_item_attribute', {})
        fields = attrs.get('fields', [])

        title = attrs.get('work_item_name', '')
        status = ''
        wstatus = attrs.get('work_item_status', {})
        if isinstance(wstatus, dict):
            status = wstatus.get('name', wstatus.get('key', ''))
        elif isinstance(wstatus, str):
            status = wstatus

        description = ''
        log_content = ''
        for field in fields:
            fk = field.get('field_key', '')
            fv = field.get('field_value', '')
            if fk in ('description', '缺陷描述'):
                description = fv
            if fk in ('log', '日志', 'log_content'):
                log_content = fv

        comments = bug.get('comments', [])

        return {
            'id': bid,
            'title': title,
            'status': status,
            'description': description,
            'log_content': log_content,
            'comments': comments,
            'source': '飞书项目',
        }


def main():
    # 启动时检查配置
    print_config_check()
    
    parser = argparse.ArgumentParser(
        description="Bug Analyzer - 缺陷分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s analyze /path/to/bug.zip
  %(prog)s analyze /path/to/log.txt --report
  %(prog)s search "USB连接异常" --code
  %(prog)s search "黑屏" --limit 10

详细文档请参考 SKILL.md
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # analyze 子命令
    analyze_parser = subparsers.add_parser("analyze", help="分析 ZIP 文件或日志")
    analyze_parser.add_argument("path", help="ZIP 文件、日志文件或目录路径")
    analyze_parser.add_argument("-o", "--output", help="输出 JSON 文件路径")
    analyze_parser.add_argument("-r", "--report", action="store_true", help="生成 Markdown 报告")
    analyze_parser.add_argument("--llm", action="store_true", help="启用 LLM 增强分析")
    analyze_parser.set_defaults(func="analyze")

    # search 子命令
    search_parser = subparsers.add_parser("search", help="搜索相似缺陷")
    search_parser.add_argument("query", help="搜索关键词")
    search_parser.add_argument("-l", "--limit", type=int, default=5, help="返回结果数量")
    search_parser.add_argument("-c", "--code", action="store_true", help="同时搜索代码")
    search_parser.add_argument("--repos", nargs="+", help="搜索的代码仓库列表")
    search_parser.set_defaults(func="search")

    # feishu 子命令
    feishu_parser = subparsers.add_parser("feishu", help="分析飞书缺陷")
    feishu_parser.add_argument("input", help="飞书缺陷链接或 ID")
    feishu_parser.add_argument("--llm", action="store_true", help="启用 LLM 增强分析")
    feishu_parser.add_argument("--git-url", nargs="+", help="Git 仓库 URL 列表，用于代码搜索（按需克隆到临时目录）")
    feishu_parser.add_argument("-p", "--project", help="飞书项目 Key（如 axr, sw_team），用于分析完成后更新 AI分析 字段")
    feishu_parser.set_defaults(func="feishu")

    # report 子命令
    report_parser = subparsers.add_parser("report", help="生成报告")
    report_parser.add_argument("input", help="分析结果 JSON 文件")
    report_parser.add_argument("-o", "--output", help="输出报告路径")
    report_parser.add_argument("-f", "--format", choices=["md", "feishu"], default="md", help="报告格式")
    report_parser.set_defaults(func="report")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    cli = BugAnalyzerCLI()

    if args.func == "analyze":
        return cli.cmd_analyze(args)
    elif args.func == "search":
        return cli.cmd_search(args)
    elif args.func == "feishu":
        return cli.cmd_feishu(args)
    elif args.func == "report":
        return cli.cmd_report(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())