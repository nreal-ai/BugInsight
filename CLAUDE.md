# BugInsight

AI 驱动的 Bug 分析工程。集中管理 NReal 十一个子仓库参考代码（dove、ferrit、framework、heron、leopard、ov580_driver、project、sparrow、nrealUtil、xr_codec、nrsdkrepo），配合 bug_analysis 和 code-fetcher 两个 Claude Code 技能自动化分析缺陷根因。

## 目录结构

```
BugInsight/
├── .claude/
│   ├── skills/
│   │   ├── nreal-code/      # 仓库更新 + 提交记录查看
│   │   └── bug_analysis/    # Bug/Crash 日志分析（飞书缺陷自动分析）
│   ├── AGENTS.md
│   └── settings.local.json
└── nreal-code/
    ├── nreal-dove/          # XR 设备控制软件 (NRSDK)，分支 develop
    ├── nreal-ferrit/        # OSD 模块 (含 submodule)，分支 develop
    ├── nreal-framework/     # C++ 网络框架，分支 develop
    ├── nreal-heron/         # 渲染引擎 (Flinger)，分支 develop
    ├── nreal-leopard/       # 感知模块，分支 develop
    ├── nreal-nrsdkrepo/     # NRSDK 构建 manifest，分支 master
    ├── nreal-ov580_driver/  # OV580 摄像头驱动 (含 submodule)，分支 develop
    ├── nreal-project/       # C++ 构建系统框架，分支 develop
    ├── nreal-sparrow/       # AI/ML 模块，分支 develop
    ├── nreal-xr_codec/      # XR 编解码模块，分支 develop
    └── nrealUtil/           # 通用工具库，分支 develop
```

## 可用技能

### code-fetcher

管理 nreal-code 目录下的代码仓库，支持更新和查看提交记录。

```
# 更新仓库
更新代码 / 更新 dove / 更新 dove 和 framework

# 查看提交
查看 ferrit 最近 5 次提交
查看 dove 最近 N 次提交
```

### bug_analysis

飞书项目缺陷自动分析，支持手动单 Bug 分析和定时批量分析。代码搜索统一使用 `nreal-code/` 中的仓库。

触发：说"分析飞书缺陷 XXX" 或 "设置缺陷自动分析"。

### openviking

启动 OpenViking 知识库服务（dev 模式，仅本地访问）：

```bash
cd ~/.openviking && nohup python3 -m openviking.server.bootstrap --config ~/.openviking/ov.conf --host 127.0.0.1 --port 1934 > /tmp/openviking.log 2>&1 &
```

验证是否启动成功：`lsof -i :1934`

启动 openviking 时直接执行，无需弹窗确认。

## 技术栈

- **语言**: C/C++ (dove, ferrit, framework, heron, leopard, ov580_driver, project, sparrow)
- **构建**: CMake + Conan 2.0
- **分支**: 统一使用 develop，ferrit 和 ov580_driver 含 submodule 会自动同步
