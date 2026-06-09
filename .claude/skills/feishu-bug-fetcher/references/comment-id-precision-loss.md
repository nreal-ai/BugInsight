# 飞书评论 ID 精度丢失问题 (19 位整数)

## 问题

飞书评论 ID (`comment_id`) 是 19 位整数，如 `7639697209299487948`。JavaScript 的 JSON 解析器使用 IEEE 754 双精度浮点数，安全整数上限为 `Number.MAX_SAFE_INTEGER = 9007199254740991`（约 16 位）。超过此范围的整数在 JSON 解析时丢失末位精度。

**mcporter 和 MCP 服务器都通过 JSON 传输数据，因此 comment_id 精度丢失不可避免。**

## 表现

```
# mcporter 返回
"comment_id": 7639697209299488000    # 错误！应为 7639697209299487948

# 删除 API 返回
{"err_code": 1000052001, "err_msg": "Record not found"}
```

## 解决方案

### 方案 1: 从原始 JSON 文本提取 (Python)

直接解析原始响应字符串，避免 JSON 数字解析：

```python
import re
import requests

# 获取原始响应
raw = resp.text
# 用正则提取 comment_id 字符串
m = re.search(r'"comment_id"\s*:\s*(\d{19})', raw)
if m:
    exact_comment_id = m.group(1)  # '7639697209299487948' — 字符串！
```

### 方案 2: MCP JSON-RPC 直接调用 (带 parse_int=str)

通过 HTTP 直接调用 MCP 服务器的 JSON-RPC 接口，启用 `parse_int=str`：

```python
import json, requests

url = "https://project.feishu.cn/mcp_server/v1"
headers = {"X-Mcp-Token": "m-..."}

body = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "list_workitem_comments",
        "arguments": {"project_key": "sw_team", "work_item_id": "6991306194"}
    }
}

class BigIntDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(parse_int=str, *args, **kwargs)

resp = requests.post(url, json=body, headers=headers, timeout=30)
data = json.loads(resp.text, cls=BigIntDecoder)
# comment_id 现在是字符串 '7639697209299487948'
```

### 方案 3: 重新添加正确评论 (最简单)

如果只需要一条正确评论而不需要删除旧评论，直接添加新的正确评论即可。

## 注意事项

- 此问题影响所有超过 16 位的飞书 ID（comment_id、work_item_id 等）
- Direct API 的 `x-plugin-token` 认证方式不受影响（Python `requests` 不做 JSON 数字解析）
- **永远不要**将 19 位整数 ID 通过 mcporter CLI 传递给需要精确匹配的操作
