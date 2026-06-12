# OpenViking 导入飞书缺陷数据 — 实测流程

> 发现日期: 2026-04-22
> OpenViking 版本: 0.3.2

## API 认证模式

OpenViking v0.3.2 的所有端点以 `/api/v1/` 为前缀。使用 Root API Key 时必须携带账户和用户头：

```python
import requests

base = "http://127.0.0.1:1934"
api_key = "<OV_API_KEY>"  # 来自 ov.conf 的 server.root_api_key
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
    "X-OpenViking-Account": "root",
    "X-OpenViking-User": "root",
}

# 验证连接
resp = requests.get(f"{base}/api/v1/sessions", headers=headers)
# → 200, 返回会话列表
```

## 可用端点 (OpenAPI)

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查（无需认证） |
| `/api/v1/sessions` | GET/POST | 会话管理 |
| `/api/v1/resources` | POST | 添加资源 |
| `/api/v1/resources/temp_upload` | POST (multipart) | 临时文件上传 |
| `/api/v1/search/search` | POST | 语义搜索 |
| `/api/v1/content/read` | POST | 读取内容 |

## 导入流程（实测通过）

### 关键限制

1. **不接受本地文件路径** — `POST /api/v1/resources` 的 `path` 参数只接受远程 URL（http/https）。直接传本地路径会返回 `PERMISSION_DENIED`。
2. **必须通过 temp_upload 中转** — 先用 multipart 上传到临时存储，再用 `temp_file_id` 添加资源。
3. **单条嵌入超时** — 单条 bug 向量化耗时 >60s 会触发 `DEADLINE_EXCEEDED`。
4. **合并导入** — 将所有 bug 合并为一个 Markdown 文件后单次导入是可行方案。

### 步骤

```python
import requests, json

base = "http://127.0.0.1:1934"
headers = {
    "Authorization": "Bearer <OV_API_KEY>",
    "X-OpenViking-Account": "root",
    "X-OpenViking-User": "root",
}

# Step 1: 上传合并后的 Markdown 文件
file_path = "~/.openviking/workspace/feishu-bugs/feishu_bugs_all.md"
with open(file_path, "rb") as f:
    resp = requests.post(
        f"{base}/api/v1/resources/temp_upload",
        files={"file": ("feishu_bugs_all.md", f, "text/markdown")},
        headers={
            "Authorization": headers["Authorization"],
            "X-OpenViking-Account": "root",
            "X-OpenViking-User": "root",
        },
        timeout=120,
    )
    temp_file_id = resp.json()["result"]["temp_file_id"]

# Step 2: 添加资源（异步处理，避免超时）
resp = requests.post(
    f"{base}/api/v1/resources",
    json={
        "temp_file_id": temp_file_id,
        "to": "viking://resources/feishu-bugs/feishu_bugs_all.md",
        "reason": "Import 3969 feishu bugs",
        "wait": False,  # 异步！wait=True 会超时
    },
    headers=headers,
    timeout=30,
)
```

### Markdown 格式模板

将每个缺陷格式化为 Markdown 段落：

```markdown
# 飞书缺陷数据总表

缺陷总数: 3969

---

## 缺陷 1: [标题]

- 缺陷ID: xxx
- 状态: Open
- 类型: 缺陷
- 创建时间: 2026-04-08T10:38:27+08:00
- 报告人: Name
- 经办人: Name
- 模板: 自动化测试

### 评论记录

- [2026-04-08 10:41:53] Author: 评论内容
- ...

---
```

## 服务管理

```bash
# 启动（端口 1934）
cd ~/.openviking && python3 -m openviking.server.bootstrap \
  --config ~/.openviking/ov.conf --host 0.0.0.0 --port 1934

# 健康检查
curl http://127.0.0.1:1934/health

# 重启
pkill -f "openviking.server.bootstrap"
# 然后重新启动

# 查看 API 文档（OpenAPI  Schema）
curl http://127.0.0.1:1934/openapi.json
```

## 配置要点

- **配置文件**: `~/.openviking/ov.conf`（JSON 格式，不是 INI）
- **默认端口**: `1934`（旧文档写的 1933 已废弃）
- **Root API Key**: `server.root_api_key` 字段
- **Embedding**: 通过外部 litellm 服务（`https://litellm.xreal.work/v1`）
- **数据目录**: `~/.openviking/workspace/`
- **临时上传目录**: `~/.openviking/workspace/temp/upload/`

## 已知问题

- 异步导入后无进度 API 可查询，需要通过 `/api/v1/observer/queue` 或日志观察处理状态
- 大文件（4MB+）导入时 embedding 和 VLM 向量化耗时较长（取决于外部 API 并发限制）
- 导入完成后可通过 `/api/v1/search/search` 验证索引是否生效
