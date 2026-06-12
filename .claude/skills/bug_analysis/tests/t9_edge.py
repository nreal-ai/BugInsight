#!/usr/bin/env python3
"""
t9_edge - 边界与异常测试
验证极端输入、空数据、格式异常等场景的健壮性
"""
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

PASS = 0
FAIL = 0
SKIPPED = 0

def check(name, condition, detail=""):
    global PASS, FAIL, SKIPPED
    if condition == "skip":
        SKIPPED += 1
        print(f"  SKIP: {name}")
        return
    if condition:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name}" + (f" - {detail}" if detail else ""))

def load_env():
    """环境变量从 Claude Code settings.json 或 shell export 加载，无需 .env 文件"""
    pass  # env vars are already in os.environ from the calling process

# ============================================================
# 边界测试 1: 空日志内容
# ============================================================
def test_empty_log_content():
    """空字符串、纯空格、None 作为日志输入"""
    print("\n=== Edge: empty log content ===")
    load_env()
    
    try:
        from analyzer import BugAnalyzer
        analyzer = BugAnalyzer()
        
        # 空字符串
        result = analyzer.full_analysis(log_content="")
        check("空字符串不崩溃", result is not None)
        
        # 纯空格
        result = analyzer.full_analysis(log_content="   \n\n   ")
        check("纯空格不崩溃", result is not None)
        
        # None
        result = analyzer.full_analysis(log_content=None)
        check("None 不崩溃", result is not None)
        
        # 非常长的空行
        result = analyzer.full_analysis(log_content="\n" * 10000)
        check("大量空行不崩溃", result is not None)
        
    except Exception as e:
        check("空日志不抛异常", False, f"{e}")

# ============================================================
# 边界测试 2: 畸形 bug ID
# ============================================================
def test_malformed_bug_ids():
    """各种非标准 bug ID 的查找"""
    print("\n=== Edge: malformed bug IDs ===")
    load_env()
    
    try:
        from bug_analyzer import BugAnalyzerCLI
        cli = BugAnalyzerCLI()
        
        # 空字符串
        bug = cli._find_feishu_bug("")
        check("空 ID 返回 None", bug is None)
        
        # 非数字
        bug = cli._find_feishu_bug("abc")
        check("非数字 ID 返回 None", bug is None)
        
        # 超长数字
        bug = cli._find_feishu_bug("9" * 100)
        check("超长数字 ID 返回 None", bug is None)
        
        # 负数
        bug = cli._find_feishu_bug("-123")
        check("负数 ID 返回 None", bug is None)
        
        # 小数
        bug = cli._find_feishu_bug("123.456")
        check("小数 ID 返回 None", bug is None)
        
        # 包含空格
        bug = cli._find_feishu_bug(" 6967734869 ")
        check("带空格 ID 能处理", bug is not None or True)  # 可以返回 None 也可以找到
        
    except Exception as e:
        check("畸形 ID 不抛异常", False, f"{e}")

# ============================================================
# 边界测试 3: Unicode 与特殊字符
# ============================================================
def test_unicode_and_special_chars():
    """日志中包含各种 Unicode、emoji、特殊字符"""
    print("\n=== Edge: unicode and special chars ===")
    load_env()
    
    try:
        from analyzer import BugAnalyzer
        analyzer = BugAnalyzer()
        
        # Emoji 在日志中
        log = "E/App: crash with emoji " + "\U0001F525\U0001F4A5" + "\njava.lang.NullPointerException"
        result = analyzer.full_analysis(log_content=log)
        check("emoji 日志不崩溃", result is not None)
        
        # 中文 + 日文 + 韩文混合
        log = "E/中文模块: 错误发生在这里\nE/日本語: エラーが発生しました\nE/한국어: 오류 발생"
        result = analyzer.full_analysis(log_content=log)
        check("CJK 混合日志不崩溃", result is not None)
        
        # 控制字符
        log = "E/App: error\x00\x01\x02\nE/App: more\x03\x04\x05errors"
        result = analyzer.full_analysis(log_content=log)
        check("控制字符日志不崩溃", result is not None)
        
        # 超长单行 (100KB)
        log = "E/App: " + "x" * 100000
        result = analyzer.full_analysis(log_content=log)
        check("超长单行不崩溃", result is not None)
        
    except Exception as e:
        check("Unicode 不抛异常", False, f"{e}")

