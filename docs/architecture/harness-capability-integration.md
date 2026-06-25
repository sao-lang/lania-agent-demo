# Agent 能力与 Harness Engineering 结合方案

> 文档角色：补充结合文档
>  
> 是否建议默认加载：按需
>  
> 适合回答的问题：
> - 各类 agent 能力怎样接到 harness engineering
> - `State / Workflow / Capability / Governance Harness` 四类管理面分别管什么
> - 当前项目代码能映射到哪一层
>  
> 建议搭配阅读：
> - `agent-capability-management-design.md`
> - `harness-composition-refactor-plan.md`

## 1. 文档目的

本文补充说明一个前面几份文档尚未单独展开的问题：

`session / memory / skill / tool / command / permission / sub-agent / hook / context / tech-stack`
这些能力如何与 `harness engineering` 结合，而不是各自平行存在。

本文承接以下文档：

- [harness-composition-refactor-plan.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-composition-refactor-plan.md)
- [harness-runtime-contracts.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-runtime-contracts.md)
- [agent-capability-management-design.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/agent-capability-management-design.md)
- [harness-final-cohesion-shape.md](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/docs/architecture/harness-final-cohesion-shape.md)

本文重点不是重新定义能力，而是回答：

1. harness engineering 到底管什么
2. 各类 agent 能力应该挂接到 harness 的哪一层
3. 当前项目代码里这些能力分别落在哪
4. 后续怎么从现状迁到目标形态

## 2. 核心原则

结合方式不是“把所有能力都塞进 harness”，而是：

- `Harness` 负责统一运行、治理、审计、恢复
- 各类能力通过清晰接口挂到 harness
- domain 能力不直接进入 harness core
- workflow 不直接越过 harness 调底层服务

一句话概括：

**Harness 是统一执行骨架，不是所有能力的实现容器。**

## 3. 四类 Harness

为了把能力接入方式说清楚，本文将 `Harness Engineering` 进一步拆成四类 harness：

```text
Harness Runtime
  -> State Harness
  -> Workflow Harness
  -> Capability Harness
  -> Governance Harness
```

这四类 harness 不是四套独立系统，而是统一 runtime 中的四个管理面。

### 3.1 State Harness

负责：

- session
- memory
- context
- checkpoint
- replay / resume
- artifact lineage

### 3.2 Workflow Harness

负责：

- skill
- recipe
- stage
- step lifecycle
- task progression

### 3.3 Capability Harness

负责：

- tool
- command
- sub-agent
- facade adapter
- capability routing

### 3.4 Governance Harness

负责：

- permission
- policy
- guardrail
- budget
- sandbox
- audit
- hook
- trace

## 4. 统一执行主链

结合后的统一主链应收口为：

```text
Request
  -> TaskSpec
  -> Harness Runtime
    -> State Harness
    -> Workflow Harness
    -> Capability Harness
    -> Governance Harness
  -> Result / Artifact / Audit
```

如果再展开一层，理想执行关系是：

```text
RuntimeEntry
  -> HarnessKernel
    -> Recipe
      -> Stage
        -> ContextProvider
        -> Policy / Guardrail
        -> ToolExecutor
          -> ToolRegistry
            -> Tool / CommandTool / SubAgent
              -> Facade
                -> Domain Service
```

这条链的含义是：

- `RuntimeEntry` 负责接收统一任务定义
- `HarnessKernel` 负责驱动整个运行时
- `Recipe / Stage` 负责任务推进
- `ContextProvider` 从 state 层切出当前 step 可消费上下文
- `Policy / Guardrail` 在执行前做治理判定
- `ToolExecutor` 统一执行副作用动作
- `ToolRegistry` 决定有哪些能力可用
- `Tool / CommandTool / SubAgent` 是统一能力面
- `Facade` 隔离上层和 domain 内部实现

## 5. 各类能力如何接到 Harness

## 5.1 Session 接到 State Harness

### 定位

`Session` 是跨轮交互容器，负责保存多轮对话上下文，不负责任务执行本身。

### 接入方式

由 `State Harness` 提供：

- session lookup
- session persistence
- summary compression
- session to task/run mapping

### 运行中怎么被用到

- request 进入时，挂载 `session_id`
- state harness 读取 session state
- context provider 把会话信息切成 `memory_slice/context_slice`
- stage 只消费切片，不直接操作 session store

### 不该怎么用

- stage 不直接读写底层 session 存储
- query engine 不应成为 session 的唯一宿主

## 5.2 Memory 接到 State Harness

### 定位

`Memory` 是跨 step、跨 run、跨 session 保留的结构化执行痕迹。

### 接入方式

由 `State Harness` 提供统一内存面：

- task memory
- session memory
- artifact memory
- reflection memory
- sub-agent memory
- tool call memory

### 运行中怎么被用到

- step 开始前，memory manager 提供摘要和切片
- step 完成后，runtime 写回 memory record
- replay/resume 从 memory + checkpoint 恢复

### 不该怎么用

