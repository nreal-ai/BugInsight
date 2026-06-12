# 飞书评论附件下载机制

## 评论中的文件 URL 来源

mcporter `list_workitem_comments` 返回的每条评论包含两个可能的文件来源：

1. **`comment['file_url']` 字段** — 直接的文件下载 URL（部分评论有，部分为空字符串）
2. **`comment['content']` 中嵌入的 URL** — 格式为 `![](https://project.feishu.cn/goapi/v5/platform/file/stream/download/{token})` 的图片/文件引用

## 下载流程

### 步骤 1: 提取所有可下载 URL

```python
import re

url_pattern = re.compile(
    r'https://project\.feishu\.cn/goapi/v5/platform/file/stream/download/[^\s)"\'\]]+'
)

def extract_file_urls_from_comments(comments):
    results = []
    seen_urls = set()
    for idx, comment in enumerate(comments):
        # 来源 A: file_url 字段
        file_url = comment.get('file_url', '').strip()
        if file_url and file_url not in seen_urls:
            seen_urls.add(file_url)
            results.append({'file_url': file_url, 'source': 'file_url_field', 'comment_index': idx})
        # 来源 B: content 中嵌入的 URL
        content = comment.get('content', '')
        if content:
            urls = url_pattern.findall(content)
            for url in urls:
                url = url.rstrip(')。')  # 清理尾部标点
                if url not in seen_urls:
                    seen_urls.add(url)
                    results.append({'file_url': url, 'source': 'content_embedded', 'comment_index': idx})
    return results
```

### 步骤 2: 通过 mcporter 获取签名下载 URL

**缺陷正文附件**（multi_attachment 字段）— `get_download_url` 返回 `"invalid param: trans to fileToken err"`，不可下载。

**评论附件**（评论 file_url 字段或 content 嵌入 URL）— `get_download_url` 可正常工作：

```python
import subprocess, json

result = subprocess.run(
    ["mcporter", "call", "meego", "get_download_url",
     "--args", json.dumps({
         "project_key": "sw_team",
         "work_item_id": "6475234318",  # 必须是字符串
         "file_url": file_url            # 评论中的下载 URL
     })],
    capture_output=True, text=True, timeout=15
)
data = json.loads(result.stdout)
# 返回: {'download_url': '...', 'sign': '...', 'sign_expire_time': ..., 'is_multipart': bool}
```

### 步骤 3: 下载文件

```python
import requests

resp = requests.get(
    data['download_url'],
    headers={"X-Meego-File-Sign": data['sign']},
    timeout=120, stream=True
)
# resp.content 就是文件内容
```

**关键**：必须带 `X-Meego-File-Sign` header，值来自 `get_download_url` 返回的 `sign` 字段。直接 GET 会返回 401。

## 实测数据

| 缺陷 ID | 评论数 | 评论附件 URL 数 | 下载成功 | 说明 |
|---------|--------|----------------|---------|------|
| 6475234318 | 18 | 7 (6 embedded + 1 file_url) | 7/7 | 包含 logcat (3.2MB)、图片、日志 |

## 与正文附件的对比

| 特性 | 正文附件 (multi_attachment) | 评论附件 |
|------|----------------------------|---------|
| 数据位置 | `fields[].field_value` (field_key=='multi_attachment') | `comment.file_url` 或 `comment.content` |
| URL 格式 | UUID (需要 uuid + work_item_id + work_item_type_key 拼接) | 完整 URL (直接传给 get_download_url) |
| get_download_url | ❌ 返回 "invalid param: trans to fileToken err" | ✅ 正常工作 |
| 下载方法 | 需用 `work_item/{type}/{id}/file/download` 端点 | mcporter get_download_url + X-Meego-File-Sign |
| 实测可用性 | 部分可用（logcat 等可下载） | 全部可用 |
