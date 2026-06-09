#!/usr/bin/env python3
"""
飞书缺陷抓取器 - 配置加载模块
从 config.yaml 加载配置，支持环境变量覆盖

用法:
    from config import get_openviking_config, get_feishu_config, load_config
    ov_cfg = get_openviking_config()
    fs_cfg = get_feishu_config()
"""
import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List

# 加载 ~/.hermes/.env 文件 (如果存在)
_dotenv_path = Path.home() / ".hermes" / ".env"
if _dotenv_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_dotenv_path)
    except ImportError:
        # 手动解析 .env 文件
        with open(_dotenv_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and not key.startswith('#'):
                        os.environ.setdefault(key, value)

# 配置文件路径 (相对于 skill 根目录)
CONFIG_DIR = Path(__file__).parent.parent
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# 缓存的配置
_config: Optional[Dict[str, Any]] = None


def load_config() -> Dict[str, Any]:
    """加载配置文件"""
    global _config
    
    if _config is not None:
        return _config
    
    # 默认配置
    default_config = {
        "openviking": {
            "api_base": "http://127.0.0.1:1933",
            "api_key": "***",
            "account": "root",
            "user": "user001"
        },
        "feishu": {
            "project_key": "",
            "mcp_key": "",
            "plugin_id": "",
            "plugin_secret": "",
            "user_key": ""
        },
        "output": {
            "base_dir": os.path.expanduser("~/.openviking/workspace/feishu-bugs")
        },
        "concurrency": {
            "max_workers": 5,
            "rate_limit": 10
        },
        "incremental": {
            "enabled": True,
            "checkpoint_file": ".fetch_progress.json"
        }
    }
    
    # 尝试加载配置文件
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}
            # 合并配置
            _config = _merge_dict(default_config, file_config)
        except Exception as e:
            print(f"Warning: Failed to load config from {CONFIG_FILE}: {e}")
            _config = default_config
    else:
        _config = default_config
    
    # 解析 _env 后缀的键: 将 {"key_env": "ENV_VAR_NAME"} 替换为 os.getenv("ENV_VAR_NAME")
    _config = _resolve_env_refs(_config)
    
    # 环境变量直接覆盖
    _config = _apply_env_overrides(_config)
    
    return _config


def _resolve_env_refs(config: Dict) -> Dict:
    """递归解析配置中的 _env 后缀键。
    
    将 {"mcp_key_env": "FEISHU_MCP_TOKEN"} 替换为 {"mcp_key": os.getenv("FEISHU_MCP_TOKEN")}
    如果环境变量不存在则保留空字符串。
    """
    result = {}
    for key, value in config.items():
        if isinstance(value, dict):
            result[key] = _resolve_env_refs(value)
        elif key.endswith('_env'):
            # {"plugin_id_env": "FEISHU_PLUGIN_ID"} → {"plugin_id": os.getenv("FEISHU_PLUGIN_ID")}
            real_key = key[:-4]  # remove '_env'
            env_val = os.getenv(value, '')
            result[real_key] = env_val
        else:
            result[key] = value
    return result


