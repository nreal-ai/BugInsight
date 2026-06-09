## 增量获取脚本 (新增)

**脚本路径**: `{{SKILL_DIR}}/references/fetch_incremental.py`

支持三种模式获取新增缺陷:

```bash
# 1. 手动 ID 模式 (推荐 - 最快，从飞书 Web UI 复制 ID)
python3 references/fetch_incremental.py --ids 123456,789012

# 2. ID 范围探测 (探测最大 ID 上方 N 个 ID)
python3 references/fetch_incremental.py --probe 1000

# 3. 自动模式 (尝试 mcporter，失败后回退到 probe)
python3 references/fetch_incremental.py
```

**数据文件**: `~/.openviking/data/viking/default/resources/feishu-bugs/`
- `bugs_index_full.json` - 缺陷索引 (id, name, status)
- `bugs_details_full.json` - 缺陷详情 (list 格式，含 create_time/update_time)
