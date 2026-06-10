---
name: bug-analyzer
description: |
  Bug分析工具，用于分析缺陷并给出结论。支持四种数据来源：
  (1) ZIP 压缩包（自动解压分析日志、图片、崩溃文件）
  (2) 本地文件或目录
  (3) 飞书对话上传的 zip 文件 + 描述
  (4) 飞书项目中的缺陷ID/链接
  
  **检测能力**:
  - ✅ Shader/渲染错误: Vulkan、OpenGL、GPU 错误、Shader 编译失败、帧率异常
  - ✅ Native Crash: SIGSEGV/SIGABRT 等崩溃信号、Tombstone、ASAN 报告
  - ✅ 时序问题: 超时、死锁、竞态条件、时钟问题、消息乱序、同步问题
  
  适用于：分析crash、解析日志、处理coredump、推理根因、生成分析报告
---

# Bug Analyzer

> 分析缺陷日志并给出结论的专业工具。
> 
> **依赖**: 飞书 MCP (FeishuProjectMcp) 需已全局配置。

## 快速开始

### 分析 ZIP 日志文件
```bash
cd bug-analyzer/scripts
python3 bug_analyzer.py analyze /path/to/bug_report.zip
```

### 分析单个日志文件
```bash
python3 bug_analyzer.py analyze /path/to/log.txt --report --llm
```

### 搜索相似缺陷
```bash
python3 bug_analyzer.py search "USB连接异常"
```

### 分析飞书项目中的缺陷
直接在对话中告诉我缺陷链接或ID，我会通过飞书MCP获取详情并分析。

## 输入方式

根据用户提供的输入类型，选择对应的处理方式：

### 方式1: ZIP 压缩包
- 用户提供本地 ZIP 文件路径
- 使用 Python 脚本解压分析: `python3 bug_analyzer.py analyze <path>`
- 或用 Claude Code 工具: 用 Bash 解压，Read 读取其中的日志/图片

### 方式2: 本地文件/目录
- 用户指定路径，如某个日志目录
- 用 Read 读取日志文件，Bash 分析 coredump

### 方式3: 飞书对话中的文件
- 用户在对话中发送了 zip 文件或日志
- 确认文件保存路径后按方式1处理

### 方式4: 飞书缺陷链接/ID
- 用户提供飞书缺陷链接或缺陷ID
- 使用飞书 MCP 工具获取缺陷详情:
  - `get_workitem_brief` - 获取缺陷概况
  - `list_workitem_field_config` - 获取字段配置
  - `get_download_url` - 获取附件下载链接
- **`get_workitem_brief` 必须传入 `fields` 参数查询附件字段**（默认返回不包含文件附件）。至少查询：`["attachment", "multi_attachment", "field_d9e47e", "description"]`。优先用 `list_workitem_field_config` 的 `field_query="附件"` 获取当前空间下所有附件字段名，然后全部传入
- **禁止**调用 `list_workitem_comments` 读取当前 Bug 的评论，分析必须独立于已有评论
- 搜索相似缺陷时，可调用 `list_workitem_comments` 查看其他 Bug 的已有分析结论进行对比

### 方式5: 实时搜索相似缺陷
- 用户说"帮我找相似的缺陷"或"搜索一下有没有类似问题"
- 使用飞书 MCP 的 `search_by_mql` 在飞书项目中实时搜索:
  ```
  search_by_mql(project_key="<project_key>", mql="SELECT work_item_id, name FROM <空间名>.issue WHERE name LIKE '%关键词%' LIMIT 10")
  ```
  - 空间名通过 `search_project_info` 获取
  - 缺陷类型通过 `list_workitem_types` 获取（通常是 `issue`）
- 对匹配到的**其他相似缺陷**，可调用 `list_workitem_comments` 查看已有分析结论进行对比（仅限相似缺陷，当前分析的 Bug 仍禁止参考评论）

## 核心原则：日志 + 代码 结合分析

> **分析 Bug 时必须同时结合日志和代码，二者缺一不可。**