- query 和 task 分别维护一套独立 memory 主链
- stage 直接往多个不同 memory 桶里散写

## 5.3 Context 接到 State Harness

### 定位

`Context` 是当前 step 的最小可消费输入，不等于 memory 原始数据。

### 接入方式

由 `State Harness` 提供 `ContextProvider`：

```text
State -> ContextProvider -> ContextBundle
```

### ContextBundle 建议组成

- `state_slice`
- `evidence_slice`
- `artifact_slice`
- `memory_slice`
- `tool_options`
- `token_budget`

### 运行中怎么被用到

- stage 不直接遍历大状态
- tool executor 也不直接接整个 workflow state
- 一切都通过 `ContextBundle` 传递最小必要信息

## 5.4 Skill 接到 Workflow Harness

### 定位

`Skill` 是任务模板，不是执行器，也不是工具。

### 接入方式

由 `Workflow Harness` 提供：

- `SkillRegistry`
- `Recipe selection`
- `Initial state builder`

### 运行中怎么被用到

- `TaskSpec.task_type` 决定选哪个 skill
- skill 决定初始 state 和 recipe
- recipe 决定 stage 组合

### 不该怎么用

- skill 里直接执行 tool
- skill 里直接 build domain service

## 5.5 Tool 接到 Capability Harness

### 定位

`Tool` 是上层唯一稳定能力面。

### 接入方式

由 `Capability Harness` 提供：

- `ToolRegistry`
- `ToolSchema`
- `ToolExecutor`

### 运行中怎么被用到

- stage 声明使用哪些 tool
- executor 调 registry 查 tool
- registry 返回 tool schema 和实现
- tool 内部转调 facade

### 不该怎么用

- workflow 节点直接调 capability/service
- stage 内部直接绕过 registry 调 tool 实例

## 5.6 Command 接到 Capability Harness

### 定位

`Command` 应作为 `Tool` 的特化，而不是平行执行体系。

### 接入方式

作为工具家族：

- `CommandTool`
- `RepositoryCommandTool`
- `ShellCommandTool`

统一仍进入：

- `ToolRegistry`
- `ToolExecutor`
- `Governance Harness`

### 原因

这样可以复用：

- policy
- sandbox
- timeout
- audit
- schema

避免长出第二套 command runtime。

## 5.7 Sub-Agent 接到 Capability Harness

### 定位

`Sub-Agent` 是受控执行资源，不是顶层运行时入口。

### 接入方式

由 `Capability Harness` 提供：

- `SubAgentRegistry`
- `SubAgentRuntime`
- `SubAgentHandoff`

### 运行中怎么被用到

- stage 判断是否需要 handoff
- capability harness 调起 sub-agent runtime
- sub-agent 只允许使用白名单工具
- 结果写回 state/memory/audit

### 不该怎么用

- sub-agent 自己控制主任务推进
- sub-agent 拥有独立 governance 面

## 5.8 Permission / Policy / Guardrail 接到 Governance Harness

### 定位

这些都是执行治理问题，不应由 workflow 或 tool 分散管理。

### 接入方式

由 `Governance Harness` 提供：

- `Permission boundary`
- `PolicyEngine`
- `GuardrailEngine`
- `Budget controller`
- `Sandbox engine`

### 运行中怎么被用到

在 stage 调 executor 前统一判定：

1. 当前请求是否有权限
2. 当前工具是否允许
3. 当前预算是否足够
4. 当前 payload 是否满足 guardrail
5. 当前调用是否必须进入 sandbox

### 不该怎么用

- 在多个 stage 中手写权限判断
- 在 workflow node 中复制 budget/fallback 逻辑

## 5.9 Hook 接到 Governance Harness

### 定位

`Hook` 是运行时扩展点，不是业务对象。

### 接入方式

由 `Governance Harness` 提供统一 hook/event 面。

建议事件点：

- `before_stage`
- `after_stage`
- `before_tool`
- `after_tool`
- `on_failure`
- `on_checkpoint`
- `on_run_completed`

### 运行中怎么被用到

- kernel 在关键节点触发 hook
- trace/audit/metrics/recovery 监听 hook
- 不要求每个业务 stage 自己记录所有事件

### 当前过渡方式

如果暂时还没有正式 hook registry，可以先把：

- `trace.record(...)`
- checkpoint created
- replay/resume events

收成统一 runtime event bus，再进一步演化为 hook 系统。

## 5.10 Tech Stack 接到 Infra Layer

### 定位

`Tech Stack` 只是承载方式，不是能力管理对象。

### 接入方式

由 `Infra Layer` 提供：

- provider factory
- container
- settings
- model router
- persistence
- sandbox worker

### 原则

workflow、stage、tool 使用方不直接感知：

- 用的是哪个 LLM provider
- 用的是哪个 vector store
- 是本地 sandbox 还是远程 sandbox

它们只消费 facade/tool 抽象。

## 6. 与当前项目代码的映射关系

## 6.1 State Harness 候选映射

当前可视为 state harness 基础的代码：

- `TaskMemory`
- session state / session persistence
- `ContextBundle`
- checkpoint / replay / resume 数据面

