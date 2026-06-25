# Harness 架构进一步内聚的目标形态

> 文档角色：补充判断文档
>  
> 是否建议默认加载：按需
>  
> 适合回答的问题：
> - 架构还能怎样继续内聚
> - 哪些概念应该继续消失
> - 最终最短主链应收成什么样
>  
> 建议搭配阅读：
> - `harness-composition-refactor-plan.md`
> - `harness-runtime-contracts.md`

## 1. 文档目的

本文不再讨论迁移步骤，也不再重复组件拆分方案，而是回答一个更尖锐的问题：

在已有重构方案的基础上，架构还能如何继续内聚，直到主对象足够少、主链足够短、职责足够稳定。

本文是以下文档的补充收口：

- [harnessed-react-agent-redesign.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harnessed-react-agent-redesign.md)
- [harness-composition-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-refactor-plan.md)
- [harness-runtime-contracts.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-runtime-contracts.md)

## 2. 当前方案还不够内聚的地方

即使已经完成前述拆分，系统中仍然容易残留以下“重复语义”：

1. 同一种任务含义在 `Request / Spec / Workflow State / Node` 多处出现。
2. 同一种知识能力在 `Tool / Capability / Facade / QueryEngine` 多处出现入口语义。
3. 同一种执行控制在 `Kernel / Stage / Executor / Harness compatibility layer` 多处出现。
4. 同一种扩展能力在 `继承 / registry / workflow glue / container` 多处都能插手。

如果这些重复语义不继续收掉，系统虽然看起来分层了，但不会真正变简单。

## 3. 最终希望保留的五类主对象

进一步内聚后，系统应尽量只保留以下五类主对象：

### 3.1 运行对象

- `TaskSpec`
- `TaskRun`

职责：

- 描述一次任务应该做什么
- 描述一次任务当前跑到了哪里

### 3.2 执行对象

- `Recipe`
- `Stage`

职责：

- 决定任务按什么步骤推进
- 决定单步该做什么

### 3.3 能力对象

- `Tool`

职责：

- 提供唯一的上层能力调用面

### 3.4 领域对象

- `Facade`

职责：

- 把某个领域内部复杂实现收口成稳定门面

### 3.5 治理对象

- `Policy`
- `Guardrail`
- `Budget`

职责：

- 决定什么能做、做到什么程度、失败后怎么收口

如果一个对象无法清晰归入这五类之一，通常说明它要么职责重复，要么应该被删掉。

## 4. 最短主链

进一步内聚后的理想主链应收口为：

```text
TaskSpec
  -> Recipe
    -> Stage
      -> Executor
        -> Tool
          -> Facade
```

这条链的含义是：

- 外层世界只提交任务定义
- 任务如何推进由 recipe 决定
- 每一步做什么由 stage 决定
- 副作用如何执行由 executor 决定
- 可调用能力统一表现为 tool
- 真正领域实现藏在 facade 后面

这条链之外，不应再出现第二条平行主链。

## 5. 需要继续消失的概念重复

### 5.1 弱化 Node 的独立语义

如果后续 `workflow node` 继续和 `stage` 并存，并且两者都承载业务语义，那么系统仍然是双轨的。

目标状态：

- `Node` 只是图执行器内部术语，或者彻底退化为实现细节
- 对外和对业务建模，只保留 `Stage`

换句话说：

- 业务方理解 `stage`
- runtime 内部如果仍然需要 graph/node，那只是底层细节，不再是架构主对象

### 5.2 弱化 Capability 的上层入口语义

如果未来上层还同时面向：

- `Tool`
- `Capability`
- `Facade`

三套入口，那知识、仓库、数据库等能力仍然不够内聚。

目标状态：

- 对 harness 和 workflow：只看见 `Tool`
- 对 domain 内部：只看见 `Facade`
- `Capability` 如果保留，只作为 provider 协议或部署边界，不再是上层主调用面

### 5.3 弱化 QueryEngine 的上层语义

如果 `RagQueryEngine` 继续和 `RagFacade` 并列成为“可以从上层直接调用的知识入口”，那就是重复。

目标状态：

- `RagFacade` 是唯一 domain 门面
- `RagQueryEngine` 如果保留，应仅是 facade 内部组件

### 5.4 弱化 Orchestrator 的业务主导语义

如果后续 `QueryWorkflowOrchestrator`、`TaskWorkflowOrchestrator` 继续承担大量领域决策，那么 `Recipe` 会沦为空壳。