| 步骤 | 做法 | 目的 |
|------|------|------|
| 从日志找线索 | 搜索错误、异常、关键函数调用 | 定位问题发生的时间点和现象 |
| 从代码找原因 | 根据日志中的函数名、错误码搜索源码实现 | 理解调用链路和逻辑 |
| 交叉验证 | 用代码逻辑验证日志行为是否正常，用日志现象回溯代码 Bug | 确认根因不是猜测，有代码+日志双重证据 |
| 验证日志完整性 | 根据代码中的日志打印，确认操作是否真的发生 | 避免分析"不存在的操作"（如日志只有 Get 没有 Set 说明操作根本没执行） |

**反例**：只看日志推测根因但不搜代码验证 → 可能得出错误结论。只看代码猜测问题但不看日志确认 → 无法确定 Bug 是否由该代码引起。

## 重要规则：全自动执行

> **Bug 分析全流程自动执行，不弹窗、不询问、不需要用户确认任何中间步骤。** 包括但不限于：下载附件、解压日志、搜索代码、调用 MCP 工具。用户说"分析"就直接出结论，中间所有操作静默完成。

## 重要规则：分析独立性

> **分析 Bug 时禁止参考该 Bug 中已有的评论内容。** 不要调用 `list_workitem_comments` 获取评论，也不要阅读任何已有评论。分析必须完全基于日志、代码和缺陷描述，确保结论不受他人观点影响。

- 只用 `get_workitem_brief` 获取缺陷描述、复现步骤、附件等基本信息
- 不调用 `list_workitem_comments`（除非用户明确要求查看评论，而非分析 bug）
- 分析结论必须来源于日志+代码的独立推理，不得引用或参考已有评论中的任何分析方向

## 重要规则：结论添加方式

> **手动模式（默认）**：分析结论必须先展示给用户，等用户确认后，才能添加到飞书缺陷备注中。禁止自动添加。
> **自动模式（Bug Auto Analyzer）**：定时扫描任务可以直接添加评论，无需人工确认。

- 手动模式下，分析完成后，在对话中展示结论，询问"需要添加到缺陷评论吗？"
- 用户明确说"添加"、"追加"、"加上去吧"等时，才调用 `add_comment`
- 用户说"删掉"、"不要加"、"先不加"时，不添加
- 自动模式下，分析完成后直接调用 `add_comment` 追加结论
- **添加评论时不要 @ 任何人**，禁止在 content 中使用 `@` mention 语法
- **每条评论末尾必须追加免责声明**：`> ⚠️ 此分析来源于 AI（Claude Code + deepseek-v4-pro），仅供参考。`
- **分析结论中必须注明具体分析的是哪个日志文件**（如有日志附件），格式：`**分析日志**：附件解压后 current_log_dir/pilot.log`（解压后直接是 current_log_dir 目录，不要写 symlink 目标如 log_12）
- **评论标题统一用 `## 🔍 AI分析结论 (by Claude Code + deepseek-v4-pro)`**
- **标题下方必须附带缺陷链接**，格式：`**缺陷链接**：[https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}](https://project.feishu.cn/{project_key}/issue/detail/{work_item_id})`
- **分析结论中不要写"缺陷概要"和"状态"等冗余信息**，直接从根因分析开始。标题已经包含缺陷链接，不需要在内容中重复缺陷名称和状态
- **每条评论必须包含置信度评估**（步骤6），发现多个独立问题时分别给出置信度，格式参见 `references/analyze_prompt.md`

## 自动分析模式

> 用户说"启动自动分析"进入此模式。全程零人工干预：扫描 → 分析 → 写评论全自动。

### 工作流程

```
查询未解决 bug
       │
       ↓
对每个候选 bug，先查评论 ──→ 有 AI 标记? ──是──→ 跳过，下一个
       │
       否
       ↓
在 analyzed_bugs 列表中? ──是──→ 跳过，下一个
       │
       否
       ↓
   分析 1 个 ──→ 写入飞书+输出摘要 ──→ 立即下一个
       │
       队列空
       ↓
等待 10 分钟 ──→ 重新查询
```

