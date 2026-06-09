#!/usr/bin/env python3
"""
Bug Analyzer - 缺陷分析工具
支持:ZIP文件分析、日志解析、相似缺陷搜索、LLM增强分析、置信度评估

增强特性 (2026-04-13):
- 日志解析规则增强:支持错误码、异常类型、堆栈跟踪、崩溃信号
- 根因推断规则库:基于规则的根因推断
- 置信度评估维度扩展:错误码明确性、时间集中度、堆栈完整性、上下文相关性
- 分类规则:自动按功能分类缺陷
- 输出报告增强:TL;DR摘要、关键错误码提取、可操作建议

增强特性 (2026-04-13 改进):
- 多格式日志解析:logcat、syslog、dmesg、XR 设备日志
- 结构化信息提取:错误码、时间戳、线程ID、堆栈跟踪、包名
- 崩溃签名提取:SIGSEGV、SIGABRT、SIGKILL 等崩溃特征
- LLM 增强分析:结构化 prompt + 代码仓库上下文查询
- 错误码关联:自动关联 NReal 错误码定义
- 分析报告增强:根因分类、影响范围、复现概率、建议优先级
"""

import json
import os
import zipfile
import re
import requests
import shutil
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime
import subprocess
import sys

# 添加代码搜索模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from code_search import CodeSearcher
from config import get_llm_config, get_code_repos, get_analysis_config, load_config, CONFIG_DIR