def _merge_dict(base: Dict, override: Dict) -> Dict:
    """递归合并字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _apply_env_overrides(config: Dict) -> Dict:
    """应用环境变量覆盖。优先使用环境变量中的敏感值。"""
    # OpenViking
    if os.getenv("OV_API_BASE"):
        config["openviking"]["api_base"] = os.getenv("OV_API_BASE")
    if os.getenv("OV_API_KEY"):
        config["openviking"]["api_key"] = os.getenv("OV_API_KEY")
    if os.getenv("OV_ACCOUNT"):
        config["openviking"]["account"] = os.getenv("OV_ACCOUNT")
    if os.getenv("OV_USER"):
        config["openviking"]["user"] = os.getenv("OV_USER")
    
    # 飞书 - 优先从环境变量读取敏感信息
    if os.getenv("FEISHU_MCP_TOKEN"):
        config["feishu"]["mcp_key"] = os.getenv("FEISHU_MCP_TOKEN")
    elif os.getenv("FEISHU_MCP_KEY"):
        config["feishu"]["mcp_key"] = os.getenv("FEISHU_MCP_KEY")
    if os.getenv("FEISHU_PLUGIN_ID"):
        config["feishu"]["plugin_id"] = os.getenv("FEISHU_PLUGIN_ID")
    if os.getenv("FEISHU_PLUGIN_SECRET"):
        config["feishu"]["plugin_secret"] = os.getenv("FEISHU_PLUGIN_SECRET")
    if os.getenv("FEISHU_PROJECT_KEY"):
        config["feishu"]["project_key"] = os.getenv("FEISHU_PROJECT_KEY")
    if os.getenv("FEISHU_USER_KEY"):
        config["feishu"]["user_key"] = os.getenv("FEISHU_USER_KEY")
    
    # 输出目录
    if os.getenv("OUTPUT_BASE_DIR"):
        config["output"]["base_dir"] = os.getenv("OUTPUT_BASE_DIR")
    
    # 并发
    if os.getenv("MAX_WORKERS"):
        config["concurrency"]["max_workers"] = int(os.getenv("MAX_WORKERS"))
    
    return config


def get_config(key: str = None, default: Any = None) -> Any:
    """
    获取配置项
    
    Args:
        key: 配置键，支持点号分隔，如 "openviking.api_base"
        default: 默认值
    
    Returns:
        配置值
    """
    config = load_config()
    
    if key is None:
        return config
    
    # 支持点号分隔的键
    keys = key.split(".")
    value = config
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default
    return value


def reload_config():
    """重新加载配置"""
    global _config
    _config = None
    return load_config()


# ============================================
# 便捷函数：直接获取各配置项
# ============================================

def get_openviking_config() -> Dict[str, str]:
    """获取 OpenViking 配置"""
    cfg = load_config()
    return cfg.get("openviking", {})


def get_feishu_config() -> Dict[str, str]:
    """获取飞书项目配置"""
    cfg = load_config()
    return cfg.get("feishu", {})


def get_output_config() -> Dict[str, Any]:
    """获取输出配置"""
    cfg = load_config()
    return cfg.get("output", {})


def get_concurrency_config() -> Dict[str, Any]:
    """获取并发配置"""
    cfg = load_config()
    return cfg.get("concurrency", {})


def get_incremental_config() -> Dict[str, Any]:
    """获取增量更新配置"""
    cfg = load_config()
    return cfg.get("incremental", {})


# ============================================
# 配置检查
# ============================================

def check_config() -> List[str]:
    """
    检查配置是否完整，返回未配置项列表
    
    Returns:
        未配置项列表，如果返回空列表则表示配置完整
    """
    missing = []
    cfg = load_config()
    
    # 检查 OpenViking 配置
    ov_cfg = cfg.get("openviking", {})
    if not ov_cfg.get("api_key") or ov_cfg.get("api_key") == "<OV_API_KEY>":
        missing.append("openviking.api_key (需要设置有效的 API Key)")
    
    # 检查飞书配置 - 这些通常通过 MCP 自动获取，但也可能需要
    fs_cfg = cfg.get("feishu", {})
    if not fs_cfg.get("project_key"):
        missing.append("feishu.project_key")
    if not fs_cfg.get("plugin_secret"):
        missing.append("feishu.plugin_secret")
    
    return missing


def print_config_check():
    """打印配置检查结果"""
    missing = check_config()
    if missing:
        print("⚠️  配置检查: 以下项目需要配置:")
        for item in missing:
            print(f"   - {item}")
        print(f"\n请编辑配置文件: {CONFIG_FILE}")
        print("或设置环境变量:")
        print("  OV_API_KEY, OV_API_BASE, OV_ACCOUNT, OV_USER")
        print("  FEISHU_PROJECT_KEY, FEISHU_PLUGIN_SECRET, FEISHU_PLUGIN_ID")
        print("-" * 50)
        return False
    
    print("✅ 配置检查通过")
    return True


# ============================================
# 入口点
# ============================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--check":
            # 检查配置
            print_config_check()
        elif sys.argv[1] == "--print":
            # 打印当前配置
            import json
            print(json.dumps(load_config(), indent=2, ensure_ascii=False))
        elif sys.argv[1] == "--key":
            # 打印特定配置项
            key = sys.argv[2] if len(sys.argv) > 2 else None
            print(get_config(key))
        else:
            print(f"用法: {sys.argv[0]} [--check|--print|--key <key>]")
    else:
        print_config_check()