- **连续分析**：有未分析 bug 时，分析完一个立即分析下一个，不等待
- **每分析完一个必须在对话中输出摘要**：包含 Bug ID、标题、链接、置信度（格式见下方"分析结果必须输出 Bug 信息"），即使评论已写入飞书也要输出
- **轮询检测**：队列为空后，每 10 分钟查询一次是否有新 bug，有则立即开始连续分析
- **⚠️ 跳过策略（按顺序执行，命中即跳过）**：
  1. **先查评论**：调用 `list_workitem_comments` 检查评论中是否包含 `by Claude Code`，有则跳过。**这是第一道防线，必须执行。**（注意：不要匹配 `分析来源于 AI`，其他工具也会写此类字样，只有 `by Claude Code` 能唯一标识本系统分析）
  2. **再查列表**：bug 已在 `analyzed_bugs` 列表中 → 跳过

### 模式切换

| 操作 | 指令 | 效果 |
|------|------|------|
| 启动自动分析 | `启动自动分析` | 模式切为 auto + 立即开始连续分析 |
| 停止自动分析 | `停止自动分析` / `停止分析` | 模式切回 manual + 终止循环 |

### 配置

所有参数存储在 `memory/bug-auto-analyzer-config.md`：

| 参数 | 说明 |
|------|------|
| `max_per_batch` | 每轮分析数量，默认 1 |
| `include_statuses` | 视为未解决的状态：`OPEN, IN PROGRESS, REOPENED` |
| `priority_order` | 优先级排序：`P0, P1, P2, 待定` |
| `analyzed_bugs` | 已分析 bug ID 列表，防止重复分析 |
| `interval_minutes` | 连续模式下为 0，队列空后 10 分钟轮询 |

## 重要规则：分析结果必须输出 Bug 信息

> **每次分析完成后，必须在对话中输出一条摘要，包含 Bug 链接。** 无论手动模式还是自动模式，都要输出。

输出格式：
```
✅ 分析完成
| 项目 | 内容 |
|------|------|
| Bug ID | {work_item_id} |
| 标题 | {bug_title} |
| 链接 | [https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}](https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}) |
| 置信度 | 🟢/🟡/🔴 (0.XX) |
| 评论 | 已写入飞书（comment_id: xxx）/ 未添加 |
```

- **飞书链接格式**：`https://project.feishu.cn/{project_key}/issue/detail/{work_item_id}`
- **{project_key}** 从 `get_workitem_brief` 返回的 `owned_project.simple_name` 获取
- 自动模式下写 `已写入飞书`，手动模式下写 `未添加（等待确认）`

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

- 分析 Bug 时在 `nreal-code/` 中搜索和阅读代码
- 修复 Bug 时在 `/Users/apple/WorkSpace/nrsdk/` 对应目录修改
- **修复前必须先切换到 develop 分支并拉取最新代码**：`git checkout develop && git pull origin develop`
- 修复完成后如需同步回 `nreal-code/`，由用户自行操作

```
1. 数据获取 → 2. 日志解析 → 3. 错误提取 → 4. 根因分析(LLM) → 5. 报告生成 → 6. 置信度评估
```

### 步骤1: 数据获取

根据输入类型选择：
- ZIP文件：用 Bash 解压到临时目录，列出文件
- 本地路径：用 Bash 递归列出文件，Read 读取日志
- 飞书缺陷：用 MCP 获取缺陷描述、复现步骤、相关附件

### 步骤2: 日志解析

关键日志类型：
- `ERROR` / `FATAL` / `CRASH` - 错误级别
- `Exception` / `Traceback` - 异常堆栈
- `Segmentation fault` - 段错误
- `core dump` - 核心转储

#### 日志文件分类与代码对应

解压后的日志通常包含多组 log 目录（如 `log_19`~`log_23`）和一个 `current_log_dir` 软链接。每个目录下包含以下文件：