class BugAnalyzer:
    """Bug 分析器"""

    # 初始化时从配置加载
    _config_cache = None

    def __init__(self):
        """初始化,从配置加载参数"""
        # 加载配置
        llm_cfg = get_llm_config()
        repos_cfg = get_code_repos()
        analysis_cfg = get_analysis_config()

        self.LLM_API_BASE = llm_cfg.get("api_base", "https://litellm.xreal.work/v1")
        self.LLM_API_KEY = llm_cfg.get("api_key", "")
        self.LLM_MODEL = llm_cfg.get("model", "qwen3-coder-plus")

        # NReal 代码仓库路径
        self.CODE_REPOS = repos_cfg

        # 代码搜索器（延迟初始化）
        self.code_searcher = None

        # 分析配置
        self.SIMILAR_BUGS_LIMIT = analysis_cfg.get("similar_bugs_limit", 10)
        self.CODE_SEARCH_LIMIT = analysis_cfg.get("code_search_limit", 20)
        self.LLM_TIMEOUT = analysis_cfg.get("llm_timeout", 90)
        self.CONFIDENCE_THRESHOLD = analysis_cfg.get("confidence_threshold", 0.7)

    # ========== 增强规则库 ==========

    # 1. 扩展错误模式
    ERROR_PATTERNS = {
        "segfault": re.compile(r"(Segmentation fault|SIGSEGV|0x[0-9a-f]{8}|signal 11)", re.I),
        "anr": re.compile(r"(ANR|Application Not Responding|watchdog|主线程阻塞)", re.I),
        "oom": re.compile(r"(OutOfMemory|OOM|memory.*exhausted|内存溢出|heap dump)", re.I),
        "timeout": re.compile(r"(timeout|timed?out|TIMEOUT|超时)", re.I),
        "connection": re.compile(r"(connection (refused|reset|failed)|ECONNREFUSED|ECONNRESET|连接失败)", re.I),
        "null_pointer": re.compile(r"(NullPointerException|NullReference|NPE|null pointer|空指针)", re.I),
        "assertion": re.compile(r"(assertion failed|ASSERT|assert\()", re.I),
        "crash": re.compile(r"(crash|fatal|abort|SIGABRT|SIGKILL|崩溃)", re.I),
        "permission": re.compile(r"(permission denied|access denied|EACCES|EPERM|权限)", re.I),
        "file_not_found": re.compile(r"(file not found|ENOENT|no such file|文件不存在)", re.I),
    }

    # 2. 日志格式解析模式
    LOG_PATTERNS = {
        # Android logcat: "12-25 10:30:15.123  1234  5678 E/Tag: message"
        "logcat": re.compile(
            r"^(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3})\s+"  # timestamp
            r"(\d+)\s+(\d+)\s+"  # pid, tid
            r"([VDIWEFA])\s*/([^:]+):\s*(.*)$"  # level/tag/message
        ),
        # Syslog: "Dec 25 10:30:15 hostname process[pid]: message"
        "syslog": re.compile(
            r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+"  # timestamp
            r"(\S+)\s+(\S+?)(?:\[(\d+)\])?:\s*(.*)$"  # host/process/pid/message
        ),
        # XR 设备日志: "[2024-12-25 10:30:15.123] [E] [dove/display] message"
        "xr_log": re.compile(
            r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*"
            r"\[([VDIWEFA])\]\s*"
            r"(?:\[([^\]]+)\])?\s*"
            r"(.*)$"
        ),
        # 简单格式: "[2026-04-14 10:00:00] ERROR: message" 或 "[2026-04-14 10:00:00] [E] message"
        "simple": re.compile(
            r"^\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]\s*"
            r"(?:\[([VDIWEFA])\]|([A-Za-z]+):)\s*"  # [E] 或 ERROR:
            r"(.*)$"
        ),
        # 通用时间戳格式
        "timestamp": re.compile(
            r"(\d{4}[-/]\d{2}[-/]\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
        ),
    }

    # 3. 错误码模式 (NReal 错误码格式: ERR_xxx, DOVE_ERR_xxx)
    ERROR_CODE_PATTERN = re.compile(
        r'\b(ERR_[A-Z_]+|DOVE_ERR_[A-Z_]+|FERR_[A-Z_]+|LEOPARD_ERR_[A-Z_]+)'
        r'(?:\s*[:=]\s*(-?\d+))?\b'
    )

    # 4. 堆栈跟踪模式
    STACK_TRACE_PATTERNS = [
        re.compile(r'^\s+at\s+(\S+)\((.+)\)$', re.MULTILINE),  # Java/Kotlin
        re.compile(r'^\s+#\d+\s+\S+\s+in\s+\S+\s+from\s+\S+$', re.MULTILINE),  # Native
        re.compile(r'^\s+(\S+)\s+\S+\s+\[0x[0-9a-f]+\]$', re.MULTILINE),  # addr2line
    ]

    # 5. 根因推断规则库
    ROOT_CAUSE_RULES = [
        {"pattern": re.compile(r"USB.*disconnect|usb.*fail|usb.*error|USB.*异常", re.I),
         "root_cause": "USB连接不稳定", "suggestion": "检查USB线缆、更换端口、确认驱动安装", "priority": "high"},
        {"pattern": re.compile(r"display.*black|screen.*black|黑屏|无显示|显示.*异常", re.I),
         "root_cause": "显示模块异常", "suggestion": "检查显示驱动、重启显示服务、确认显示屏连接", "priority": "high"},
        {"pattern": re.compile(r"gesture|touch.*fail|触控.*异常|手势.*失灵|触控.*无响应", re.I),
         "root_cause": "触控/手势模块故障", "suggestion": "校准触控、重启手势服务、检查传感器", "priority": "high"},
        {"pattern": re.compile(r"battery|power.*low|充电.*异常|电源.*问题|电量.*低", re.I),
         "root_cause": "电源管理问题", "suggestion": "检查电池健康、充电器功率、电源管理配置", "priority": "medium"},
        {"pattern": re.compile(r"audio|sound.*fail|音频.*异常|扬声器.*无声|声音.*异常", re.I),
         "root_cause": "音频模块故障", "suggestion": "检查音频驱动、确认扬声器连接、重置音频服务", "priority": "medium"},
        {"pattern": re.compile(r"wifi|network.*fail|连接.*失败|网络.*异常|WiFi.*断开", re.I),
         "root_cause": "网络连接问题", "suggestion": "检查WiFi模块、重启网络服务、确认信号强度", "priority": "medium"},
        {"pattern": re.compile(r"bluetooth|BT.*fail|蓝牙.*异常|配对.*失败", re.I),
         "root_cause": "蓝牙连接问题", "suggestion": "检查蓝牙驱动、重新配对设备", "priority": "medium"},
        {"pattern": re.compile(r"camera.*fail|摄像头.*异常|相机.*黑屏|拍摄.*异常", re.I),
         "root_cause": "摄像头模块故障", "suggestion": "检查摄像头驱动、重启相机服务", "priority": "medium"},
        {"pattern": re.compile(r"overheat|thermal|temp.*high|过热.*温度|温度.*高", re.I),
         "root_cause": "设备过热", "suggestion": "降低负载、清理散热通道、检查风扇", "priority": "high"},
        {"pattern": re.compile(r"storage|disk.*full|存储.*不足|空间.*满", re.I),
         "root_cause": "存储空间不足", "suggestion": "清理存储空间、删除缓存文件", "priority": "medium"},
    ]

    # 6. 崩溃信号定义
    CRASH_SIGNALS = {
        11: "SIGSEGV - 段错误 (内存访问违规)",
        6: "SIGABRT - 程序异常终止",
        9: "SIGKILL - 被强制终止",
        8: "SIGFPE - 浮点异常",
        4: "SIGILL - 非法指令",
        7: "SIGBUS - 总线错误",
        10: "SIGUSR1 - 用户自定义信号1",
        12: "SIGUSR2 - 用户自定义信号2",
    }

    # ========== 渲染相关错误码 ==========
    RENDER_ERROR_CODES = {
        "VK_ERROR_OUT_OF_DEVICE_MEMORY": "Vulkan 设备内存不足",
        "VK_ERROR_OUT_OF_HOST_MEMORY": "Vulkan 主机内存不足",
        "VK_ERROR_DEVICE_LOST": "Vulkan 设备丢失",
        "VK_ERROR_INITIALIZATION_FAILED": "Vulkan 初始化失败",
        "VK_ERROR_EXTENSION_NOT_PRESENT": "Vulkan 扩展不存在",
        "GL_INVALID_ENUM": "OpenGL 无效枚举",
        "GL_INVALID_VALUE": "OpenGL 无效值",
        "GL_INVALID_OPERATION": "OpenGL 无效操作",
        "GL_STACK_OVERFLOW": "OpenGL 栈溢出",
        "GL_STACK_UNDERFLOW": "OpenGL 栈下溢",
        "GL_OUT_OF_MEMORY": "OpenGL 内存不足",
    }

    # 7. 日志级别映射
    LOG_LEVEL_MAP = {
        "V": "VERBOSE", "D": "DEBUG", "I": "INFO",
        "W": "WARN", "E": "ERROR", "F": "FATAL", "A": "ASSERT"
    }

    # ========== 时序问题检测模式 ==========
    TIMING_ISSUE_PATTERNS = {
        # 超时相关
        "timeout": re.compile(
            r"(timeout|timed?\s*out|TIMEOUT|超时|操作超时|响应超时|等待超时|connection\s*timeout"
            r"|read\s*timeout|write\s*timeout|network\s*timeout|socket\s*timeout)",
            re.I
        ),
        # 延迟/卡顿
        "delay": re.compile(
            r"(delay|lag|latency|卡顿|jank|stutter|延迟|耗时过长|处理慢|响应慢"
            r"|frame\s*drop|掉帧|slow\s*operation|block|阻塞)",
            re.I
        ),
        # 死锁/锁等待
        "deadlock": re.compile(
            r"(deadlock|dead\s*lock|lock\s*wait|mutex\s*wait|spinlock|rcu\s*stall"
            r"|等待锁|死锁|持锁时间过长|锁竞争)",
            re.I
        ),
        # 竞态条件
        "race_condition": re.compile(
            r"(race\s*condition|race\s*window|TOCTOU|check-then-act|竞态|并发冲突"
            r"|concurrent\s*access|同时访问|数据竞争)",
            re.I
        ),
        # 时钟问题
        "clock_issue": re.compile(
            r"(clock\s*(drift|skew|sync|invalid|wrong|error)|ntp\s*(fail|sync|error)"
            r"|timestamp\s*(invalid|error|wrong|异常)|时间戳.*错误|时钟.*不同步|时间.*漂移)",
            re.I
        ),
        # 消息顺序错误
        "out_of_order": re.compile(
            r"(out\s*of\s*order|out-of-order|message\s*order|seq\s*(error|mismatch)"
            r"|sequence\s*(error|wrong)|顺序.*错误|乱序|消息.*错乱)",
            re.I
        ),
        # 重放/重复
        "replay": re.compile(
            r"(replay\s*attack|duplicate\s*(message|request|packet)|retransmit|重放"
            r"|重复.*请求|重复.*消息|duplicate\s*frame)",
            re.I
        ),
        # 事件间隔异常
        "interval_anomaly": re.compile(
            r"(interval\s*(abnormal|error|too\s*(long|short))|gap\s*detected"
            r"|间隙.*异常|间隔.*异常|时间间隔.*问题)",
            re.I
        ),
        # 同步问题
        "sync_issue": re.compile(
            r"(sync\s*(fail|error|timeout|deadline)|synchroniz(e|ation)\s*(fail|error)"
            r"|同步.*失败|数据.*不一致|状态.*不同步)",
            re.I
        ),
        # 状态机错误
        "state_machine": re.compile(
            r"(invalid\s*state|state\s*(machine|transition|error)|unexpected\s*state"
            r"|状态.*错误|状态机.*异常|非法.*状态)",
            re.I
        ),
    }

    # ========== Shader/渲染错误检测规则 ==========
    SHADER_ERROR_PATTERNS = {
        "shader_compile": re.compile(
            r"(shader.*(compil|fail|error)|failed.*shader|shader.*(not.*found|missing)|"
            r"compil.*shader.*fail|Vulkan.*shader.*error|OpenGL.*shader.*error|EGL.*error)",
            re.I
        ),
        "vulkan_error": re.compile(
            r"(Vulkan|VK_)(error|fail|validation|crash|device.*lost|out-of-memory|initialization.*fail)",
            re.I
        ),
        "opengl_error": re.compile(
            r"(GL_|OpenGL)(error|invalid|enum|operation|stack.*overflow|context.*lost)",
            re.I
        ),
        "render_pipeline": re.compile(
            r"(render.*pipeline|draw.*call.*fail|frame.*buffer|swapchain|present.*fail|"
            r"graphics.*pipeline|渲染.*失败|渲染.*错误|画面.*异常)",
            re.I
        ),
        "gpu_error": re.compile(
            r"(GPU|Graphics).*(error|fail|crash|hang|timeout|not.*respond|driver.*error|设备.*不支持)",
            re.I
        ),
        "memory_gpu": re.compile(
            r"(GPU.*memory|显存|vram|graphics.*memory.*exhausted|out.*of.*memory.*GPU)",
            re.I
        ),
        "frame_rate": re.compile(
            r"(fps.*(low|drop|dropped|异常)|frame.*(rate.*low|stutter|jank)|卡顿|帧率.*低|掉帧)",
            re.I
        ),
        "display_mode": re.compile(
            r"(display.*mode|resolution.*not.*support|刷新率|刷新.*异常|显示.*模式|屏幕.*参数)",
            re.I
        ),
    }

    # ========== Native Crash 检测规则 ==========
    NATIVE_CRASH_PATTERNS = {
        "sigsegv": re.compile(
            r"(SIGSEGV|signal 11|segmentation fault|0x[0-9a-f]{8}.*rip|access.*violation|"
            r"memory.*access.*violat)",
            re.I
        ),
        "sigabrt": re.compile(
            r"(SIGABRT|signal 6|abort|abnormal.*termination|raise.*abort)",
            re.I
        ),
        "sigbus": re.compile(r"(SIGBUS|signal 7|bus.*error|misaligned.*access)", re.I),
        "sigfpe": re.compile(r"(SIGFPE|signal 8|float.*exception|divide.*by.*zero)", re.I),
        "sigill": re.compile(r"(SIGILL|signal 4|illegal.*instruction|invalid.*opcode)", re.I),
        "native_crash": re.compile(
            r"(native.*crash|native.*abort|lib.*crash|so.*crash|jni.*call|JNI.*Check|"
            r"UnsatisfiedLinkError|java_vm|art_method|art::|llvm::|libc\.so.*abort)",
            re.I
        ),
        "tombstone": re.compile(r"(tombstone|debuggerd|logger.*write.*crash)", re.I),
        "asan_error": re.compile(
            r"(AddressSanitizer|ASAN|heap-buffer-overflow|heap-use-after-free|"
            r"stack-buffer-overflow|global-buffer-overflow|use-after-poison)",
            re.I
        ),
        "native_assert": re.compile(
            r"(CHECK_FAILED|FATAL EXCEPTION|google::glog|absl::|assertion.*fail.*native)",
            re.I
        ),
    }

    # Native 崩溃相关函数/模块
    NATIVE_MODULES = [
        "libart.so", "libdvm.so", "libvm.so", "libc.so", "libutils.so",
        "libnativehelper.so", "libmediandk.so", "libvulkan.so", "libGLES",
        "libdove.so", "libleopard.so", "libnr", "libxrf",
    ]

    # 3. 分类规则
    CATEGORY_RULES = {
        "画面/显示": ["display", "screen", "render", "画面", "黑屏", "闪烁", "分辨率", "画质", "投影"],
        "手势/交互": ["gesture", "touch", "input", "手势", "触控", "交互", "按键", "摇杆"],
        "连接": ["connection", "network", "usb", "wifi", "连接", "蓝牙", "配对"],
        "电源": ["battery", "power", "charge", "电源", "充电", "续航", "耗电"],
        "音频": ["audio", "sound", "speaker", "音频", "声音", "扬声器", "麦克风"],
        "发热/温控": ["overheat", "thermal", "temperature", "过热", "发热", "温控"],
        "空间计算": ["slam", "tracking", "定位", "空间", "ar", "mr", "6dof"],
        "应用崩溃": ["crash", "anr", "app.*fail", "应用.*崩溃"],
        "系统问题": ["system", "kernel", "boot", "系统", "启动", "重启"],
    }

    # ========== 增强的日志解析方法 ==========

    def detect_log_format(self, log_content: str) -> str:
        """检测日志格式"""
        lines = log_content.split('\n')[:20]

        for line in lines:
            line_stripped = line.strip()
            if self.LOG_PATTERNS["logcat"].match(line_stripped):
                return "logcat"
            if self.LOG_PATTERNS["syslog"].match(line_stripped):
                return "syslog"
            if self.LOG_PATTERNS["xr_log"].match(line_stripped):
                return "xr_log"
            if self.LOG_PATTERNS["simple"].match(line_stripped):
                return "simple"

        # 尝试检测通用格式
        if any(kw in log_content.lower() for kw in ['android', 'logcat', 'pid:', 'tid:']):
            return "logcat"
        if any(kw in log_content.lower() for kw in ['dove_', 'dove/', 'xr_device']):
            return "xr_log"
        if any(kw in log_content.lower() for kw in ['error', 'warning', 'fatal', 'crash']):
            return "simple"

        return "unknown"

    def parse_log_entry(self, line: str, log_format: str) -> Optional[Dict]:
        """解析单条日志"""
        entry = {"raw": line.strip(), "parsed": False}

        # 尝试 logcat 格式
        if log_format == "logcat":
            m = self.LOG_PATTERNS["logcat"].match(line.strip())
            if m:
                entry.update({
                    "parsed": True,
                    "timestamp": m.group(1),
                    "pid": m.group(2),
                    "tid": m.group(3),
                    "level": self.LOG_LEVEL_MAP.get(m.group(4), m.group(4)),
                    "tag": m.group(5),
                    "message": m.group(6),
                })
                return entry

        # 尝试 XR 日志格式
        if log_format == "xr_log":
            m = self.LOG_PATTERNS["xr_log"].match(line.strip())
            if m:
                entry.update({
                    "parsed": True,
                    "timestamp": m.group(1),
                    "level": self.LOG_LEVEL_MAP.get(m.group(2), m.group(2)),
                    "module": m.group(3),
                    "message": m.group(4),
                })
                return entry

        # 尝试简单格式: "[2026-04-14 10:00:00] ERROR: message" 或 "[2026-04-14 10:00:00] [E] message"
        if log_format == "simple":
            m = self.LOG_PATTERNS["simple"].match(line.strip())
            if m:
                level = m.group(2) or m.group(3)  # [E] 或 ERROR
                # 标准化级别
                level_upper = level.upper()
                if level_upper in self.LOG_LEVEL_MAP:
                    level = self.LOG_LEVEL_MAP[level_upper]
                elif level_upper in ['ERROR', 'ERR']:
                    level = 'ERROR'
                elif level_upper in ['WARN', 'WARNING']:
                    level = 'WARN'
                elif level_upper in ['INFO', 'INF']:
                    level = 'INFO'
                elif level_upper in ['DEBUG', 'DBG']:
                    level = 'DEBUG'
                elif level_upper in ['FATAL', 'CRASH']:
                    level = 'FATAL'

                entry.update({
                    "parsed": True,
                    "timestamp": m.group(1),
                    "level": level,
                    "message": m.group(4),
                })
                return entry

        # 尝试 syslog 格式
        if log_format == "syslog":
            m = self.LOG_PATTERNS["syslog"].match(line.strip())
            if m:
                entry.update({
                    "parsed": True,
                    "timestamp": m.group(1),
                    "hostname": m.group(2),
                    "process": m.group(3),
                    "pid": m.group(4),
                    "message": m.group(5),
                })
                return entry

        # 通用解析:提取时间戳
        ts_match = self.LOG_PATTERNS["timestamp"].search(line)
        if ts_match:
            entry["timestamp"] = ts_match.group(1)

        return entry

    def extract_error_codes(self, log_content: str) -> List[Dict]:
        """提取错误码"""
        error_codes = []
        for match in self.ERROR_CODE_PATTERN.finditer(log_content):
            code = match.group(1)
            value = match.group(2)  # 可能有的错误码值

            error_codes.append({
                "code": code,
                "value": int(value) if value else None,
                "context": log_content[max(0, match.start()-30):match.end()+30]
            })

        return error_codes

    def extract_stack_traces(self, log_content: str) -> List[Dict]:
        """提取堆栈跟踪"""
        traces = []
        lines = log_content.split('\n')

        in_trace = False
        current_trace = []
        start_line = 0

        for i, line in enumerate(lines):
            # 检测堆栈开始
            if any(kw in line.lower() for kw in ['exception', 'traceback', 'stack:', 'backtrace', '-----']):
                if not in_trace:
                    in_trace = True
                    start_line = i
                    current_trace = [line]
                else:
                    current_trace.append(line)
            elif in_trace:
                # 继续堆栈
                if line.strip().startswith(('at ', '#', ' ')) and len(line.strip()) > 0:
                    current_trace.append(line)
                else:
                    # 堆栈结束
                    if len(current_trace) > 2:
                        traces.append({
                            "start_line": start_line + 1,
                            "lines": len(current_trace),
                            "content": '\n'.join(current_trace[:20])  # 限制长度
                        })
                    in_trace = False
                    current_trace = []

        # 处理最后一个堆栈
        if len(current_trace) > 2:
            traces.append({
                "start_line": start_line + 1,
                "lines": len(current_trace),
                "content": '\n'.join(current_trace[:20])
            })

        return traces

    def extract_crash_signature(self, log_content: str) -> Optional[Dict]:
        """提取崩溃签名"""
        content_lower = log_content.lower()

        # 检测崩溃信号
        for sig_num, sig_desc in self.CRASH_SIGNALS.items():
            if f"signal {sig_num}" in content_lower or sig_desc.split()[0].lower() in content_lower:
                return {
                    "signal": sig_num,
                    "description": sig_desc,
                    "fatal": sig_num in [6, 9, 11]
                }

        # 检测崩溃关键词
        crash_keywords = [
            ("segmentation fault", "段错误 - 内存访问违规"),
            ("fatal exception", "致命异常"),
            ("process exit", "进程退出"),
            ("abnormal termination", "异常终止"),
        ]

        for keyword, desc in crash_keywords:
            if keyword in content_lower:
                return {
                    "keyword": keyword,
                    "description": desc,
                    "fatal": True
                }

        return None

    def detect_shader_errors(self, log_content: str) -> Dict:
        """检测 Shader/渲染错误
        
        返回结构:
        {
            "has_shader_error": bool,
            "errors": [{"type": "shader_compile" | "vulkan_error" | ..., 
                        "description": str,
                        "context": str}],
            "error_types": set of detected error types
        }
        """
        result = {
            "has_shader_error": False,
            "errors": [],
            "error_types": set(),
            "summary": {}
        }
        
        content_lower = log_content.lower()
        
        # 检测各类渲染错误
        for error_type, pattern in self.SHADER_ERROR_PATTERNS.items():
            matches = pattern.findall(log_content)
            if matches:
                result["has_shader_error"] = True
                result["error_types"].add(error_type)
                
                # 提取匹配的上下文
                for match in pattern.finditer(log_content):
                    start = max(0, match.start() - 50)
                    end = min(len(log_content), match.end() + 100)
                    context = log_content[start:end].strip()
                    
                    # 错误类型描述
                    type_desc = {
                        "shader_compile": "Shader编译错误",
                        "vulkan_error": "Vulkan API 错误",
                        "opengl_error": "OpenGL 错误",
                        "render_pipeline": "渲染管线错误",
                        "gpu_error": "GPU 硬件错误",
                        "memory_gpu": "GPU 显存错误",
                        "frame_rate": "帧率异常",
                        "display_mode": "显示模式错误",
                    }.get(error_type, error_type)
                    
                    result["errors"].append({
                        "type": error_type,
                        "description": type_desc,
                        "context": context[:200],
                        "severity": "high" if error_type in ["vulkan_error", "gpu_error", "shader_compile"] else "medium"
                    })
        
        # 检测渲染相关错误码
        render_error_codes = re.findall(
            r'\b(VK_ERROR_|GL_INVALID_|EGL_\w+)\b', 
            log_content
        )
        if render_error_codes:
            result["has_shader_error"] = True
            result["error_codes"] = list(set(render_error_codes))
            for code in result["error_codes"]:
                desc = self.RENDER_ERROR_CODES.get(code, "渲染错误码")
                result["errors"].append({
                    "type": "render_error_code",
                    "description": desc,
                    "context": code,
                    "severity": "high"
                })
        
        # 统计摘要
        if result["error_types"]:
            result["summary"] = {
                "total_errors": len(result["errors"]),
                "error_types_count": len(result["error_types"]),
                "severity": "high" if any(e["severity"] == "high" for e in result["errors"]) else "medium"
            }
        
        result["error_types"] = list(result["error_types"])
        return result

    def detect_native_crash(self, log_content: str) -> Dict:
        """检测 Native Crash
        
        返回结构:
        {
            "has_native_crash": bool,
            "crash_info": {
                "signal": int,
                "signal_name": str,
                "description": str,
                "fatal": bool,
                "module": str (可能的崩溃模块),
                "context": str
            },
            "errors": [...],
            "tombstone": str (如果有),
            "asan_report": str (如果有 ASAN 报告)
        }
        """
        result = {
            "has_native_crash": False,
            "crash_info": None,
            "errors": [],
            "tombstone": None,
            "asan_report": None,
            "summary": {}
        }
        
        content_lower = log_content.lower()
        
        # 1. 检测崩溃信号
        for crash_type, pattern in self.NATIVE_CRASH_PATTERNS.items():
            match = pattern.search(log_content)
            if match:
                result["has_native_crash"] = True
                
                # 获取上下文
                start = max(0, match.start() - 100)
                end = min(len(log_content), match.end() + 200)
                context = log_content[start:end]
                
                # 获取匹配到的具体信号或关键词
                matched_text = match.group(0) if match.groups() else crash_type
                
                # 判断是否致命
                fatal = crash_type in ["sigsegv", "sigabrt", "tombstone", "asan_error", "native_assert"]
                
                # 描述映射
                desc_map = {
                    "sigsegv": "段错误 - 内存访问违规 (SIGSEGV)",
                    "sigabrt": "程序异常终止 (SIGABRT)",
                    "sigbus": "总线错误 (SIGBUS)",
                    "sigfpe": "浮点异常 (SIGFPE)",
                    "sigill": "非法指令 (SIGILL)",
                    "native_crash": "Native 层崩溃",
                    "tombstone": "Tombstone 记录 - native 崩溃",
                    "asan_error": "AddressSanitizer 检测到内存错误",
                    "native_assert": "Native 层断言失败",
                }
                
                result["crash_info"] = {
                    "crash_type": crash_type,
                    "matched_text": matched_text[:100],
                    "description": desc_map.get(crash_type, crash_type),
                    "fatal": fatal,
                    "context": context[:300]
                }
                
                result["errors"].append({
                    "type": crash_type,
                    "description": desc_map.get(crash_type, crash_type),
                    "context": context[:200],
                    "severity": "critical" if fatal else "high"
                })
        
        # 2. 提取具体的信号编号
        signal_match = re.search(r'signal\s+(\d+)', content_lower)
        if signal_match:
            sig_num = int(signal_match.group(1))
            if sig_num in self.CRASH_SIGNALS:
                result["has_native_crash"] = True
                if not result["crash_info"]:
                    result["crash_info"] = {}
                result["crash_info"]["signal"] = sig_num
                result["crash_info"]["signal_name"] = self.CRASH_SIGNALS[sig_num]
        
        # 3. 检测崩溃的 Native 模块
        for module in self.NATIVE_MODULES:
            if module.lower() in content_lower:
                if result.get("crash_info"):
                    result["crash_info"]["module"] = module
                # 也记录包含该模块的错误
                module_match = re.search(
                    rf'.{{0,100}}{re.escape(module)}.{{0,100}}',
                    log_content
                )
                if module_match:
                    result["errors"].append({
                        "type": "native_module",
                        "description": f"涉及模块: {module}",
                        "context": module_match.group(0)[:150],
                        "severity": "high"
                    })
        
        # 4. 检测 Tombstone (Android native 崩溃转储)
        if "tombstone" in content_lower or "debuggerd" in content_lower:
            result["has_native_crash"] = True
            result["tombstone"] = "存在 tombstone 记录"
            # 尝试提取完整 tombstone
            tombstone_match = re.search(
                r'(--- *\n *pid:.*?--- *\n)',
                log_content,
                re.DOTALL
            )
            if tombstone_match:
                result["tombstone"] = tombstone_match.group(1)[:500]
        
        # 5. 检测 ASAN 报告
        if result["asan_report"] is None:
            asan_match = re.search(
                r'(==\d+==.*?(?:ABORTING|ABORT|LEAK|ERROR))',
                log_content,
                re.DOTALL | re.IGNORECASE
            )
            if asan_match:
                result["has_native_crash"] = True
                result["asan_report"] = asan_match.group(0)[:500]
        
        # 6. 统计摘要
        if result["has_native_crash"]:
            fatal_count = sum(1 for e in result["errors"] if e.get("severity") == "critical")
            result["summary"] = {
                "total_issues": len(result["errors"]),
                "has_tombstone": result["tombstone"] is not None,
                "has_asan": result["asan_report"] is not None,
                "fatal": fatal_count > 0,
                "severity": "critical" if fatal_count > 0 else "high"
            }
        
        return result

    def detect_timing_issues(self, log_content: str) -> Dict:
        """检测时序问题
        
        返回结构:
        {
            "has_timing_issue": bool,
            "issues": [{
                "type": "timeout" | "delay" | "deadlock" | ...,
                "description": str,
                "context": str,
                "severity": "high" | "medium" | "low",
                "timestamp": str (如果解析到)
            }],
            "issue_types": list of detected types,
            "summary": {
                "total_issues": int,
                "severity": str
            }
        }
        """
        result = {
            "has_timing_issue": False,
            "issues": [],
            "issue_types": [],
            "summary": {}
        }
        
        content_lower = log_content.lower()
        
        # 时序问题类型描述
        issue_desc = {
            "timeout": "超时问题 - 操作在预期时间内未完成",
            "delay": "延迟/卡顿 - 处理时间超过预期",
            "deadlock": "死锁/锁等待 - 线程相互等待形成循环",
            "race_condition": "竞态条件 - 并发访问时序不当导致结果不确定",
            "clock_issue": "时钟问题 - 时间戳错误或时钟不同步",
            "out_of_order": "消息乱序 - 数据包或消息未按预期顺序到达",
            "replay": "重放/重复 - 检测到重复请求或消息",
            "interval_anomaly": "间隔异常 - 事件间隔不符合预期",
            "sync_issue": "同步问题 - 数据或状态同步失败",
            "state_machine": "状态机错误 - 状态转换非法或异常"
        }
        
        # 严重程度映射
        severity_map = {
            "deadlock": "high",
            "race_condition": "high",
            "timeout": "medium",
            "clock_issue": "medium",
            "sync_issue": "medium",
            "state_machine": "medium",
            "delay": "low",
            "out_of_order": "low",
            "replay": "low",
            "interval_anomaly": "low"
        }
        
        # 检测各类时序问题
        for issue_type, pattern in self.TIMING_ISSUE_PATTERNS.items():
            matches = list(pattern.finditer(log_content))
            if matches:
                result["has_timing_issue"] = True
                result["issue_types"].append(issue_type)
                
                # 提取匹配上下文
                for match in matches[:5]:  # 每个类型最多5个匹配
                    start = max(0, match.start() - 50)
                    end = min(len(log_content), match.end() + 100)
                    context = log_content[start:end].strip()
                    
                    # 尝试提取时间戳
                    ts_match = self.LOG_PATTERNS["timestamp"].search(context)
                    timestamp = ts_match.group(1) if ts_match else None
                    
                    result["issues"].append({
                        "type": issue_type,
                        "description": issue_desc.get(issue_type, issue_type),
                        "context": context[:200],
                        "severity": severity_map.get(issue_type, "medium"),
                        "timestamp": timestamp
                    })
        
        # 检测时间戳相关异常 (时序问题的核心指标)
        # 1. 检测时间倒退
        timestamp_order_issues = self._check_timestamp_order(log_content)
        if timestamp_order_issues:
            result["has_timing_issue"] = True
            result["issues"].extend(timestamp_order_issues)
            if "timestamp_order" not in result["issue_types"]:
                result["issue_types"].append("timestamp_order")
        
        # 2. 检测时间间隔异常
        interval_issues = self._check_interval_anomaly(log_content)
        if interval_issues:
            result["has_timing_issue"] = True
            result["issues"].extend(interval_issues)
            if "interval_anomaly" not in result["issue_types"]:
                result["issue_types"].append("interval_anomaly")
        
        # 统计摘要
        if result["issues"]:
            high_severity = sum(1 for i in result["issues"] if i.get("severity") == "high")
            result["summary"] = {
                "total_issues": len(result["issues"]),
                "unique_types": len(set(i["type"] for i in result["issues"])),
                "severity": "high" if high_severity > 0 else "medium"
            }
        
        return result

    def _check_timestamp_order(self, log_content: str) -> List[Dict]:
        """检查时间戳顺序异常 (时间倒退检测)"""
        issues = []
        
        # 提取所有时间戳
        timestamps = []
        for match in self.LOG_PATTERNS["timestamp"].finditer(log_content):
            ts_str = match.group(1)
            # 标准化时间格式
            ts_str = ts_str.replace('/', '-')
            if 'T' not in ts_str:
                ts_str = ts_str.replace(' ', 'T')
            try:
                # 解析时间
                from datetime import datetime
                # 处理不同格式
                if '.' in ts_str:
                    base_fmt = '%Y-%m-%dT%H:%M:%S.%f'
                else:
                    base_fmt = '%Y-%m-%dT%H:%M:%S'
                # 去掉时区
                if '+' in ts_str or 'Z' in ts_str:
                    ts_str = ts_str[:19]
                dt = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
                timestamps.append((dt, match.group(0), match.start()))
            except:
                pass
        
        # 检查时间是否倒退
        for i in range(1, len(timestamps)):
            prev_ts, prev_str, prev_pos = timestamps[i-1]
            curr_ts, curr_str, curr_pos = timestamps[i]
            
            if curr_ts < prev_ts:
                # 时间倒退了
                context_start = max(0, prev_pos - 30)
                context_end = min(len(log_content), curr_pos + len(curr_str) + 30)
                context = log_content[context_start:context_end]
                
                diff = prev_ts - curr_ts
                issues.append({
                    "type": "timestamp_order",
                    "description": f"时间戳倒退 {diff.total_seconds():.1f} 秒",
                    "context": context[:200],
                    "severity": "high",
                    "timestamp": str(curr_str),
                    "prev_timestamp": str(prev_str)
                })
        
        return issues

    def _check_interval_anomaly(self, log_content: str) -> List[Dict]:
        """检查时间间隔异常"""
        issues = []
        
        # 提取时间戳
        timestamps = []
        for match in self.LOG_PATTERNS["timestamp"].finditer(log_content):
            ts_str = match.group(1)
            ts_str = ts_str.replace('/', '-')
            if 'T' not in ts_str:
                ts_str = ts_str.replace(' ', 'T')
            try:
                from datetime import datetime
                if '.' in ts_str:
                    base_fmt = '%Y-%m-%dT%H:%M:%S.%f'
                else:
                    base_fmt = '%Y-%m-%dT%H:%M:%S'
                if '+' in ts_str or 'Z' in ts_str:
                    ts_str = ts_str[:19]
                dt = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
                timestamps.append((dt, match.start()))
            except:
                pass
        
        # 检查连续时间间隔
        if len(timestamps) >= 3:
            intervals = []
            for i in range(1, len(timestamps)):
                diff = (timestamps[i][0] - timestamps[i-1][0]).total_seconds()
                intervals.append(diff)
            
            # 计算平均间隔
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                
                # 检测异常大的间隔
                for i, diff in enumerate(intervals):
                    if avg_interval > 0 and diff > avg_interval * 10 and diff > 1:
                        # 间隔异常大
                        pos = timestamps[i+1][1]
                        context = log_content[max(0, pos-50):pos+100]
                        
                        issues.append({
                            "type": "interval_anomaly",
                            "description": f"时间间隔异常: {diff:.1f}s (平均 {avg_interval:.1f}s)",
                            "context": context[:200],
                            "severity": "medium",
                            "interval": diff
                        })
        
        return issues

    def extract_package_info(self, log_content: str) -> Dict:
        """提取包名/模块信息"""
        packages = []

        # Android 包名
        android_pkgs = re.findall(r'([a-z][a-z0-9_]*\.[a-z][a-z0-9_]+\.[A-Za-z0-9_]+)', log_content)
        packages.extend(android_pkgs[:5])

        # XR 模块名
        xr_modules = re.findall(r'\[([a-z]+/[a-z_]+)\]', log_content)
        packages.extend(xr_modules[:5])

        return {
            "android_packages": list(set(android_pkgs)),
            "xr_modules": list(set(xr_modules)),
        }

    def analyze_log_enhanced(self, log_content: str) -> Dict:
        """增强的日志分析 (支持 Shader/渲染错误和 Native Crash 检测)"""
        result = {
            "format": self.detect_log_format(log_content),
            "error_count": 0,
            "warning_count": 0,
            "fatal_count": 0,
            "errors": [],
            "warnings": [],
            "error_codes": [],
            "stack_traces": [],
            "crash_signature": None,
            "packages": {},
            "summary": {},
            # 新增: Shader/渲染错误检测
            "shader_errors": {
                "has_shader_error": False,
                "errors": [],
                "error_types": [],
                "error_codes": []
            },
            # 新增: Native Crash 检测
            "native_crash": {
                "has_native_crash": False,
                "crash_info": None,
                "errors": [],
                "tombstone": None,
                "asan_report": None
            },
            # 新增: 时序问题检测
            "timing_issues": {
                "has_timing_issue": False,
                "issues": [],
                "issue_types": [],
                "summary": {}
            },
        }

        if not log_content:
            return result

        # 提取结构化信息
        result["error_codes"] = self.extract_error_codes(log_content)
        result["stack_traces"] = self.extract_stack_traces(log_content)
        result["crash_signature"] = self.extract_crash_signature(log_content)
        result["packages"] = self.extract_package_info(log_content)

        # ========== 新增: Shader/渲染错误检测 ==========
        result["shader_errors"] = self.detect_shader_errors(log_content)
        
        # 如果检测到渲染错误，增加相关计数
        if result["shader_errors"]["has_shader_error"]:
            shader_err_count = len(result["shader_errors"]["errors"])
            result["error_count"] += shader_err_count
            # 严重渲染错误增加 fatal 计数
            if result["shader_errors"]["summary"].get("severity") == "high":
                result["fatal_count"] += 1

        # ========== 新增: Native Crash 检测 ==========
        result["native_crash"] = self.detect_native_crash(log_content)
        
        # 如果检测到 native crash，增加 fatal 计数
        if result["native_crash"]["has_native_crash"]:
            crash_info = result["native_crash"]["crash_info"]
            if crash_info and crash_info.get("fatal"):
                result["fatal_count"] += 2  # Native crash 视为严重错误

        # ========== 新增: 时序问题检测 ==========
        result["timing_issues"] = self.detect_timing_issues(log_content)
        
        # 如果检测到时序问题，增加 error 计数
        if result["timing_issues"]["has_timing_issue"]:
            timing_count = len(result["timing_issues"]["issues"])
            result["error_count"] += timing_count
            # 严重的时序问题增加 fatal 计数
            if result["timing_issues"]["summary"].get("severity") == "high":
                result["fatal_count"] += 1

        # 逐行解析
        log_format = result["format"]
        lines = log_content.split('\n')

        error_keywords = ['fatal', 'crash', 'segmentation', 'exception', 'error', 'fail', 'failed']
        warning_keywords = ['warning', 'warn', 'deprecate']

        for line in lines[:5000]:  # 限制行数
            line_lower = line.lower()

            # 解析结构化日志
            entry = self.parse_log_entry(line, log_format)
            msg = entry.get("message", entry.get("raw", ""))

            # 判断级别
            level = entry.get("level", "").upper()

            if any(kw in line_lower for kw in ['fatal', 'crash', 'segmentation fault', 'abort']):
                result["fatal_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", f"L{len(result['errors'])+1}"),
                    "type": "FATAL",
                    "content": line.strip()[:200],
                    "parsed": entry.get("parsed", False)
                })
            elif 'error' in line_lower or 'exception' in line_lower:
                result["error_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", f"L{len(result['errors'])+1}"),
                    "type": "ERROR",
                    "content": line.strip()[:200],
                    "parsed": entry.get("parsed", False)
                })
            elif any(kw in line_lower for kw in warning_keywords):
                result["warning_count"] += 1
                result["warnings"].append({
                    "line": entry.get("timestamp", f"L{len(result['warnings'])+1}"),
                    "content": line.strip()[:200],
                })

        # 生成摘要 (增强版)
        result["summary"] = {
            "total_lines": len(lines),
            "analyzed_lines": min(len(lines), 5000),
            "error_rate": round(result["error_count"] / min(len(lines), 5000) * 100, 2) if lines else 0,
            "has_crash": result["crash_signature"] is not None,
            "has_stack_trace": len(result["stack_traces"]) > 0,
            "unique_error_codes": len(set(ec["code"] for ec in result["error_codes"])),
            # 新增: Shader/渲染错误摘要
            "has_shader_error": result["shader_errors"]["has_shader_error"],
            "shader_error_types": result["shader_errors"]["error_types"],
            # 新增: Native Crash 摘要
            "has_native_crash": result["native_crash"]["has_native_crash"],
            "native_crash_types": result["native_crash"]["crash_info"]["crash_type"] if result["native_crash"]["crash_info"] else None,
            "has_tombstone": result["native_crash"]["tombstone"] is not None,
            "has_asan": result["native_crash"]["asan_report"] is not None,
            # 新增: 时序问题摘要
            "has_timing_issue": result["timing_issues"]["has_timing_issue"],
            "timing_issue_types": result["timing_issues"]["issue_types"],
            "timing_issue_count": len(result["timing_issues"]["issues"]),
        }

        return result

    def analyze_zip(self, zip_path: str) -> Dict:
        """分析 ZIP 文件"""
        result = {"files": [], "logs": [], "images": [], "crash_files": [], "error": None}

        try:
            extract_dir = "/tmp/bug_analyzer"
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir)

            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(extract_dir)

            for root, dirs, files in os.walk(extract_dir):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    rel_path = os.path.relpath(filepath, extract_dir)
                    file_info = {"name": rel_path, "size": os.path.getsize(filepath)}

                    ext = filename.lower().split('.')[-1] if '.' in filename else ''

                    if ext in ['log', 'txt', 'cat', 'dmp'] or 'log' in filename.lower():
                        result["logs"].append(file_info)
                    elif ext in ['jpg', 'jpeg', 'png', 'gif']:
                        result["images"].append(file_info)
                    elif ext in ['core', 'dmp', 'pcap']:
                        result["crash_files"].append(file_info)

                    result["files"].append(file_info)

            result["file_count"] = len(result["files"])
            result["logs_count"] = len(result["logs"])
            result["images_count"] = len(result["images"])

            # 分析日志
            all_log = ""
            for log_file in result["logs"][:10]:
                log_paths = [
                    f"/tmp/bug_analyzer/{log_file['name']}",
                    f"/tmp/bug_analyzer/logs/{os.path.basename(log_file['name'])}"
                ]
                for lp in log_paths:
                    if os.path.exists(lp):
                        try:
                            with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                                all_log += f"\n--- {log_file['name']} ---\n" + f.read(100000)
                        except:
                            pass
                        break

            result["log_analysis"] = self.analyze_log(all_log)

        except Exception as e:
            result["error"] = str(e)

        return result

    def analyze_log(self, log_content: str) -> Dict:
        """分析日志 (调用增强版本)"""
        return self.analyze_log_enhanced(log_content)

    def find_similar_bugs(self, query: str, limit: int = 5) -> List[Dict]:
        """搜索相似缺陷 (优先 OpenViking 语义搜索，回退到本地 JSON)"""
        # 1. 优先使用 OpenViking 语义搜索
        ov_results = self._search_openviking_bugs(query, limit)
        if ov_results:
            print(f"[OpenViking] 找到 {len(ov_results)} 个相似缺陷")
            return ov_results

        # 2. 回退到本地 JSON 关键词搜索
        local_results = self._search_local_bugs(query, limit)
        if local_results:
            print(f"[本地] 找到 {len(local_results)} 个相似缺陷")
            return local_results

        return []

    def _search_openviking_bugs(self, query: str, limit: int = 5) -> List[Dict]:
        """通过 OpenViking ov find 语义搜索相似缺陷"""
        import subprocess

        # 检查 ov CLI 是否可用
        try:
            result = subprocess.run(
                ["ov", "system", "health"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or "true" not in result.stdout.lower():
                return []
        except Exception:
            return []

        # 执行语义搜索
        try:
            result = subprocess.run(
                ["ov", "find", query, "-u", "/resources/feishu-bugs", "-n", str(limit * 3)],
                capture_output=True, text=True, timeout=15,
            )

            if result.returncode != 0:
                return []

            return self._parse_ov_output(result.stdout, limit)
        except Exception as e:
            print(f"  OpenViking 搜索失败: {e}")
            return []

    def _parse_ov_output(self, output: str, limit: int) -> List[Dict]:
        """解析 ov find 输出，提取缺陷信息"""
        results = []
        # 从 OpenViking URI 读取实际文件内容获取完整信息
        for line in output.strip().split("\n"):
            # 跳过表头和摘要行
            if "context_type" in line or "viking://temp" in line or ".abstract" in line:
                continue

            # 解析格式: resource  viking://resources/feishu-bugs/6984866764/6984866764.md  L2  score
            parts = line.strip().split()
            if len(parts) < 2:
                continue

            uri = ""
            score = 0.0
            for part in parts:
                if part.startswith("viking://resources/feishu-bugs/"):
                    uri = part
                try:
                    score = float(part)
                except ValueError:
                    pass

            if not uri:
                continue

            # 从 URI 提取 bug_id
            uri_parts = uri.replace("viking://", "").split("/")
            bug_id = ""
            for i, p in enumerate(uri_parts):
                if p == "feishu-bugs" and i + 1 < len(uri_parts):
                    bug_id = uri_parts[i + 1]
                    break

            if not bug_id or not bug_id.isdigit():
                continue

            # 尝试从本地 Markdown 文件读取标题
            md_path = Path.home() / f".openviking/data/viking/default/feishu-bugs/{bug_id}.md"
            title = ""
            status = ""
            if md_path.exists():
                try:
                    content = md_path.read_text(encoding="utf-8")
                    for md_line in content.split("\n"):
                        if md_line.startswith("# ") and not title:
                            title = md_line[2:].strip()
                        elif md_line.startswith("- **状态**:") and not status:
                            status = md_line.replace("- **状态**:", "").strip()
                        if title and status:
                            break
                except Exception:
                    pass

            if not title:
                title = f"缺陷 {bug_id}"

            results.append({
                "id": bug_id,
                "title": title[:80],
                "score": round(score, 2),
                "status": status or "UNKNOWN",
                "comments": [],
            })

        # 按分数排序，去重
        seen = set()
        unique_results = []
        for r in sorted(results, key=lambda x: x["score"], reverse=True):
            if r["id"] not in seen:
                seen.add(r["id"])
                unique_results.append(r)
                if len(unique_results) >= limit:
                    break

        return unique_results

    def _search_local_bugs(self, query: str, limit: int = 5) -> List[Dict]:
        """从本地 JSON 文件搜索缺陷"""
        import json

        # 支持两个路径：skill 目录下的 data 或用户 home 目录
        search_paths = [
            CONFIG_DIR / "data" / "feishu-bugs" / "batch",
            Path.home() / ".openviking/workspace/feishu-bugs/batch",
        ]
        bug_files = ["bugs_index.json", "bugs_all_with_details.json", "bugs_full_all.json"]

        # 拆分搜索词
        query_words = query.lower().split()
        results = []

        for search_path in search_paths:
            if not search_path.exists():
                continue
            for bf in bug_files:
                bf_path = search_path / bf
                if not bf_path.exists():
                    continue

                try:
                    with open(bf_path, 'r', encoding='utf-8') as f:
                        bugs = json.load(f)

                    if isinstance(bugs, dict):
                        bugs = bugs.get('data', [])

                    # 搜索匹配的缺陷 - 任意关键词匹配即可
                    for bug in bugs:
                        name = bug.get('name', bug.get('title', ''))
                        if not name:
                            continue

                        name_lower = name.lower()
                        # 任意一个关键词匹配即可
                        if any(word in name_lower for word in query_words):
                            results.append({
                                "id": bug.get('id', ''),
                                "title": name[:50],
                                "score": 0.8,
                                "status": bug.get('status', 'UNKNOWN'),
                                "comments": []
                            })
                            if len(results) >= limit * 3:  # 收集更多以备筛选
                                break

                    if results:
                        break

                except Exception as e:
                    print(f"读取 {bf} 失败: {e}")
                    continue

            if results:
                break

        # 返回匹配的结果
        return results[:limit]

    def call_llm(self, prompt: str, system_prompt: str = None) -> str:
        """调用 LLM"""
        import subprocess
        import json
        import shlex

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        data_json = json.dumps({
            "model": self.LLM_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1024
        }, ensure_ascii=False)

        cmd = f"""curl -s --max-time {self.LLM_TIMEOUT} -X POST {self.LLM_API_BASE}/chat/completions -H 'Authorization: Bearer {self.LLM_API_KEY}' -H 'Content-Type: application/json' -d {shlex.quote(data_json)}"""

        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            resp_data = json.loads(result.stdout)
            return resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            return f"LLM调用失败: {str(e)}"

    def llm_analyze(self, analysis_result: Dict, force: bool = False) -> Dict:
        """LLM 增强分析 - 增强版,包含更多代码和日志上下文"""
        confidence = self.evaluate_confidence(analysis_result)

        if not force and confidence["score"] >= 0.8:
            return {"used_llm": False, "confidence": confidence, "note": "置信度已足够"}

        log_analysis = analysis_result.get("log_analysis", {})

        # ========== 增强的日志上下文提取 ==========
        # 错误:增加到20个
        errors = log_analysis.get("errors", [])[:20]
        # 警告:增加到10个
        warnings = log_analysis.get("warnings", [])[:10]
        # 错误码:增加到10个
        error_codes = log_analysis.get("error_codes", [])[:10]
        crash_sig = log_analysis.get("crash_signature")
        # 堆栈:增加到5个
        stack_traces = log_analysis.get("stack_traces", [])[:5]

        # ========== 代码上下文搜索 (增强版) ==========
        code_context = ""
        if self.code_searcher:
            search_keywords = []

            # 从错误码提取
            for ec in error_codes[:5]:
                code = ec.get('code', '')
                if code and len(code) >= 3:
                    search_keywords.append(code)

            # 从崩溃签名提取
            if crash_sig:
                signal = crash_sig.get('signal', '')
                desc = crash_sig.get('description', '')
                if signal:
                    search_keywords.append(f"SIG{signal}" if isinstance(signal, int) else signal)
                # 从描述提取关键词
                sig_keywords = re.findall(r'\b[A-Za-z]{4,}\b', desc)
                search_keywords.extend(sig_keywords[:3])

            # 从错误消息提取
            for e in errors[:8]:
                content = e.get('content', '')
                # 提取函数调用模式
                funcs = re.findall(r'(\w+(?:/\w+)?::)?\w+\([^)]*\)', content)
                for f in funcs[:2]:
                    if len(f) >= 4:
                        search_keywords.append(f.replace('/', '::'))
                # 提取错误类型
                if 'Exception' in content or 'Error' in content:
                    match = re.search(r'(\w+(?:Exception|Error))', content)
                    if match:
                        search_keywords.append(match.group(1))
                # 提取模块名
                module_match = re.search(r'\[(\w+/\w+)\]', content)
                if module_match:
                    search_keywords.append(module_match.group(1))

            # 去重
            search_keywords = list(set(search_keywords))[:8]

            # 执行代码搜索 - 搜索所有仓库
            if search_keywords:
                code_results = []
                seen = set()
                # 搜索所有仓库
                all_repos = ["dove", "framework", "leopard", "sparrow", "project"]

                for kw in search_keywords:
                    for repo in all_repos:
                        try:
                            results = self.code_searcher.search_code(
                                kw,
                                max_results=8,  # 每个关键词更多结果
                                repos=[repo]
                            )
                            for r in results:
                                key = f"{r['repo']}:{r['file']}"
                                if key not in seen:
                                    seen.add(key)
                                    code_results.append(r)
                                    if len(code_results) >= 15:  # 最多15个文件
                                        break
                        except Exception as e:
                            pass
                        if len(code_results) >= 15:
                            break
                    if len(code_results) >= 15:
                        break

                # 构建增强的代码上下文
                if code_results:
                    code_context += "\n## 代码上下文 (参考)\n"
                    for r in code_results[:12]:  # 最多显示12个文件
                        code_context += f"\n### [{r['repo']}] {r['file']}\n"
                        # 显示更多匹配行
                        matches = r.get('matches', [])
                        for m in matches[:3]:  # 每个文件最多3个匹配
                            line_num = m.get('line_num', '?')
                            content = m.get('content', '')[:100]
                            code_context += f"L{line_num}: {content}\n"

        # ========== 构建结构化 prompt (增强版) ==========
        prompt_parts = ["# Bug 分析请求\n"]

        # 错误摘要
        prompt_parts.append("## 错误摘要")
        prompt_parts.append(f"- 错误数: {log_analysis.get('error_count', 0)}")
        prompt_parts.append(f"- 警告数: {log_analysis.get('warning_count', 0)}")
        prompt_parts.append(f"- 致命错误: {log_analysis.get('fatal_count', 0)}")

        if crash_sig:
            prompt_parts.append(f"- 崩溃签名: {crash_sig}")

        # 错误码(更多)
        if error_codes:
            prompt_parts.append("\n## 错误码")
            for ec in error_codes:
                prompt_parts.append(f"- {ec['code']}: {ec.get('context', '')[:80]}")

        # 主要错误(更多)
        if errors:
            prompt_parts.append("\n## 主要错误 (按时间顺序)")
            for i, e in enumerate(errors[:15]):  # 增加到15个
                prompt_parts.append(f"{i+1}. [{e.get('type', 'E')}] {e.get('content', '')[:150]}")

        # 警告(更多)
        if warnings:
            prompt_parts.append("\n## 警告信息")
            for w in warnings[:8]:  # 增加到8个
                prompt_parts.append(f"- {w.get('content', '')[:120]}")

        # 堆栈跟踪(更多)
        if stack_traces:
            prompt_parts.append("\n## 堆栈跟踪")
            for st in stack_traces:
                prompt_parts.append(f"```\n{st.get('content', '')[:400]}\n```")

        # 已有推断
        if analysis_result.get("root_cause"):
            prompt_parts.append(f"\n## 已有推断\n{analysis_result['root_cause']}")

        # 评论信息 (飞书评论)
        comments = analysis_result.get("comments", [])
        if comments:
            prompt_parts.append("\n## 飞书评论 (已知信息)")
            for i, c in enumerate(comments[:5], 1):
                content = c.get('content', '')
                if content:
                    if content.strip() in ['[图片]', '[图片]', '[]']:
                        continue
                    prompt_parts.append(f"{i}. {content[:300]}")

        # ========== 相似历史缺陷 (OpenViking 语义搜索) ==========
        similar_bugs = analysis_result.get("similar_bugs", [])
        if similar_bugs:
            prompt_parts.append("\n## 相似历史缺陷 (OpenViking 语义匹配)")
            prompt_parts.append("以下是与当前问题语义相似的历史缺陷，请参考它们的描述和处理方式：")
            for i, sb in enumerate(similar_bugs[:5], 1):
                prompt_parts.append(f"{i}. [{sb.get('id', '')}] {sb.get('title', '')}")
                prompt_parts.append(f"   状态: {sb.get('status', 'UNKNOWN')} | 相似度: {sb.get('score', 0):.2f}")

        # 代码上下文
        if code_context:
            prompt_parts.append(code_context)

        prompt_parts.append("\n---\n请分析并输出:\n")
        prompt_parts.append("### 根因分析 (最可能的原因)")
        prompt_parts.append("[直接给出最可能的1-2个根本原因,如果代码上下文中有相关线索请特别指出]\n")
        prompt_parts.append("### 影响范围")
        prompt_parts.append("[受影响的模块/功能]\n")
        prompt_parts.append("### 复现概率")
        prompt_parts.append("[高/中/低及理由]\n")
        prompt_parts.append("### 建议措施")
        prompt_parts.append("[按优先级排列的具体解决步骤]")

        result = self.call_llm("\n".join(prompt_parts),
            "你是一个专业的XR设备Bug分析专家。请基于提供的日志信息和代码上下文进行严谨分析,输出结构化的结论。如果代码中有相关实现或错误处理逻辑,请结合分析。")

        return {
            "used_llm": True,
            "result": result,
            "confidence": confidence,
            "llm_prompt_tokens": sum(len(p.split()) for p in prompt_parts)
        }

    def evaluate_confidence(self, analysis_result: Dict) -> Dict:
        """评估置信度 (多维度 - 2026-04-14 增强版)"""
        log_analysis = analysis_result.get("log_analysis", {})

        # 0. 日志来源可信度 (Bonus) - 来自可信日志源的明确崩溃证据
        source_score = self._evaluate_log_source(log_analysis, analysis_result)

        # 1. 日志完整性 (15%) - 错误数量
        error_count = log_analysis.get("error_count", 0)
        fatal_count = log_analysis.get("fatal_count", 0)
        log_score = min(1.0, (error_count + fatal_count * 2) / 15)

        # 2. 堆栈质量 (15%) - 是否有完整堆栈
        stack_traces = log_analysis.get("stack_traces", [])
        has_quality_stack = any(s.get("lines", 0) >= 3 for s in stack_traces)
        stack_score = 1.0 if has_quality_stack else (0.5 if stack_traces else 0.2)

        # 3. 错误明确性 (15%) - 是否有明确的错误码或异常类型
        error_codes = log_analysis.get("error_codes", [])
        crash_sig = log_analysis.get("crash_signature")
        error_code_score = min(1.0, len(set(ec["code"] for ec in error_codes)) / 3) if error_codes else 0
        if crash_sig:
            error_code_score = max(error_code_score, 0.8)

        # ========== 4. Shader/渲染错误检测 (15%) - 新增 ==========
        shader_errors = log_analysis.get("shader_errors", {})
        shader_score = 0.0
        if shader_errors.get("has_shader_error"):
            error_types = shader_errors.get("error_types", [])
            # Vulkan/OpenGL/GPU 错误权重更高
            high_severity = ["vulkan_error", "gpu_error", "opengl_error"]
            if any(et in high_severity for et in error_types):
                shader_score += 0.15
            # Shader 编译失败
            if "shader_compile" in error_types:
                shader_score += 0.1
            # 帧率异常
            if "frame_rate" in error_types:
                shader_score += 0.05
            shader_score = min(0.2, shader_score)  # 最高加 0.2

        # ========== 5. Native Crash 检测 (15%) - 新增 ==========
        native_crash = log_analysis.get("native_crash", {})
        native_score = 0.0
        if native_crash.get("has_native_crash"):
            crash_info = native_crash.get("crash_info", {})
            # Native 崩溃 (SIGSEGV/SIGABRT)
            if crash_info and crash_info.get("fatal"):
                native_score += 0.2
            # Tombstone 记录
            if native_crash.get("tombstone"):
                native_score += 0.15
            # ASAN 报告
            if native_crash.get("asan_report"):
                native_score += 0.15
            native_score = min(0.3, native_score)  # 最高加 0.3

        # ========== 6. 时序问题检测 (10%) - 新增 ==========
        timing_issues = log_analysis.get("timing_issues", {})
        timing_score = 0.0
        if timing_issues.get("has_timing_issue"):
            issue_types = timing_issues.get("issue_types", [])
            # 严重时序问题权重更高
            high_severity_types = ["deadlock", "race_condition", "timestamp_order"]
            if any(it in high_severity_types for it in issue_types):
                timing_score += 0.15
            # 超时问题
            if "timeout" in issue_types:
                timing_score += 0.1
            # 时钟问题
            if "clock_issue" in issue_types:
                timing_score += 0.1
            # 同步问题
            if "sync_issue" in issue_types:
                timing_score += 0.1
            timing_score = min(0.2, timing_score)  # 最高加 0.2

        # 7. 相似匹配度 (15%) - 相似缺陷匹配
        similar = analysis_result.get("similar_bugs", [])
        high_score_similar = [s for s in similar if s.get("score", 0) >= 0.7]
        similar_score = min(1.0, len(high_score_similar) / 3 + len(similar) / 10)

        # 7. 根因确定性 (10%) - 是否有明确的根因推断
        root_cause = analysis_result.get("root_cause", "")
        if "需要进一步分析" in root_cause or not root_cause:
            root_score = 0.2
        elif any(kw in root_cause for kw in ["可能", "疑似", "不确定"]):
            root_score = 0.5
        else:
            root_score = 0.9

        # 8. 时间集中度 (Bonus) - 错误是否在短时间内集中出现
        time_concentrated = self._check_time_concentration(log_analysis)

        # 加权计算 (更新版权重)
        total = (
            log_score * 0.10 +
            stack_score * 0.10 +
            error_code_score * 0.10 +
            shader_score * 0.15 +
            native_score * 0.15 +
            timing_score * 0.10 +
            similar_score * 0.15 +
            root_score * 0.15
        )

        # 调整1: 如果时间集中，增加置信度
        if time_concentrated:
            total = min(1.0, total + 0.1)

        # 调整2: 日志来源可信度
        if source_score > 0:
            total = min(1.0, total + source_score)

        level = "🟢 高" if total >= 0.7 else "🟡 中" if total >= 0.4 else "🔴 低"

        return {
            "score": round(total, 2),
            "level": level,
            "details": {
                "log_completeness": round(log_score, 2),
                "stack_quality": round(stack_score, 2),
                "error_clarity": round(error_code_score, 2),
                "shader_detection": round(shader_score, 2),
                "native_crash_detection": round(native_score, 2),
                "timing_detection": round(timing_score, 2),
                "similarity_match": round(similar_score, 2),
                "root_cause_certainty": round(root_score, 2),
                "time_concentrated": time_concentrated,
                "log_source_score": round(source_score, 2),
            }
        }

    def _check_time_concentration(self, log_analysis: Dict) -> bool:
        """检查错误是否在短时间内集中出现"""
        errors = log_analysis.get("errors", [])
        if len(errors) < 3:
            return False

        # 提取时间戳
        timestamps = []
        for e in errors:
            ts = e.get("line", "")
            # 尝试解析各种时间格式
            if ts.startswith("20") or ts.startswith("19"):  # ISO 格式
                timestamps.append(ts)
            elif ":" in ts and len(ts) <= 12:  # logcat 格式 "12-25 10:30:15"
                timestamps.append(ts)

        # 简单判断:如果有多个相同时间前缀,认为是集中的
        if len(set(timestamps[:5])) <= 2:
            return True

        return False

    def _evaluate_log_source(self, log_analysis: Dict, analysis_result: Dict) -> float:
        """评估日志来源可信度 - 来自可信日志源的明确崩溃证据

        规则:
        - kernel.log 中有 coredump: +0.2 (最高可信的崩溃证据)
        - dmesg 中有崩溃信息: +0.15
        - syslog 中有 fatal 错误: +0.1
        - 混合多个可信源: 累加
        """
        score = 0.0

        # 获取日志文件信息(在 ZIP 分析时可能包含文件名)
        log_sources = analysis_result.get("log_sources", [])

        # 检查日志内容中是否包含明确的崩溃证据
        errors = log_analysis.get("errors", [])
        warnings = log_analysis.get("warnings", [])
        all_content = " ".join([
            e.get("content", "") for e in errors
        ] + [w.get("content", "") for w in warnings]
        ).lower()

        # 1. kernel.log / dmesg 中的 coredump 是最可靠的崩溃证据
        if any(keyword in all_content for keyword in [
            "core dump", "coredump", "core dumped",
            "dump captured", "writing core", "generated core"
        ]):
            # 检查是否来自 kernel/dmesg 级别日志
            has_kernel_evidence = any([
                "kernel" in src.lower() or "dmesg" in src.lower() or "kmsg" in src.lower()
                for src in log_sources
            ]) or "dmesg" in all_content or "kmsg" in all_content

            if has_kernel_evidence:
                score += 0.25  # kernel.log + coredump = 最高可信度
            else:
                score += 0.15  # 通用日志中的 coredump

        # 2. dmesg 中的硬件/驱动错误
        if "dmesg" in all_content or "kmsg" in all_content:
            if any(kw in all_content for kw in ["hardware error", "hardware", "driver error", "firmware"]):
                score += 0.1

        # 3. syslog 中的 fatal 错误
        if any(kw in all_content for kw in ["fatal", "panic", "system crash"]):
            score += 0.1

        # 4. 多个可信日志源同时出现
        unique_sources = set()
        for src in log_sources:
            src_lower = src.lower()
            if "kernel" in src_lower or "dmesg" in src_lower:
                unique_sources.add("kernel")
            elif "syslog" in src_lower:
                unique_sources.add("syslog")
            elif "logcat" in src_lower:
                unique_sources.add("logcat")

        if len(unique_sources) >= 3:
            score += 0.1  # 多源交叉验证

        return min(0.3, score)  # 最多加 0.3

    def full_analysis(self, log_content: str = None, bug_description: str = None, comments: list = None) -> Dict:
        """完整分析流程"""
        result = {
            "keywords": [],
            "root_cause": "需要进一步分析",
            "suggestion": "请提供更多日志信息",
            "similar_bugs": [],
            "log_analysis": {"error_count": 0, "warning_count": 0},
            "comments": comments or []  # 存储评论数据
        }

        if log_content:
            result["log_analysis"] = self.analyze_log(log_content)
            result["keywords"] = self.extract_keywords(log_content)

            root_cause = self.infer_root_cause(result["log_analysis"])
            if root_cause:
                result["root_cause"] = root_cause
                result["suggestion"] = "根据日志分析,建议检查相关模块"

        if bug_description:
            # 优先使用传入的 bug_description 作为搜索词
            query = bug_description
            # 如果没有提供描述，才使用日志关键词
            if not query or len(query) < 3:
                if result.get("keywords"):
                    query = " ".join(result["keywords"][:3])

            result["similar_bugs"] = self.find_similar_bugs(query)

        return result

    def extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        chinese = re.findall(r'[\u4e00-\u9fa5]{2,6}', text)
        english = re.findall(r'\b\w{4,}\b', text.lower())
        keywords.extend(chinese[:5])
        keywords.extend([w for w in english if len(w) > 4][:5])
        return list(set(keywords))[:10]

    def infer_root_cause(self, error_info: Dict) -> Optional[str]:
        """推断根因 (规则库 + LLM 增强)"""
        errors = error_info.get("errors", [])
        warnings = error_info.get("warnings", [])
        crash_sig = error_info.get("crash_signature")
        error_codes = error_info.get("error_codes", [])
        packages = error_info.get("packages", {})

        if not errors:
            return None

        # 合并所有日志内容用于匹配
        all_content = " ".join([
            e.get("content", "") for e in errors[:20]
        ] + [w.get("content", "") for w in warnings[:10]]
        ).lower()

        # 1. 优先检查崩溃签名
        if crash_sig:
            if crash_sig.get("signal") == 11:
                return "段错误 (SIGSEGV) - 内存访问违规,可能是空指针或野指针"
            elif crash_sig.get("signal") == 6:
                return "程序异常终止 (SIGABRT) - 可能是断言失败或未捕获异常"
            elif crash_sig.get("fatal"):
                return f"致命错误: {crash_sig.get('description', '未知')}"

        # 2. 检查错误码
        unique_codes = list(set(ec["code"] for ec in error_codes))
        if unique_codes:
            code = unique_codes[0]
            if "USB" in code or "CONN" in code:
                return "USB连接问题"
            elif "DISPLAY" in code or "RENDER" in code:
                return "显示/渲染模块问题"
            elif "TIMEOUT" in code:
                return "操作超时"
            elif "MEMORY" in code or "OOM" in code:
                return "内存问题"

        # 3. 使用规则库匹配
        for rule in self.ROOT_CAUSE_RULES:
            if rule["pattern"].search(all_content):
                return f"{rule['root_cause']} (匹配规则: {rule.get('suggestion', '')[:20]}...)"

        # 4. 检查 XR 模块
        xr_modules = packages.get("xr_modules", [])
        if xr_modules:
            module = xr_modules[0]
            if "display" in module:
                return "显示模块异常"
            elif "gesture" in module or "touch" in module:
                return "触控/手势模块问题"
            elif "audio" in module:
                return "音频模块问题"
            elif "network" in module or "usb" in module:
                return "连接模块问题"

        # 5. 分析首条错误
        first_error = errors[0].get("content", "").lower()

        # 崩溃类型
        for err_type, keywords in self.ERROR_PATTERNS.items():
            if keywords.search(first_error):
                cause_map = {
                    "segfault": "段错误 - 内存访问问题",
                    "anr": "主线程阻塞 - 应用无响应",
                    "oom": "内存溢出",
                    "timeout": "操作超时",
                    "connection": "网络连接失败",
                    "null_pointer": "空指针异常",
                    "assertion": "断言失败",
                    "crash": "程序崩溃",
                    "permission": "权限问题",
                    "file_not_found": "文件不存在"
                }
                return cause_map.get(err_type, f"{err_type}类型错误")

        return "需要进一步分析"


if __name__ == "__main__":
    analyzer = BugAnalyzer()
    result = analyzer.full_analysis(
        log_content="ERROR: USB connection failed\nERROR: Device not responding",
        bug_description="眼镜连接电脑黑屏"
    )
    print(f"关键词: {result['keywords']}")
    print(f"根因: {result['root_cause']}")
    print(f"相似缺陷: {len(result['similar_bugs'])}个")
    conf = analyzer.evaluate_confidence(result)
    print(f"置信度: {conf['score']} {conf['level']}")