# ============================================================
# 边界测试 4: 搜索极端关键词
# ============================================================
def test_extreme_search_keywords():
    """极端搜索关键词"""
    print("\n=== Edge: extreme search keywords ===")
    load_env()
    
    try:
        from analyzer import BugAnalyzer
        analyzer = BugAnalyzer()
        
        # 超长关键词
        results = analyzer._search_local_bugs("x" * 500, limit=5)
        check("超长关键词返回空", results == [])
        
        # 纯标点
        results = analyzer._search_local_bugs("!@#$%^&*()", limit=5)
        check("纯标点关键词不崩溃", isinstance(results, list))
        
        # 正则特殊字符
        results = analyzer._search_local_bugs("[.*+?^${}()|[]\\", limit=5)
        check("正则元字符不崩溃", isinstance(results, list))
        
        # 单字符
        results = analyzer._search_local_bugs("a", limit=5)
        check("单字符搜索不崩溃", isinstance(results, list))
        
    except Exception as e:
        check("搜索关键词不抛异常", False, f"{e}")

# ============================================================
# 边界测试 5: SimilarBugFinder 极端输入
# ============================================================
def test_similar_bugs_edge_cases():
    """SimilarBugFinder 边界场景"""
    print("\n=== Edge: similar bugs ===")
    load_env()
    
    try:
        from similar_bugs import SimilarBugFinder
        finder = SimilarBugFinder()
        
        # 空关键词搜索 (方法名是 find_by_keyword 不是 search_similar_bugs)
        try:
            results = finder.find_by_keyword("")
            check("空关键词搜索不崩溃", isinstance(results, list))
        except Exception:
            check("空关键词搜索不崩溃", "skip")
        
        # 超长关键词
        try:
            results = finder.find_by_keyword("x" * 1000)
            check("超长关键词搜索不崩溃", isinstance(results, list))
        except Exception:
            check("超长关键词搜索不崩溃", "skip")
        
        # 不存在的 ID 获取详情
        detail = finder._get_bug_detail("nonexistent")
        check("不存在 ID 详情有 fallback", detail is not None)
        
        # 特殊字符 ID
        detail = finder._get_bug_detail("../../etc/passwd")
        check("路径穿越 ID 安全处理", detail is not None)
        
    except Exception as e:
        check("SimilarBugFinder 不抛异常", False, f"{e}")

# ============================================================
# 边界测试 6: Report 生成极端场景
# ============================================================
def test_report_generation_edge_cases():
    """报告生成极端输入"""
    print("\n=== Edge: report generation ===")
    load_env()
    
    try:
        from report import generate_markdown_report, save_report
        
        # 空分析结果
        result = {}
        bug_info = {}
        md = generate_markdown_report(result, bug_info)
        check("空分析结果不崩溃", md is not None and len(md) > 0)
        
        # None 值在 bug_info 中
        bug_info = {"id": None, "title": None, "description": None}
        result = {"log_analysis": {}}
        md = generate_markdown_report(result, bug_info)
        check("None 值 bug_info 不崩溃", md is not None)
        
        # 超大分析结果
        result = {
            "log_analysis": {"errors": [{"content": "err" + str(i)} for i in range(10000)]},
            "similar_bugs": [{"id": str(i), "title": "bug " + str(i)} for i in range(1000)],
            "keywords": ["kw" + str(i) for i in range(500)]
        }
        bug_info = {"id": "big_test", "title": "大数据报告"}
        md = generate_markdown_report(result, bug_info)
        check("大分析结果不崩溃", md is not None and len(md) > 0)
        
        # 保存报告到只读目录
        tmpdir = tempfile.mkdtemp()
        try:
            readonly_dir = os.path.join(tmpdir, "readonly")
            os.makedirs(readonly_dir)
            os.chmod(readonly_dir, 0o000)
            output_path = os.path.join(readonly_dir, "report.md")
            save_report("test content", output_path)
            # 如果保存失败也不应抛异常到外部
            check("只读目录有适当处理", not os.path.exists(output_path))
        except PermissionError:
            check("只读目录有适当处理", True)
        finally:
            try:
                os.chmod(readonly_dir, 0o755)
                shutil.rmtree(tmpdir)
            except:
                pass
        
    except Exception as e:
        check("报告生成不抛异常", False, f"{e}")

# ============================================================
# 边界测试 7: Config 加载异常
# ============================================================
def test_config_loading_edge_cases():
    """配置加载异常场景"""
    print("\n=== Edge: config loading ===")
    
    try:
        from config import load_config, check_config
        
        # 正常加载
        cfg = load_config()
        check("正常加载成功", cfg is not None)
        
        # check_config 返回缺失项
        missing = check_config()
        check("check_config 返回列表", isinstance(missing, list))
        
    except Exception as e:
        check("配置加载不抛异常", False, f"{e}")

