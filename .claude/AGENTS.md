# Skills

## bug-analyzer

- **Path**: `.claude/skills/bug-analyzer`
- **Description**: Bug 分析工具，支持四种数据来源：
  - ZIP 压缩包（自动解压分析日志、图片、崩溃文件）
  - 本地文件或目录
  - 飞书对话上传的 zip 文件 + 描述
  - 飞书项目中的缺陷 ID/链接
- **检测能力**: Shader/渲染错误、Native Crash（SIGSEGV/SIGABRT）、时序问题（超时/死锁/竞态）

## feishu-bug-fetcher

- **Path**: `.claude/skills/feishu-bug-fetcher`
- **Description**: 飞书项目缺陷数据获取工具，用于：
  - 批量获取缺陷列表、详情（含完整字段）、评论数据、附件信息
  - 通过 MCP 向缺陷追加/删除评论（含 @人员、图片、附件格式）

## code-fetcher

- **Path**: `.claude/skills/nreal-code`
- **Description**: 管理 nreal-code 目录下的 11 个代码仓库，支持更新和查看提交记录。
