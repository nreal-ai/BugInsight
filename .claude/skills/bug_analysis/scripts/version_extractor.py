#!/usr/bin/env python3
"""
Version Extractor - 从日志中提取各组件的版本信息。

支持提取的版本:
- Glasses 端 (pilot.log / pilot.log.*):
  * Dove Version (眼镜端软件版本)
  * SystemVersion / SystemVersionCode (BSP 固件版本)
  * HWVersion (硬件版本)
  * DspVersion (DSP 版本)
  * GlassesSN / GlassesModelName (设备信息)
- Host 端:
  * NRSDK Version (Host SDK / sparrow 版本)
  * HMD version (眼镜固件版本，从 host 视角)

用法:
    from version_extractor import extract_versions
    versions = extract_versions(log_content)
    # versions['dove_version'] = '1.9.0.20260520210629'
    # versions['bsp_version'] = '15.1.03.329_USERROOT'
    # versions['nrsdk_version'] = '3.1.2.20260317164456'
    # versions['hmd_version'] = '1.8.1.20260318103929'
"""

import re
from typing import Dict, List, Optional


# 版本提取规则
VERSION_PATTERNS = {
    # ===== 眼镜端 (Glasses) 版本 =====
    'dove_version': {
        'pattern': re.compile(r'Dove\s+Version:\s*([\d.]+(?:_\w+)?)', re.I),
        'label': 'Dove 版本 (眼镜端软件)',
        'source': 'pilot.log',
    },
    'bsp_version': {
        'pattern': re.compile(r'Dove\s+SystemVersion:\s*([\w.]+(?:_USERROOT)?)', re.I),
        'label': 'BSP 固件版本',
        'source': 'pilot.log',
    },
    'bsp_version_code': {
        'pattern': re.compile(r'SystemVersionCode:\s*([\w.]*)', re.I),
        'label': 'BSP 版本代码',
        'source': 'pilot.log',
    },
    'hw_version': {
        'pattern': re.compile(r'HWVersion:\s*([\w._]+)', re.I),
        'label': '硬件版本',
        'source': 'pilot.log',
    },
    'dsp_version': {
        'pattern': re.compile(r'DspVersion:\s*([\w._]+(?:_\d+)?)', re.I),
        'label': 'DSP 版本',
        'source': 'pilot.log',
    },
    'glasses_sn': {
        'pattern': re.compile(r'GlassesSN:\s*([\w]+)', re.I),
        'label': '眼镜序列号',
        'source': 'pilot.log',
    },
    'glasses_model': {
        'pattern': re.compile(r'GlassesModelName:\s*([\w\s]+?)(?:\s*,|\s*$)', re.I),
        'label': '眼镜型号',
        'source': 'pilot.log',
    },
    
    # ===== Host 端版本 =====
    'nrsdk_version': {
        'pattern': re.compile(r'NRSDK\s+Version:\s*([\d.]+(?:_\w+)?)', re.I),
        'label': 'NRSDK 版本 (Host SDK / Sparrow)',
        'source': 'host log',
    },
    'hmd_system_version': {
        'pattern': re.compile(r'HMD\s+version:\s*system:\s*(\d+)', re.I),
        'label': 'HMD 系统版本号',
        'source': 'host log',
    },
    'hmd_software_version': {
        'pattern': re.compile(r'HMD\s+version:[^,]*?,\s*software:\s*([\d.]+(?:_\w+)?)', re.I),
        'label': '眼镜固件版本 (HMD software)',
        'source': 'host log',
    },
    
    # ===== 通用版本 =====
    'linux_kernel': {
        'pattern': re.compile(r'Linux\s+version\s+([\d._+-]+)', re.I),
        'label': 'Linux 内核版本',
        'source': 'dmesg / kernel log',
    },
    'android_version': {
        'pattern': re.compile(r'(?:Android\s+|ro\.build\.version\.release[=:])\s*([\d.]+)', re.I),
        'label': 'Android 版本',
        'source': 'logcat',
    },
}


