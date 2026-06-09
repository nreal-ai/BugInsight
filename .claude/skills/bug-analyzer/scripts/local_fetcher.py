#!/usr/bin/env python3
"""
本地文件获取器 - 从本地文件系统获取日志和dump文件
"""

import os
import re
from pathlib import Path
from typing import List, Dict


def scan_directory(dir_path: str, extensions: List[str] = None) -> List[str]:
    """
    扫描目录下的文件
    
    Args:
        dir_path: 目录路径
        extensions: 文件扩展名过滤，如 ['.log', '.txt']
    
    Returns:
        list: 文件路径列表
    """
    files = []
    for root, dirs, filenames in os.walk(dir_path):
        for f in filenames:
            if extensions is None or any(f.endswith(ext) for ext in extensions):
                full_path = os.path.join(root, f)
                files.append(full_path)
    return files


def read_log_file(file_path: str, max_lines: int = 1000) -> str:
    """
    读取日志文件
    
    Args:
        file_path: 文件路径
        max_lines: 最大行数
    
    Returns:
        str: 日志内容
    """
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = []
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                lines.append(line)
            return ''.join(lines)
    except Exception as e:
        return f"Error reading {file_path}: {e}"


def find_errors(log_content: str) -> List[Dict]:
    """
    从日志中查找错误
    
    Args:
        log_content: 日志内容
    
    Returns:
        list: 错误列表
    """
    errors = []
    
    # 错误模式
    patterns = [
        r'(?i)(ERROR|FATAL|CRASH|SEVERE):\s*(.+)',
        r'(?i)Exception.*:\s*(.+)',
        r'(?i)Traceback.*',
        r'Segmentation fault',
        r'core dump',
        r'(?i)failed:\s*(.+)',
    ]
    
    lines = log_content.split('\n')
    for i, line in enumerate(lines):
        for pattern in patterns:
            if re.search(pattern, line):
                # 获取上下文（前后5行）
                start = max(0, i - 5)
                end = min(len(lines), i + 6)
                context = '\n'.join(lines[start:end])
                
                errors.append({
                    'line_num': i + 1,
                    'content': line,
                    'context': context,
                    'pattern': pattern
                })
                break
    
    return errors


def analyze_coredump(dump_file: str) -> Dict:
    """
    分析 coredump 文件（需要 gdb）
    
    Args:
        dump_file: coredump 文件路径
    
    Returns:
        dict: 分析结果
    """
    print(f"Analyzing coredump: {dump_file}")
    
    # 使用 gdb 分析
    # gdb -batch -ex "bt" -ex "thread apply all bt" <exe> <core>
    
    # TODO: 实际实现
    
    return {
        "file": dump_file,
        "has_dump": os.path.exists(dump_file),
        "size": os.path.getsize(dump_file) if os.path.exists(dump_file) else 0
    }


if __name__ == "__main__":
    # 测试
    files = scan_directory("/tmp/logs/", [".log", ".txt", ".json"])
    print("Files found:", files)