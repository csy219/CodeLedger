# ADR-001: LangGraph 负责编排，Runtime 保留自研

## Status

Accepted · 2026-10-18

## Context

CodeLedger 需要同时具备：

- Agent 工作流编排
- 条件分支
- human-in-the-loop
- 断点恢复
- 幂等与失败恢复
- 可审计 trace

LangGraph 擅长状态图、条件边和 interrupt。
SourceLedger 已经有成熟的 checkpoint、retry、幂等与恢复经验。

如果把所有状态都交给 LangGraph，会带来两个问题：

1. LangGraph checkpoint 与项目已有 Runtime 状态重复。
2. 后续做数据库持久化、跨进程恢复、patch 幂等时，状态边界不清晰。

## Decision

- LangGraph 负责 Agent 编排：
  - StateGraph
  - 条件边
  - 子图
  - interrupt / human-in-the-loop
- 自研 Runtime 负责持久执行：
  - RunState
  - checkpoint
  - retry
  - idempotency
  - rollback
  - recovery
- LangGraph checkpoint 与 Runtime snapshot 通过明确的 bridge 同步。
- Runtime snapshot 是 source of truth。
- LangGraph 只保存编排状态，不保存完整业务事实。

## Consequences

### Positive

- 编排逻辑与持久执行逻辑解耦。
- 可以单独测试 Runtime 的恢复和幂等。
- 可以做 LangGraph 与自研 loop 的对比实验。
- 面试时可以解释“为什么用框架，但不被框架绑死”。

### Negative

- 需要维护 bridge。
- 存在两套状态模型，必须做一致性测试。
- 需要定义清楚 graph state 和 run state 的边界。

### Mitigation

- graph state 必须可序列化。
- 每次节点切换写入 Runtime checkpoint。
- 恢复时先从 Runtime snapshot 重建 graph state。
- 增加 kill/resume 回归测试，验证不会重复应用 patch。