def extract_versions(log_content: str, 
                     bug_title: str = "", 
                     bug_description: str = "") -> Dict:
    """
    从日志内容中提取所有版本信息。
    
    Args:
        log_content: 日志内容
        bug_title: 缺陷标题 (可选)
        bug_description: 缺陷描述 (可选)
    
    Returns:
        dict: {
            'versions': {key: value, ...},  # 提取到的版本
            'summary': '版本摘要字符串',     # 人类可读的版本摘要
            'source': 'glasses' | 'host' | 'mixed' | 'unknown',  # 版本来源
            'evidence': [(key, label, matched_text), ...]  # 证据
        }
    """
    result = {
        'versions': {},
        'summary': '',
        'source': 'unknown',
        'evidence': []
    }
    
    # 合并所有文本
    full_text = log_content or ""
    if bug_title:
        full_text += "\n" + bug_title
    if bug_description:
        full_text += "\n" + bug_description
    
    if not full_text.strip():
        return result
    
    # 逐个模式匹配
    glasses_found = False
    host_found = False
    
    for key, config in VERSION_PATTERNS.items():
        match = config['pattern'].search(full_text)
        if match:
            value = match.group(1).strip()
            if value:  # 跳过空值 (如 SystemVersionCode 可能为空)
                result['versions'][key] = value
                result['evidence'].append((key, config['label'], match.group(0)[:100]))
                
                # 判断来源
                if key in ('dove_version', 'bsp_version', 'hw_version', 
                          'dsp_version', 'glasses_sn', 'glasses_model', 'bsp_version_code'):
                    glasses_found = True
                elif key in ('nrsdk_version', 'hmd_system_version', 'hmd_software_version'):
                    host_found = True
    
    # 确定版本来源
    if glasses_found and host_found:
        result['source'] = 'mixed'
    elif glasses_found:
        result['source'] = 'glasses'
    elif host_found:
        result['source'] = 'host'
    
    # 生成人类可读的版本摘要
    summary_parts = []
    
    if result['versions'].get('glasses_model'):
        summary_parts.append(f"设备: {result['versions']['glasses_model']}")
    if result['versions'].get('glasses_sn'):
        summary_parts.append(f"SN: {result['versions']['glasses_sn']}")
    if result['versions'].get('hw_version'):
        summary_parts.append(f"HW: {result['versions']['hw_version']}")
    
    if result['versions'].get('dove_version'):
        summary_parts.append(f"Dove: {result['versions']['dove_version']}")
    if result['versions'].get('bsp_version'):
        summary_parts.append(f"BSP: {result['versions']['bsp_version']}")
    if result['versions'].get('dsp_version'):
        summary_parts.append(f"DSP: {result['versions']['dsp_version']}")
    if result['versions'].get('bsp_version_code'):
        summary_parts.append(f"BSP Code: {result['versions']['bsp_version_code']}")
    
    if result['versions'].get('nrsdk_version'):
        summary_parts.append(f"NRSDK: {result['versions']['nrsdk_version']}")
    if result['versions'].get('hmd_software_version'):
        summary_parts.append(f"HMD 固件: {result['versions']['hmd_software_version']}")
    if result['versions'].get('hmd_system_version'):
        summary_parts.append(f"HMD System: {result['versions']['hmd_system_version']}")
    
    if result['versions'].get('linux_kernel'):
        summary_parts.append(f"Kernel: {result['versions']['linux_kernel']}")
    if result['versions'].get('android_version'):
        summary_parts.append(f"Android: {result['versions']['android_version']}")
    
    result['summary'] = ' | '.join(summary_parts) if summary_parts else '未检测到版本信息'
    
    return result


