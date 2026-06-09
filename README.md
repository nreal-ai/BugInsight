# BugInsight

AI 驱动的 Bug 分析工程。集中管理 NReal 八个子仓库参考代码，配合 Claude Code 技能自动化分析缺陷根因。

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/nreal-ai/BugInsight.git
cd BugInsight
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入真实的飞书项目认证信息：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
FEISHU_PLUGIN_ID=你的插件ID
FEISHU_PLUGIN_SECRET=你的插件密钥
FEISHU_USER_KEY=你的用户Key
FEISHU_MCP_TOKEN=你的MCP Token
FEISHU_PROJECT_KEY=sw_team
```

> `.env` 已被 `.gitignore` 排除，不会被提交到仓库。

### 3. 加载环境变量

使用前确保环境变量已加载：

```bash
# 方式一：手动 export
export $(grep -v '^#' .env | xargs)

# 方式二：使用 direnv（推荐）
echo "dotenv" > .envrc && direnv allow
```

### 4. 拉取参考代码

```bash
# 在 Claude Code 中执行
/nreal-code 更新代码
```

## 减少权限提示

BugInsight 的技能在执行过程中会频繁下载附件、解压日志、调用飞书 MCP，默认情况下 Claude Code 会对这些操作逐一弹窗确认。为减少中断，**建议使用 `--dangerously-skip-permissions` 启动 Claude Code**：

```bash
claude --dangerously-skip-permissions
```

> ⚠️ 此选项会跳过所有权限弹窗。仅在信任仓库代码的场景下使用，确保你的 `.env` 和脚本配置来自可信来源。

也可以在项目的 `.claude/settings.local.json` 中针对常用操作添加 allowlist，更精细地控制权限。详见 `/fewer-permission-prompts` 技能。

## 配置说明

之前硬编码在 `delete_comment.py` 和 `config.json` 中的密钥已移至环境变量。各环境变量的用途：

| 环境变量 | 必填 | 说明 |
|---------|------|------|
| `FEISHU_PLUGIN_ID` | 是 | 飞书项目插件 ID，用于获取 Plugin Token 调用 Direct API |
| `FEISHU_PLUGIN_SECRET` | 是 | 飞书项目插件密钥 |
| `FEISHU_USER_KEY` | 是 | 飞书用户 Key，作为 Direct API 请求的身份标识 |
| `FEISHU_MCP_TOKEN` | 是 | MCP Server Token，用于 MCP 工具调用 |
| `FEISHU_PROJECT_KEY` | 否 | 飞书项目标识，默认 `sw_team` |

所有脚本在加载配置时都会优先使用环境变量，其次才读取 `config.json` 中的占位符值。

## 可用技能

### bug-analyzer — 自动化 Bug 分析

支持四种数据来源：
- ZIP 压缩包（自动解压分析日志、图片、崩溃文件）
- 本地文件或目录
- 飞书对话上传的 zip 文件 + 描述
- 飞书项目中的缺陷 ID/链接

检测能力：Shader/渲染错误、Native Crash (SIGSEGV/SIGABRT)、时序问题（超时/死锁/竞态）

```
/bug-analyzer <飞书缺陷链接>
/bug-analyzer <本地日志目录>
```

### feishu-bug-fetcher — 飞书缺陷数据获取

批量获取飞书项目的缺陷数据，支持 MCP 查询和 Direct API 两种方式。

```
/feishu-bug-fetcher 查询 sw_team 最近 50 条 bug
/feishu-bug-fetcher 获取 6970632429 详情
```

### nreal-code — 代码仓库管理

管理 `nreal-code/` 下的八个参考代码仓库。

```
/nreal-code 更新代码
/nreal-code 查看 dove 最近 5 次提交
```

### openviking — 知识库服务

启动 OpenViking 语义搜索服务（dev 模式，本地 1934 端口）。

## 目录结构

```
BugInsight/
├── .claude/
│   ├── scripts/                 # 工具脚本
│   │   └── delete_comment.py    # 删除飞书评论（Direct API）
│   └── skills/
│       ├── bug-analyzer/        # Bug 分析技能
│       ├── feishu-bug-fetcher/  # 飞书数据获取技能
│       │   ├── scripts/         # 脚本（fetch_bugs.py 等）
│       │   └── references/      # 旧版参考脚本
│       └── nreal-code/          # 仓库管理技能
├── nreal-code/                  # 八个参考代码仓库（不纳入版本控制）
├── .env.example                 # 环境变量模板
├── .env                         # 真实密钥（已忽略）
└── README.md
```

## 技术栈

- **分析语言**: Python 3
- **参考代码**: C/C++ (dove, ferrit, framework, heron, leopard, ov580_driver, project, sparrow)
- **构建系统**: CMake + Conan 2.0
- **认证**: 飞书 Plugin Token + MCP Token
