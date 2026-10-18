# ADR-002: MCP 作为外部工具接口，不替代内部 Function Calling

## Status

Accepted · 2026-10-18

## Context

CodeLedger 的 Agent 需要调用：

- 文件搜索
- 文件读取
- patch 应用
- 测试执行
- git diff
- 指标查询

这些工具既要被内部 Agent 调用，也要能被外部 MCP 客户端调用。

如果所有工具都只通过 MCP 暴露，会带来两个问题：

1. 内部调用多一层协议开销。
2. MCP 协议细节会渗透到 Agent 核心逻辑。

如果只做内部 Function Calling，又无法证明工具可以被其他 Agent / IDE / 桌面应用复用。

## Decision

- 内部工具调用继续使用 Function Calling + Pydantic schema。
- 对外提供 MCP server，作为工具接口的 adapter。
- MCP 不替代内部 tool registry。
- MCP 工具必须有风险分级：
  - `read`
  - `write`
  - `danger`
- 写操作必须有路径白名单和审计日志。
- 所有工具输出必须做 secret redaction。
- MCP 调用必须经过 Runtime 和 Sandbox，不允许绕过执行层。
- MCP 只负责协议互操作，不负责业务状态。

## Consequences

### Positive

- 内部调用保持简单、低开销。
- 外部客户端可以通过标准 MCP 协议复用工具。
- 可以做真实的 MCP server/client demo。
- 权限分级和审计日志可以复用到其他工具。

### Negative

- 需要维护内部 schema 与 MCP schema 的映射。
- 需要额外处理认证、权限和协议版本。
- 工具数量增加后，测试成本上升。

### Mitigation

- 内部 tool registry 作为唯一工具定义源。
- MCP adapter 从 registry 生成工具描述。
- 为每个工具增加集成测试。
- 写操作默认需要人工确认。