| 日志文件 | 打印来源 | 对应代码 |
|---------|---------|---------|
| `pilot.log` / `pilot.log.0` | Pilot 应用层（dove、ferrit、framework、heron、leopard、project） | ✅ 有源码，在 `nreal-code/` 下 |
| `user.log` | 用户态系统日志 | ❌ BSP 层，无源码 |
| `kernel.log` | Linux 内核日志（驱动、硬件异常等） | ❌ BSP 层，无源码 |
| `messages` | 系统消息日志 | ❌ BSP 层，无源码 |
| `daemon.log` | 守护进程日志 | ❌ BSP 层，无源码 |
| `auth.log` | 认证相关日志 | ❌ BSP 层，无源码 |
| `mpp.log` | 多媒体处理平台日志 | ❌ BSP 层，无源码 |
| `tcpm.log` | TCPM (USB PD) 日志 | ❌ BSP 层，无源码 |
| `aw35615.log` | AW35615 芯片日志 | ❌ BSP/MCU 硬件层，无源码 |

**代码覆盖说明**：
- ✅ **有源码**：`nreal-code/nreal-{dove,ferrit,framework,heron,leopard,project}` — 可搜索、可定位具体代码行
- ❌ **无源码（BSP/MCU/IMU 等硬件底层）**：MCU 固件、IMU 驱动、Perception 算法插件等偏硬件底层代码不在本仓库中
- ❌ **无源码（系统层）**：`user.log`、`kernel.log` 等为 Linux 系统/BSP 层打印，不在本仓库中

**分析策略**：
1. **优先从 `pilot.log` 入手** — 搜索错误码、异常堆栈、关键函数名，这是可直接定位源码的入口
2. **pilot.log 找不到线索时**，再分析其他日志文件（如 `user.log`、`kernel.log`）寻找系统层/BSP 层的异常信号
3. **跨日志交叉验证** — 用 `pilot.log` 中的时间戳对齐 `kernel.log` 中的驱动事件，判断问题发生在应用层还是底层

### 步骤3: 错误提取

提取关键信息：
- 错误类型 (Exception type)
- 错误消息 (Error message)
- 堆栈跟踪 (Stack trace)
- 时间戳 (Timestamp)
- 线程/进程ID

### 步骤4: 根因分析 (LLM 增强)

- 先用规则推断基础根因
- 置信度 < 0.7 时，调用 LLM 增强分析
- 可使用 Python 脚本: `python3 bug_analyzer.py analyze <path> --llm`

### 步骤5: 报告生成

- 使用 Python 脚本生成 Markdown 报告: `python3 bug_analyzer.py analyze <path> --report`
- 或直接输出到对话中
- 用户需要飞书文档时，用 MCP 创建文档

### 步骤6: 置信度评估

评估维度：
| 维度 | 权重 | 说明 |
|------|------|------|
| 日志完整性 | 10% | 错误数量，致命错误×2 |
| 堆栈质量 | 10% | 是否有≥3行的完整堆栈 |
| 错误明确性 | 10% | 错误码数量、崩溃签名 |
| Shader/渲染错误 | 15% | Vulkan/OpenGL/GPU 错误检测 |
| Native Crash | 15% | SIGSEGV 等崩溃信号、Tombstone、ASAN |
| 时序问题 | 10% | 超时、死锁、竞态条件、时钟问题 |
| 相似匹配度 | 15% | 相似缺陷数量和高分占比 |
| 根因确定性 | 15% | 根因推断的明确程度 |
| 日志来源可信度 | Bonus | 来自 kernel.log 的 coredump 证据 (+0.25) |
| 时间集中度 | +10% | 错误是否在短时间内集中 |

等级：
- 🟢 高 (≥0.7)：可直接使用
- 🟡 中 (0.4-0.7)：可参考，建议补充
- 🔴 低 (<0.4)：需更多日志

## 输出目标

