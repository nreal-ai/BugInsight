# LLM 配置与错误检测指南

## 问题症状

`bug_analyzer.py` 输出的 `llm_analysis` 字段包含：
```
LLM HTTP 401: {"error":{"message":"Authentication Error, Malformed API Key passed in. Ensure Key has `Bearer ` prefix."...
```

脚本返回 exit_code 0（因为降级到规则引擎），但评论包含了错误信息而非分析结果。

## 根因：配置链路三层断裂

### 1. 环境变量缺失

`~/.hermes/.env` 中 LiteLLM proxy 的 key 是 `LITELLM_API_KEY`，但 `config.py` 读取的是 `LLM_BASE_URL` 和 `LLM_API_KEY`。

**修复**：在 `.env` 中同时添加：
```
LLM_BASE_URL=https://litellm.xreal.work/v1
LLM_API_KEY=<与LITELLM_API_KEY相同值>
```

### 2. Key 名不匹配

`config.py` 的 `get_llm_config()` 返回 `base_url`，但 `analyzer.py` 读取 `api_base`：

```python
# 错误：llm_cfg.get("api_base", "https://litellm.xreal.work/v1")  → 永远回退到默认值
# 正确：llm_cfg.get("base_url", llm_cfg.get("api_base", "https://litellm.xreal.work/v1"))
```

### 3. 默认模型不在允许列表中

`config.py` 默认模型 `gpt-4o` 不在 LiteLLM proxy 的 team 允许列表中。
可用模型：`qwen3.6-plus`, `deepseek-v4-pro`, `glm-5.1` 等。
**修复**：`config.py` 默认 model 改为 `qwen3.6-plus`。

## LLM 错误检测（防止误报成功）

`bug_analyzer.py` 即使 LLM 401 也会 exit_code 0。必须在 `Cron 自动分析任务` 中检测：

```python
# run_analysis() 中
llm_result = analysis.get("llm_analysis", "")
if llm_result and ("HTTP 401" in llm_result or "Authentication Error" in llm_result or "403" in llm_result):
    print(f"  [LLM_ERROR] LLM analysis failed: {llm_result[:150]}")
    return analysis, False  # 不应评论

# build_comment() 中
if not llm or "LLM 调用失败" in llm or "HTTP 401" in llm or "Authentication Error" in llm or "403" in llm:
    # Fallback: 规则引擎摘要
```

## 关键陷阱：config.yaml 不存在导致 404（2026-06-05）

`feishu-bug-pipeline/config.yaml` 文件**不存在**时，`config.py` 的 `load_config()` 使用硬编码默认配置：

```python
"llm": {
    "api_base": "http://127.0.0.1:1933",  # ← 这是 OpenViking 地址，不是 LLM！
    "model": "qwen3.6-plus",
    "api_key": ""
}
```

当 `config.yaml` 不存在时，`get_llm_config()` 返回上述默认值。由于默认配置没有 `_env` 后缀，不会触发 `_resolve_env_refs()` 去读环境变量。bug_analyzer.py 最终调用 `http://127.0.0.1:1933/v1/chat/completions`，得到 **HTTP 404**。

**修复**：创建 `~/.hermes/skills/bug-analysis/feishu-bug-pipeline/config.yaml`，使用 `_env` 后缀引用环境变量。详见下方配置示例。

## 完整配置链路图

```
~/.hermes/.env
  ├── LLM_BASE_URL=https://litellm.xreal.work/v1    ← 必须显式设置
  ├── LLM_API_KEY=sk-xxx                            ← 必须显式设置
  └── LITELLM_API_KEY=sk-xxx                        ← hermes 自身使用

config.py → get_llm_config()
  ├── provider: "openai"
  ├── model: "qwen3.6-plus"                         ← 必须在 LiteLLM 允许列表中
  ├── base_url: os.getenv("LLM_BASE_URL", "")       ← key 名是 base_url
  └── api_key: os.getenv("LLM_API_KEY", "")         ← key 名是 api_key

analyzer.py → __init__()
  ├── LLM_API_BASE = llm_cfg.get("base_url", llm_cfg.get("api_base", ...))  ← 兼容两种
  ├── LLM_API_KEY  = llm_cfg.get("api_key", os.getenv("LLM_API_KEY", ""))
  └── LLM_MODEL    = llm_cfg.get("model", "qwen3.6-plus")
```
