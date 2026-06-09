#!/usr/bin/env python3
"""
配置加载模块
从 config.yaml 加载配置，支持环境变量覆盖
"""
import os
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, List

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
        "llm": {
            "api_base": "https://litellm.xreal.work/v1",
            "api_key": "",
            "model": "qwen3-coder-plus"
        },
        "code_repos": {
            "dove": "../../nreal-code/nreal-dove/",
            "framework": "../../nreal-code/nreal-framework/",
            "leopard": "../../nreal-code/nreal-leopard/",
            "sparrow": "../../nreal-code/nreal-sparrow/",
            "project": "../../nreal-code/nreal-project/",
        },
        "feishu": {
            "project_key": "",
            "plugin_secret": "",
            "plugin_id": "",
            "user_key": "",
            "mcp_key": ""
        },
        "analysis": {
            "similar_bugs_limit": 10,
            "code_search_limit": 20,
            "llm_timeout": 90,
            "confidence_threshold": 0.7
        }
    }
    
    # 尝试加载配置文件
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                file_config = yaml.safe_load(f) or {}
            # 合并配置
            _config = merge_dict(default_config, file_config)
        except Exception as e:
            print(f"Warning: Failed to load config from {CONFIG_FILE}: {e}")
            _config = default_config
    else:
        _config = default_config
    
    # 环境变量覆盖
    _config = apply_env_overrides(_config)
    
    return _config


def merge_dict(base: Dict, override: Dict) -> Dict:
    """递归合并字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def apply_env_overrides(config: Dict) -> Dict:
    """应用环境变量覆盖"""
    # LLM
    if os.getenv("LLM_API_BASE"):
        config["llm"]["api_base"] = os.getenv("LLM_API_BASE")
    if os.getenv("LLM_API_KEY"):
        config["llm"]["api_key"] = os.getenv("LLM_API_KEY")
    if os.getenv("LLM_MODEL"):
        config["llm"]["model"] = os.getenv("LLM_MODEL")

    # 飞书 (备用, 飞书MCP已全局配置时不需要)
    if os.getenv("BUG_INSIGHT_FEISHU_PROJECT_KEY"):
        config["feishu"]["project_key"] = os.getenv("BUG_INSIGHT_FEISHU_PROJECT_KEY")
    if os.getenv("BUG_INSIGHT_FEISHU_PLUGIN_SECRET"):
        config["feishu"]["plugin_secret"] = os.getenv("BUG_INSIGHT_FEISHU_PLUGIN_SECRET")
    if os.getenv("BUG_INSIGHT_FEISHU_PLUGIN_ID"):
        config["feishu"]["plugin_id"] = os.getenv("BUG_INSIGHT_FEISHU_PLUGIN_ID")
    if os.getenv("BUG_INSIGHT_FEISHU_USER_KEY"):
        config["feishu"]["user_key"] = os.getenv("BUG_INSIGHT_FEISHU_USER_KEY")
    if os.getenv("BUG_INSIGHT_FEISHU_MCP_KEY"):
        config["feishu"]["mcp_key"] = os.getenv("BUG_INSIGHT_FEISHU_MCP_KEY")

    return config


def get_config(key: str = None, default: Any = None) -> Any:
    """
    获取配置项
    
    Args:
        key: 配置键，支持点号分隔，如 "llm.api_base"
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


# 便捷函数：直接获取各配置项
def get_llm_config() -> Dict[str, str]:
    """获取 LLM 配置"""
    cfg = load_config()
    return cfg.get("llm", {})


def get_code_repos() -> Dict[str, str]:
    """获取代码仓库路径配置"""
    cfg = load_config()
    return cfg.get("code_repos", {})


def get_analysis_config() -> Dict[str, Any]:
    """获取分析配置"""
    cfg = load_config()
    return cfg.get("analysis", {})


def get_feishu_config() -> Dict[str, str]:
    """获取飞书项目配置"""
    cfg = load_config()
    return cfg.get("feishu", {})


def check_config() -> List[str]:
    """
    检查配置是否完整，返回未配置项列表

    Returns:
        未配置项列表，如果返回空列表则表示配置完整
    """
    missing = []
    cfg = load_config()

    # 检查 LLM 配置
    llm_cfg = cfg.get("llm", {})
    if not llm_cfg.get("api_key"):
        missing.append("llm.api_key (请设置环境变量 LLM_API_KEY)")
    if not llm_cfg.get("api_base"):
        missing.append("llm.api_base")
    if not llm_cfg.get("model"):
        missing.append("llm.model")

    # 检查代码仓库是否存在（可选，仅警告）
    code_repos = cfg.get("code_repos", {})
    for name, path in code_repos.items():
        full_path = CONFIG_DIR / path
        if not full_path.exists():
            pass

    return missing


def print_config_check():
    """打印配置检查结果"""
    missing = check_config()
    if missing:
        print("⚠️  配置检查: 以下项目需要配置:")
        for item in missing:
            print(f"   - {item}")
        print(f"\n请编辑配置文件: {CONFIG_FILE}")
        print("或设置环境变量: LLM_API_KEY, LLM_API_BASE, LLM_MODEL")
        print("-" * 50)
        return False
    return True