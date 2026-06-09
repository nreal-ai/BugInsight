#!/usr/bin/env python3
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
import sys
from datetime import datetime
from typing import Dict, List, Optional

# 添加脚本目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from analyzer import BugAnalyzer
from code_search import CodeSearcher
from config import print_config_check


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
        output_file = args.output or f"/tmp/bug_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
        result = {"files": [], "logs": [], "log_analysis": {"errors": [], "warnings": []}}

        for root, dirs, files in os.walk(dir_path):
            for filename in files:
                if filename.endswith(('.log', '.txt', '.cat')) or 'log' in filename.lower():
                    filepath = os.path.join(root, filename)
                    try:
                        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read(50000)  # 限制大小
                        analysis = self.analyzer.analyze_log(content)
                        result["log_analysis"]["errors"].extend(analysis.get("errors", []))
                        result["log_analysis"]["warnings"].extend(analysis.get("warnings", []))
                        result["logs"].append(filename)
                    except:
                        pass

        result["log_analysis"]["error_count"] = len(result["log_analysis"]["errors"])
        result["log_analysis"]["warning_count"] = len(result["log_analysis"]["warnings"])
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

        # 搜索本地缺陷知识库
        bugs = self.analyzer.find_similar_bugs(query, limit=limit)

        if bugs:
            print(f"\n找到 {len(bugs)} 个相似缺陷:\n")
            for i, bug in enumerate(bugs, 1):
                print(f"{i}. [{bug.get('id', '')}] {bug.get('title', '')}")
                print(f"   状态: {bug.get('status', 'UNKNOWN')} | 相似度: {bug.get('score', 0):.2f}")
                if bug.get('comments'):
                    print(f"   评论: {bug['comments'][0][:60]}...")
                print()
        else:
            print("未找到相似缺陷")

        # 代码搜索
        if args.code:
            print("\n" + "=" * 50)
            print("📚 代码搜索结果:\n")
            code_results = self.searcher.search_code(
                query,
                repos=args.repos or ["dove", "framework"],
                max_results=10
            )
            if code_results:
                for r in code_results[:5]:
                    print(f"[{r['repo']}] {r['file']}")
                    for m in r['matches'][:2]:
                        print(f"  L{m['line_num']}: {m['content'][:60]}")
                    print()
            else:
                print("未找到相关代码")

        return 0

    def cmd_feishu(self, args):
        """获取飞书缺陷信息"""
        # 这里调用飞书 API 获取缺陷详情
        # 需要实现 feishu_bitable_get_record 等调用
        print("📋 飞书缺陷分析功能")
        print("请提供飞书缺陷链接或 ID")
        return 0


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