主要落点：

- [app/agents/memory.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/agents/memory.py)
- [app/harness/models.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/harness/models.py)
- `query_orchestrator.py` / `task_orchestrator.py` 中的 checkpoint 主链

当前问题：

- query memory 和 task memory 仍偏双轨
- session 仍偏 query 内聚，不是统一 state 面

## 6.2 Workflow Harness 候选映射

当前可视为 workflow harness 基础的代码：

- `TaskSkillRegistry`
- `TaskWorkflowOrchestrator`
- `TaskSpec / StepSpec / TaskRun`

主要落点：

- [app/workflows/tasks/skill.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/workflows/tasks/skill.py)
- [app/models/task.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/models/task.py)

当前问题：

- query/chat 还没有完全纳入 skill/recipe 统一面
- node/orchestrator 语义仍较强

## 6.3 Capability Harness 候选映射

当前可视为 capability harness 基础的代码：

- `ToolRegistry`
- `ExecutionHarness`
- `SubAgentRegistry`
- `SubAgentRuntime`
- 各类 capability tool

主要落点：

- [app/agents/tools/registry.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/agents/tools/registry.py)
- [app/harness/execution.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/harness/execution.py)
- [app/agents/subagents.py](file:///Users/bytedance/Desktop/files/self/lania-zip/rag/app/agents/subagents.py)

当前问题：

- `ExecutionHarness` 过厚，同时承担了 capability build 和执行治理
- RAG 还没有完全收成 `Facade -> Tool` 统一入口

## 6.4 Governance Harness 候选映射

当前可视为 governance harness 基础的代码：

- `GuardrailEngine`
- `PolicyEngine`
- `ToolSandbox`
- trace / checkpoint / replay / recover

主要落点：

- `app/harness/guardrails.py`
- `app/harness/policy.py`
- `app/harness/sandbox.py`
- `app/harness/recovery.py`
- `TraceRecorder`

当前问题：

- hook 仍未正式建模
- query/task 权限治理语义仍未完全统一
- 一部分治理逻辑仍散落在 workflow/node 中

## 7. 目标目录建议

如果完全按 harness engineering 落下来，建议目录逐步演进到：

```text
app/
  harness/
    core/
      kernel.py
      runtime_entry.py
    state/
      session_manager.py
      memory_manager.py
      context_provider.py
      checkpoint_manager.py
    workflow/
      skill_registry.py
      recipes/
      stages/
    capability/
      tool_registry.py
      tool_executor.py
      subagent_runtime.py
      adapters/
    governance/
      policy.py
      guardrail.py
      budget.py
      hooks.py
      audit.py
  domain/
    rag/
    repository/
    database/
    artifact/
  infra/
    container/
    providers/
    persistence/
    sandbox/
```

如果暂时不重排目录，也应先重排依赖方向。

## 8. 分阶段接入建议

## Phase 1：先立四类 harness 的管理归口

目标：

- 明确 state/workflow/capability/governance 四类归属
- 禁止新增跨层直连

### 当期重点

- `TaskMemory + session + ContextBundle` 按 state 面收口
- `TaskSkillRegistry` 扩为 workflow 面主入口
- `ToolRegistry + SubAgentRuntime` 作为 capability 面稳定入口
- `PolicyEngine + GuardrailEngine + ToolSandbox` 作为 governance 面稳定入口

## Phase 2：统一能力接入方式

目标：

- domain 能力统一经 facade
- harness 统一只认 tool/sub-agent

### 当期重点

- `RagFacade`
- `rag_* tools`
- 让 workflow/stage 不再直连 knowledge capability

## Phase 3：统一 state 与 hook

目标：

- query/task 共用 memory/context/checkpoint 主链
- trace/event 收口为 hook 面

### 当期重点

- memory data plane 统一
- `HookRegistry` 或 runtime event bus
- query/task 权限表达统一

## 9. 验收标准

完成后应能回答以下问题，而且答案只有一个：

1. session 归谁管  
   `State Harness`
2. memory 归谁管  
   `State Harness`
3. skill 归谁管  
   `Workflow Harness`
4. tool/command/sub-agent 归谁管  
   `Capability Harness`
5. permission/policy/hook/audit 归谁管  
   `Governance Harness`
6. tech stack 归谁管  
   `Infra Layer`

此外还应满足：

1. stage 不直接调 domain service
2. workflow 不直接写权限治理
3. tool 不是第二套 runtime
4. command 不成为平行执行体系
5. sub-agent 不是顶层入口

## 10. 一句话结论

这些 agent 能力与 harness engineering 的正确结合方式是：

**由 harness 提供统一运行骨架，再把 state、workflow、capability、governance 四类能力分别挂接进去。**

最终目标不是“所有能力都在 harness 里”，而是：

- `HarnessKernel` 只做统一运行
- `State Harness` 管状态
- `Workflow Harness` 管任务推进
- `Capability Harness` 管可调用能力
- `Governance Harness` 管约束、审计和扩展点
