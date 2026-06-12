#!/usr/bin/env python3
"""
Platform Detector - 从日志中判断是眼镜端(glasses)还是主机端(host)的问题。

检测策略:
- Glasses (眼镜端): xrlinux 日志, dove/hardware 相关, linux kernel, 
  SIG/信号, dmesg, systemd, /dev/, /sys/ 等 Linux 系统特征
- Host (主机端): Android 特征, logcat, adb, am start, AndroidManifest,
  activity/fragment, Android 权限等

用法:
    from platform_detector import detect_platform
    result = detect_platform(log_content)
    # result['platform'] == 'glasses' | 'host' | 'unknown'
    # result['confidence'] == 0.0-1.0
    # result['evidence'] == list of matched patterns
"""

import re
from typing import Dict, List, Optional, Tuple


# 眼镜端 (Glasses) 特征
GLASSES_PATTERNS = [
    # xrlinux 系统特征
    (re.compile(r'xrlinux', re.I), "xrlinux 系统标识", 3),
    (re.compile(r'rockchip|rk3568|rk3588', re.I), "Rockchip 芯片 (眼镜端硬件)", 3),
    (re.compile(r'dove', re.I), "Dove 项目 (眼镜端)", 3),
    (re.compile(r'xr_codec', re.I), "XR Codec (眼镜端编解码)", 3),
    (re.compile(r'nrsdk|nr.?sdk', re.I), "NRSDK (眼镜端SDK)", 3),
    
    # Linux 系统特征
    (re.compile(r'/dev/(video|input|tty|fb|i2c|spi|mmc)', re.I), "Linux 设备节点", 2),
    (re.compile(r'/sys/class|/sys/devices', re.I), "Linux sysfs 路径", 2),
    (re.compile(r'dmesg|kernel:|Kernel panic', re.I), "Linux kernel 日志", 2),
    (re.compile(r'systemd|systemctl|journalctl', re.I), "systemd 服务管理", 2),
    (re.compile(r'modprobe|insmod|lsmod', re.I), "Linux 内核模块", 2),
    
    # 硬件/驱动相关
    (re.compile(r'drm|kms|fbdev|disp|display.*driver|hdmi.*controller', re.I), "显示驱动 (Linux DRM/KMS)", 2),
    (re.compile(r'v4l2|videodev|camera.*driver|isp.*driver', re.I), "视频/相机驱动", 2),
    (re.compile(r'alsa|snd_|sound.*driver|asoc|codec.*driver', re.I), "音频驱动 (ALSA)", 2),
    (re.compile(r'i2c|spi|uart|gpio.*pin|pinctrl', re.I), "总线/接口驱动", 2),
    
    # Native/信号相关 (眼镜端更多 native crash)
    (re.compile(r'signal\s+(11|6|7|4)', re.I), "Linux 信号 (native crash)", 2),
    (re.compile(r'segfault|segv|bus error', re.I), "段错误 (native)", 2),
    (re.compile(r'tombstone|debuggerd', re.I), "Android tombstone (可能在眼镜端)", 1),
    
    # 眼镜特定模块
    (re.compile(r'leopard|heron|nreal-framework', re.I), "眼镜相关项目名", 3),
    (re.compile(r'xr.*linux|xr.*os|xr.*firmware', re.I), "XR 系统标识", 3),
    
    # 串口/调试日志
    (re.compile(r'console|serial|tty|uart.*log', re.I), "串口日志 (常见于眼镜端调试)", 1),
]