目标状态：

- orchestrator 只负责装配、提交、运行切换
- 真正业务推进逻辑下沉到 `Recipe / Stage`

## 6. 哪些层可以继续合并

### 6.1 Query 与 Task 的运行骨架

如果 `query` 和 `task` 只是入口不同、领域 recipe 不同，那么它们不应再拥有两套语义不同的运行骨架。

应合并为：

- 同一个 `RuntimeEntry`
- 同一个 `HarnessKernel`
- 同一组基础治理组件

差异只体现在：

- request adapter
- recipe
- stage 组合

### 6.2 Knowledge Surface 的重复入口

以下几层最终应压成两层：

```text
Tool Surface
Facade Surface
```

而不是：

```text
Tool Surface
Capability Surface
Facade Surface
Query Engine Surface
```

### 6.3 Harness 组件中的策略分布

如果 fallback、sandbox、budget、policy 逻辑继续散落在：

- stage
- executor
- compatibility harness
- workflow glue

那 runtime 仍然不够内聚。

目标状态：

- 执行策略统一进入 executor/policy 层
- stage 不直接持有治理逻辑

## 7. 哪些扩展点应继续收缩

一个真正内聚的系统，扩展面不应太多。建议最终只保留两类扩展：

### 7.1 结构扩展

通过继承扩展：

- `Recipe`
- `Stage`
- `Tool`
- `SubAgent`

### 7.2 领域能力扩展

通过组合接入：

- `Facade`
- `ToolAdapter`
- `Tool`

除此之外，以下对象不应成为常规扩展点：

- `Kernel`
- `RuntimeEntry`
- `ToolExecutor`
- `ToolRegistry`

原因很简单：这些对象一旦频繁被继承和定制，就会重新长成新的隐式框架。

## 8. 更高内聚的判断标准

下面这些问题可以用来判断系统是否已经进一步收紧：

### 8.1 新增一个任务类型时，需要改什么

理想答案：

- 新增 request adapter
- 新增 recipe
- 视需要新增 stage

不理想答案：

- 要改 kernel
- 要改 orchestrator 主循环
- 要改 executor

### 8.2 新增一个领域能力时，需要改什么

理想答案：

- 新增 facade
- 新增 tool adapter
- 新增 tool

不理想答案：

- 直接改 workflow 节点
- 直接改 stage 内部逻辑
- 直接让 harness 认识这个 domain service

### 8.3 新增一种治理策略时，需要改什么

理想答案：

- 扩展 policy/guardrail 配置
- 调整 executor 或 governance component

不理想答案：

- 在多个 stage 里手写判断
- 在多个 node 里复制 budget/fallback 逻辑

## 9. 对当前项目的最终收口建议

结合当前代码，最值得继续内聚的三件事如下。

### 9.1 继续收掉 RAG 的上层重复入口

目标：

- 对上层只保留 `rag_* tools`
- 对 domain 只保留 `RagFacade`
- `KnowledgeCapability / RagQueryEngine` 都退回内部实现或兼容层

### 9.2 继续收掉 workflow 中 `node` 的业务语义

目标：

- `Recipe / Stage` 成为唯一业务推进语言
- node 退回 runtime/graph 内部细节

### 9.3 继续收掉 stage 中的治理逻辑

目标：

- stage 只表达单步业务动作
- timeout/retry/sandbox/fallback/budget 由 executor/policy 统一管理

## 10. 进一步内聚后的终态描述

最终理想状态下，这个系统可以用下面几句话解释清楚：

1. 外部系统只提交 `TaskSpec`。
2. runtime 用 `Recipe` 决定任务怎么推进。
3. 每个 `Stage` 只负责一个单步动作。
4. 所有可调用能力统一表现为 `Tool`。
5. 每个领域的真实实现都藏在 `Facade` 后面。
6. `Kernel / Executor / Registry` 是稳定骨架，不作为常规扩展点。

如果未来能用这六句话把系统解释清楚，说明架构已经足够内聚。

## 11. 一句话结论

继续内聚的关键不是再拆更多组件，而是继续消灭“同一种语义在多层重复出现”的现象。

最终应把系统收成：

`TaskSpec -> Recipe -> Stage -> Executor -> Tool -> Facade`

并且只允许：

- `Recipe / Stage / Tool / SubAgent` 扩展
- `Facade` 演进
- 其余骨架保持稳定