根据用户要求输出到：
- 飞书文档 (使用飞书 MCP)
- 本地文件 (使用 Write 工具)
- 直接在对话中回复

## Bug 类型与代码仓库映射

分析 bug 时，根据用户描述中的关键词自动锁定相关代码仓库：

| Bug 类型关键词 | 对应仓库 | 说明 |
|---------------|---------|------|
| pilot、眼镜、device、端侧 | dove, ferrit, framework, heron, leopard, project | 眼镜端侧固件/软件问题 |
| host、主端、PC、电脑端 | ov580_driver, sparrow, project | 主机端驱动/软件问题 |

> **用法**: 当用户描述中包含上述关键词时，分析根因时优先在对应仓库中搜索相关代码和错误来源。

## NReal 代码仓库引用

已同步到项目根目录 `nreal-code/`:

| 仓库 | 说明 | 路径 |
|------|------|------|
| dove | 控制软件(NRSDK) | `nreal-code/nreal-dove/` |
| ferrit | Ferrit 模块 | `nreal-code/nreal-ferrit/` |
| framework | C++ 网络框架 | `nreal-code/nreal-framework/` |
| heron | 渲染引擎(Flinger) | `nreal-code/nreal-heron/` |
| leopard | 感知模块 | `nreal-code/nreal-leopard/` |
| ov580_driver | OV580 摄像头驱动 | `nreal-code/nreal-ov580_driver/` |
| sparrow | AI/ML 模块 | `nreal-code/nreal-sparrow/` |
| project | C++ 构建系统框架 | `nreal-code/nreal-project/` |

### 关键目录

- **dove/** — XR 设备控制核心
  - `dove/model/` — 设备状态模型
  - `dove/control/` — 业务逻辑控制器
  - `dove/plugin/` — 动态加载插件
  - `dove/util/` — 工具类 (日志: `DOVE_LOG_*`)

- **framework/** — 网络框架
  - `framework/net/engine/` — 高层 API
  - `framework/net/component/` — 底层网络组件
  - `framework/net/loop/` — 事件循环

### 代码搜索

在分析缺陷时，用 LSP 或 grep 在代码仓库中搜索相关错误码、函数定义：
```bash
# 在 dove 中搜索错误码
grep -rn "DOVE_ERR" nreal-code/nreal-dove/

# 搜索函数定义
grep -rn "function_name" nreal-code/nreal-framework/
```

## 飞书缺陷分析

从飞书获取缺陷信息并进行分析：

### 方式1: 飞书缺陷链接

```
用户: 分析这个缺陷 https://xxx
```

处理流程：
1. 从链接解析 project_key 和 work_item_id
2. 使用 `get_workitem_brief` 获取缺陷概况
3. 使用 `get_download_url` 获取附件并下载分析
4. 结合日志进行根因分析
5. **禁止**使用 `list_workitem_comments` 获取评论，分析必须独立

### 方式2: 缺陷ID

```
用户: 分析 BUG-1234
```

处理流程：
1. 使用飞书 MCP 搜索该 ID 对应的缺陷
2. 获取缺陷描述和日志
3. 进行分析

## 配置说明

配置文件位置：`bug-analyzer/config.yaml`

### 配置优先级

1. **环境变量**（最高优先级）
   - `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL`

2. **config.yaml 配置文件**

3. **代码默认值**（最低优先级）

### 敏感信息

API Key 等敏感信息不要写入 config.yaml，请使用环境变量：
```bash
export LLM_API_KEY="your-key"
export LLM_API_BASE="https://your-api-base/v1"
export LLM_MODEL="qwen3-coder-plus"
```

## 相关脚本

- **scripts/bug_analyzer.py** - 统一 CLI 入口（推荐）
- **scripts/analyzer.py** - 核心分析模块
- **scripts/code_search.py** - NReal 代码仓库搜索
- **scripts/similar_bugs.py** - 相似缺陷检测
- **scripts/report.py** - 报告生成
- **references/analyze_prompt.md** - 分析prompt模板