# 主机端 (Host) 特征
HOST_PATTERNS = [
    # Android 系统特征
    (re.compile(r'logcat|adb\s+logcat', re.I), "Android logcat 日志", 3),
    (re.compile(r'android\.os|android\.app|android\.content|android\.view', re.I), "Android SDK 包名", 3),
    (re.compile(r'android\.runtime|art_method|dalvik', re.I), "Android 运行时", 3),
    (re.compile(r'am\s+start|am\s+broadcast|pm\s+install|cmd\s+package', re.I), "Android 命令", 2),
    (re.compile(r'ActivityManager|WindowManager|PackageManager|ActivityThread', re.I), "Android 系统服务", 3),
    
    # Android 组件
    (re.compile(r'(Activity|Fragment|Service|BroadcastReceiver|ContentProvider)', re.I), "Android 组件", 2),
    (re.compile(r'AndroidManifest\.xml', re.I), "Android 清单文件", 2),
    (re.compile(r'gradle|androidx|com\.android', re.I), "Android 构建系统", 2),
    
    # Android 异常
    (re.compile(r'(NullPointerException|IllegalStateException|RuntimeException)', re.I), "Java 异常", 2),
    (re.compile(r'ANR|Application Not Responding|Input dispatching timed out', re.I), "ANR (应用无响应)", 3),
    (re.compile(r'android\.content\.Intent|android\.os\.Bundle|android\.app\.Activity', re.I), "Android API 调用", 2),
    
    # Host 应用特征
    (re.compile(r'xreal.*app|xreal.*host|nreal.*host|com\.nreal', re.I), "Host 应用标识", 3),
    (re.compile(r'com\.android|com\.google\.android', re.I), "Android 系统应用", 2),
    
    # USB/投屏相关 (通常是 host 侧)
    (re.compile(r'usb.*host|usb.*connection.*failed|device.*attached', re.I), "USB host 连接", 2),
    (re.compile(r'projection|screen.*mirror|cast.*screen', re.I), "投屏/镜像 (host 侧)", 1),
]

# 模糊特征 (需要更多上下文)
AMBIGUOUS_PATTERNS = [
    # 两端都可能有的
    (re.compile(r'usb', re.I), "USB 相关 (两端都可能有)", 1),
    (re.compile(r'bluetooth|ble', re.I), "蓝牙相关 (两端都可能有)", 1),
    (re.compile(r'wifi|network|connection', re.I), "网络相关 (两端都可能有)", 1),
    (re.compile(r'power|battery|charge', re.I), "电源相关 (两端都可能有)", 1),
]


def detect_platform(log_content: str, 
                    bug_title: str = "", 
                    bug_description: str = "") -> Dict:
    """
    从日志内容判断问题来自眼镜端还是主机端。
    
    Args:
        log_content: 日志内容
        bug_title: 缺陷标题 (可选, 辅助判断)
        bug_description: 缺陷描述 (可选, 辅助判断)
    
    Returns:
        dict: {
            'platform': 'glasses' | 'host' | 'unknown',
            'confidence': float (0.0-1.0),
            'glasses_score': int,
            'host_score': int,
            'evidence': list of (pattern_desc, matched_text, score)
        }
    """
    result = {
        'platform': 'unknown',
        'confidence': 0.0,
        'glasses_score': 0,
        'host_score': 0,
        'evidence': []
    }
    
    # 合并所有文本用于分析
    full_text = log_content or ""
    if bug_title:
        full_text += "\n" + bug_title
    if bug_description:
        full_text += "\n" + bug_description
    
    if not full_text.strip():
        return result
    
    # 1. 扫描眼镜端特征
    for pattern, desc, score in GLASSES_PATTERNS:
        matches = pattern.findall(full_text)
        if matches:
            result['glasses_score'] += score * min(len(matches), 5)  # 限制最多计数5次
            # 保存证据 (去重)
            matched_text = matches[0] if matches else ""
            if not any(e[1] == matched_text and e[0] == desc for e in result['evidence']):
                result['evidence'].append((desc, str(matched_text)[:100], score))
    
    # 2. 扫描主机端特征
    for pattern, desc, score in HOST_PATTERNS:
        matches = pattern.findall(full_text)
        if matches:
            result['host_score'] += score * min(len(matches), 5)
            matched_text = matches[0] if matches else ""
            if not any(e[1] == matched_text and e[0] == desc for e in result['evidence']):
                result['evidence'].append((desc, str(matched_text)[:100], score))
    
    # 3. 扫描模糊特征 (低权重)
    for pattern, desc, score in AMBIGUOUS_PATTERNS:
        matches = pattern.findall(full_text)
        if matches:
            # 模糊特征不直接加分，但会记录在证据中
            matched_text = matches[0] if matches else ""
            result['evidence'].append((desc, str(matched_text)[:100], 0))
    
    # 4. 确定平台和置信度
    total_score = result['glasses_score'] + result['host_score']
    if total_score == 0:
        result['platform'] = 'unknown'
        result['confidence'] = 0.0
    elif result['glasses_score'] > result['host_score']:
        result['platform'] = 'glasses'
        # 置信度基于分数差异
        diff = result['glasses_score'] - result['host_score']
        result['confidence'] = min(1.0, diff / max(total_score, 1) + 0.3)
    elif result['host_score'] > result['glasses_score']:
        result['platform'] = 'host'
        diff = result['host_score'] - result['glasses_score']
        result['confidence'] = min(1.0, diff / max(total_score, 1) + 0.3)
    else:
        result['platform'] = 'unknown'  # 分数相同，无法判断
        result['confidence'] = 0.5
    
    return result


