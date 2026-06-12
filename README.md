# BugInsight

AI 驱动的 Bug 分析工程 — 基于 [Claude Code](https://claude.ai/code) 技能系统，自动拉取飞书项目缺陷、下载日志附件、分析根因并回写结论到飞书评论。

## 工作流程

```
飞书缺陷 → feishu-bug-fetcher（拉取详情+附件）→ bug-analyzer（解压+分析日志/源码）→ 飞书评论（回写结论）
                                                   ↑
                                           code-fetcher（11 个 C++ 参考仓库）
```

## 前置条件

| 依赖 | 用途 |
|------|------|
| [Claude Code](https://claude.ai/code) | 运行技能的 AI 引擎 |
| 飞书项目 MCP 服务 | 调用飞书项目 API（缺陷增删改查、附件下载） |
| Git + GitHub SSH Key | 克隆/更新 9 个 C++ 参考源码仓库 |
| Python 3 | 执行辅助脚本（删除评论等） |
| tmux（推荐） | 后台持续运行自动分析 |

## 快速部署

### 1. 克隆仓库

```bash
git clone git@github.com:nreal-ai/BugInsight.git
cd BugInsight
```

### 2. 设置环境变量

```bash
export BUG_INSIGHT_FEISHU_PROJECT_KEY="<飞书空间 key，如 axr、sw_team>"
export BUG_INSIGHT_FEISHU_PLUGIN_ID="<飞书插件 ID>"
export BUG_INSIGHT_FEISHU_PLUGIN_SECRET="<飞书插件密钥>"
export BUG_INSIGHT_FEISHU_USER_KEY="<飞书用户 key>"
export BUG_INSIGHT_FEISHU_MCP_TOKEN="<MCP 服务 token>"
export BUG_INSIGHT_FEISHU_MCP_KEY="<MCP 服务 key>"
```

> 建议写入 `~/.zshrc` 或 `~/.bashrc`，避免每次手动 export。

### 3. 拉取参考源码

```bash
cd nreal-code
for repo in dove ferrit framework heron leopard ov580_driver project sparrow; do
  git clone --branch develop git@github.com:nreal-ai/nreal-${repo}.git
done
# ferrit 和 ov580_driver 含 submodule，需额外初始化
cd nreal-ferrit && git submodule update --init --recursive && cd ..
cd nreal-ov580_driver && git submodule update --init --recursive && cd ..
# 工具库
cd nrealUtil && git clone --branch develop git@github.com:nreal-ai/nrealUtil.git . && cd ..
cd ..
```

### 4. 减少权限弹窗（推荐）

BugInsight 在分析过程中会频繁下载附件、解压日志、调用 MCP，建议用以下方式启动 Claude Code 以减少弹窗中断：

```bash
claude --dangerously-skip-permissions
```

> 仅在信任仓库代码的场景下使用。也可以在 `.claude/settings.local.json` 中精细配置 allowlist，使用 `/fewer-permission-prompts` 技能自动生成。

### 5. 验证部署

在 Claude Code 会话中输入以下命令确认各技能可用：

```
更新代码              # 触发 code-fetcher
分析 axr bug          # 触发 bug-analyzer + feishu-bug-fetcher
```

## 使用方式

所有操作通过**自然语言对话**完成，无需记忆命令格式。

### 手动分析单个 Bug

```
分析 axr bug 7014151056
分析 sw_team bug 7014987726
```

系统自动：拉取缺陷详情 → 下载日志/截图附件 → 解压分析 → 展示结论。确认无误后：

```
添加评论
```

结论即写入飞书缺陷评论。

### 批量扫描

```
扫描 axr 未分析 bug
扫描 sw_team 未分析 bug
```

按优先级（P0 → P1 → P2 → 待定）依次分析，每个 Bug 分析完毕后展示结论，等待确认再写入飞书。

### 自动定时模式

```
切换到自动模式
```

启动后每分钟扫描一次新 Bug，全自动分析并回写结论，无需人工干预。切换回手动：

```
切换到手动模式
```

| 模式 | 分析过程 | 写入飞书 | 触发方式 |
|------|---------|---------|---------|
| **手动（默认）** | 全自动 | 人工确认后 | 用户对话触发 |
| **自动** | 全自动 | 全自动（零干预） | 定时扫描 |

### 更新参考源码

```
更新代码              # 更新全部 9 个仓库
更新 dove             # 仅更新 dove
查看 ferrit 最近 5 次提交
```

### 独立分析（不依赖飞书）

也支持直接分析崩溃日志或 zip 包：

```
分析 /path/to/crash.log
分析 /path/to/logs.zip
```

## 后台持续运行（tmux）

自动分析是会话级别的，退出终端后会停止。用 tmux 实现后台常驻：

```bash
# 安装 tmux
brew install tmux

# 创建会话并启动
tmux new -s buginsight
cd ~/WorkSpace/BugInsight
claude --dangerously-skip-permissions
# → 输入「切换到自动模式」

# 脱离会话（后台继续跑）
Ctrl+B 然后 D

# 下次重新连接
tmux attach -t buginsight
```

| 操作 | 命令 |
|------|------|
| 创建会话 | `tmux new -s <名字>` |
| 脱离会话 | `Ctrl+B` 然后 `D` |
| 重新连接 | `tmux attach -t <名字>` |
| 查看所有会话 | `tmux ls` |
| 删除会话 | `tmux kill-session -t <名字>` |
| 翻页查看历史 | `Ctrl+B` 然后 `[`（方向键翻页，`q` 退出） |

> **关键**：不要 `/exit` 或 `Ctrl+C` 退出 Claude Code，而是用 `Ctrl+B D` 脱离 tmux 会话。这样 Claude Code 在后台持续运行，SSH 断开也不受影响。

## 分析能力

| 类别 | 检测项 |
|------|--------|
| **Shader/渲染** | Vulkan/OpenGL 错误、Shader 编译失败、帧率异常 |
| **Native Crash** | SIGSEGV/SIGABRT、Tombstone、ASAN 报告 |
| **时序问题** | 超时、死锁、竞态条件、时钟问题、消息乱序 |
| **通用日志** | 异常堆栈、错误码模式、关键日志匹配 |

分析结论含置信度评分：🟢 高（≥0.80）、🟡 中（0.40-0.79）、🔴 低（<0.40）。

## 防重复分析

系统在飞书缺陷评论中写入 `by Claude Code` 标记。分析每个 Bug 前会先检查评论中是否已有此标记，有则跳过。确保同一 Bug 不会被重复分析。

## 分析记录

所有自动分析结果记录在 [`analysis_log.md`](analysis_log.md)，按项目（AXR / SW Team）分开，包含 Bug ID、标题、置信度、耗时和飞书链接。

## 目录结构

```
BugInsight/
├── .claude/
│   ├── skills/
│   │   ├── bug-analyzer/         # Bug 分析核心逻辑
│   │   ├── feishu-bug-fetcher/   # 飞书缺陷数据获取
│   │   └── nreal-code/           # 仓库更新 + 提交查看
│   ├── scripts/
│   │   ├── delete_comment.py     # 删除飞书评论（Direct API）
│   │   └── fetch_bug_log.sh      # 下载缺陷附件
│   ├── AGENTS.md                 # 技能注册
│   └── settings.local.json      # 权限配置
├── nreal-code/
│   ├── nreal-dove/               # XR 设备控制软件 (NRSDK)
│   ├── nreal-ferrit/             # OSD 模块
│   ├── nreal-framework/          # C++ 网络框架
│   ├── nreal-heron/              # 渲染引擎 (Flinger)
│   ├── nreal-leopard/            # 感知模块
│   ├── nreal-ov580_driver/       # OV580 摄像头驱动
│   ├── nreal-project/            # C++ 构建系统框架
│   ├── nreal-sparrow/            # AI/ML 模块
│   └── nrealUtil/                # 通用工具库
└── analysis_log.md               # 自动分析结果记录
```

## 技术栈

- **AI 引擎**: Claude Code CLI + Claude Agent SDK
- **参考源码**: C/C++（CMake + Conan 2.0，统一 `develop` 分支）
- **集成**: 飞书项目 MCP + Direct API
- **辅助脚本**: Python 3

## 常见问题

**Q: 分析一个 Bug 大概多久？**  
A: 1-5 分钟，取决于日志附件大小和问题复杂度。

**Q: 如何新增一个项目？**  
A: 在 Claude Code 中说「添加项目 <project_key>」，系统会更新配置。当前已支持 `axr` 和 `sw_team`。

**Q: 自动模式会不会漏掉 Bug？**  
A: 按优先级排序（P0 优先），且通过评论标记防止重复。如果需要排查某特定 Bug，可以手动指定 ID 分析。

**Q: 参考代码必须拉取吗？**  
A: 非必须，但没有源码参考时分析深度会受影响——只能基于日志推断，无法对照代码定位根因。
