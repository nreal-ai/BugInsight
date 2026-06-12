#!/usr/bin/env python3
"""
t7_cli - CLI 命令测试
测试 bug_analyzer.py 的命令行接口
"""
import subprocess
import sys
import os
import json
import tempfile
import shutil
from pathlib import Path

# 添加脚本路径
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

def run_cli(args, expect_rc=0):
    """运行 bug_analyzer.py CLI"""
    cmd = [sys.executable, str(SCRIPT_DIR / "bug_analyzer.py")] + args
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return r

def test_help():
    """测试 --help 输出"""
    print("\n=== CLI: help ===")
    r = run_cli(["--help"])
    check("--help 返回 0", r.returncode == 0, f"rc={r.returncode}")
    check("显示 analyze 子命令", "analyze" in r.stdout)
    check("显示 search 子命令", "search" in r.stdout)
    check("显示 feishu 子命令", "feishu" in r.stdout)
    check("显示 report 子命令", "report" in r.stdout)

def test_no_command():
    """测试无子命令时的行为"""
    print("\n=== CLI: no command ===")
    r = run_cli([])
    check("无命令返回非0", r.returncode == 1, f"rc={r.returncode}")
    check("显示帮助信息", "usage" in r.stdout.lower() or "示例" in r.stdout)

def test_search_local():
    """测试 search 子命令（本地搜索）"""
    print("\n=== CLI: search ===")
    r = run_cli(["search", "音效"])
    check("search 返回 0", r.returncode == 0, f"rc={r.returncode}, stderr={r.stderr[:200]}")
    check("输出包含结果", "音效" in r.stdout or "找到" in r.stdout or "相似" in r.stdout, 
         f"stdout={r.stdout[:300]}")

def test_search_chinese():
    """测试中文关键词搜索"""
    print("\n=== CLI: search Chinese ===")
    r = run_cli(["search", "黑屏"])
    check("黑屏搜索返回 0", r.returncode == 0, f"rc={r.returncode}")
    check("有输出结果", len(r.stdout.strip()) > 0)

def test_search_limit():
    """测试 search --limit 参数"""
    print("\n=== CLI: search --limit ===")
    r = run_cli(["search", "USB", "--limit", "3"])
    check("--limit 返回 0", r.returncode == 0, f"rc={r.returncode}")

def test_search_with_code():
    """测试 search --code 参数（代码搜索）"""
    print("\n=== CLI: search --code ===")
    r = run_cli(["search", "NullPointerException", "--code"])
    # --code 可能因为没有 repo 配置而跳过
    check("--code 不崩溃", r.returncode in (0, 1), f"rc={r.returncode}")