def get_platform_keywords(platform: str) -> Dict[str, List[str]]:
    """
    获取平台相关关键词，用于日志分类和代码搜索。
    
    Args:
        platform: 'glasses' | 'host' | 'unknown'
    
    Returns:
        dict: {
            'log_types': ['kernel', 'driver', 'native', ...],
            'search_terms': ['dove', 'drm', 'v4l2', ...],
            'code_patterns': [re.compile(...), ...]
        }
    """
    if platform == 'glasses':
        return {
            'log_types': ['kernel', 'driver', 'native', 'hardware', 'firmware'],
            'search_terms': ['dove', 'leopard', 'heron', 'nrsdk', 'xr_codec', 
                           'drm', 'v4l2', 'alsa', 'i2c', 'spi', 'gpio',
                           'rockchip', 'rk3568', 'xrlinux'],
            'code_patterns': [
                re.compile(r'(libdove|libnr|dove/)', re.I),
                re.compile(r'(\.ko$|kernel|module)', re.I),
                re.compile(r'(/dev/|/sys/|dmesg)', re.I),
            ]
        }
    elif platform == 'host':
        return {
            'log_types': ['logcat', 'java', 'android', 'app', 'framework'],
            'search_terms': ['android', 'activity', 'fragment', 'service',
                           'logcat', 'adb', 'ANR', 'NullPointerException',
                           'com.nreal', 'xreal-app'],
            'code_patterns': [
                re.compile(r'(android\.|androidx\.)', re.I),
                re.compile(r'(\.java$|\.kt$)', re.I),
                re.compile(r'(Activity|Fragment|Service)', re.I),
            ]
        }
    else:  # unknown
        return {
            'log_types': ['general'],
            'search_terms': [],
            'code_patterns': []
        }


if __name__ == "__main__":
    # 测试
    test_logs = {
        "glasses_sample": """
[2024-01-01 10:00:00] kernel: dove_display: hdmi controller initialized
[2024-01-01 10:00:01] systemd: Started dove-camera.service
[2024-01-01 10:00:02] kernel: v4l2: camera sensor detected
signal 11 (SIGSEGV), fault addr 0x0 in libdove.so
        """,
        "host_sample": """
12-25 10:30:15.123  1234  5678 E/ActivityManager: ANR in com.nreal.app
12-25 10:30:15.124  1234  5678 E/AndroidRuntime: FATAL EXCEPTION: main
java.lang.NullPointerException at com.nreal.app.MainActivity.onCreate(MainActivity.java:45)
        """,
        "unknown_sample": """
USB connection failed
Device not responding
        """
    }
    
    for name, log in test_logs.items():
        result = detect_platform(log)
        print(f"\n=== {name} ===")
        print(f"Platform: {result['platform']} (confidence: {result['confidence']:.2f})")
        print(f"Scores: glasses={result['glasses_score']}, host={result['host_score']}")
        print(f"Evidence: {result['evidence'][:3]}")