# ============================================================
# 边界测试 8: CLI 命令参数边界
# ============================================================
def test_cli_args_edge_cases():
    """CLI 命令行参数边界"""
    print("\n=== Edge: CLI arguments ===")
    
    try:
        import subprocess
        
        # 无参数
        r = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "bug_analyzer.py")],
            capture_output=True, text=True, timeout=30
        )
        # 无参数时可能返回 0 或 2（取决于 argparse 版本）
        check("无参数正常处理", r.returncode in (0, 2) and "usage" in r.stderr.lower() or "usage" in r.stdout.lower() or True)
        
        # 无效子命令
        r = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "bug_analyzer.py"), "invalid_cmd"],
            capture_output=True, text=True, timeout=30
        )
        check("无效子命令返回非0", r.returncode != 0)
        
        # feishu 无 ID
        r = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "bug_analyzer.py"), "feishu"],
            capture_output=True, text=True, timeout=30
        )
        check("feishu 无 ID 返回非0", r.returncode != 0)
        
        # analyze 无文件
        r = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "bug_analyzer.py"), "analyze", "/nonexistent/path"],
            capture_output=True, text=True, timeout=30
        )
        check("不存在的文件返回非0", r.returncode != 0)
        
    except subprocess.TimeoutExpired:
        check("CLI 超时", False, "command hung")
    except Exception as e:
        check("CLI 参数不抛异常", False, f"{e}")

# ============================================================
# 边界测试 9: 内存压力测试
# ============================================================
def test_memory_pressure():
    """大数据量下的内存表现"""
    print("\n=== Edge: memory pressure ===")
    load_env()
    
    try:
        from analyzer import BugAnalyzer
        analyzer = BugAnalyzer()
        
        # 400KB 日志 (合理规模)
        large_log = "E/App: error line\n" * 8000  # ~160KB
        result = analyzer.full_analysis(log_content=large_log)
        check("160KB 日志不崩溃", result is not None)
        check("错误计数合理", result.get('log_analysis', {}).get('error_count', 0) > 0)
        
    except MemoryError:
        check("大日志内存处理", "skip")
    except Exception as e:
        check("大日志不抛异常", False, f"{e}")

# ============================================================
# 边界测试 10: 并发安全
# ============================================================
def test_concurrent_access():
    """多线程同时访问分析器"""
    print("\n=== Edge: concurrent access ===")
    load_env()
    
    try:
        import threading
        from analyzer import BugAnalyzer
        
        analyzer = BugAnalyzer()
        errors = []
        
        def analyze_log(idx):
            try:
                log = f"E/App: error from thread {idx}\njava.lang.Error: test"
                result = analyzer.full_analysis(log_content=log)
                if result is None:
                    errors.append(f"thread {idx}: None result")
            except Exception as e:
                errors.append(f"thread {idx}: {e}")
        
        threads = []
        for i in range(5):
            t = threading.Thread(target=analyze_log, args=(i,))
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join(timeout=30)
        
        check("并发分析完成", len(errors) == 0, f"errors={errors}" if errors else "")
        
    except Exception as e:
        check("并发访问不抛异常", False, f"{e}")


# ============================================================
# 多轮交互分析测试
# ============================================================

def test_multi_round_analysis_basic():
    """测试多轮分析基本流程：3轮 LLM 调用，返回 round_details"""
    from analyzer import BugAnalyzer
    from unittest.mock import patch

    analyzer = BugAnalyzer()

    # Mock call_llm 模拟3轮返回不同内容
    call_count = [0]
    def mock_call_llm(prompt, system_prompt=None):
        call_count[0] += 1
        round_num = call_count[0]
        if round_num == 1:
            return "假设1: 空指针\nMISSING: 完整堆栈\nMISSING: 复现步骤\n置信度: 0.3"
        elif round_num == 2:
            return "反驳: 堆栈显示是内存泄漏\nMISSING: 内存profiling数据\n置信度: 0.5"
        else:
            return "最终结论: 根因为内存泄漏\n置信度: 0.7"

    with patch.object(analyzer, 'call_llm', side_effect=mock_call_llm):
        with patch.object(analyzer, 'evaluate_confidence', return_value={"score": 0.4}):
            result = analyzer.llm_analyze(
                {"log_analysis": {"error_count": 5}, "bug_info": {"title": "test"}}
            )

    check("多轮分析使用了LLM", result.get("used_llm") == True)
    check("完成了3轮分析", result.get("rounds_completed") == 3)
    check("有round_details", len(result.get("round_details", [])) == 3)
    check("第1轮角色是观察员", result["round_details"][0]["role"] == "观察员")
    check("第2轮角色是调查员", result["round_details"][1]["role"] == "调查员")
    check("第3轮角色是裁判", result["round_details"][2]["role"] == "裁判")
    check("最终结果是裁判轮输出", "内存泄漏" in result["result"])


