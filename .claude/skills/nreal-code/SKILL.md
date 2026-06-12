---
name: code-fetcher
description: |
  管理 nreal-code 目录下的代码仓库。支持更新仓库、查看最近 N 次提交记录（含作者和时间）。
  可用仓库: dove, ferrit, framework, leopard, ov580_driver, project, sparrow, heron, util, xr_codec, nrsdkrepo
  用法: 说"更新代码"、"更新 dove"、"查看 ferrit 最近 5 次提交"
---

# Nreal Code Manager

> 管理本地 nreal-code 参考代码仓库，支持更新远程同步、查看提交记录。
> 除 nrsdkrepo 使用 master 分支外，其余仓库统一更新到 develop 分支。ferrit 和 ov580_driver 包含 submodule，会自动同步。

## 可用仓库

| 别名 | 目录 | 分支 | 说明 |
|------|------|------|------|
| dove | `nreal-code/nreal-dove/` | develop | XR 设备控制软件 (NRSDK) |
| ferrit | `nreal-code/nreal-ferrit/` | develop | OSD 模块 (含 submodule) |
| framework | `nreal-code/nreal-framework/` | develop | C++ 网络框架 |
| leopard | `nreal-code/nreal-leopard/` | develop | 感知模块 |
| ov580_driver | `nreal-code/nreal-ov580_driver/` | develop | OV580 摄像头驱动 (含 submodule) |
| project | `nreal-code/nreal-project/` | develop | C++ 构建系统框架 |
| sparrow | `nreal-code/nreal-sparrow/` | develop | AI/ML 模块 |
| heron | `nreal-code/nreal-heron/` | develop | 渲染引擎 (Flinger) |
| util | `nreal-code/nrealUtil/` | develop | 通用工具库（协调、SlamConf 等） |
| xr_codec | `nreal-code/nreal-xr_codec/` | develop | XR 编解码模块 |
| nrsdkrepo | `nreal-code/nreal-nrsdkrepo/` | master | NRSDK 构建 manifest 仓库 |

## 使用方法

| 用户说法 | 行为 |
|---------|------|
| "更新代码" / "更新全部" / "都更新" | 更新所有 8 个仓库 |
| "更新 dove" | 只更新 dove |
| "更新 dove 和 framework" | 更新 dove + framework |
| "更新 ferrit" | 更新 ferrit + submodule |
| "查看 ferrit 最近 5 次提交" | 显示 ferrit 最近 5 次提交（含作者、时间） |
| "查看 dove 最近 N 次提交" | 显示 dove 最近 N 次提交 |

## 执行脚本

```bash
cd .claude/skills/nreal-code
python3 update_code.py update [all|dove|ferrit|framework|leopard|project|sparrow ...]
python3 update_code.py log <仓库别名> <数量>
```

### 更新仓库

参数:
- `all` — 更新全部仓库
- 仓库别名（可多个）— 只更新指定的仓库

### 查看提交记录

参数:
- `仓库别名` — 指定要查询的仓库
- `数量` — 显示最近 N 次提交，默认 5

## 输出示例

### 更新

```
[✓] dove       develop  已是最新
[✓] ferrit     develop  更新了 3 个提交 (submodules synced)
[✗] leopard    develop  远程连接失败
```

### 提交记录

```
提交       | 作者              | 时间                | 说明
afde4a4    | pengxianheng-nreal| 2026-04-29 18:05:23 | chore: update claude.md
4d9f1d2    | subenle           | 2026-04-29 17:08:15 | feat: enter suspended state...
```

## 代码修改规则

> **`nreal-code/` 目录下的代码是只读参考代码，禁止直接修改。** 排查问题后如需修复，必须在用户指定的工作目录进行。

| 仓库 | 参考代码（只读） | 修复代码（可写） |
|------|-----------------|-----------------|
| dove | `nreal-code/nreal-dove/` | `/Users/apple/WorkSpace/nrsdk/dove` |
| ferrit | `nreal-code/nreal-ferrit/` | `/Users/apple/WorkSpace/nrsdk/ferrit` |
| framework | `nreal-code/nreal-framework/` | `/Users/apple/WorkSpace/nrsdk/framework` |
| heron | `nreal-code/nreal-heron/` | `/Users/apple/WorkSpace/nrsdk/heron` |
| leopard | `nreal-code/nreal-leopard/` | `/Users/apple/WorkSpace/nrsdk/leopard` |
| sparrow | `nreal-code/nreal-sparrow/` | `/Users/apple/WorkSpace/nrsdk/sparrow` |
| project | `nreal-code/nreal-project/` | `/Users/apple/WorkSpace/nrsdk/project` |
| ov580_driver | `nreal-code/nreal-ov580_driver/` | `/Users/apple/WorkSpace/nrsdk/ov580_driver` |
| util | `nreal-code/nrealUtil/` | `/Users/apple/WorkSpace/nrsdk/nrealUtil` |

- 分析 Bug 时在 `nreal-code/` 中搜索和阅读代码
- 修复 Bug 时在 `/Users/apple/WorkSpace/nrsdk/` 对应目录修改
- **修复前必须先切换到 develop 分支并拉取最新代码**：`git checkout develop && git pull origin develop`
- 修复完成后如需同步回 `nreal-code/`，由用户自行操作
