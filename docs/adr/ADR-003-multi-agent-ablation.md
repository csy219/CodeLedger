# ADR-003: Multi-Agent 必须做消融，默认模式由数据决定

## Status

Accepted · 2026-10-18

## Context

CodeLedger 需要完成：

- 问题定位
- 代码修改
- 测试验证
- 失败诊断
- 最终检查

这些职责可以拆成 Planner / Editor / Verifier 三个 Agent。

但 Multi-Agent 会增加：

- 通信成本
- 状态复杂度
- 延迟
- 失败模式
- 调试难度

如果只是把三个 prompt 顺序调用，不能称为 Multi-Agent 系统。

## Decision

- 实现 Multi-Agent 模式：
  - Planner：定位问题、拆解任务、制定修改计划
  - Editor：根据计划生成 patch
  - Verifier：运行测试、检查 diff、判断是否通过
- 使用 LangGraph 子图编排三个角色。
- 必须有共享状态和 handoff 协议。
- 必须有最大轮数、预算和死锁/打转防护。
- 必须与单 Agent 做同环境、同预算、同任务集对比。
- 默认模式由数据决定：
  - resolve rate
  - cost per issue
  - p95 latency
  - regression rate
- 如果没有增益，Multi-Agent 保留为可选模式，并如实报告负结果。

## Consequences

### Positive

- 可以验证多 Agent 是否真的优于单 Agent。
- 可以观察角色分工对失败恢复的影响。
- 训练系统设计、状态管理和协作协议能力。
- 面试时可以展示真实消融数据，而不是概念堆砌。

### Negative

- 需要维护两套模式。
- 多 Agent 可能更慢、更贵。
- 共享状态和 handoff 容易产生 bug。
- 需要额外测试死锁、打转和预算耗尽。

### Mitigation

- 单 Agent 作为 baseline。
- 所有模式使用同一套任务集和预算。
- 记录每次 handoff 的输入、输出和耗时。
- 设置最大轮数和 token 预算。
- 无增益时默认使用单 Agent。