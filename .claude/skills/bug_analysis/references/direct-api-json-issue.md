# Direct API 评论获取 JSON 解析失败调查

## 问题

`_fetch_live_feishu_data()` 调用飞书 Direct API 获取评论时，持续返回:
```
评论获取失败: Extra data: line 1 column 5 (char 4)
```

**影响范围**: 2026-04-29 所有测试用例 (6194388200, 6578455657, 5312922812) 均失败，回退到缓存数据。

## 错误分析

Python `json.loads()` 报 "Extra data: line 1 column 5" 表示:
- 前 4 个字符成功解析为一个完整 JSON 值
- 第 5 个字符开始有多余内容

**可能原因**:
1. API 返回 `true{...}` 或类似带前缀的响应
2. API 返回多个 JSON 对象拼接 (如 `true\n{"data":...}`)
3. HTTP 层有 gzip/brotli 压缩但被当作明文解析
4. API 返回 HTML 错误页面 (但前 4 字符恰好是有效 JSON)

## 调试建议

```python
# 在 cmd_feishu 中 _fetch_live_feishu_data 处添加调试:
resp = requests.get(url, headers=headers, timeout=30)
print(f"Status: {resp.status_code}")
print(f"Content-Type: {resp.headers.get('Content-Type')}")
print(f"Raw body (first 500 chars): {resp.text[:500]}")
print(f"Raw body (bytes first 20): {resp.content[:20]}")
```

## 变通方案

当前回退到 v3 缓存数据 (`~/.openviking/workspace/feishu-bugs/.bug_index_cache.json`) 仍然可用:
- 4060 条缺陷
- 2377 条有评论
- 3424 条有附件元数据
- 1330 条有日志附件

缓存数据在 `enrich_cache.py` 运行时更新，对于非实时性要求高的场景足够使用。

## 参考

- 飞书 Direct API 文档: `https://project.feishu.cn/open_api/{project_key}/work_item/issue/{bug_id}/comment`
- 认证方式: `x-plugin-token` + `x-user-key` 请求头
- Plugin Token 获取: `POST https://project.feishu.cn/open_api/authen/plugin_token`
