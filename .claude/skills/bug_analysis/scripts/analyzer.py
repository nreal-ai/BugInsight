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
import tempfile
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
from config import get_openviking_config, get_llm_config, get_code_repos, get_analysis_config, load_config
from git_cloner import GitCloner
from platform_detector import detect_platform as _detect_platform, get_platform_keywords as _get_platform_keywords
from manifest_parser import get_platform_repos as _get_platform_repos
from version_extractor import extract_versions as _extract_versions, format_versions_for_prompt as _format_versions_for_prompt
from build_version_db_query import BuildVersionDB, format_version_repo_prompt
from bsp_version_query import BspVersionDB, format_bsp_version_prompt
from version_repo_mapper import VersionRepoMapper

class BugAnalyzer:
    """Bug 分析器"""

    # 初始化时从配置加载
    _config_cache = None

    def __init__(self, git_urls: list = None):
        """初始化,从配置加载参数
        
        Args:
            git_urls: Git 仓库 URL 列表。如果提供，分析时会克隆到临时目录
        """
        # 加载配置
        ov_cfg = get_openviking_config()
        llm_cfg = get_llm_config()
        repos_cfg = get_code_repos()
        analysis_cfg = get_analysis_config()

        self.OV_API_BASE = ov_cfg.get("api_base", "http://127.0.0.1:1933")
        self.OV_API_KEY = ov_cfg.get("api_key", "")
        self.OV_ACCOUNT = ov_cfg.get("account", "default")
        self.OV_USER = ov_cfg.get("user", "xreal")
        
        # OpenViking 请求头
        self.headers = {
            "Authorization": f"Bearer {self.OV_API_KEY}",
            "Content-Type": "application/json",
            "X-OpenViking-Account": self.OV_ACCOUNT,
            "X-OpenViking-User": self.OV_USER
        }

        self.LLM_API_BASE = llm_cfg.get("base_url", llm_cfg.get("api_base", "https://litellm.xreal.work/v1"))
        self.LLM_API_KEY = llm_cfg.get("api_key", os.getenv("LLM_API_KEY", ""))
        self.LLM_MODEL = llm_cfg.get("model", "qwen3.6-plus")

        # NReal 代码仓库路径
        self.CODE_REPOS = repos_cfg

        # Git URL 列表（由调用方传入）
        self.git_urls = git_urls or []
        self._git_cloner = GitCloner() if self.git_urls else None

        # 代码搜索器（延迟初始化，等克隆完成后设置路径）
        self.code_searcher = None
        self._bug_index = None  # P0.2: 倒排索引缓存

        # 分析配置
        self.SIMILAR_BUGS_LIMIT = analysis_cfg.get("similar_bugs_limit", 10)
        self.CODE_SEARCH_LIMIT = analysis_cfg.get("code_search_limit", 20)
        self.LLM_TIMEOUT = analysis_cfg.get("llm_timeout", 180)
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
    # 错误码匹配模式 —— 捕获多种格式:
    #   ERR_FOO / DOVE_ERR_BAR / NREAL_ERROR_XYZ / GL_ERR_XXX / VK_ERROR_YYY
    #   DOVE_RENDER_FAILED / VULKAN_SHADER_COMPILE_FAILED (xxx_FAILED)
    #   E_ERRCODE: 123 / error_code=42 / Failed with error=5
    #   大写的错误常量: 包含 _ERR_ / _ERROR_ / _FAILED_ / _INVALID_ 等关键词
    ERROR_CODE_PATTERN = re.compile(
        r"\b"
        r"("
        # 标准错误宏: ERR_XXX / DOVE_ERR_XXX / NREAL_ERR_XXX
        r"(?:[A-Z_]*ERR[A-Z_]*_[A-Z_]+)"
        r"|"
        # ERROR_XXX / NREAL_ERROR_XXX
        r"(?:[A-Z_]*ERROR[A-Z_]*_[A-Z_]+)"
        r"|"
        # xxx_FAILED / xxx_INVALID / xxx_NOT_FOUND (模块_操作_状态)
        r"(?:[A-Z_]+_(?:FAILED|FAILURE|INVALID|NOT_FOUND|MISMATCH|TIMEOUT))"
        r"|"
        # Failed/Exception + error code 模式
        r"(?:Error\s+Code[:\s]+\w+|error_code\s*=\s*\w+)"
        r")"
        r"(?:\s*[:=]\s*(-?\d+))?"
        r"\b"
    )

    # 4. 堆栈跟踪模式
    STACK_TRACE_PATTERNS = [
        re.compile(r'^\s+at\s+(\S+)\((.+)\)$', re.MULTILINE),  # Java/Kotlin
        re.compile(r'^\s+#\d+\s+\S+\s+in\s+\S+\s+from\s+\S+$', re.MULTILINE),  # Native
        re.compile(r'^\s+(\S+)\s+\S+\s+\[0x[0-9a-f]+\]$', re.MULTILINE),  # addr2line
    ]

    # 5. 根因推断规则库 (增强版 - 按优先级排序,细分根因)
    ROOT_CAUSE_RULES = [
        # ===== 显示模块 - 高优先级细分根因 =====
        # 刷新率切换导致黑屏 (最具体)
        {"pattern": re.compile(r"刷新率.*切换|帧速率.*提升.*黑屏|240.*黑屏|120.*240.*黑屏|帧率.*切换.*无画面|Hz.*切换.*异常", re.I),
         "root_cause": "刷新率切换时序异常导致显示管线崩溃 - 显示控制器在Hz切换时帧缓冲区状态机未正确同步，新Hz的时序参数未与EDID握手完成就切换输出，导致帧缓冲区欠载(underrun)和显示管线死锁", "suggestion": "1. 检查display driver中Hz切换的握手协议，确认frame buffer分配器在切换时是否正确释放旧Hz的buffer；2. 验证240Hz时序参数(Pixel Clock/H-Blank/V-Blank)是否符合EDID规范；3. 检查显示管线的VSync信号在切换期间是否中断", "priority": "critical"},

        {"pattern": re.compile(r"帧速率.*提升|帧率.*提升.*异常|240[Hh][Zz].*异常|刷新率.*异常|Hz.*异常", re.I),
         "root_cause": "高刷新率模式兼容性问题 - 显示驱动或固件在高Hz模式下存在未覆盖的边界条件，可能是时序参数计算错误或硬件不支持该Hz", "suggestion": "1. 检查显示驱动中高Hz模式的时序参数计算逻辑；2. 验证硬件规格是否支持目标Hz；3. 对比低Hz(60/120)模式的初始化流程差异", "priority": "high"},

        # OSD/UI残留
        {"pattern": re.compile(r"OSD.*残留|菜单.*不消失|UI.*残留|overlay.*未清除|切换.*菜单.*存在", re.I),
         "root_cause": "UI层(Overlay)在场景切换后未正确销毁 - OSD渲染线程在2D/3D状态切换时未收到清除信号，导致Overlay buffer仍叠加在最终画面上", "suggestion": "1. 检查OSD渲染生命周期管理，确认场景切换事件是否正确传递到UI层；2. 验证Overlay buffer在切换后是否被正确清除或disable；3. 检查Display Composer是否正确重置layer状态", "priority": "high"},

        # 2D/3D模式切换
        {"pattern": re.compile(r"2.*3.*切换|3.*2.*切换|2D.*3D|3D.*空间.*切换|模式.*切换.*异常", re.I),
         "root_cause": "2D/3D显示模式切换状态机异常 - 切换过程中部分渲染管线(如depth buffer、stereo rendering)未正确重置，导致渲染管线状态不一致", "suggestion": "1. 检查模式切换状态机，确认切换事件序列的完整性；2. 验证各渲染管线(3D depth buffer、stereo rendering)的重置逻辑；3. 检查切换过程中的buffer swap是否正确执行", "priority": "high"},

        # 画面撕裂
        {"pattern": re.compile(r"画面.*撕裂|tearing|撕裂.*现象|画面.*错位|帧.*不同步", re.I),
         "root_cause": "VSync同步失效 - 帧输出与垂直同步信号未对齐，display controller在frame buffer swap中间输出，导致上半帧和下半帧来自不同frame", "suggestion": "1. 启用VSync同步；2. 检查frame buffer swap链配置(双缓冲/三缓冲)；3. 验证显示控制器的VSync信号和Triple Buffer配置", "priority": "medium"},

        # 花屏
        {"pattern": re.compile(r"花屏|artifact|画面.*异常.*颜色|颜色.*异常|色块|杂色.*画面", re.I),
         "root_cause": "GPU渲染管线数据损坏 - 可能是显存越界访问、帧缓冲区指针错误或render target格式不匹配", "suggestion": "1. 检查GPU驱动显存管理，确认frame buffer地址对齐；2. 验证render target格式(RGB565/RGB888)是否与显示控制器配置一致；3. 检查渲染管线状态机", "priority": "high"},

        # EDID/分辨率协商
        {"pattern": re.compile(r"分辨率.*不支持|resolution.*not.*support|EDID.*异常|显示.*参数.*错误|HDMI.*异常|链路.*训练.*失败", re.I),
         "root_cause": "EDID参数协商失败 - 设备间分辨率/刷新率协商不一致，或HDMI/USB-C链路训练未正确完成", "suggestion": "1. 读取并验证EDID数据(EDID 1.3/2.0)；2. 检查HDMI/USB-C链路训练过程；3. 确认显示设备支持的分辨率列表(EDID detailed timing)", "priority": "medium"},

        # 通用黑屏 (兜底)
        {"pattern": re.compile(r"黑屏|无显示|无画面|屏幕.*不亮|显示.*异常|画面.*异常", re.I),
         "root_cause": "显示模块异常 - 可能是显示驱动初始化失败、显示管线配置错误、硬件连接问题或固件版本不兼容", "suggestion": "1. 检查显示驱动(dove_display)初始化日志；2. 确认显示管线(pipe/crtc)配置；3. 验证硬件连接(USB-C/DP)和固件版本兼容性", "priority": "high"},

        # ===== 音频模块 - 细分根因 =====
        {"pattern": re.compile(r"杂音.*卡死|爆音.*卡死|噪音.*卡死|无声.*杂音", re.I),
         "root_cause": "音频DMA传输中断导致爆音，同时系统级死锁 - 音频缓冲区欠载产生噪声，同时主处理线程阻塞导致系统无响应", "suggestion": "1. 检查音频DMA传输配置和buffer填充率；2. 检查内核日志中是否有线程阻塞或死锁；3. 分析系统资源使用情况", "priority": "critical"},

        {"pattern": re.compile(r"杂音|噪音|爆音|audio.*noise|sound.*distortion|音频.*异常.*噪声", re.I),
         "root_cause": "音频时钟同步异常或DMA传输中断 - 音频缓冲区欠载(underrun)导致噪声或爆音，可能是音频PLL不稳定或I2S时钟漂移", "suggestion": "1. 检查音频时钟源(PLL)稳定性；2. 验证DMA传输配置和buffer大小；3. 检查I2S/PCM接口时钟同步", "priority": "high"},

        {"pattern": re.compile(r"无声.*输出|speaker.*no.*sound|音频.*无声|静音.*异常", re.I),
         "root_cause": "音频输出路径被禁用或路由错误 - 音频数据未正确路由到物理扬声器，可能是codec mute状态或route配置错误", "suggestion": "1. 检查音频路由配置(DAPM/codec route)；2. 验证扬声器使能信号(GPIO/power)；3. 检查音频编解码器状态", "priority": "medium"},

        # ===== 系统/固件 - 细分根因 =====
        {"pattern": re.compile(r"死机|卡死|freeze|hang.*无响应|系统.*锁死|无法.*操作|类似卡死", re.I),
         "root_cause": "系统级死锁或资源耗尽 - 可能是内核线程阻塞(如等待硬件响应超时)、内存泄漏或硬件看门狗未正确喂狗", "suggestion": "1. 检查内核日志中的阻塞线程和call trace；2. 分析内存使用情况(是否有memory leak)；3. 验证看门狗配置和喂狗逻辑；4. 检查是否有IRQ storm或硬件中断未处理", "priority": "critical"},

        {"pattern": re.compile(r"重启.*自动|auto.*reboot|系统.*重启|意外.*重启", re.I),
         "root_cause": "系统异常重启 - 可能是内核panic、看门狗超时或电源管理异常触发", "suggestion": "1. 检查内核panic日志(last_kmsg/pstore)；2. 验证看门狗超时配置；3. 检查电源管理状态机", "priority": "critical"},

        {"pattern": re.compile(r"过热|overheat|thermal.*shutdown|温度.*过高|发热.*严重", re.I),
         "root_cause": "热管理失效 - SoC温度超过阈值触发降频或关机保护", "suggestion": "1. 检查散热设计；2. 验证温控阈值配置；3. 分析功耗分布", "priority": "high"},

        # ===== 连接/外设 - 细分根因 =====
        {"pattern": re.compile(r"蓝牙.*断开|bluetooth.*disconnect|BT.*异常", re.I),
         "root_cause": "蓝牙链路层连接超时 - 可能是射频干扰或蓝牙协议栈状态异常", "suggestion": "1. 检查蓝牙射频干扰；2. 验证协议栈状态；3. 检查连接间隔参数", "priority": "medium"},

        {"pattern": re.compile(r"WiFi.*断开|wifi.*drop|网络.*中断|网络.*异常", re.I),
         "root_cause": "网络连接中断 - 可能是无线驱动异常或网络栈状态不一致", "suggestion": "1. 检查无线驱动日志；2. 验证网络栈状态；3. 检查AP连接参数", "priority": "medium"},

        # ===== 手势/交互 - 细分根因 =====
        {"pattern": re.compile(r"手势.*失灵|gesture.*fail|触控.*无响应|touch.*not.*respond", re.I),
         "root_cause": "手势识别服务异常 - 可能是传感器数据中断或手势识别算法状态错误", "suggestion": "1. 检查传感器数据流；2. 验证手势识别服务状态；3. 检查校准参数", "priority": "high"},

        # ===== 电池/电源 - 细分根因 =====
        {"pattern": re.compile(r"电池.*异常|battery.*fail|充电.*异常|电源.*问题", re.I),
         "root_cause": "电源管理IC状态异常 - 可能是充电协议错误或电池保护触发", "suggestion": "1. 检查PMIC寄存器状态；2. 验证充电协议；3. 检查电池健康度", "priority": "high"},

        # ===== 其他通用兜底 =====
        {"pattern": re.compile(r"audio|sound.*fail|音频.*异常|扬声器.*无声|声音.*异常", re.I),
         "root_cause": "音频模块故障", "suggestion": "检查音频驱动、确认扬声器连接、重置音频服务", "priority": "medium"},

        {"pattern": re.compile(r"wifi|network.*fail|连接.*失败|网络.*异常|WiFi.*断开", re.I),
         "root_cause": "网络连接问题", "suggestion": "检查WiFi模块、重启网络服务、确认信号强度", "priority": "medium"},

        {"pattern": re.compile(r"bluetooth|BT.*fail|蓝牙.*异常|配对.*失败", re.I),
         "root_cause": "蓝牙连接问题", "suggestion": "检查蓝牙驱动、重新配对设备", "priority": "medium"},

        {"pattern": re.compile(r"camera.*fail|摄像头.*异常|相机.*黑屏|拍摄.*异常", re.I),
         "root_cause": "摄像头模块故障", "suggestion": "检查摄像头驱动、重启相机服务", "priority": "medium"},

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

        # 提取所有时间戳 — 支持两种格式:
        # 1. YYYY-MM-DD HH:MM:SS (ISO/xr_log/simple)
        # 2. MM-DD HH:MM:SS (logcat)
        timestamps = []

        # Pattern 1: full ISO timestamps
        for match in self.LOG_PATTERNS["timestamp"].finditer(log_content):
            ts_str = match.group(1)
            ts_str = ts_str.replace('/', '-')
            if 'T' not in ts_str:
                ts_str = ts_str.replace(' ', 'T')
            try:
                from datetime import datetime
                if '+' in ts_str or 'Z' in ts_str:
                    ts_str = ts_str[:19]
                dt = datetime.strptime(ts_str[:19], '%Y-%m-%dT%H:%M:%S')
                timestamps.append((dt, match.group(0), match.start()))
            except (ValueError, IndexError):
                pass

        # Pattern 2: logcat "MM-DD HH:MM:SS.mmm"
        if not timestamps:
            logcat_ts_pat = re.compile(r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\.\d+')
            for match in logcat_ts_pat.finditer(log_content):
                ts_str = match.group(1)
                try:
                    from datetime import datetime
                    dt = datetime.strptime(ts_str, '%m-%d %H:%M:%S')
                    # 使用固定年份，只比较月日时分秒
                    timestamps.append((dt, match.group(0), match.start()))
                except (ValueError, IndexError):
                    pass

        # 检查时间是否倒退
        for i in range(1, len(timestamps)):
            prev_ts, prev_str, prev_pos = timestamps[i-1]
            curr_ts, curr_str, curr_pos = timestamps[i]

            if curr_ts < prev_ts:
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
            except (ValueError, IndexError):
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

        # === Noise filtering: remove markdown images, @mentions, and pure URL lines ===
        # Remove Feishu HTML comment mention blocks (JSON embedded in HTML comments)
        log_content = re.sub(r'<!--\s*mention:\{[^}]*\}\s*-->', '', log_content)
        # Remove remaining empty HTML comments
        log_content = re.sub(r'<!--.*?-->', '', log_content)
        
        lines = log_content.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            # Skip pure markdown image lines
            if re.match(r'^!\[.*?\]\(.*?\)$', stripped):
                continue
            # Skip pure image URL lines
            if re.match(r'^https?://.*\.(png|jpg|jpeg|gif|webp|bmp|svg)(\?.*)?$', stripped, re.IGNORECASE):
                continue
            # Remove inline markdown images but keep the rest of the line
            line = re.sub(r'!\[.*?\]\(.*?\)', '', line)
            # Remove @mentions (but keep the rest)
            line = re.sub(r'@\w+', '', line)
            # Skip if line is now empty
            if not line.strip():
                continue
            cleaned_lines.append(line)
        
        log_content = '\n'.join(cleaned_lines)
        if not log_content.strip():
            return result
        # === End noise filtering ===

        # 提取结构化信息
        result["error_codes"] = self.extract_error_codes(log_content)
        result["stack_traces"] = self.extract_stack_traces(log_content)
        result["crash_signature"] = self.extract_crash_signature(log_content)
        result["packages"] = self.extract_package_info(log_content)

        # ========== P0.3: 单次遍历合并所有模式检测 (Shader/Native Crash/Timing/ANR) ==========
        # 原代码对同一段日志做 30+ 次 finditer/search，现合并为一次扫描
        content_lower = log_content.lower()

        # --- Shader/渲染错误 ---
        _shader_desc = {
            "shader_compile": "Shader编译错误", "vulkan_error": "Vulkan API 错误",
            "opengl_error": "OpenGL 错误", "render_pipeline": "渲染管线错误",
            "gpu_error": "GPU 硬件错误", "memory_gpu": "GPU 显存错误",
            "frame_rate": "帧率异常", "display_mode": "显示模式错误",
        }
        _shader_high = {"vulkan_error", "gpu_error", "shader_compile"}
        for error_type, pattern in self.SHADER_ERROR_PATTERNS.items():
            for match in pattern.finditer(log_content):
                result["shader_errors"]["has_shader_error"] = True
                result["shader_errors"]["error_types"].append(error_type)
                ctx = log_content[max(0, match.start() - 50):min(len(log_content), match.end() + 100)].strip()
                src = self._extract_source_from_context(ctx)
                clean_ctx = self._strip_file_prefix(ctx)[:200]
                entry = {
                    "type": error_type, "description": _shader_desc.get(error_type, error_type),
                    "context": clean_ctx, "severity": "high" if error_type in _shader_high else "medium",
                    "source_file": src['source_file'], "line_number": src['line_number'],
                }
                result["shader_errors"]["errors"].append(entry)
        render_codes = re.findall(r'\b(VK_ERROR_|GL_INVALID_|EGL_\w+)\b', log_content)
        if render_codes:
            result["shader_errors"]["has_shader_error"] = True
            result["shader_errors"]["error_codes"] = list(set(render_codes))
            for code in set(render_codes):
                result["shader_errors"]["errors"].append({
                    "type": "render_error_code", "description": self.RENDER_ERROR_CODES.get(code, "渲染错误码"),
                    "context": code, "severity": "high",
                })

        # --- Native Crash ---
        _crash_desc = {
            "sigsegv": "段错误 - 内存访问违规 (SIGSEGV)", "sigabrt": "程序异常终止 (SIGABRT)",
            "sigbus": "总线错误 (SIGBUS)", "sigfpe": "浮点异常 (SIGFPE)",
            "sigill": "非法指令 (SIGILL)", "native_crash": "Native 层崩溃",
            "tombstone": "Tombstone 记录 - native 崩溃",
            "asan_error": "AddressSanitizer 检测到内存错误",
            "native_assert": "Native 层断言失败",
        }
        _crash_fatal = {"sigsegv", "sigabrt", "tombstone", "asan_error", "native_assert"}
        for crash_type, pattern in self.NATIVE_CRASH_PATTERNS.items():
            match = pattern.search(log_content)
            if match:
                result["native_crash"]["has_native_crash"] = True
                ctx = log_content[max(0, match.start() - 100):min(len(log_content), match.end() + 200)]
                src = self._extract_source_from_context(ctx)
                clean_ctx = self._strip_file_prefix(ctx)
                fatal = crash_type in _crash_fatal
                matched_text = (match.group(0) if match.groups() else crash_type)[:100]
                ci = {
                    "crash_type": crash_type, "matched_text": matched_text,
                    "description": _crash_desc.get(crash_type, crash_type),
                    "fatal": fatal, "context": clean_ctx[:300],
                    "source_file": src['source_file'], "line_number": src['line_number'],
                }
                result["native_crash"]["crash_info"] = ci
                result["native_crash"]["errors"].append({
                    "type": crash_type, "description": _crash_desc.get(crash_type, crash_type),
                    "context": clean_ctx[:200], "severity": "critical" if fatal else "high",
                    "source_file": src['source_file'], "line_number": src['line_number'],
                })
        sig_match = re.search(r'signal\s+(\d+)', content_lower)
        if sig_match:
            sig_num = int(sig_match.group(1))
            if sig_num in self.CRASH_SIGNALS:
                result["native_crash"]["has_native_crash"] = True
                if not result["native_crash"]["crash_info"]:
                    result["native_crash"]["crash_info"] = {}
                result["native_crash"]["crash_info"]["signal"] = sig_num
                result["native_crash"]["crash_info"]["signal_name"] = self.CRASH_SIGNALS[sig_num]
        for module in self.NATIVE_MODULES:
            if module.lower() in content_lower and result["native_crash"].get("crash_info"):
                result["native_crash"]["crash_info"]["module"] = module
        if "tombstone" in content_lower or "debuggerd" in content_lower:
            result["native_crash"]["has_native_crash"] = True
            tomb_match = re.search(r'(--- *\n *pid:.*?--- *\n)', log_content, re.DOTALL)
            result["native_crash"]["tombstone"] = tomb_match.group(1)[:500] if tomb_match else "存在 tombstone 记录"
        asan_match = re.search(r'(==\d+==.*?(?:ABORTING|ABORT|LEAK|ERROR))', log_content, re.DOTALL | re.IGNORECASE)
        if asan_match:
            result["native_crash"]["has_native_crash"] = True
            result["native_crash"]["asan_report"] = asan_match.group(0)[:500]

        # --- 时序问题 ---
        _issue_desc = {
            "timeout": "超时问题", "delay": "延迟/卡顿", "deadlock": "死锁/锁等待",
            "race_condition": "竞态条件", "clock_issue": "时钟问题",
            "out_of_order": "消息乱序", "replay": "重放/重复",
            "interval_anomaly": "间隔异常", "sync_issue": "同步问题",
            "state_machine": "状态机错误",
        }
        _sev_map = {
            "deadlock": "high", "race_condition": "high", "timeout": "medium",
            "clock_issue": "medium", "sync_issue": "medium", "state_machine": "medium",
            "delay": "low", "out_of_order": "low", "replay": "low", "interval_anomaly": "low",
        }
        for issue_type, pattern in self.TIMING_ISSUE_PATTERNS.items():
            matches = list(pattern.finditer(log_content))
            if matches:
                result["timing_issues"]["has_timing_issue"] = True
                result["timing_issues"]["issue_types"].append(issue_type)
                for m in matches[:5]:
                    ctx = log_content[max(0, m.start() - 50):min(len(log_content), m.end() + 100)].strip()
                    src = self._extract_source_from_context(ctx)
                    clean_ctx = self._strip_file_prefix(ctx)[:200]
                    ts_m = self.LOG_PATTERNS["timestamp"].search(clean_ctx)
                    result["timing_issues"]["issues"].append({
                        "type": issue_type, "description": _issue_desc.get(issue_type, issue_type),
                        "context": clean_ctx, "severity": _sev_map.get(issue_type, "medium"),
                        "timestamp": ts_m.group(1) if ts_m else None,
                        "source_file": src['source_file'], "line_number": src['line_number'],
                    })
        ts_issues = self._check_timestamp_order(log_content)
        if ts_issues:
            result["timing_issues"]["has_timing_issue"] = True
            result["timing_issues"]["issues"].extend(ts_issues)
            if "timestamp_order" not in result["timing_issues"]["issue_types"]:
                result["timing_issues"]["issue_types"].append("timestamp_order")
        iv_issues = self._check_interval_anomaly(log_content)
        if iv_issues:
            result["timing_issues"]["has_timing_issue"] = True
            result["timing_issues"]["issues"].extend(iv_issues)
            if "interval_anomaly" not in result["timing_issues"]["issue_types"]:
                result["timing_issues"]["issue_types"].append("interval_anomaly")

        # --- 汇总计数 ---
        if result["shader_errors"]["has_shader_error"]:
            result["error_count"] += len(result["shader_errors"]["errors"])
            if any(e.get("severity") == "high" for e in result["shader_errors"]["errors"]):
                result["fatal_count"] += 1
        ci = result["native_crash"]["crash_info"]
        if result["native_crash"]["has_native_crash"] and ci and ci.get("fatal"):
            result["fatal_count"] += 2
        if result["timing_issues"]["has_timing_issue"]:
            result["error_count"] += len(result["timing_issues"]["issues"])
            if result["timing_issues"]["summary"].get("severity") == "high":
                result["fatal_count"] += 1

        # --- 逐行解析 (合并 ANR 检测到循环内) ---
        log_format = result["format"]
        lines = log_content.split('\n')
        warning_kws = {'warning', 'warn', 'deprecate'}
        anr_kw = ['anr', 'application not responding', 'watchdog', '主线程阻塞']
        anr_found = False

        for line in lines[:5000]:
            # Parse FILE/LINE prefix for source attribution
            line_info = self._parse_line_prefix(line)
            source_file = line_info['source_file']
            line_number = line_info['line_number']
            display_line = line.strip()[:200] if source_file is None else line_info['original_text'].strip()[:200]

            ll = display_line.lower()
            entry = self.parse_log_entry(display_line, log_format)
            level = entry.get("level", "").upper()

            # ANR 检测 (合并到逐行扫描)
            if not anr_found and any(kw in ll for kw in anr_kw):
                anr_found = True
                result["error_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", "ANR_DETECTED"), "type": "ANR",
                    "content": display_line, "parsed": entry.get("parsed", False),
                    "source_file": source_file, "line_number": line_number,
                })
                result["fatal_count"] += 1

            if level in ("WARN", "WARNING"):
                result["warning_count"] += 1
                result["warnings"].append({
                    "line": entry.get("timestamp", f"L{len(result['warnings'])+1}"),
                    "content": display_line,
                    "source_file": source_file, "line_number": line_number,
                })
            elif level == "ERROR":
                result["error_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", f"L{len(result['errors'])+1}"),
                    "type": "ERROR", "content": display_line,
                    "parsed": entry.get("parsed", False),
                    "source_file": source_file, "line_number": line_number,
                })
            elif level in ("FATAL", "ASSERT"):
                result["fatal_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", f"L{len(result['errors'])+1}"),
                    "type": "FATAL", "content": display_line,
                    "parsed": entry.get("parsed", False),
                    "source_file": source_file, "line_number": line_number,
                })
            elif any(kw in ll for kw in ['fatal', 'crash', 'segmentation fault', 'abort']):
                result["fatal_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", f"L{len(result['errors'])+1}"),
                    "type": "FATAL", "content": display_line,
                    "parsed": entry.get("parsed", False),
                    "source_file": source_file, "line_number": line_number,
                })
            elif any(kw in ll for kw in ['error', 'exception']):
                result["error_count"] += 1
                result["errors"].append({
                    "line": entry.get("timestamp", f"L{len(result['errors'])+1}"),
                    "type": "ERROR", "content": display_line,
                    "parsed": entry.get("parsed", False),
                    "source_file": source_file, "line_number": line_number,
                })
            elif any(kw in ll for kw in warning_kws):
                result["warning_count"] += 1
                result["warnings"].append({
                    "line": entry.get("timestamp", f"L{len(result['warnings'])+1}"),
                    "content": display_line,
                    "source_file": source_file, "line_number": line_number,
                })

        # ANR 兜底 (逐行未命中时全文扫描)
        if not anr_found:
            anr_m = re.search(r'(ANR|Application\s+Not\s+Responding|watchdog\s+timed?\s*out|主线程阻塞|ActivityManager.*ANR)', log_content, re.I)
            if anr_m:
                result["error_count"] += 1
                result["errors"].append({
                    "line": "ANR_DETECTED", "type": "ANR",
                    "content": f"[ANR - 应用无响应/主线程阻塞] {anr_m.group(0)[:150]}",
                    "parsed": False,
                })
                result["fatal_count"] += 1

        # 去重
        result["shader_errors"]["error_types"] = list(set(result["shader_errors"]["error_types"]))

        # 生成摘要
        ci = result["native_crash"]["crash_info"]
        result["summary"] = {
            "total_lines": len(lines),
            "analyzed_lines": min(len(lines), 5000),
            "error_rate": round(result["error_count"] / min(len(lines), 5000) * 100, 2) if lines else 0,
            "has_crash": result["crash_signature"] is not None,
            "has_stack_trace": len(result["stack_traces"]) > 0,
            "unique_error_codes": len(set(ec["code"] for ec in result["error_codes"])),
            "has_shader_error": result["shader_errors"]["has_shader_error"],
            "shader_error_types": result["shader_errors"]["error_types"],
            "has_native_crash": result["native_crash"]["has_native_crash"],
            "native_crash_types": ci["crash_type"] if ci else None,
            "has_tombstone": result["native_crash"]["tombstone"] is not None,
            "has_asan": result["native_crash"]["asan_report"] is not None,
            "has_timing_issue": result["timing_issues"]["has_timing_issue"],
            "timing_issue_types": result["timing_issues"]["issue_types"],
            "timing_issue_count": len(result["timing_issues"]["issues"]),
        }

        return result

    def analyze_zip(self, zip_path: str) -> Dict:
        """分析 ZIP 文件"""
        result = {"files": [], "logs": [], "images": [], "crash_files": [], "error": None}

        try:
            extract_dir = tempfile.mkdtemp(prefix="bug_analyzer_")

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
                    f"{extract_dir}/{log_file['name']}",
                    f"{extract_dir}/logs/{os.path.basename(log_file['name'])}"
                ]
                for lp in log_paths:
                    if os.path.exists(lp):
                        try:
                            with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                                all_log += f"\n--- {log_file['name']} ---\n" + f.read(100000)
                        except (IOError, OSError, UnicodeDecodeError):
                            pass
                        break

            result["log_analysis"] = self.analyze_log(all_log)

            # 补充完整分析链路
            if all_log.strip():
                result["keywords"] = self.extract_keywords(all_log)
                result["root_cause"] = self.infer_root_cause(result["log_analysis"]) or "需要进一步分析"
                result["suggestion"] = "根据日志分析,建议检查相关模块" if result["root_cause"] != "需要进一步分析" else "请提供更多日志信息"
                title = os.path.basename(zip_path)
                result["title"] = title
                query = title if title else " ".join(result["keywords"][:3]) if result.get("keywords") else ""
                if query and len(query) >= 2:
                    result["similar_bugs"] = self.find_similar_bugs(query)
                result["confidence"] = self.evaluate_confidence(result)

        except Exception as e:
            result["error"] = str(e)

        return result

    def _parse_line_prefix(self, line: str) -> Dict:
        """Parse '# FILE: filename | LINE: N | original text' prefix from tagged log lines.

        Returns dict with 'source_file', 'line_number', 'original_text' fields.
        If no prefix found, returns defaults.
        """
        import re
        m = re.match(r'^# FILE:\s*(.+?)\s*\|\s*LINE:\s*(\d+)\s*\|\s(.*)', line)
        if m:
            return {
                'source_file': m.group(1).strip(),
                'line_number': int(m.group(2)),
                'original_text': m.group(3),
            }
        return {
            'source_file': None,
            'line_number': None,
            'original_text': line,
        }

    def _extract_source_from_context(self, context: str) -> Dict:
        """Extract source_file/line_number from tagged text context.

        Scans context for '# FILE: ... | LINE: N |' patterns and returns
        the first match found. Also strips the prefix from the returned text.
        """
        import re
        m = re.search(r'# FILE:\s*(.+?)\s*\|\s*LINE:\s*(\d+)\s*\|', context)
        if m:
            return {
                'source_file': m.group(1).strip(),
                'line_number': int(m.group(2)),
            }
        return {'source_file': None, 'line_number': None}

    def _strip_file_prefix(self, text: str) -> str:
        """Remove '# FILE: ... | LINE: N | ' prefixes from text."""
        import re
        return re.sub(r'^# FILE:\s*.+?\s*\|\s*LINE:\s*\d+\s*\|\s*', '', text, flags=re.MULTILINE)

    def analyze_log(self, log_content: str) -> Dict:
        """分析日志 (调用增强版本)"""
        return self.analyze_log_enhanced(log_content)

    def find_similar_bugs(self, query: str, limit: int = 5) -> List[Dict]:
        """搜索相似缺陷 (优先从本地 JSON 搜索，回退到 OpenViking)"""
        # 首先尝试从本地 JSON 搜索（更可靠）
        local_results = self._search_local_bugs(query, limit)
        if local_results:
            print(f"[本地] 找到 {len(local_results)} 个相似缺陷")
            return local_results
        
        # 回退: 从 OpenViking 搜索
        print(f"[OpenViking] 搜索: {query}")
        try:
            url = f"{self.OV_API_BASE}/api/v1/search/find"
            data = {"query": query, "limit": limit}

            resp = requests.post(url, json=data, headers=self.headers, timeout=30)

            if resp.status_code == 200:
                json_resp = resp.json()
                # Check resources first (where feishu-bug data lives), then memories
                results = json_resp.get('result', {}).get('resources', [])
                if not results:
                    results = json_resp.get('result', {}).get('memories', [])
                if results:
                    bug_results = []
                    for r in results:
                        uri = r.get('uri', '')
                        # 跳过系统生成的记忆
                        if any(x in uri for x in ['memory/', '.dreams/', 'docs/']):
                            continue
                        bug_results.append({
                            "id": uri.split('/')[-1].replace('.md', '') if uri else r.get('id', ''),
                            "title": r.get('abstract', '')[:50] if r.get('abstract') else r.get('title', 'N/A')[:50],
                            "score": r.get('score', 0),
                            "source": "openviking",
                            "status": r.get('category', 'UNKNOWN'),
                            "comments": []
                        })
                        if len(bug_results) >= limit:
                            break
                    if bug_results:
                        return bug_results
        except Exception as e:
            print(f"OpenViking 搜索失败: {e}")

        return []

    def _search_local_bugs(self, query: str, limit: int = 5) -> List[Dict]:
        """从本地 JSON 文件搜索缺陷 (增强评分: TF-IDF 式打分 + 排序优化)"""
        import json
        import re

        if not hasattr(self, '_bug_index') or self._bug_index is None:
            self._build_bug_index()

        if not self._bug_index:
            return []

        index = self._bug_index
        query_lower = query.lower()
        
        # Extract query tokens: English words (2+ chars) + Chinese substrings (2+ chars)
        space_parts = query_lower.split()
        query_words = [w for w in space_parts if len(w) >= 2]
        
        # Extract Chinese character groups and their n-gram combinations
        chinese_chars = re.findall(r'[\u4e00-\u9fa5]', query_lower)
        if chinese_chars:
            query_str = ''.join(chinese_chars)
            for i in range(len(query_str)):
                for j in range(i + 2, min(i + 5, len(query_str) + 1)):
                    sub = query_str[i:j]
                    if sub not in query_words:
                        query_words.append(sub)
        
        # Precompute document frequencies for IDF-like weighting
        doc_freq = {}
        total_docs = len(index)
        for word in query_words:
            count = sum(1 for e in index.values() if word in e['search_text'])
            doc_freq[word] = count

        # Score each document with TF-IDF-like approach
        matched_ids = set()
        scores = {}
        for bug_id, entry in index.items():
            text = entry['search_text']
            name_lower = entry.get('name_lower', '')
            desc_lower = entry.get('desc_lower', '')
            score = 0.0

            # 1. Exact phrase match (highest priority) - 5x boost
            if query_lower in text:
                score += 5.0
            if query_lower in name_lower:
                score += 4.0  # Title match is very relevant

            # 2. TF-IDF-like word/substring scoring
            text_len = max(len(text), 1)
            for word in query_words:
                # Count occurrences in text (term frequency)
                tf = text.count(word)
                if tf > 0:
                    # IDF-like: rarer words get higher weight
                    df = doc_freq.get(word, total_docs)
                    idf = (total_docs / max(df, 1))
                    word_score = tf * (0.5 + min(len(word) * 0.15, 0.8)) * idf
                    
                    # Title occurrence gets extra boost
                    title_tf = name_lower.count(word)
                    word_score += title_tf * 2.0
                    
                    # Description-only match gets moderate boost
                    desc_tf = desc_lower.count(word) if desc_lower else 0
                    word_score += desc_tf * 0.5
                    
                    score += word_score

            # 3. Consecutive match bonus
            # Reward documents where multiple query words appear close together
            word_positions = []
            for word in query_words:
                pos = 0
                while True:
                    pos = text.find(word, pos)
                    if pos == -1:
                        break
                    word_positions.append(pos)
                    pos += 1
            
            if len(word_positions) >= 2:
                word_positions.sort()
                # Find minimum gap between any two query words
                min_gap = min(b - a for a, b in zip(word_positions, word_positions[1:]))
                if min_gap < 50:  # Within 50 characters = high relevance
                    score += 2.0 * (1 - min_gap / 50)

            if score > 0:
                matched_ids.add(bug_id)
                scores[bug_id] = score

        if not matched_ids:
            return []

        # Normalize scores to [0, 1] range
        max_score = max(scores.values())
        if max_score > 0:
            for bid in scores:
                scores[bid] = round(scores[bid] / max_score, 4)

        # Sort by score descending
        sorted_bugs = sorted(scores.keys(), key=lambda bid: scores[bid], reverse=True)
        results = []
        for bug_id in sorted_bugs[:limit]:
            entry = index[bug_id]
            results.append({
                "id": bug_id,
                "title": entry['name'],
                "score": scores[bug_id],
                "status": entry.get('status', 'UNKNOWN'),
                "comments": entry.get('comments', []),
                "attachments": entry.get('attachments', {}),
            })
        return results

    def _build_bug_index(self) -> None:
        """构建倒排索引 (延迟加载，支持磁盘缓存避免重复构建)"""
        import json
        import hashlib
        import time

        # Check cache first (version 2 or 3: includes comments and attachments)
        cache_path = Path.home() / ".openviking/workspace/feishu-bugs/.bug_index_cache.json"
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                if cache.get('version') in ('2', '3') and cache.get('count', 0) > 0:
                    self._bug_index = cache.get('index', {})
                    print(f"[倒排索引] 从缓存加载: {len(self._bug_index)} 条缺陷 (instant)")
                    return
            except Exception:
                pass  # Cache corrupted, rebuild

        # Pre-load enrichment data (comments and attachments)
        enrichment_data = {}
        data_root = Path.home() / ".openviking/workspace/feishu-bugs"
        
        # Load comments if available
        comments_file = data_root / "batch/bugs_comments.json"
        if comments_file.exists():
            try:
                with open(comments_file, 'r', encoding='utf-8') as f:
                    all_comments = json.load(f)
                for bug_id, comments in all_comments.items():
                    if bug_id not in enrichment_data:
                        enrichment_data[bug_id] = {}
                    enrichment_data[bug_id]['comments'] = comments
            except Exception:
                pass
        
        # Load attachment metadata if available
        attachments_file = data_root / "batch/bugs_attachments.json"
        if attachments_file.exists():
            try:
                with open(attachments_file, 'r', encoding='utf-8') as f:
                    all_attachments = json.load(f)
                for entry in all_attachments:
                    bug_id = str(entry.get("id", ""))
                    atts = entry.get("attachments", [])
                    if atts:
                        if bug_id not in enrichment_data:
                            enrichment_data[bug_id] = {}
                        # Extract log file info
                        log_files = []
                        for att in atts:
                            name = att.get("name", "")
                            ext = name.split(".")[-1].lower() if "." in name else ""
                            if ext in ("txt", "log", "cat", "csv", "json", "xml", "yaml", "yml") or "log" in name.lower() or "串口" in name:
                                log_files.append({"name": name, "size": att.get("size", "")})
                        enrichment_data[bug_id]['attachments'] = {
                            "has_log": len(log_files) > 0,
                            "log_count": len(log_files),
                            "log_names": [f["name"] for f in log_files[:5]],
                        }
            except Exception:
                pass
        
        print(f"[倒排索引] 加载辅助数据: {len(enrichment_data)} 条 (comments + attachments)")

        self._bug_index = {}
        possible_paths = [
            Path.home() / ".openviking/workspace/feishu-bugs/batch",
            Path("/home/xreal/.openviking/workspace/feishu-bugs/batch"),
        ]
        bug_files = ["bugs_details_full.json", "bugs_index_full.json", "bugs_full_all.json", "bugs_all_with_details.json", "bugs_index.json"]

        for search_path in possible_paths:
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
                    for bug in bugs:
                        bug_id = bug.get('id', '')
                        if not bug_id:
                            # bugs_details_full.json has ID nested in work_item_attribute
                            attrs = bug.get('work_item_attribute', {})
                            bug_id = attrs.get('work_item_id', '')
                        if not bug_id or bug_id in self._bug_index:
                            continue
                        # Handle nested Feishu schema
                        if 'work_item_attribute' in bug:
                            attrs = bug['work_item_attribute']
                        elif 'detail' in bug and isinstance(bug['detail'], dict):
                            attrs = bug['detail'].get('work_item_attribute', bug['detail'])
                        else:
                            attrs = bug
                        
                        name = attrs.get('work_item_name', attrs.get('name', bug.get('title', '')))
                        
                        # Description: Direct API stores it in fields list
                        fields = attrs.get('fields', [])
                        fields_dict = {}
                        for f in fields:
                            if 'field_key' in f:
                                fields_dict[f['field_key']] = f.get('field_value', '')
                        desc = fields_dict.get('description', '')
                        if not desc and isinstance(attrs.get('description'), str):
                            desc = attrs.get('description', '')
                        
                        status = attrs.get('work_item_status', {}).get('label', '')
                        if not status:
                            status = fields_dict.get('status', {}).get('label', '')
                        
                        # Build summary from fields
                        summary_parts = []
                        for fk in ['severity', 'priority', 'iteration', 'module']:
                            fv = fields_dict.get(fk, '')
                            if isinstance(fv, dict):
                                fv = fv.get('label', '')
                            if fv:
                                summary_parts.append(fv)
                        summary = " ".join(summary_parts)
                        
                        # Get enrichment data for this bug
                        bug_enrichment = enrichment_data.get(bug_id, {})
                        comments_data = bug.get('comments', [])
                        if not comments_data and 'detail' in bug:
                            comments_data = bug['detail'].get('comments', [])
                        # Merge with pre-loaded comments if source data doesn't have them
                        if not comments_data and 'comments' in bug_enrichment:
                            comments_data = bug_enrichment['comments']
                        
                        comments_text = " ".join([
                            c.get('content', c.get('text', '')) if isinstance(c, dict) else str(c)
                            for c in comments_data[:5]
                            if isinstance(c, (dict, str))
                        ])
                        
                        # Build attachment info for cache
                        attachments_info = bug_enrichment.get('attachments', {})
                        
                        search_text = f"{name} {desc} {summary} {comments_text}"
                        if attachments_info.get('has_log'):
                            search_text += " " + " ".join(attachments_info.get('log_names', []))
                        
                        entry = {
                            'name': name[:80],
                            'name_lower': name.lower(),
                            'desc_lower': desc.lower()[:500],  # For TF-IDF scoring
                            'status': status or 'UNKNOWN',
                            'search_text': search_text.lower(),
                        }
                        
                        # Include comments in cache if available
                        if comments_data:
                            entry['comments'] = [
                                {
                                    'content': c.get('content', c.get('text', '')) if isinstance(c, dict) else str(c),
                                    'created_at': c.get('created_at', ''),
                                }
                                for c in comments_data[:10]
                                if isinstance(c, dict) and c.get('content', c.get('text', ''))
                            ]
                        
                        # Include attachment metadata
                        if attachments_info:
                            entry['attachments'] = attachments_info
                        
                        self._bug_index[bug_id] = entry
                    if self._bug_index:
                        print(f"[倒排索引] 构建完成: {len(self._bug_index)} 条缺陷")
                        # Save cache (version 3: includes comments and attachments)
                        try:
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            with open(cache_path, 'w', encoding='utf-8') as f:
                                json.dump({
                                    'version': '3',
                                    'count': len(self._bug_index),
                                    'index': self._bug_index,
                                }, f, ensure_ascii=False)
                        except Exception as e:
                            print(f"[缓存] 保存失败: {e}")
                        return
                except Exception as e:
                    print(f"读取 {bf} 失败: {e}")
                    continue
        print("[倒排索引] 未找到缺陷数据文件")

    def call_llm(self, prompt: str, system_prompt: str = None, max_retries: int = 1) -> str:
        """调用 LLM (使用流式请求避免超时)，支持重试

        使用 stream=True 模式，每 chunk 超时独立计算，避免大 prompt
        导致整体超时。connect=15s, read=300s per chunk.
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.LLM_API_KEY}",
            "Content-Type": "application/json"
        }

        # LLM 输出 tokens 上限（可通过环境变量 BUG_ANALYZER_LLM_MAX_TOKENS 覆盖）
        llm_max_tokens = int(os.environ.get("BUG_ANALYZER_LLM_MAX_TOKENS", "8192"))

        data = {
            "model": self.LLM_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": llm_max_tokens,
            "stream": True  # 流式请求避免整体超时
        }

        last_error = ""
        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.LLM_API_BASE}/chat/completions",
                    headers=headers,
                    json=data,
                    stream=True,
                    timeout=(15, 300)  # (connect, read) per chunk
                )
                resp.raise_for_status()

                # 解析 SSE 流
                content = ""
                for line in resp.iter_lines(decode_unicode=True):
                    if line.startswith("data: "):
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if token:
                                content += token
                        except json.JSONDecodeError:
                            continue

                if content:
                    return content
                return f"LLM 返回空内容"

            except requests.exceptions.Timeout:
                last_error = f"LLM 调用超时 (>300s/chunk)"
            except requests.exceptions.HTTPError as e:
                status = e.response.status_code
                if 400 <= status < 500:
                    return f"LLM HTTP {status}: {e.response.text[:200]}"
                last_error = f"LLM HTTP {status}"
            except requests.exceptions.ConnectionError:
                last_error = "LLM 连接失败"
            except Exception as e:
                last_error = f"LLM 调用失败: {str(e)}"

            if attempt < max_retries:
                import time
                wait = 2 ** attempt
                time.sleep(wait)

        return f"LLM 调用失败 (重试 {max_retries} 次): {last_error}"

    def _build_llm_prompt(self, analysis_result: Dict, round_num: int = 1,
                          previous_analysis: str = "", missing_info: str = "",
                          round_context: str = "") -> str:
        """构建 LLM 分析 prompt（提取为独立方法，支持多轮复用）
        
        Args:
            analysis_result: 分析结果字典
            round_num: 当前轮次（1=观察员, 2=调查员, 3=裁判）
            previous_analysis: 上一轮分析结果（供后续轮次参考）
            missing_info: 缺失信息列表（供调查员针对性查找）
            round_context: 本轮角色上下文说明
        """

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

        # Prompt 总大小预算控制（约 6000 token = ~24000 字符）
        # 每个 section 有独立预算，超出则截断
        BUDGET = {
            "error_codes_total": 600,       # 所有错误码合计字符上限
            "errors_total": 2000,           # 所有错误消息合计上限
            "warnings_total": 800,          # 所有警告合计上限
            "stack_traces_total": 3000,     # 所有堆栈合计上限
            "code_context_total": 4000,     # 代码上下文上限
            "comments_total": 1000,         # 评论合计上限
            "native_crash_total": 1200,     # native crash 上限
            "timing_total": 600,            # 时序问题上限
            "shader_total": 600,            # shader 错误上限
        }

        # 按优先级分配：崩溃签名和堆栈最重要，其次错误码，最后补充信息
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

            # Fallback: 当没有错误/日志关键词时，从分析结果中的 bug 信息提取
            if not search_keywords:
                bug_info = analysis_result.get("bug_info", {})
                fallback_text = bug_info.get("title", "") + " " + bug_info.get("description", "")
                if fallback_text:
                    # 1. 括号中的英文术语（如 OSD, 2D, 3D, HDMI, USB）
                    bracket_terms = re.findall(r'[（(]\s*([A-Za-z0-9]+)\s*[）)]', fallback_text)
                    search_keywords.extend(bracket_terms)
                    # 2. 英文单词（4+ 字符）
                    eng_words = re.findall(r'\b[A-Za-z]{4,}\b', fallback_text)
                    search_keywords.extend(eng_words)
                    # 3. 关键中文名词：使用预定义的 XR 设备功能词表
                    domain_terms = [
                        '音效', '音频', '声音', '音乐', '电影', '游戏', '标准', '模式',
                        '菜单', '选项', '设置', '显示', '屏幕', '画面', '亮度', '对比',
                        '蓝牙', 'WiFi', '网络', '连接', '断开', '配对',
                        '电池', '充电', '发热', '温度',
                        '摄像头', '追踪', '手势', '眼动',
                        '麦克风', '扬声器', '耳机', '漏音',
                        '重启', '死机', '卡顿', '崩溃', '黑屏', '花屏',
                        '固件', '升级', '版本',
                        '视频', '播放', '暂停', '录制',
                        'USB', 'TypeC', 'HDMI', 'DP',
                        '2D', '3D', 'OSD',
                    ]
                    for term in domain_terms:
                        if term in fallback_text:
                            search_keywords.append(term)
                    # 4. 从标题中【】括号提取模块标签
                    module_tags = re.findall(r'【([^】]{2,6})】', fallback_text)
                    search_keywords.extend(module_tags)
                    # 去重并限制
                    search_keywords = list(set(search_keywords))[:8]

            # 执行代码搜索 - 使用平台检测后的仓库子集
            if search_keywords:
                code_results = []
                seen = set()

                # ===== 新增: 版本感知的代码检出 (方案1: git checkout 到对应 commit) =====
                # 从 build_version_mapping 提取每个 repo 的 commit SHA，
                # 在搜索前 checkout 到对应 commit，确保代码与 log 版本一致。
                repo_commits = {}  # {local_repo_name: commit_sha}
                version_mappings = analysis_result.get("build_version_mapping", {}).get("repo_mappings", [])
                if version_mappings:
                    for mapping in version_mappings:
                        for repo_info in mapping.get("repos", []):
                            repo_name = repo_info.get("name", "")
                            revision = repo_info.get("revision", "")
                            is_commit = repo_info.get("is_commit", False)
                            if is_commit and len(revision) >= 8:
                                # manifest name 格式: "nreal-ai/dove" -> local dir: "nreal-dove" (nreal-code/ 统一命名)
                                local_name_raw = repo_name.split("/")[-1] if "/" in repo_name else repo_name
                                local_name = f"nreal-{local_name_raw}" if local_name_raw != "nrealUtil" else "nrealUtil"
                                repo_commits[local_name] = revision
                    if repo_commits:
                        print(f"[analyzer] 版本感知检出: {len(repo_commits)} 个仓库将 checkout 到对应 commit")

                # 保存当前 HEAD，搜索后恢复
                original_refs = {}
                checked_out_repos = set()
                CLONES_ROOT = self.code_searcher.CODE_ROOT if hasattr(self.code_searcher, 'CODE_ROOT') else os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))), "nreal-code")

                def _get_remote_default_branch(repo_path: str) -> str:
                    try:
                        out = subprocess.run(
                            ["git", "ls-remote", "--symref", "origin", "HEAD"],
                            capture_output=True, text=True, cwd=repo_path, timeout=10
                        ).stdout.strip()
                        for line in out.split('\n'):
                            if line.startswith('ref: refs/heads/'):
                                return line.split('refs/heads/')[1].split('\t')[0]
                    except Exception:
                        pass
                    return "master"

                def _checkout_to_commit(repo_name: str, commit: str) -> bool:
                    repo_path = os.path.join(CLONES_ROOT, repo_name)
                    if not os.path.isdir(os.path.join(repo_path, ".git")):
                        return False
                    try:
                        # 记录当前 HEAD/branch
                        r = subprocess.run(
                            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True, timeout=5, cwd=repo_path
                        )
                        current_ref = r.stdout.strip() if r.returncode == 0 else "HEAD"
                        original_refs[repo_name] = current_ref

                        # 检查 commit 是否存在于本地
                        r = subprocess.run(
                            ["git", "cat-file", "-t", commit],
                            capture_output=True, text=True, timeout=5, cwd=repo_path
                        )
                        if r.returncode != 0:
                            # commit 不在本地 shallow clone 中，尝试 deepen
                            default_branch = _get_remote_default_branch(repo_path)
                            print(f"[analyzer]   {repo_name}: commit {commit[:8]} 不在本地，尝试 fetch --deepen=500 {default_branch}...")
                            subprocess.run(
                                ["git", "fetch", "--deepen=500", "origin", default_branch],
                                capture_output=True, text=True, timeout=120, cwd=repo_path
                            )
                            # 再次检查
                            r = subprocess.run(
                                ["git", "cat-file", "-t", commit],
                                capture_output=True, text=True, timeout=5, cwd=repo_path
                            )
                            if r.returncode != 0:
                                # 再尝试 deepen 到 2000
                                subprocess.run(
                                    ["git", "fetch", "--deepen=2000", "origin", default_branch],
                                    capture_output=True, text=True, timeout=120, cwd=repo_path
                                )
                                r = subprocess.run(
                                    ["git", "cat-file", "-t", commit],
                                    capture_output=True, text=True, timeout=5, cwd=repo_path
                                )
                                if r.returncode != 0:
                                    print(f"[analyzer]   {repo_name}: deepen 后仍找不到 {commit[:8]}，跳过 checkout（搜索当前分支）")
                                    return False

                        # Checkout (detached HEAD)
                        subprocess.run(
                            ["git", "checkout", "--detach", commit],
                            capture_output=True, text=True, timeout=10, cwd=repo_path
                        )
                        print(f"[analyzer]   {repo_name}: 已 checkout 到 {commit[:8]}")
                        checked_out_repos.add(repo_name)
                        return True
                    except Exception as e:
                        print(f"[analyzer]   {repo_name}: checkout 失败: {e}")
                        return False

                def _restore_original_refs():
                    for repo_name, ref in original_refs.items():
                        repo_path = os.path.join(CLONES_ROOT, repo_name)
                        if not os.path.isdir(os.path.join(repo_path, ".git")):
                            continue
                        try:
                            if ref in ("HEAD",):
                                # 之前是 detached HEAD 或无分支状态，尝试恢复 origin/HEAD
                                for candidate in ["origin/HEAD", "origin/master", "origin/main", "origin/develop"]:
                                    r = subprocess.run(
                                        ["git", "rev-parse", "--verify", candidate],
                                        capture_output=True, text=True, timeout=5, cwd=repo_path
                                    )
                                    if r.returncode == 0:
                                        subprocess.run(
                                            ["git", "checkout", candidate],
                                            capture_output=True, text=True, timeout=10, cwd=repo_path
                                        )
                                        print(f"[analyzer]   {repo_name}: 已恢复 {candidate}")
                                        break
                                else:
                                    print(f"[analyzer]   {repo_name}: 无法自动恢复原始 HEAD")
                            else:
                                subprocess.run(
                                    ["git", "checkout", ref],
                                    capture_output=True, text=True, timeout=10, cwd=repo_path
                                )
                                print(f"[analyzer]   {repo_name}: 已恢复 {ref}")
                        except Exception:
                            pass

                # 执行 checkout
                for repo_name, commit in repo_commits.items():
                    _checkout_to_commit(repo_name, commit)

                # 根据日志类型筛选仓库，减少无用代码
                log_content_for_types = analysis_result.get("_raw_log_content", "")
                log_types = self._classify_log_types(log_content_for_types) if log_content_for_types else []
                target_repos = self._get_repos_for_log_types(log_types) if log_types else getattr(self, '_platform_repos', None) or ["dove", "framework", "leopard", "sparrow", "project"]

                if log_types:
                    print(f"[analyzer] 日志类型: {log_types}，目标仓库: {target_repos}")

                for kw in search_keywords:
                    for repo in target_repos:
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

                # 搜索完成，恢复各仓库原始分支/HEAD
                if checked_out_repos:
                    _restore_original_refs()
                    print(f"[analyzer] 已恢复 {len(checked_out_repos)} 个仓库的原始分支")

                # 构建增强的代码上下文
                if code_results:
                    # 标注代码版本信息
                    version_label = ""
                    if repo_commits:
                        checked_out_info = []
                        for rn, cs in repo_commits.items():
                            if rn in checked_out_repos:
                                checked_out_info.append(f"{rn}={cs[:8]}")
                            else:
                                checked_out_info.append(f"{rn}=当前分支(浅克隆缺commit)")
                        version_label = f"（代码版本: {', '.join(checked_out_info)}）"
                    code_context += f"\n## 代码上下文 (参考){version_label}\n"
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

        # ===== 新增: 平台检测信息 =====
        plat_info = analysis_result.get("platform_detection", {})
        if plat_info.get("platform"):
            plat = plat_info["platform"]
            conf = plat_info.get("confidence", 0)
            plat_label = "眼镜端 (Glasses)" if plat == "glasses" else "主机端 (Host)" if plat == "host" else "未知"
            prompt_parts.append(f"## 平台检测\n- 判定平台: {plat_label} (置信度: {conf:.2f})")
            
            # 注入平台对应的仓库列表
            plat_repos = getattr(self, '_platform_repos', [])
            if plat_repos:
                prompt_parts.append(f"- 目标仓库: {', '.join(plat_repos)}")
            
            # 注入日志分类信息
            raw_log = analysis_result.get("_raw_log_content", "")
            log_types = self._classify_log_types(raw_log) if raw_log else []
            if log_types:
                prompt_parts.append(f"- 日志类型: {', '.join(log_types)}")
            
            prompt_parts.append("")

        # ===== 新增: 版本信息 =====
        versions_prompt = _format_versions_for_prompt(analysis_result.get("versions", {}))
        if versions_prompt:
            prompt_parts.append(versions_prompt)

        # ===== 新增: 飞书构建版本-仓库映射 =====
        build_version_mapping = analysis_result.get("build_version_mapping", {})
        if build_version_mapping and build_version_mapping.get('repo_mappings'):
            build_version_prompt = format_version_repo_prompt(build_version_mapping)
            if build_version_prompt:
                prompt_parts.append(build_version_prompt)

        # ===== BSP 固件版本信息 =====
        bsp_version_info = analysis_result.get("bsp_version_info", {})
        if bsp_version_info and bsp_version_info.get('tag_info'):
            bsp_prompt = format_bsp_version_prompt(bsp_version_info)
            if bsp_prompt:
                prompt_parts.append(bsp_prompt)

        # Bug 基本信息（精简版）
        bug_info = analysis_result.get("bug_info", {})
        if bug_info:
            parts = []
            for key in ["title", "description", "status"]:
                val = bug_info.get(key, "")
                if val:
                    label = {"title": "标题", "description": "描述", "status": "状态"}.get(key, key)
                    parts.append(f"- {label}: {val[:300]}")
            # 用户提交的原始日志摘要
            raw_log = bug_info.get("log_content", "")
            if raw_log and raw_log.strip():
                parts.append(f"- 用户提交日志: {raw_log[:300]}")
            if parts:
                prompt_parts.append("## Bug 基本信息")
                prompt_parts.extend(parts)

        # 关键词提取
        keywords = analysis_result.get("keywords", [])
        if keywords:
            prompt_parts.append(f"\n## 关键词\n{', '.join(keywords[:10])}")

        # 错误摘要（增强版 - 包含 summary 完整字段）
        prompt_parts.append("\n## 错误摘要")
        prompt_parts.append(f"- 错误数: {log_analysis.get('error_count', 0)}")
        prompt_parts.append(f"- 警告数: {log_analysis.get('warning_count', 0)}")
        prompt_parts.append(f"- 致命错误: {log_analysis.get('fatal_count', 0)}")
        
        # 注入 summary 中的关键标志位
        summary = log_analysis.get("summary", {})
        if summary:
            flags = []
            for k in ["has_crash", "has_native_crash", "has_tombstone", "has_asan",
                      "has_stack_trace", "has_shader_error", "has_timing_issue"]:
                if summary.get(k):
                    flags.append(k.replace("has_", ""))
            if flags:
                prompt_parts.append(f"- 检测标志: {', '.join(flags)}")
            if summary.get("error_rate"):
                prompt_parts.append(f"- 错误率: {summary['error_rate']:.1f}%")
            if summary.get("analyzed_lines"):
                prompt_parts.append(f"- 分析行数: {summary['analyzed_lines']:,}")
        
        if crash_sig:
            prompt_parts.append(f"- 崩溃签名: {crash_sig}")
        
        # 日志格式
        log_format = log_analysis.get("format")
        if log_format:
            prompt_parts.append(f"- 日志格式: {log_format}")

        # 置信度细分 (让 LLM 了解各维度可靠性)
        confidence = analysis_result.get("confidence", {})
        conf_details = confidence.get("details", {})
        if conf_details:
            # 选取差异最大的几个维度注入
            key_dims = {
                "log_completeness": "日志完整度",
                "stack_quality": "堆栈质量",
                "error_clarity": "错误明确度",
                "similarity_match": "相似匹配",
                "root_cause_certainty": "根因确信度",
            }
            dim_parts = []
            for k, label in key_dims.items():
                if k in conf_details:
                    dim_parts.append(f"{label}: {conf_details[k]:.2f}")
            if dim_parts:
                prompt_parts.append(f"- 置信度细分: {', '.join(dim_parts)}")

        # 数据完整性 (飞书附件下载统计)
        feishu_evidence = analysis_result.get("feishu_evidence", {})
        download_result = feishu_evidence.get("download_result")
        if download_result:
            total = download_result.get("total_found", 0)
            dl = download_result.get("downloaded_count", 0)
            failed = download_result.get("failed_count", 0)
            skipped = download_result.get("skipped_count", 0)
            if total > 0:
                prompt_parts.append(f"- 数据覆盖: {total} 个附件 ({dl} 已下载, {failed} 失败, {skipped} 跳过)")

        # 附件可用性详情
        log_attachments = feishu_evidence.get("log_attachments", [])
        if log_attachments:
            available = [a["name"] for a in log_attachments if a.get("available")]
            unavailable = [a["name"] for a in log_attachments if not a.get("available")]
            if available or unavailable:
                parts = []
                if available:
                    parts.append(f"可用: {', '.join(available)}")
                if unavailable:
                    parts.append(f"不可用: {', '.join(unavailable)}")
                prompt_parts.append(f"- 附件状态: {'; '.join(parts)}")

        # 错误码(更多)
        if error_codes:
            prompt_parts.append("\n## 错误码")
            budget_left = BUDGET["error_codes_total"]
            for ec in error_codes:
                line = f"- {ec['code']}: {ec.get('context', '')[:80]}"
                if len(line) > budget_left:
                    line = line[:budget_left] + "..."
                    prompt_parts.append(line)
                    break
                prompt_parts.append(line)
                budget_left -= len(line)

        # 主要错误(按重要性排序，有堆栈的优先)
        if errors:
            prompt_parts.append("\n## 主要错误 (按时间顺序)")
            budget_left = BUDGET["errors_total"]
            # 优先展示含关键词的错误
            priority_keywords = ["crash", "SIG", "Fatal", "Exception", "assertion", "null"]
            scored = []
            for e in errors[:15]:
                content = e.get('content', '')
                score = 1 if any(kw.lower() in content.lower() for kw in priority_keywords) else 0
                scored.append((score, e))
            scored.sort(key=lambda x: -x[0])
            for score, e in scored:
                line = f"[{e.get('type', 'E')}] {e.get('content', '')[:150]}"
                if len(line) > budget_left:
                    break
                prompt_parts.append(line)
                budget_left -= len(line)

        # 警告(更多)
        if warnings:
            prompt_parts.append("\n## 警告信息")
            budget_left = BUDGET["warnings_total"]
            for w in warnings[:8]:
                line = f"- {w.get('content', '')[:120]}"
                if len(line) > budget_left:
                    break
                prompt_parts.append(line)
                budget_left -= len(line)

        # 堆栈跟踪(更多)
        if stack_traces:
            prompt_parts.append("\n## 堆栈跟踪")
            budget_left = BUDGET["stack_traces_total"]
            for st in stack_traces:
                truncated_content = st.get('content', '')[:400]
                entry = f"```\n{truncated_content}\n```"
                if len(entry) > budget_left:
                    # 截断最后一个堆栈
                    entry = entry[:budget_left] + "\n...[truncated]```"
                    prompt_parts.append(entry)
                    break
                prompt_parts.append(entry)
                budget_left -= len(entry)

        # 已有推断
        if analysis_result.get("root_cause"):
            prompt_parts.append(f"\n## 已有推断\n{analysis_result['root_cause']}")

        # ========== 新增: 增强检测数据 ==========
        if log_analysis.get("native_crash", {}).get("has_native_crash"):
            nc = log_analysis["native_crash"]
            prompt_parts.append("\n## Native Crash 检测")
            budget_left = BUDGET["native_crash_total"]
            ci = nc.get("crash_info", {})
            if ci:
                lines = [
                    f"- 类型: {ci.get('crash_type', 'N/A')}",
                    f"- 描述: {ci.get('description', 'N/A')}",
                ]
                if ci.get("module"):
                    lines.append(f"- 模块: {ci['module']}")
                lines.append(f"- 上下文: {ci.get('context', '')[:200]}")
                for line in lines:
                    if len(line) > budget_left:
                        break
                    prompt_parts.append(line)
                    budget_left -= len(line)
            if nc.get("tombstone"):
                t = f"- Tombstone: {nc['tombstone'][:200]}"
                if len(t) <= budget_left:
                    prompt_parts.append(t)
                    budget_left -= len(t)
            if nc.get("asan_report"):
                a = f"- ASAN 报告: {nc['asan_report'][:200]}"
                if len(a) <= budget_left:
                    prompt_parts.append(a)
                    budget_left -= len(a)

        if log_analysis.get("timing_issues", {}).get("has_timing_issue"):
            ti = log_analysis["timing_issues"]
            prompt_parts.append("\n## 时序问题检测")
            budget_left = BUDGET["timing_total"]
            line_types = f"- 类型: {', '.join(ti.get('issue_types', []))}"
            prompt_parts.append(line_types)
            budget_left -= len(line_types)
            for issue in ti.get("issues", [])[:3]:
                line = f"- {issue.get('type', '')}: {issue.get('description', '')[:100]}"
                if len(line) > budget_left:
                    break
                prompt_parts.append(line)
                budget_left -= len(line)

        if log_analysis.get("shader_errors", {}).get("has_shader_error"):
            se = log_analysis["shader_errors"]
            prompt_parts.append("\n## Shader/渲染错误检测")
            budget_left = BUDGET["shader_total"]
            line_types = f"- 类型: {', '.join(se.get('error_types', []))}"
            prompt_parts.append(line_types)
            budget_left -= len(line_types)
            for err in se.get("errors", [])[:3]:
                line = f"- {err.get('type', '')}: {err.get('description', '')[:100]}"
                if len(line) > budget_left:
                    break
                prompt_parts.append(line)
                budget_left -= len(line)

        # 飞书已知信息（精简版：仅附件列表，评论内容已合并到"飞书技术证据"中）
        comments = analysis_result.get("comments", [])
        attachments = analysis_result.get("attachments", {})
        if attachments and attachments.get("has_log"):
            prompt_parts.append("\n## 飞书已知信息")
            prompt_parts.append(f"- 日志附件 ({attachments.get('log_count', 0)} 个): {', '.join(attachments.get('log_names', [])[:5])}")
        
        if comments:
            # 仅注入结论性评论（最终解决方案、关键发现），其余交给技术证据部分
            conclusion_comments = []
            for c in comments:
                ctext = c.get("content", "")
                if not ctext or ctext.strip() in ['[图片]', '[]']:
                    continue
                # 只保留包含解决方案或最终结论的评论
                if any(kw in ctext for kw in ["问题已解决", "问题已重新修改", "修复", "commit", "Commit",
                        "修改方案", "修改内容", "修改函数"]):
                    created = c.get("created_at", "")
                    conclusion_comments.append(f"- [{created}] {ctext[:400]}")
            if conclusion_comments:
                prompt_parts.append("\n## 飞书结论性信息")
                prompt_parts.append("### 解决方案/最终结论:")
                for line in conclusion_comments[:3]:
                    prompt_parts.append(line)

        # 代码上下文
        if code_context:
            truncated_ctx = code_context[:BUDGET["code_context_total"]]
            if len(code_context) > BUDGET["code_context_total"]:
                truncated_ctx += "\n...[truncated]"
            prompt_parts.append(truncated_ctx)

        # ========== 新增: 飞书技术证据（清洗后的评论）直接注入 ==========
        feishu_evidence = analysis_result.get("feishu_evidence", {})
        if feishu_evidence:
            prompt_parts.append("\n## 飞书技术证据（清洗后的评论与线索）")
            budget_left = BUDGET["comments_total"]
            
            technical = feishu_evidence.get('technical_evidence', [])
            if technical:
                prompt_parts.append("### 技术线索（已过滤噪声，仅保留技术内容）:")
                for entry in technical[:10]:
                    time_str = f"[{entry.get('created_at', '')}] " if entry.get('created_at') else ""
                    content = entry.get('content', '')[:400]
                    line = f"{time_str}评论#{entry.get('index', '?')}: {content}"
                    if len(line) > budget_left:
                        line = line[:budget_left] + "..."
                        prompt_parts.append(line)
                        break
                    prompt_parts.append(line)
                    budget_left -= len(line)
                if len(technical) > 10:
                    prompt_parts.append(f"... 还有 {len(technical) - 10} 条技术线索已省略")
            
            timeline = feishu_evidence.get('timeline', [])
            if timeline:
                prompt_parts.append("\n### 事件时间线:")
                for event in timeline[:15]:
                    line = f"- [{event.get('type', '')}] {event.get('time', '')}: {event.get('summary', '')[:100]}"
                    if len(line) > budget_left:
                        break
                    prompt_parts.append(line)
                    budget_left -= len(line)

        # ========== 新增: 下载日志内容直接注入（动态分块策略）==========
        download_result = feishu_evidence.get("download_result") if feishu_evidence else None
        downloaded_logs = analysis_result.get("downloaded_log_contents", {})
        if downloaded_logs:
            # qwen3.6-plus 上下文窗口 1M tokens (TPM 5M, RPM 30K)。
            # 单次请求可安全使用 ~100K tokens（留足输出和并发余量）。
            # 其他 section 约 10K tokens，日志预算可放到 200K chars (~50K tokens)。
            LOG_CONTENTS_BUDGET = 200000  # 所有日志合计 200K 字符（约 50K tokens）
            budget_left = LOG_CONTENTS_BUDGET
            prompt_parts.append("\n## 下载的日志文件内容（按重要性排序，仅保留关键上下文）")

            # === 动态分块：按日志重要性打分排序 ===
            bug_desc = analysis_result.get("description", "") or analysis_result.get("bug_info", {}).get("description", "")
            comments_raw = analysis_result.get("comments", [])
            # 提取评论文本：可能是 dict 或 str
            comment_texts = []
            for c in comments_raw:
                if isinstance(c, dict):
                    comment_texts.append(c.get("content", str(c)))
                else:
                    comment_texts.append(str(c))
            scored_files = self._score_log_files(downloaded_logs, bug_description=bug_desc, comments=comment_texts)
            total_files = len(scored_files)

            if total_files <= 10:
                # 文件少：全部注入，按预算截断
                valid_files = scored_files
                prompt_parts.append(f"\n（共 {total_files} 个日志文件，按重要性排序）")
            else:
                # 文件多：只注入 Top N，其余用摘要替代
                top_n = min(10, total_files)
                valid_files = scored_files[:top_n]
                skipped = scored_files[top_n:]
                prompt_parts.append(f"\n（共 {total_files} 个日志文件，以下是最重要的 {top_n} 个；其余 {len(skipped)} 个文件摘要见下方）")

                # 添加跳过文件的摘要
                if skipped:
                    prompt_parts.append("\n### 其他日志文件摘要（未全文注入）")
                    summary_lines = []
                    for fname, score, _ in skipped[:20]:  # 最多展示20个
                        # 提取文件的关键信息：错误数、文件大小
                        err_count = self._count_errors_in_content(fname, downloaded_logs)
                        summary_lines.append(f"- `{fname}` (重要性:{score:.2f}, 错误数:{err_count})")
                    if len(skipped) > 20:
                        summary_lines.append(f"... 还有 {len(skipped) - 20} 个文件未列出")
                    prompt_parts.append("\n".join(summary_lines))

            for filename, score, content in valid_files:
                # 大文件只取关键段落（错误前后 50 行）
                if len(content) > 40000:
                    truncated = self._extract_error_context(content, max_chars=40000)
                else:
                    per_file = max(1000, budget_left // len(valid_files)) if valid_files else 1000
                    truncated = content[:per_file]

                if len(content) > len(truncated):
                    truncated += f"\n... ({len(content) - len(truncated)} chars omitted)"
                entry = f"\n### {filename} (重要性: {score:.2f})\n```\n{truncated}\n```"
                prompt_parts.append(entry)
                budget_left -= len(entry)
                if budget_left <= 0:
                    prompt_parts.append("... (更多日志内容已省略，总预算已用完)")
                    break

        # ========== 相似缺陷关键信息 ==========
        similar_bugs = analysis_result.get("similar_bugs", [])
        if similar_bugs:
            SIMILAR_BUGS_BUDGET = 2000  # 相似缺陷合计 2000 字符
            budget_left = SIMILAR_BUGS_BUDGET
            
            prompt_parts.append("\n## 历史相似缺陷（参考）")
            for sb in similar_bugs[:5]:  # 最多5个
                score = sb.get("score", 0)
                if score < 0.1:
                    continue  # 忽略低相似度
                title = sb.get("title", "")[:80]
                status = sb.get("status", "")
                bug_id = sb.get("id", "")
                
                # 提取结论性评论（仅含技术关键词的评论）
                conclusive_comments = []
                for c in sb.get("comments", [])[:3]:
                    ctext = c.get("content", "")
                    if not ctext or ctext.strip() in ['[图片]', '[]']:
                        continue
                    # 只保留包含技术关键词的评论（排除纯对话如"没看到新日志"）
                    if any(kw in ctext for kw in ["crash", "SIG", "mutex", "null", "leak", "ANR",
                            "崩溃", "空指针", "内存泄漏", "死锁", "线程", "修复", "已解决",
                            "根因", "原因", "由于"]):
                        conclusive_comments.append(ctext[:150])
                
                entry = f"- [{bug_id}] (相似度:{score:.2f}, 状态:{status}) {title}"
                if conclusive_comments:
                    entry += f"\n  结论: {'; '.join(conclusive_comments[:2])}"
                entry += "\n"
                
                if len(entry) > budget_left:
                    entry = entry[:budget_left] + "...\n"
                    prompt_parts.append(entry)
                    break
                prompt_parts.append(entry)
                budget_left -= len(entry)
            
            if budget_left <= 0:
                prompt_parts.append("... (更多相似缺陷已省略)")

        # ========== 轮次特定的输出要求 ==========
        if round_num == 1:
            # 第1轮：观察员 - 收集证据，提出假设，列出缺失信息
            prompt_parts.append("\n---\n【第1轮 - 观察员模式】\n")
            prompt_parts.append("请执行以下任务：")
            prompt_parts.append("1. 总结当前可用的所有技术证据")
            prompt_parts.append("2. 提出1-3个最可能的根本原因假设（按可能性排序）")
            prompt_parts.append("3. 对每个假设给出支持证据和反对证据")
            prompt_parts.append("4. 列出你还需要的关键缺失信息（最多5项），格式为：\n   MISSING: <具体需要的信息>\n")
            prompt_parts.append("5. 给出当前置信度评估（0.0-1.0）\n")
            prompt_parts.append("注意：这是第一轮分析，你的输出将被后续轮次参考。")
        elif round_num == 2:
            # 第2轮：调查员 - 挑战假设，寻找反证
            prompt_parts.append("\n---\n【第2轮 - 调查员模式】\n")
            prompt_parts.append("上一轮分析结果：\n" + previous_analysis[:2000] + "\n")
            if missing_info:
                prompt_parts.append("上一轮指出的缺失信息：\n" + missing_info + "\n")
            prompt_parts.append("请执行以下任务：")
            prompt_parts.append("1. 评估上一轮的每个假设，寻找支持或反驳的证据")
            prompt_parts.append("2. 针对缺失信息，检查当前数据中是否有间接线索可以推断")
            prompt_parts.append("3. 提出上一轮可能忽略的替代假设")
            prompt_parts.append("4. 对每个假设更新置信度评估")
            prompt_parts.append("5. 如果信息已足够得出明确结论，请声明：ENOUGH_INFO\n")
            prompt_parts.append("注意：你的角色是批判性审查，不是简单重复第一轮结论。")
        else:
            # 第3轮：裁判 - 综合结论（最终轮）
            prompt_parts.append("\n---\n【第3轮 - 裁判模式（最终结论）】\n")
            prompt_parts.append("前两轮分析历史：\n" + previous_analysis[:3000] + "\n")
            prompt_parts.append("请综合所有证据，输出最终结论：\n")
            prompt_parts.append("### 根因分析（最可能的原因）")
            prompt_parts.append("[直接给出最可能的1-2个根本原因,如果代码上下文中有相关线索请特别指出]\n")
            prompt_parts.append("### 影响范围")
            prompt_parts.append("[受影响的模块/功能]\n")
            prompt_parts.append("### 复现概率")
            prompt_parts.append("[高/中/低及理由]\n")
            prompt_parts.append("### 建议措施")
            prompt_parts.append("[按优先级排列的具体解决步骤]\n")
            prompt_parts.append("### 结论置信度")
            prompt_parts.append("[0.0-1.0]及理由\n")
            prompt_parts.append("注意：这是最终结论轮，必须给出明确判断，不能以'信息不足'结尾。")

        full_prompt = "\n".join(prompt_parts)
        return full_prompt

    def llm_analyze(self, analysis_result: Dict, force: bool = False) -> Dict:
        """LLM 增强分析 - 多轮交互模式（最多3轮，有终止条件）"""
        confidence = self.evaluate_confidence(analysis_result)

        if not force and confidence["score"] >= 0.85:
            return {"used_llm": False, "confidence": confidence, "note": "置信度已足够"}

        # 多轮分析配置
        MAX_ROUNDS = int(os.environ.get("BUG_ANALYZER_MAX_ROUNDS", "3"))
        CONFIDENCE_STOP = float(os.environ.get("BUG_ANALYZER_CONFIDENCE_STOP", "0.75"))

        round_results = []  # 记录每轮结果
        missing_info_history = ""
        previous_analysis = ""

        for round_num in range(1, MAX_ROUNDS + 1):
            # 构建本轮 prompt
            prompt = self._build_llm_prompt(
                analysis_result,
                round_num=round_num,
                previous_analysis=previous_analysis,
                missing_info=missing_info_history
            )

            # 设置 system prompt
            if round_num == 1:
                system_prompt = "你是一个专业的XR设备Bug分析专家（观察员角色）。请仔细审查所有可用证据，提出初步假设。"
            elif round_num == 2:
                system_prompt = "你是一个专业的Bug分析审查员（调查员角色）。你的任务是批判性审查第一轮的分析结果，寻找遗漏和错误。"
            else:
                system_prompt = "你是一个资深技术专家（裁判角色）。请综合前几轮的分析，给出最终、明确的结论。"

            # 调用 LLM
            result = self.call_llm(prompt, system_prompt)

            # 解析本轮结果
            round_data = {
                "round": round_num,
                "role": ["观察员", "调查员", "裁判"][round_num - 1],
                "analysis": result
            }
            round_results.append(round_data)

            # 提取缺失信息（仅第1-2轮）
            if round_num < MAX_ROUNDS:
                missing_lines = [l.strip() for l in result.split("\n")
                                 if l.strip().startswith("MISSING:")]
                if missing_lines:
                    missing_info_history += "\n".join(missing_lines) + "\n"

            # 检查是否声明信息已足够
            if "ENOUGH_INFO" in result and round_num < MAX_ROUNDS:
                # 提前进入最终轮
                round_num += 1  # 跳到裁判轮
                previous_analysis = "\n\n".join(
                    f"【第{r['round']}轮 - {r['role']}】\n{r['analysis']}"
                    for r in round_results
                )
                prompt = self._build_llm_prompt(
                    analysis_result,
                    round_num=round_num,
                    previous_analysis=previous_analysis,
                    missing_info=missing_info_history
                )
                final_result = self.call_llm(prompt,
                    "你是一个资深技术专家（裁判角色）。请综合前几轮的分析，给出最终、明确的结论。")
                round_results.append({
                    "round": round_num,
                    "role": "裁判",
                    "analysis": final_result
                })
                break

            previous_analysis = "\n\n".join(
                f"【第{r['round']}轮 - {r['role']}】\n{r['analysis']}"
                for r in round_results
            )

            # 如果达到置信度阈值，提前结束
            if round_num >= 2:  # 至少2轮
                current_confidence = self.evaluate_confidence(analysis_result)
                if current_confidence["score"] >= CONFIDENCE_STOP:
                    # 提前进入最终轮
                    final_round = round_num + 1
                    if final_round <= MAX_ROUNDS:
                        prompt = self._build_llm_prompt(
                            analysis_result,
                            round_num=final_round,
                            previous_analysis=previous_analysis,
                            missing_info=missing_info_history
                        )
                        final_result = self.call_llm(prompt,
                            "你是一个资深技术专家（裁判角色）。请综合前几轮的分析，给出最终、明确的结论。")
                        round_results.append({
                            "round": final_round,
                            "role": "裁判",
                            "analysis": final_result
                        })
                    break

        # 合并所有轮次结果为最终输出
        final_analysis = round_results[-1]["analysis"] if round_results else "分析失败"

        return {
            "used_llm": True,
            "result": final_analysis,
            "confidence": self.evaluate_confidence(analysis_result),
            "rounds_completed": len(round_results),
            "round_details": round_results,
            "llm_prompt_tokens": len(final_analysis.split())
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
        root_cause = analysis_result.get("root_cause") or ""
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

        # 调整3: 开发者评论加分（有开发者分析/修复说明 = 更高置信度）
        comments = analysis_result.get("comments", [])
        if comments:
            comment_texts = " ".join(c.get("content", "") for c in comments if isinstance(c, dict))
            has_root_cause = any(kw in comment_texts for kw in ["修复", "解决", "原因", "根因", "由于", "导致", "问题同", "Gerrit", "Gerrit Url"])
            has_verification = any(kw in comment_texts for kw in ["验证", "回归", "已关闭", "已解决"])
            if has_root_cause:
                total = min(1.0, total + 0.15)
            elif has_verification:
                total = min(1.0, total + 0.1)
            else:
                total = min(1.0, total + 0.05)

        # 调整4: 附件信息加分（有日志附件 = 数据完整度更高）
        attachments = analysis_result.get("attachments", {})
        if attachments.get("has_log"):
            total = min(1.0, total + 0.05)

        # 调整5: 飞书下载的实际日志内容加分（真实日志文件 > 元数据）
        feishu_evidence = analysis_result.get("feishu_evidence", {})
        download_result = feishu_evidence.get("download_result")
        if download_result and download_result.get('downloaded_count', 0) > 0:
            downloaded_count = download_result['downloaded_count']
            bonus = min(0.2, downloaded_count * 0.08)  # 每个下载日志 +0.08，最高 +0.2
            total = min(1.0, total + bonus)

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

        # 提取时间戳 — 兼容 line 和 timestamp 字段
        timestamps = []
        for e in errors:
            ts = e.get("timestamp") or e.get("line", "")
            if not ts:
                continue
            # 尝试解析各种时间格式
            if ts.startswith("20") or ts.startswith("19"):  # ISO 格式
                timestamps.append(ts)
            elif ":" in ts and len(ts) <= 18:  # logcat 格式 "04-14 10:30:15.123" 或 "12-25 10:30:15"
                timestamps.append(ts)

        if not timestamps:
            return False

        # 截断到分钟级别进行比较，消除秒级差异
        def truncate_to_minute(ts):
            # ISO: "2024-04-14 10:00:15" -> "2024-04-14 10:00"
            if 'T' in ts:
                idx = ts.index('T')
            elif ' ' in ts:
                idx = ts.index(' ')
            else:
                return ts
            time_part = ts[idx + 1:]  # "10:00:15" or "10:00:15.123"
            colon2 = time_part.find(':', time_part.find(':') + 1)
            if colon2 > 0:
                truncated_time = time_part[:colon2]  # "10:00"
            else:
                truncated_time = time_part
            return ts[:idx + 1] + truncated_time

        minute_prefixes = [truncate_to_minute(ts) for ts in timestamps]

        # 如果前5个时间戳去重后 <= 2 个唯一分钟级前缀，认为是集中的
        if len(set(minute_prefixes[:5])) <= 2:
            return True

        return False

    # ========== 动态分块辅助方法（2026-04-30 新增）==========

    def _score_log_files(self, downloaded_logs: Dict, bug_description: str = "", comments: List[str] = None) -> List[Tuple[str, float, str]]:
        """对日志文件按重要性打分排序。

        评分规则:
        - 缺陷描述/评论中提到的文件名: +0.5（开发者明确指出的日志，即使不一定准确，也作为强参考信号）
        - 包含崩溃/堆栈跟踪: +0.3
        - 包含 FATAL/ERROR 关键词: +0.2 per unique error type
        - 包含 AddressSanitizer/TSAN/UBSAN: +0.4 (明确内存错误)
        - 文件名包含 crash/tombstone/kernel/dmesg/logcat: +0.2
        - 文件越大信息越多: +0.05 per 10KB (max 0.15)

        返回: [(filename, score, content), ...] 按 score 降序排列
        """
        scored = []
        crash_patterns = [
            'crash', 'tombstone', 'coredump', 'kernel.log', 'dmesg',
            'logcat', 'panic', 'fatal', 'bugreport', 'stacktrace',
            'backtrace', 'sanitizer', 'asan', 'ubsan', 'tsan'
        ]
        error_keywords = [
            'FATAL', 'ERROR', 'SIGSEGV', 'SIGABRT', 'SIGBUS',
            'NullPointerException', 'IndexOutOfBounds', 'ANR',
            'AddressSanitizer', 'use-after-free', 'heap-buffer-overflow',
            'stack-overflow', 'double-free', 'LEAK', 'segfault'
        ]

        # 从缺陷描述和评论中提取可能提到的日志文件名（作为参考信号）
        mentioned_files = set()
        all_text = bug_description or ""
        if comments:
            all_text += "\n" + "\n".join(comments)

        if all_text.strip():
            # 匹配常见日志文件名模式: xxx.log, current_log_dir, log_17, tombstone_xx, pilot.log, kernel.log 等
            log_file_pattern = re.compile(
                r'([\w][\w._-]*(?:log|tombstone|dmesg|bugreport|crash|trace|dump|kernel|pilot|daemon|user|messages|system|current|anr|main|radio|events))',
                re.IGNORECASE
            )
            for m in log_file_pattern.finditer(all_text):
                name = m.group(1).lower().strip()
                # 排除太短、纯数字或常见非日志词的匹配
                excluded = {'system', 'messages', 'events', 'current', 'main', 'radio', 'anr'}
                if len(name) >= 3 and not name.isdigit() and name not in excluded:
                    mentioned_files.add(name)

        for filename, content in downloaded_logs.items():
            if not content or content == "[二进制文件，无法读取内容]":
                continue

            score = 0.0
            content_upper = content[:5000].upper()  # 只看前5KB打分
            content_lower = content[:5000].lower()
            name_lower = filename.lower()

            # 缺陷描述/评论提到的文件名（强参考信号）
            if mentioned_files:
                for mf in mentioned_files:
                    # 模糊匹配：文件名包含或包含于描述中提到的名称
                    if mf in name_lower or name_lower in mf:
                        score += 0.5
                        break

            # 文件名加分
            for kw in crash_patterns:
                if kw.lower() in name_lower:
                    score += 0.2
                    break

            # 错误类型数量
            found_errors = set()
            for kw in error_keywords:
                if kw.upper() in content_upper:
                    found_errors.add(kw)
            score += min(0.4, len(found_errors) * 0.2)

            # 崩溃信号
            if any(sig in content_upper for sig in ['SIGSEGV', 'SIGABRT', 'SIGBUS']):
                score += 0.3
            if 'backtrace' in content_lower or 'stacktrace' in content_lower:
                score += 0.2

            # 文件大小说明信息量
            size_bonus = min(0.15, len(content) / 10000 * 0.05)
            score += size_bonus

            scored.append((filename, round(score, 3), content))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def _count_errors_in_content(self, filename: str, downloaded_logs: Dict) -> int:
        """统计文件中的错误/异常数量。"""
        content = downloaded_logs.get(filename, '')
        if not content:
            return 0
        count = 0
        content_upper = content.upper()
        for kw in ['FATAL', 'ERROR', 'EXCEPTION', 'CRASH', 'SIGSEGV', 'SIGABRT', 'ANR']:
            count += content_upper.count(kw)
        return count

    def _extract_error_context(self, content: str, max_chars: int = 40000) -> str:
        """从大文件中提取错误附近的关键段落。

        策略:
        1. 找到所有错误行（含 FATAL/ERROR/exception/crash/SIG）
        2. 对每个错误行，取前后50行上下文
        3. 合并重叠区域，截断到 max_chars
        """
        lines = content.split('\n')
        error_lines = set()

        error_keywords = ['FATAL', 'ERROR', 'Exception', 'crash', 'SIG',
                          'panic', 'abort', 'backtrace', 'stacktrace',
                          'AddressSanitizer', 'segfault']

        for i, line in enumerate(lines):
            if any(kw.lower() in line.lower() for kw in error_keywords):
                # 添加前后50行
                start = max(0, i - 50)
                end = min(len(lines), i + 50)
                for j in range(start, end):
                    error_lines.add(j)

        if not error_lines:
            # 没有错误行，返回文件头部
            return content[:max_chars]

        # 排序并合并
        sorted_lines = sorted(error_lines)
        result_lines = []
        for i in sorted_lines:
            result_lines.append(lines[i])

        truncated = '\n'.join(result_lines)
        if len(truncated) > max_chars:
            truncated = truncated[:max_chars] + f'\n... ({len(truncated) - max_chars} more chars omitted)'
        return truncated

    # ========== 结束动态分块辅助方法 ==========

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
            e.get("content", "") if isinstance(e, dict) else str(e) for e in errors
        ] + [w.get("content", "") if isinstance(w, dict) else str(w) for w in warnings]
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
            "comments": comments or [],  # 存储评论数据
            "confidence": {}
        }

        # ===== 新增: 平台检测 (glasses vs host) =====
        platform_info = _detect_platform(
            log_content or "",
            bug_title=bug_description or "",
            bug_description=bug_description or ""
        )
        result["platform_detection"] = platform_info
        self._platform = platform_info.get("platform", "unknown")
        self._platform_keywords = _get_platform_keywords(self._platform)
        
        # 根据平台获取对应仓库列表
        self._platform_repos = self._resolve_platform_repos()

        # ===== 新增: 版本信息提取 =====
        versions_info = _extract_versions(
            log_content or "",
            bug_title=bug_description or "",
            bug_description=bug_description or ""
        )
        result["versions"] = versions_info
        self._versions = versions_info.get("versions", {})
        
        # ===== 新增: 飞书构建版本-仓库映射查询 =====
        try:
            version_db = BuildVersionDB()
            # 推断已知项目名称
            known_project = None
            if self._platform == "glasses":
                known_project = "xrlinux"
            elif self._platform == "host":
                known_project = "android"
            
            build_version_query = version_db.query_for_bug(
                bug_description=bug_description or "",
                log_content=log_content or "",
                comments=comments or [],
                known_project=known_project
            )
            result["build_version_mapping"] = build_version_query
        except Exception as e:
            print(f"[analyzer] 构建版本查询异常: {e}")
            result["build_version_mapping"] = {
                'versions_found': [],
                'repo_mappings': [],
                'summary': f'版本查询异常: {e}'
            }
        
        # ===== BSP 固件版本查询 =====
        try:
            bsp_db = BspVersionDB()
            bsp_query = bsp_db.query_for_bug(
                bug_description=bug_description or "",
                log_content=log_content or "",
                comments=comments or []
            )
            result["bsp_version_info"] = bsp_query
        except Exception as e:
            print(f"[analyzer] BSP 固件版本查询异常: {e}")
            result["bsp_version_info"] = {
                'bsp_version': None,
                'tag': None,
                'tag_info': None,
                'commit_sha': None,
                'extract_from': None,
                'summary': 'BSP 版本查询异常',
            }
        
        # 根据版本信息修正平台判断（有 Dove 版本 = 眼镜端日志，有 NRSDK/HMD = host 日志）
        if self._platform == "unknown" and self._versions:
            if self._versions.get('dove_version'):
                self._platform = "glasses"
                self._platform_keywords = _get_platform_keywords("glasses")
                self._platform_repos = self._resolve_platform_repos()
            elif self._versions.get('nrsdk_version') or self._versions.get('hmd_software_version'):
                self._platform = "host"
                self._platform_keywords = _get_platform_keywords("host")
                self._platform_repos = self._resolve_platform_repos()

        if log_content:
            # 保存原始日志供 _build_llm_prompt 进行日志分类
            result["_raw_log_content"] = log_content
            result["log_analysis"] = self.analyze_log(log_content)
            result["keywords"] = self.extract_keywords(log_content)

            root_cause = self.infer_root_cause(result["log_analysis"], bug_description)
            if root_cause:
                result["root_cause"] = root_cause
                result["suggestion"] = "根据日志分析,建议检查相关模块"

            # 评估置信度
            result["confidence"] = self.evaluate_confidence(result)

        elif bug_description:
            # 即使没有日志，只有bug描述，也要尝试根因推断
            root_cause = self.infer_root_cause({}, bug_description)
            if root_cause:
                result["root_cause"] = root_cause
                result["suggestion"] = "基于缺陷描述的初步推断，建议补充日志验证"

        if bug_description:
            # 优先使用传入的 bug_description 作为搜索词
            query = bug_description
            # 如果没有提供描述，才使用日志关键词
            if not query or len(query) < 3:
                if result.get("keywords"):
                    query = " ".join(result["keywords"][:3])

            result["similar_bugs"] = self.find_similar_bugs(query)

        return result

    def _resolve_platform_repos(self) -> List[str]:
        """根据平台解析对应的仓库列表。
        
        优先从 manifest XML 解析，找不到则使用 config.yaml 中的 fallback 列表。
        """
        try:
            cfg = load_config()
            platform_cfg = cfg.get("platform_repos", {})
            if not platform_cfg:
                return self._default_all_repos()
            
            plat = self._platform
            if plat not in ('glasses', 'host'):
                return self._default_all_repos()
            
            plat_config = platform_cfg.get(plat, {})
            
            # 1. 尝试从 manifest XML 解析
            manifest_repos = _get_platform_repos(plat)
            if manifest_repos:
                repo_names = [r['name'] for r in manifest_repos]
                print(f"[analyzer] 从 manifest 解析到 {plat} 平台仓库: {repo_names}")
                return repo_names
            
            # 2. Fallback: 使用 config.yaml 中的 fallback_repos
            fallback = plat_config.get("fallback_repos", [])
            if fallback:
                print(f"[analyzer] 使用 config fallback {plat} 平台仓库: {fallback}")
                return fallback
            
            return self._default_all_repos()
        except Exception as e:
            print(f"[analyzer] 平台仓库解析异常: {e}，使用全部仓库")
            return self._default_all_repos()

    def _default_all_repos(self) -> List[str]:
        """返回全部已知仓库作为兜底。"""
        return ["dove", "framework", "leopard", "sparrow", "project",
                "nrealUtil", "heron", "xr_codec", "nrsdkrepo"]

    def _classify_log_types(self, log_content: str) -> List[str]:
        """对日志内容分类，识别不同类型的日志段。
        
        返回类型列表: ['kernel', 'driver', 'java', 'logcat', 'native', 'android_framework', ...]
        """
        if not log_content:
            return []
        
        types = set()
        lines = log_content.split('\n')
        
        # 检测日志来源文件名（ZIP 中解压后的路径）
        # kernel.log 来源：syslog facility local7 → Linux kernel 日志
        # user.log 来源：syslog facility user → 用户态 daemon/app 日志
        # syslog.conf: local7.* → kernel.log, user.* → user.log
        has_kernel_log = 'kernel.log' in log_content.lower() or any(
            re.search(r'(kernel:|\[[\d.]+\]\s)', line) for line in lines[:50]
        )
        has_user_log = 'user.log' in log_content.lower()
        
        for line in lines:
            line_lower = line.lower()
            
            # kernel.log 内容：[time] 格式的 syslog kernel 日志，或包含 dmesg 特征
            if re.search(r'^(\d{10}\.\d+|[\d.]+)\s+\S+\s+kernel:', line) or \
               re.search(r'^\[.*?\]\s+\w+\s*:', line) or \
               re.search(r'(kernel:|dmesg|/dev/|/sys/|modprobe|insmod)', line_lower):
                types.add('kernel')
            
            # user.log 内容：syslog user facility，包含 daemon/app 日志
            if has_user_log and re.search(r'^(\d{10}\.\d+|[\d.]+)\s+\S+\s+(?!kernel:)', line):
                types.add('user_daemon')
            
            # logcat 日志
            if re.search(r'(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+\d+\s+[VDIWEFA]/)', line):
                types.add('logcat')
            
            # Java 异常
            if re.search(r'(java\.lang\.\w+Exception|at\s+[\w\.]+\.java:\d+)', line_lower):
                types.add('java')
            
            # Android framework
            if re.search(r'(ActivityManager|WindowManager|PackageManager|android\.runtime)', line_lower):
                types.add('android_framework')
            
            # Native crash
            if re.search(r'(signal\s+(11|6|7)|SIGSEGV|SIGABRT|tombstone|native.*crash)', line_lower):
                types.add('native')
            
            # XR 设备日志
            if re.search(r'(\[dove/|\[leopard/|\[heron/|xr_codec|nrsdk)', line_lower):
                types.add('xr_device')
            
            # 显示/驱动
            if re.search(r'(drm|kms|v4l2|fbdev|display.*driver|hdmi.*controller)', line_lower):
                types.add('display_driver')
            
            # 音频
            if re.search(r'(alsa|snd_|audio.*driver|codec.*driver)', line_lower):
                types.add('audio_driver')
        
        return sorted(types)

    def _get_repos_for_log_types(self, log_types: List[str]) -> List[str]:
        """根据日志类型返回应该搜索的仓库子集。
        
        映射规则:
        - logcat/java → project, framework
        - kernel/driver → dove, leopard, framework, heron, nrsdkrepo
        - native crash → dove, leopard, heron, nrsdkrepo, xr_codec
        - xr_device → dove, leopard, heron, nrsdkrepo, xr_codec
        """
        all_repos = {"dove", "framework", "leopard", "sparrow", "project",
                     "nrealUtil", "heron", "xr_codec", "nrsdkrepo"}
        selected = set(all_repos)
        
        # logcat/java → project, framework, sparrow
        if 'logcat' in log_types or 'java' in log_types or 'android_framework' in log_types:
            selected &= {'project', 'framework', 'sparrow', 'dove'}
            if not selected:
                selected = {'project', 'framework', 'sparrow'}
        
        elif 'kernel' in log_types or 'driver' in log_types:
            # 内核/驱动 → dove, leopard, framework, heron, nrsdkrepo, xr_codec
            selected &= {'dove', 'leopard', 'framework', 'heron', 'nrsdkrepo', 'xr_codec'}
            if not selected:
                selected = {'dove', 'leopard', 'framework'}
        
        elif 'native' in log_types:
            # native crash → dove, leopard, heron, nrsdkrepo, xr_codec
            selected &= {'dove', 'leopard', 'heron', 'nrsdkrepo', 'xr_codec'}
            if not selected:
                selected = {'dove', 'leopard', 'heron'}
        
        elif 'xr_device' in log_types:
            # XR 设备日志 → dove, leopard, heron, nrsdkrepo, xr_codec
            selected &= {'dove', 'leopard', 'heron', 'nrsdkrepo', 'xr_codec'}
            if not selected:
                selected = {'dove', 'leopard', 'heron'}
        
        return sorted(selected)

    def extract_keywords(self, text: str) -> List[str]:
        """提取关键词"""
        keywords = []
        chinese = re.findall(r'[\u4e00-\u9fa5]{2,6}', text)
        english = re.findall(r'\b\w{4,}\b', text.lower())
        keywords.extend(chinese[:5])
        keywords.extend([w for w in english if len(w) > 4][:5])
        return list(set(keywords))[:10]

    def infer_root_cause(self, error_info: Dict, bug_description: str = None) -> Optional[str]:
        """推断根因 (规则库 + 增强检测数据 + 多规则组合)"""
        errors = error_info.get("errors", [])
        warnings = error_info.get("warnings", [])
        crash_sig = error_info.get("crash_signature")
        error_codes = error_info.get("error_codes", [])
        packages = error_info.get("packages", {})

        # ========== 增强: 使用增强检测数据构建更完整的日志内容 ==========
        # 除了 errors/warnings，还要包含 native_crash、timing_issues、shader_errors 的上下文
        all_content_parts = [
            e.get("content", "") for e in errors[:20]
        ] + [
            w.get("content", "") for w in warnings[:10]
        ]

        # 加入 bug_description 用于规则匹配（关键！让中文测试报告也能触发规则）
        if bug_description:
            all_content_parts.append(bug_description)

        # Native crash 上下文
        native_crash = error_info.get("native_crash", {})
        if native_crash.get("has_native_crash"):
            crash_info = native_crash.get("crash_info", {})
            if crash_info:
                all_content_parts.append(crash_info.get("description", ""))
                all_content_parts.append(crash_info.get("matched_text", ""))
                all_content_parts.append(crash_info.get("context", ""))
                if crash_info.get("module"):
                    all_content_parts.append(crash_info["module"])

        # Timing issues 上下文
        timing_issues = error_info.get("timing_issues", {})
        if timing_issues.get("has_timing_issue"):
            for issue in timing_issues.get("issues", [])[:5]:
                all_content_parts.append(issue.get("description", ""))
                all_content_parts.append(issue.get("context", ""))

        # Shader errors 上下文
        shader_errors = error_info.get("shader_errors", {})
        if shader_errors.get("has_shader_error"):
            for err in shader_errors.get("errors", [])[:5]:
                all_content_parts.append(err.get("description", ""))
                all_content_parts.append(err.get("context", ""))

        all_content = " \n".join(all_content_parts)
        all_content_lower = all_content.lower()

        if not errors and not native_crash.get("has_native_crash") and not shader_errors.get("has_shader_error") and not bug_description:
            return None

        # 1. 优先检查 Native Crash（最高优先级）
        if native_crash.get("has_native_crash"):
            crash_info = native_crash.get("crash_info", {})
            if crash_info:
                crash_type = crash_info.get("crash_type", "")
                module = crash_info.get("module", "")
                desc = crash_info.get("description", "")

                if crash_type in ("sigsegv",):
                    module_info = f" (模块: {module})" if module else ""
                    return f"Native 段错误 (SIGSEGV) - 内存访问违规{module_info}，可能是空指针、野指针或缓冲区溢出"
                elif crash_type in ("sigabrt",):
                    return f"Native 异常终止 (SIGABRT){f' (模块: {module})' if module else ''}，可能是断言失败或未捕获异常"
                elif crash_type == "asan_error":
                    return "AddressSanitizer 检测到内存错误 - 堆溢出/use-after-free/栈溢出"
                elif crash_type == "tombstone":
                    return f"Native 崩溃转储 (Tombstone){f' (模块: {module})' if module else ''}"
                elif desc:
                    return f"Native 崩溃: {desc}"
                return "Native 层崩溃"

        # 2. 检查崩溃签名 (Java/通用层)
        if crash_sig:
            if crash_sig.get("signal") == 11:
                return "段错误 (SIGSEGV) - 内存访问违规,可能是空指针或野指针"
            elif crash_sig.get("signal") == 6:
                return "程序异常终止 (SIGABRT) - 可能是断言失败或未捕获异常"
            elif crash_sig.get("fatal"):
                return f"致命错误: {crash_sig.get('description', '未知')}"

        # 3. 检查 ANR（在 errors 中查找 ANR 类型）
        anr_errors = [e for e in errors if e.get("type") == "ANR"]
        if anr_errors:
            return "ANR (应用无响应) - 主线程阻塞超过看门狗超时，可能是死锁、同步调用耗时过长或 UI 线程阻塞"

        # 4. 使用规则库匹配 - 收集所有匹配的规则，组合输出
        matched_rules = []
        for rule in self.ROOT_CAUSE_RULES:
            if rule["pattern"].search(all_content_lower):
                matched_rules.append(rule)
                # 最多匹配前3条最具体的规则
                if len(matched_rules) >= 3:
                    break

        if matched_rules:
            # 组合多条规则的输出
            if len(matched_rules) == 1:
                rule = matched_rules[0]
                return f"{rule['root_cause']}\n\n排查建议: {rule['suggestion']}"
            else:
                # 多规则组合：主因 + 伴随症状
                primary = matched_rules[0]
                secondary = matched_rules[1:]
                result_parts = [
                    f"主要原因: {primary['root_cause']}",
                    f"排查建议: {primary['suggestion']}",
                ]
                if secondary:
                    result_parts.append("\n伴随症状:")
                    for r in secondary:
                        result_parts.append(f"  - {r['root_cause']}")
                return "\n".join(result_parts)

        # 5. 检查错误码
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

        # 6. 检查 Shader/渲染错误
        if shader_errors.get("has_shader_error"):
            error_types = shader_errors.get("error_types", [])
            if "vulkan_error" in error_types:
                return "Vulkan API 错误 - 检查设备初始化、扩展支持和内存分配"
            elif "opengl_error" in error_types:
                return "OpenGL 错误 - 检查上下文状态和渲染管线"
            elif "shader_compile" in error_types:
                return "Shader 编译失败 - 检查着色器代码语法和 GLSL 版本"
            elif "gpu_error" in error_types:
                return "GPU 硬件错误 - 可能是驱动问题或硬件不支持"

        # 7. 检查时序问题
        if timing_issues.get("has_timing_issue"):
            issue_types = timing_issues.get("issue_types", [])
            if "deadlock" in issue_types:
                return "死锁 - 线程相互等待形成循环依赖"
            elif "race_condition" in issue_types:
                return "竞态条件 - 并发访问时序不当导致结果不确定"
            elif "timeout" in issue_types and "delay" in issue_types:
                return "超时 + 延迟 - 操作响应时间过长，可能是资源竞争或外部依赖缓慢"

        # 8. 检查 XR 模块
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

        # 9. 分析首条错误
        if errors:
            first_error = errors[0].get("content", "").lower()
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