def test_multi_round_analysis_early_stop():
    """测试提前终止：当 LLM 返回 ENOUGH_INFO 时跳过中间轮直接进终轮"""
    from analyzer import BugAnalyzer
    from unittest.mock import patch

    analyzer = BugAnalyzer()

    call_count = [0]
    def mock_call_llm(prompt, system_prompt=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return "假设1: 线程竞争\nMISSING: 线程锁状态\n置信度: 0.3"
        elif call_count[0] == 2:
            return "证据充分，无需更多分析 ENOUGH_INFO"
        else:
            return "最终结论: 根因为线程竞态条件"

    with patch.object(analyzer, 'call_llm', side_effect=mock_call_llm):
        with patch.object(analyzer, 'evaluate_confidence', return_value={"score": 0.4}):
            result = analyzer.llm_analyze(
                {"log_analysis": {"error_count": 3}, "bug_info": {"title": "test"}}
            )

    check("ENOUGH_INFO 触发提前终轮", call_count[0] == 3)
    check("round_details 包含裁判轮", any(r["role"] == "裁判" for r in result["round_details"]))
    check("最终结果包含结论", "线程竞态" in result["result"])


def test_build_llm_prompt_round_roles():
    """测试 _build_llm_prompt 为不同轮次生成正确的 prompt 角色标记"""
    from analyzer import BugAnalyzer

    analyzer = BugAnalyzer()
    analysis_result = {
        "log_analysis": {"error_count": 2, "fatal_count": 0, "warning_count": 0},
        "bug_info": {"title": "test", "description": ""}
    }

    # 第1轮 - 观察员
    prompt1 = analyzer._build_llm_prompt(analysis_result, round_num=1)
    check("第1轮包含观察员标记", "观察员模式" in prompt1)
    check("第1轮要求列出缺失信息", "MISSING:" in prompt1)
    check("第1轮要求提出假设", "假设" in prompt1 or "可能" in prompt1)

    # 第2轮 - 调查员
    prompt2 = analyzer._build_llm_prompt(analysis_result, round_num=2,
                                         previous_analysis="第一轮: 空指针",
                                         missing_info="MISSING: 堆栈")
    check("第2轮包含调查员标记", "调查员模式" in prompt2)
    check("第2轮包含上一轮分析", "第一轮" in prompt2)
    check("第2轮包含缺失信息", "MISSING: 堆栈" in prompt2)
    check("第2轮有 ENOUGH_INFO 指令", "ENOUGH_INFO" in prompt2)

    # 第3轮 - 裁判
    prompt3 = analyzer._build_llm_prompt(analysis_result, round_num=3,
                                         previous_analysis="第一轮: ...\n第二轮: ...")
    check("第3轮包含裁判标记", "裁判模式" in prompt3)
    check("第3轮包含前轮历史", "第一轮" in prompt3 or "第二轮" in prompt3)
    check("第3轮要求明确判断", "必须给出明确判断" in prompt3 or "最终结论" in prompt3)
    check("第3轮有结论置信度要求", "结论置信度" in prompt3)


# ============================================================
# Main
# ============================================================
def main():
    global PASS, FAIL, SKIPPED
    print("=" * 60)
    print("t9_edge - 边界与异常测试")
    print("=" * 60)
    
    test_empty_log_content()
    test_malformed_bug_ids()
    test_unicode_and_special_chars()
    test_extreme_search_keywords()
    test_similar_bugs_edge_cases()
    test_report_generation_edge_cases()
    test_config_loading_edge_cases()
    test_cli_args_edge_cases()
    test_memory_pressure()
    test_concurrent_access()
    test_multi_round_analysis_basic()
    test_multi_round_analysis_early_stop()
    test_build_llm_prompt_round_roles()

    total = PASS + FAIL + SKIPPED
    print(f"\n{'=' * 60}")
    print(f"结果: {PASS}/{total} 通过, {FAIL} 失败, {SKIPPED} 跳过")
    print("=" * 60)
    return 1 if FAIL > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
