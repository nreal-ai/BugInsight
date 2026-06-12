#!/usr/bin/env python3
"""
共享 MCP JSON-RPC 客户端模块。

替代 mcporter CLI，直接通过 HTTP JSON-RPC 调用飞书 Project MCP Server。
Token 优先从 BUG_INSIGHT_* 环境变量读取，fallback 到旧路径。

用法:
    from mcp_client import mcp_call, mcp_add_comment, get_mcp_token
    result, err = mcp_call("search_by_mql", {"project_key": "sw_team", "mql": "..."})
    ok, comment_id = mcp_add_comment("sw_team", "7007264060", "分析结果...")
"""
import json
import os
import requests
from typing import Any, Dict, Optional, Tuple

MCP_URL = "https://project.feishu.cn/mcp_server/v1"


def get_mcp_token() -> Optional[str]:
    """获取 MCP Token，三级 fallback:
    1. BUG_INSIGHT_FEISHU_MCP_TOKEN 环境变量（Claude Code 环境）
    2. FEISHU_MCP_TOKEN 环境变量（旧 Hermes 环境兼容）
    3. ~/.mcporter/mcporter.json 文件（mcporter CLI 配置）
    """
    # Priority 1: Claude Code env var
    token = os.environ.get("BUG_INSIGHT_FEISHU_MCP_TOKEN", "")
    if token:
        return token

    # Priority 2: Legacy env var
    token = os.environ.get("FEISHU_MCP_TOKEN", "")
    if token:
        return token

    # Priority 3: mcporter config file
    mcporter_path = os.path.expanduser("~/.mcporter/mcporter.json")
    if os.path.exists(mcporter_path):
        try:
            with open(mcporter_path) as f:
                mc_data = json.load(f)
            return mc_data.get("mcpServers", {}).get("meego", {}).get("headers", {}).get("X-Mcp-Token")
        except Exception:
            pass

    return None


def get_plugin_token(plugin_id: str = None, plugin_secret: str = None) -> Optional[str]:
    """获取飞书 Plugin Token（有效期 2 小时）。

    优先从参数获取，否则从环境变量读取。
    环境变量优先级: BUG_INSIGHT_FEISHU_PLUGIN_ID → FEISHU_PLUGIN_ID
    """
    if not plugin_id:
        plugin_id = os.environ.get("BUG_INSIGHT_FEISHU_PLUGIN_ID", "")
        if not plugin_id:
            plugin_id = os.environ.get("FEISHU_PLUGIN_ID", "")
    if not plugin_secret:
        plugin_secret = os.environ.get("BUG_INSIGHT_FEISHU_PLUGIN_SECRET", "")
        if not plugin_secret:
            plugin_secret = os.environ.get("FEISHU_PLUGIN_SECRET", "")

    if not plugin_id or not plugin_secret:
        return None

    try:
        resp = requests.post(
            "https://project.feishu.cn/open_api/authen/plugin_token",
            json={"plugin_id": plugin_id, "plugin_secret": plugin_secret, "type": 0},
            timeout=10,
        )
        return resp.json().get("data", {}).get("token")
    except Exception:
        return None


def get_user_key() -> str:
    """获取飞书用户 Key。
    优先级: BUG_INSIGHT_FEISHU_USER_KEY → FEISHU_USER_KEY → 空字符串
    """
    user_key = os.environ.get("BUG_INSIGHT_FEISHU_USER_KEY", "")
    if user_key:
        return user_key
    user_key = os.environ.get("FEISHU_USER_KEY", "")
    if user_key:
        return user_key
    return ""