def test_report_md():
    """测试 report 子命令生成 Markdown 报告"""
    print("\n=== CLI: report ===")
    # 创建临时分析结果文件
    sample_result = {
        "bug_info": {"id": "test_001", "title": "测试缺陷"},
        "log_analysis": {
            "error_count": 3,
            "warning_count": 5,
            "errors": [{"type": "Exception", "message": "Test error"}],
            "warnings": [],
        },
        "similar_bugs": [],
        "root_cause": "测试根因",
        "confidence": {"score": 0.75},
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(sample_result, f, ensure_ascii=False)
        tmpfile = f.name
    
    try:
        outfile = tmpfile.replace('.json', '.md')
        r = run_cli(["report", tmpfile, "-o", outfile])
        check("report 返回 0", r.returncode == 0, f"rc={r.returncode}, stderr={r.stderr[:200]}")
        check("输出文件存在", os.path.exists(outfile))
        if os.path.exists(outfile):
            content = open(outfile, 'r', encoding='utf-8').read()
            check("报告包含标题", "测试缺陷" in content or "test_001" in content)
            check("报告是 Markdown 格式", "#" in content)
    finally:
        for p in [tmpfile, outfile]:
            if os.path.exists(p):
                os.unlink(p)

def test_report_feishu():
    """测试 report --format feishu"""
    print("\n=== CLI: report feishu ===")
    sample_result = {
        "bug_info": {"id": "test_002", "title": "飞书报告测试"},
        "log_analysis": {"error_count": 1, "warning_count": 0, "errors": [], "warnings": []},
        "similar_bugs": [],
        "root_cause": "测试",
        "confidence": {"score": 0.5},
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(sample_result, f, ensure_ascii=False)
        tmpfile = f.name
    
    try:
        outfile = tmpfile.replace('.json', '.txt')
        r = run_cli(["report", tmpfile, "-f", "feishu", "-o", outfile])
        check("feishu 报告返回 0", r.returncode == 0, f"rc={r.returncode}")
        check("输出文件存在", os.path.exists(outfile))
    finally:
        for p in [tmpfile, outfile]:
            if os.path.exists(p):
                os.unlink(p)

def test_report_nonexistent():
    """测试 report 不存在的文件"""
    print("\n=== CLI: report nonexistent ===")
    r = run_cli(["report", "/tmp/nonexistent_bug_analysis_12345.json"])
    check("返回非0", r.returncode != 0, f"rc={r.returncode}")
    check("显示错误信息", "不存在" in r.stdout or "错误" in r.stdout or "Error" in r.stderr)

def test_feishu_by_id():
    """测试 feishu 子命令通过 ID 查找"""
    print("\n=== CLI: feishu by ID ===")
    # 使用一个已知的 bug ID
    r = run_cli(["feishu", "6967734869"])
    check("feishu ID 返回 0", r.returncode == 0, f"rc={r.returncode}, stderr={r.stderr[:300]}")
    check("输出包含 bug 信息", 
         "6967734869" in r.stdout or "音效" in r.stdout or "飞书" in r.stdout,
         f"stdout={r.stdout[:300]}")

def test_feishu_by_url():
    """测试 feishu 子命令通过 URL 解析"""
    print("\n=== CLI: feishu by URL ===")
    fake_url = "https://project.feishu.cn/xreal_project/issue/detail/6967734869"
    r = run_cli(["feishu", fake_url])
    check("feishu URL 不崩溃", r.returncode in (0, 1), f"rc={r.returncode}")
    # 应该能解析出 ID 6967734869
    check("能解析 URL 中的 ID", "6967734869" in r.stdout or "音效" in r.stdout or "飞书" in r.stdout or 
         "未找到" in r.stdout or "找不到" in r.stdout, f"stdout={r.stdout[:200]}")

def test_feishu_invalid_id():
    """测试 feishu 无效 ID"""
    print("\n=== CLI: feishu invalid ID ===")
    r = run_cli(["feishu", "0"])
    check("无效 ID 不崩溃", r.returncode in (0, 1), f"rc={r.returncode}")

def test_analyze_log_text():
    """测试 analyze 子命令分析日志文本"""
    print("\n=== CLI: analyze log ===")
    # 创建临时日志文件
    log_content = """E/AndroidRuntime: FATAL EXCEPTION: main
Process: com.example.app, PID: 12345
java.lang.NullPointerException: Attempt to invoke virtual method on null object reference
    at com.example.app.MainActivity.onCreate(MainActivity.java:42)
    at android.app.Activity.performCreate(Activity.java:8000)
W/System.err: java.io.IOException: File not found
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
        f.write(log_content)
        tmpfile = f.name
    
    try:
        r = run_cli(["analyze", tmpfile])
        check("analyze log 返回 0", r.returncode == 0, f"rc={r.returncode}, stderr={r.stderr[:200]}")
        check("输出包含错误信息", "NullPointerException" in r.stdout or "error" in r.stdout.lower() or "错误" in r.stdout)
    finally:
        os.unlink(tmpfile)

def test_analyze_with_report():
    """测试 analyze --report 生成报告"""
    print("\n=== CLI: analyze --report ===")
    log_content = """E/Crash: Fatal signal 11 (SIGSEGV), code 1
pid: 12345, tid: 12346, name: main
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False, encoding='utf-8') as f:
        f.write(log_content)
        tmpfile = f.name
    
    try:
        r = run_cli(["analyze", tmpfile, "--report"])
        check("analyze --report 返回 0", r.returncode == 0, f"rc={r.returncode}")
        check("生成报告文件", "报告" in r.stdout or "report" in r.stdout.lower() or ".md" in r.stdout)
    finally:
        os.unlink(tmpfile)

def test_analyze_nonexistent():
    """测试 analyze 不存在的文件"""
    print("\n=== CLI: analyze nonexistent ===")
    r = run_cli(["analyze", "/tmp/nonexistent_log_12345.log"])
    check("返回非0", r.returncode != 0, f"rc={r.returncode}")

def main():
    global PASS, FAIL, SKIPPED
    print("=" * 60)
    print("t7_cli - CLI 命令测试")
    print("=" * 60)
    
    test_help()
    test_no_command()
    test_search_local()
    test_search_chinese()
    test_search_limit()
    test_search_with_code()
    test_report_md()
    test_report_feishu()
    test_report_nonexistent()
    test_feishu_by_id()
    test_feishu_by_url()
    test_feishu_invalid_id()
    test_analyze_log_text()
    test_analyze_with_report()
    test_analyze_nonexistent()
    
    total = PASS + FAIL + SKIPPED
    print(f"\n{'=' * 60}")
    print(f"结果: {PASS}/{total} 通过, {FAIL} 失败, {SKIPPED} 跳过")
    print("=" * 60)
    return 1 if FAIL > 0 else 0

if __name__ == "__main__":
    sys.exit(main())
