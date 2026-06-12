# 跨 Agent 分析准确度对比：Hermes vs Claude Code

## 背景

| 条件 | 说明 |
|------|------|
| **对比时间** | 2026-06-12 |
| **Skill** | `ai_bug_analysis`，来源 [github.com/nreal-ai/ai_bug_analysis.git](https://github.com/nreal-ai/ai_bug_analysis.git) |
| **大模型** | `deepseek-v4-pro`（双方相同） |
| **Agent 平台** | Hermes vs Claude Code（不同） |
| **分析结论数** | 每个 Bug 各有双方分别产生的 >=1 条评论 |
| **Claude Code 标签** | `AI分析结论 (by Claude Code + ai_bug_analysis)` |
| **Hermes 标签** | `AI分析 自动缺陷分析报告` |

---

## 总览

| # | Bug ID | 缺陷简述 | Hermes 分析 | Claude Code 分析 | 胜出 |
|---|--------|---------|------------|-----------------|------|
| 1 | [7016096596](https://project.feishu.cn/axr/issue/detail/7016096596) | SteamDeck UAC→DP黑屏 | 5 假设并列，NOT_ENOUGH_INFO | 日志精确定位 NRDpGetFrame error ×12，🎯DP链路未就绪 | **Claude Code** |
| 2 | [7010662757](https://project.feishu.cn/axr/issue/detail/7010662757) | 超宽屏X键直接退出 | 抽象归因"冷启动路径分支不一致" | 精确代码 `control_glasses.cc:594-600` SceneMode 门控 | **Claude Code** |
| 3 | [7006901369](https://project.feishu.cn/axr/issue/detail/7006901369) | 6dof插拔黑屏 | 泛化分析"SDK回调竞态" | 日志证据链：`pose_buffer empty ×233`→`not_shown:20999`→`pose_error:1104057` | **Claude Code** |
| 4 | [7010973731](https://project.feishu.cn/axr/issue/detail/7010973731) | 模式切换声音卡住 | "IPC阻塞/状态机竞态"，NOT_ENOUGH_INFO | `need_to_change_dp:false` 铁证下层问题+`CameraIntrinsic failed ×6`+7秒断连 | **Claude Code** |
| 5 | [7010756083](https://project.feishu.cn/axr/issue/detail/7010756083) | 超宽屏花屏 | 核心正确："映射缺失+Clamp失效" 0.90 | 具体定位：`permanent_config.h` 缺 `ultra_wide_` CanvasPanel | **平局** |
| 6 | [7013445690](https://project.feishu.cn/axr/issue/detail/7013445690) | 3D空间悬停黑屏 | 两假设摇摆，0.56 | 代码级精确：`control_state_machine.cc:466-469` 强制退出3D SBS | **Claude Code** |

---

## 详细对比

### Bug 1: [7016096596](https://project.feishu.cn/axr/issue/detail/7016096596) — SteamDeck OLED UAC→DP 黑屏

| 维度 | Hermes | Claude Code |
|------|--------|-------------|
| 方法 | 多假设枚举法（5 个假设） | 日志驱动单根因法 |
| 代码 | 无具体代码引用 | 引用 Flinger 渲染管线 |
| 日志 | 仅 14 行，未分析 | NRDpGetFrame + NRGdcProcess + RenderFrame failed 精确引用 |
| 自信度 | 0.40~0.85（分散），NOT_ENOUGH_INFO | 0.80（集中），明确结论 |
| 结论 | "需 dmesg/DRM debug 日志" | "DP Link Training 耗时过长超出眼镜端预期超时窗口" |

**差异**: Hermes 抛出 5 个假设（DP训练/USB PD/DSP版本/EDID/显示模块），无法收敛；Claude Code 从日志中提取具体错误码并定位到 Flinger 渲染管线崩溃。

---

### Bug 2: [7010662757](https://project.feishu.cn/axr/issue/detail/7010662757) — 超宽屏 X 键直接退出

| 维度 | Hermes | Claude Code |
|------|--------|-------------|
| 方法 | 架构推理 | 代码搜索 + 逻辑推导 |
| 代码 | 提到"冷启动走 DisplayManager.restoreConfig()"但无具体文件 | `control_glasses.cc:594-600` + `control_key_event.cc:738-743` |
| 日志 | 引用已有分析中的日志 | 引用 `space_mode:0 ultra:2` 分离状态 |
| 自信度 | 0.78 | 0.86 |
| 结论 | "交互状态机初始化回调遗漏"（方向对但不精确） | "SceneMode 门控导致非 SPACE_SCREEN 模式跳过超宽屏配置" |

**差异**: 两者方向一致，但 Hermes 停留在架构推理层面，Claude Code 找到了具体代码行和精确的因果链。

---

### Bug 3: [7006901369](https://project.feishu.cn/axr/issue/detail/7006901369) — 6dof 插拔黑屏

| 维度 | Hermes | Claude Code |
|------|--------|-------------|
| 代码 | 无具体引用 | `PerceptionChannelProvider::NotifyDataToAll()` |
| 日志 | 未分析原始日志 | `pose_buffer is empty ×233`, `not_shown:20999`, `pose_error:1104057` |
| 平台判断 | **误判为"Windows UVC驱动"**——但实际日志来自眼镜端 xrlinux | 正确识别为眼镜端 log_39/pilot.log |
| 自信度 | 0.50 | 0.84 |

**显著差异**: Hermes 将平台误判为 Windows NUC 侧（实际是眼镜端日志），导致整个分析方向偏差。Claude Code 从实际日志中提取眼镜端的 Flinger 统计数据，正确定位到眼镜端 Perception Pose 管道初始化失败。

---

### Bug 4: [7010973731](https://project.feishu.cn/axr/issue/detail/7010973731) — 模式切换声音卡住

| 维度 | Hermes | Claude Code |
|------|--------|-------------|
| 关键证据 | 缺失 | `need_to_change_dp:false` → DP 仍断开（证明下层问题非 Dove 触发） |
| 定量分析 | 无 | 15 次 DP 重协商 → 7 秒故障 |
| 日志 | 仅 20 行摘要 | 完整 14988 行 pilot.log 精确提取每次 DP 事件 |
| 自信度 | NOT_ENOUGH_INFO | 0.80 |

**显著差异**: 最关键的区别在于 Claude Code 发现了 `need_to_change_dp:false` 这个反直觉证据——Dove 认为自己不需要切换 DP，但 DP 实际断开了，直接排除了 Dove 层的逻辑错误假设。

---

### Bug 5: [7010756083](https://project.feishu.cn/axr/issue/detail/7010756083) — 超宽屏花屏

| 维度 | Hermes | Claude Code |
|------|--------|-------------|
| 核心判断 | "映射缺失+Clamp失效+状态不同步" ✅ | "缺 ultra_wide_ CanvasPanel + CanvasDepthInfo 边界过宽" ✅ |
| 代码 | 引用历史 Bug 6496287377 作为参照 | `permanent_config.h:31-36`（缺 ultra_wide_ panel）+ `flinger_model.h:10-15`（min_depth=0.1m 过小） |
| 自信度 | 0.90（偏高） | 0.70（更保守） |

**平局**: 两者核心判断一致，Hermes 更早给出结论（ENOUGH_INFO），Claude Code 代码定位更具体但置信度更保守。

---

### Bug 6: [7013445690](https://project.feishu.cn/axr/issue/detail/7013445690) — 3D 模式空间悬停黑屏

| 维度 | Hermes | Claude Code |
|------|--------|-------------|
| 根因 | "DP重协商失败" 与 "Pose渲染管线错误" 两假设摇摆 | `SetTranslationEnable` 第 466-469 行强制退出 3D SBS（设计行为） |
| 代码 | 无 | `control_state_machine.cc:466-469`（精确）+ `:572-576`（6DoF≠3D SBS 互斥确认） |
| 自信度 | 0.56（低） | 0.86（高） |

**显著差异**: Hermes 无法区分"DP重协商失败"和"Pose错误"哪个是根因；Claude Code 找到了代码级的强制退出逻辑，明确这是设计行为而非 Bug。

---

## 总体统计

| 指标 | Hermes | Claude Code |
|------|--------|-------------|
| **有代码级定位（文件:行号）** | 0/6 | 5/6 |
| **有日志原始数据引用** | 0/6 | 5/6 |
| **置信度 ≥ 0.80** | 1/6 | 5/6 |
| **ENOUGH_INFO 判定** | 1/6 | 5/6 |
| **平台/架构判断错误** | 1 次（Bug 3 误判平台为 Windows） | 0 次 |
| **平均置信度** | ~0.62 | ~0.81 |

---

## 结论

**Claude Code Agent 显著优于 Hermes Agent**（5:0:1，即 Claude Code 胜 5 场 : Hermes 胜 0 场 : 平局 1 场），核心差异在于：

1. **工具链差异**: Claude Code 能直接执行代码搜索（`grep`）+ 脚本提取日志数据；Hermes 依赖预缓存的摘要信息
2. **证据质量**: Claude Code 分析 5/6 包含原始日志时间戳和代码行号；Hermes 0/6 有具体代码引用
3. **分析粒度**: Claude Code 给出具体文件:行号的代码级根因；Hermes 多停留在"可能/推测"层面
4. **收敛能力**: Hermes 2/6 卡在 NOT_ENOUGH_INFO；Claude Code 5/6 给出明确结论
5. **Bug 5（花屏）平局**: Hermes 在纯架构推断场景下表现也不错，方向正确