def mcp_call(tool_name: str, arguments: Dict[str, Any], timeout: int = 30) -> Tuple[Optional[Any], Optional[str]]:
    """直接调用 MCP Server JSON-RPC 端点。

    Args:
        tool_name: MCP 工具名 (如 search_by_mql, add_comment, get_download_url)
        arguments: 工具参数字典
        timeout: HTTP 超时秒数

    Returns:
        (parsed_result, error_string) — 成功时 error 为 None，result 为解析后的 Python 对象。
        解析时使用 parse_int=str 避免 19 位 comment_id 精度丢失。
    """
    mcp_token = get_mcp_token()
    if not mcp_token:
        return None, "No MCP token found"

    headers = {"X-Mcp-Token": mcp_token, "Content-Type": "application/json"}
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {"name": tool_name, "arguments": arguments},
    }
    try:
        resp = requests.post(MCP_URL, headers=headers, json=payload, timeout=timeout)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"
        data = resp.json()
        if "error" in data:
            return None, f"JSON-RPC error: {data['error']}"
        content_list = data.get("result", {}).get("content", [])
        if not content_list:
            return None, "Empty result"
        text = content_list[0].get("text", "")
        if not text:
            return None, "Empty text in result"
        return json.loads(text, parse_int=str), None
    except requests.Timeout:
        return None, "Request timeout"
    except json.JSONDecodeError as e:
        return None, f"JSON decode error: {e}"
    except Exception as e:
        return None, str(e)


def mcp_add_comment(project_key: str, work_item_id: str, content: str, timeout: int = 30) -> Tuple[bool, str]:
    """通过 MCP JSON-RPC 向飞书缺陷添加评论。

    Args:
        project_key: 飞书空间标识
        work_item_id: 缺陷 ID
        content: Markdown 格式的评论内容
        timeout: 超时秒数

    Returns:
        (success, detail) — success 为 True 时 detail 是 comment_id，
        success 为 False 时 detail 是错误信息。
    """
    import re
    result, err = mcp_call(
        "add_comment",
        {
            "work_item_id": work_item_id,
            "project_key": project_key,
            "content": content,
        },
        timeout=timeout,
    )
    if err:
        return False, f"Failed: {err}"

    # 尝试从返回结果中提取 comment_id
    if isinstance(result, dict):
        comment_id = result.get("comment_id") or result.get("id") or result.get("data", {}).get("comment_id")
        if comment_id:
            return True, str(comment_id)

    # 尝试从字符串结果中提取
    result_str = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
    match = re.search(r"comment[_\s]?id[:\s]+(\d+)", result_str, re.IGNORECASE)
    if match:
        return True, match.group(1)

    return True, "unknown"


def mcp_get_download_url(project_key: str, work_item_id: str, file_url: str, timeout: int = 15) -> Optional[Dict[str, str]]:
    """通过 MCP JSON-RPC 获取文件签名下载 URL。

    Args:
        project_key: 飞书空间标识
        work_item_id: 缺陷 ID
        file_url: 飞书内部文件 URL
        timeout: 超时秒数

    Returns:
        {"download_url": str, "sign": str} 或 None
    """
    result, err = mcp_call(
        "get_download_url",
        {
            "project_key": project_key,
            "work_item_id": work_item_id,
            "file_url": file_url,
        },
        timeout=timeout,
    )
    if err:
        return None

    if isinstance(result, dict):
        download_url = result.get("download_url", "")
        sign = result.get("sign", "")
        if download_url:
            return {"download_url": download_url, "sign": sign}

    return None


def parse_moql_field_list(moql_field_list: list) -> dict:
    """解析 MQL search_by_mql 返回的 moql_field_list 为平铺字典。"""
    fields = {}
    for field in moql_field_list:
        key = field.get("key")
        value_obj = field.get("value", {})
        if "long_value" in value_obj:
            fields[key] = str(value_obj["long_value"])
        elif "string_value" in value_obj:
            fields[key] = value_obj["string_value"]
        elif "key_label_value_list" in value_obj:
            klvl = value_obj["key_label_value_list"]
            if klvl:
                fields[key] = klvl[0].get("key", "")
        else:
            fields[key] = value_obj
    return fields