def format_versions_for_prompt(versions_result: Dict) -> str:
    """
    将版本信息格式化为 LLM prompt 可读的文本。
    
    Args:
        versions_result: extract_versions() 的返回值
    
    Returns:
        str: 格式化后的版本信息文本
    """
    if not versions_result.get('versions'):
        return ""
    
    lines = ["## 版本信息"]
    
    v = versions_result['versions']
    
    # 设备信息
    device_info = []
    if v.get('glasses_model'):
        device_info.append(f"型号: {v['glasses_model']}")
    if v.get('glasses_sn'):
        device_info.append(f"序列号: {v['glasses_sn']}")
    if v.get('hw_version'):
        device_info.append(f"硬件版本: {v['hw_version']}")
    if device_info:
        lines.append("### 设备")
        lines.extend([f"- {info}" for info in device_info])
    
    # 眼镜端版本
    glasses_versions = []
    if v.get('dove_version'):
        glasses_versions.append(f"Dove 软件版本: {v['dove_version']}")
    if v.get('bsp_version'):
        glasses_versions.append(f"BSP 固件版本: {v['bsp_version']}")
    if v.get('bsp_version_code'):
        glasses_versions.append(f"BSP 版本代码: {v['bsp_version_code']}")
    if v.get('dsp_version'):
        glasses_versions.append(f"DSP 版本: {v['dsp_version']}")
    if glasses_versions:
        lines.append("### 眼镜端")
        lines.extend([f"- {info}" for info in glasses_versions])
    
    # Host 端版本
    host_versions = []
    if v.get('nrsdk_version'):
        host_versions.append(f"NRSDK 版本: {v['nrsdk_version']}")
    if v.get('hmd_software_version'):
        host_versions.append(f"HMD 固件版本: {v['hmd_software_version']}")
    if v.get('hmd_system_version'):
        host_versions.append(f"HMD 系统版本: {v['hmd_system_version']}")
    if host_versions:
        lines.append("### Host 端")
        lines.extend([f"- {info}" for info in host_versions])
    
    # 通用版本
    common_versions = []
    if v.get('linux_kernel'):
        common_versions.append(f"Linux 内核: {v['linux_kernel']}")
    if v.get('android_version'):
        common_versions.append(f"Android 版本: {v['android_version']}")
    if common_versions:
        lines.append("### 系统")
        lines.extend([f"- {info}" for info in common_versions])
    
    lines.append("")  # 空行分隔
    return '\n'.join(lines)


def get_version_compatibility_note(versions: Dict) -> Optional[str]:
    """
    根据版本信息检测已知的版本兼容性问题。
    
    这里可以维护一个版本兼容性规则表，当检测到特定版本组合时给出提示。
    
    Args:
        versions: extract_versions() 返回的 versions 字典
    
    Returns:
        str: 兼容性提示，如果没有问题则返回 None
    """
    notes = []
    
    dove = versions.get('dove_version', '')
    bsp = versions.get('bsp_version', '')
    nrsdk = versions.get('nrsdk_version', '')
    hmd_sw = versions.get('hmd_software_version', '')
    
    # 示例：可以在此添加版本兼容性规则
    # if dove and bsp:
    #     dove_prefix = dove.split('.')[0] if '.' in dove else dove
    #     if dove_prefix == '1' and '14.' in bsp:
    #         notes.append("Dove 1.x 与 BSP 14.x 可能存在兼容性问题")
    
    return '\n'.join(notes) if notes else None


if __name__ == "__main__":
    # 测试眼镜端日志
    glasses_log = """
Jan 25 00:00:00 XREAL[519]: [2027-01-25 00:00:00.753] [519] [INFO] [Dove] Dove Version: 1.9.0.20260520210629
Jan 25 00:00:01 XREAL[519]: [2027-01-25 00:00:01.166] [519] [INFO] [Dove] Dove SystemVersion: 15.1.03.329_USERROOT, SystemVersionCode: , HWVersion: GF_6, DspVersion:15.A.00.069_20241211, GlassesSN:G2X64BM168613L GlassesModelName:XREAL One
"""
    print("=== 眼镜端日志版本提取 ===")
    result = extract_versions(glasses_log)
    print(f"来源: {result['source']}")
    print(f"摘要: {result['summary']}")
    print(f"版本: {result['versions']}")
    
    # 测试 Host 端日志
    host_log = """
[2026-05-19 23:11:41.135] [19180] [INFO] [NRSDK] NRSDK Version: 3.1.2.20260317164456
[2026-05-19 23:11:41.184] [19180] [INFO] [NRSDK] HMD version: system:3251, software:1.8.1.20260318103929
"""
    print("\n=== Host 端日志版本提取 ===")
    result = extract_versions(host_log)
    print(f"来源: {result['source']}")
    print(f"摘要: {result['summary']}")
    print(f"版本: {result['versions']}")
    
    # 测试 prompt 格式化
    print("\n=== Prompt 格式化 ===")
    print(format_versions_for_prompt(result))
