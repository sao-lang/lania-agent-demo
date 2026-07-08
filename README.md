# Lania Agent Platform

> **Harness-first 通用 Agent 运行时平台** — RAG 只是能力之一，治理是平台核心。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-green.svg)](https://fastapi.tiangolo.com/)
[![LlamaIndex](https://img.shields.io/badge/LlamaIndex-0.10%2B-orange.svg)](https://www.llamaindex.ai/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-purple.svg)](https://www.langchain.com/langgraph)

---

## 📋 项目概览

这是一个**通用 Agent 运行时平台**，不是传统的 RAG 问答应用。核心设计理念是：

```text
传统 RAG 应用： 用户 → RAG Query Engine → 检索 → 生成 → 回答

Lania Agent Runtime：
  用户 → TaskSpec → HarnessKernel → Recipe/Stage
    → Tool(rag_*/repo_*/database_*/weather_*/finance_*/...) → Facade → Domain Service
```

**核心变化**：

1. **RAG 降级为能力** — RAG 只是众多 Tool 之一，与 repository / database / api_contract 平级，不再是产品主轴
2. **Harness 是顶层** — 治理（policy / guardrail / budget / sandbox / hook）由 Harness 统一控制，Agent 只负责任务推进
3. **ReAct 是局部的** — 只在单 step 内运行有界循环（max_turns=3），不接管系统治理

---

## 🏗️ 整体架构

系统采用**六层分层架构**，从外到内依次是：

```text
┌────────────────────────────────────────────────────────────┐
│                 Entry Layer (入口层)                        │
│   API / Service / Worker 入口、请求适配为 TaskSpec           │
├────────────────────────────────────────────────────────────┤
│                  State Layer (状态层)                        │
│   Memory / Context / Checkpoint / Artifact Lineage          │
├────────────────────────────────────────────────────────────┤
│                 Workflow Layer (工作流层)                    │
│   Recipe / Stage / Step Lifecycle / LangGraph 编排          │
├────────────────────────────────────────────────────────────┤
│                Capability Layer (能力层)                     │
│   Tool / SubAgent / Facade Adapter / Capability Routing      │
├────────────────────────────────────────────────────────────┤
│                Governance Layer (治理层)                     │
│   Policy / Guardrail / EventBus Hook / Sandbox / Budget      │
├────────────────────────────────────────────────────────────┤
│                  Infra Layer (基础设施层)                    │
│   LLM Provider / Vector Store / SQLite / Sandbox Worker     │
└────────────────────────────────────────────────────────────┘
```

### 核心执行主链

```text
请求 → Entry → TaskSpec → HarnessKernel → Recipe → Stage → ToolExecutor
                                    ↓                          ↓
                               EventBus ──→ HookRegistry → Trace/Memory/Checkpoint
           (每次 stage/tool 事件都广播，治理逻辑通过 Hook 横向切面)
```

**一句话主链**：`TaskSpec → Recipe → Stage → Executor → Tool → Facade`

这条链之外不应有第二条平行主链。

---

## 📁 项目目录结构

```text
app/
├── main.py                       # FastAPI 应用入口
├── container.py                  # [Assembly] 依赖容器，所有组件按顺序装配
│
├── api/                          # [Entry] HTTP 接口层
│   └── v1/endpoints/
│       ├── query.py              # query / chat / stream 接口
│       ├── tasks.py              # 任务 CRUD 接口
│       ├── agent.py              # 统一 Agent 对话入口（新增）
│       ├── documents.py          # 文档上传/导入
│       ├── collections.py        # 知识库集合管理
│       ├── sessions.py           # 会话管理
│       ├── eval.py               # 评测接口
│       ├── feedback.py           # 反馈接口
│       ├── health.py             # 健康检查/metrics
│       ├── capabilities.py       # Capability 列表
│       └── admin_*/              # 管理后台接口（LLM/Skill/Agent/Prompt/MCP）
│
├── services/                     # [Entry+Infra] 业务服务入口
│   ├── query_service.py          # query/chat 请求入口服务
│   ├── task_service.py           # task 任务入口服务
│   ├── agent_service.py          # [新增] 统一 Agent 服务（处理 mode/capability）
│   ├── session_manager.py        # 会话管理器（持久化到 SQLite）
│   ├── document_service.py       # 文档导入服务
│   ├── collection_service.py    # 集合管理服务
│   ├── task_dispatcher.py        # 任务队列分发器
│   ├── sqlite_store.py           # SQLite 持久化存储（sessions/tasks/memory/profiles）
│   ├── state.py                  # InMemoryState 进程内缓存
│   ├── semantic_cache.py         # 语义缓存服务
│   ├── eval_service.py           # 评测服务（RAGAS / benchmark）
│   ├── feedback_service.py        # 反馈收集服务
│   ├── graph_service.py          # GraphRAG 图谱服务
│   ├── memory_commit_gate.py     # [记忆改造] 记忆提交门（trust 提升 + semantic 晋升）
│   ├── user_profile_service.py  # [记忆改造] 用户画像服务
│   ├── intent_matcher.py         # [Agent Platform] 意图匹配到 Capability
│   ├── llm_router.py             # [Agent Platform] LLM 按用途路由
│   ├── plan_generator.py         # [Agent Platform] plan 模式生成计划
│   ├── plan_executor.py          # [Agent Platform] plan 模式执行计划
│   └── auth_manager.py           # 认证管理
│
├── harness/                      # [Governance+Workflow] Harness 核心骨架
│   ├── core/
│   │   ├── kernel.py             # HarnessKernel：顺序执行 recipe stages
│   │   ├── recipe.py             # BaseRecipe + RecipeRegistry：工作流注册表
│   │   ├── stage.py              # BaseStage：单步动作契约
│   │   ├── hooks.py              # HookRegistry + EventBus：事件总线
│   │   ├── trace_hook.py         # TraceHook / MemoryHook：事件→Trace/Memory
│   │   ├── runtime_context.py    # RuntimeContext：运行时依赖容器
│   │   ├── prompt_registry.py    # PromptVersionRegistry：提示词版本治理
│   │   └── sandbox_extensions.py # ContextSandbox + CapabilitySandbox：三层沙盒
│   ├── components/               # 治理组件
│   │   ├── execution_hooks.py    # ExecutionHooks：运行时摘要 + EventBus 转发
│   │   ├── execution_policy.py   # ExecutionPolicyResolver
│   │   ├── fallback_handler.py   # FallbackHandler：失败兜底
│   │   ├── tool_executor.py      # ToolExecutor：timeout/retry/circuit_breaker
│   │   └── context_builders.py   # TaskContextBuilder / QueryContextBuilder
│   ├── recipes/                  # 具体 Recipe 定义
│   │   ├── query_recipe.py       # QueryRecipe / ChatRecipe
│   │   └── task_recipe.py        # DocumentAnalysisRecipe
│   ├── execution.py              # ExecutionHarness facade：完整工具执行治理链入口
│   ├── context.py                # ContextHarness facade：上下文构建入口
│   ├── policy.py                 # PolicyEngine：权限策略检查
│   ├── guardrails.py             # GuardrailEngine：安全护栏
│   ├── sandbox.py                # ToolSandbox：工具级沙盒
│   ├── reflection.py             # ReflectionHarness：反思评估
│   ├── recovery.py               # RecoveryManager：失败恢复
│   ├── prompting.py              # PromptBuilder：提示词构造
│   ├── model_router.py           # ModelRouter：模型路由
│   └── models.py                 # ContextBundle / ToolExecutionResult / ...
│
├── workflows/                    # [Workflow] LangGraph 编排层
│   ├── query_orchestrator.py     # QueryWorkflowOrchestrator：组装 Query LangGraph
│   ├── query_graph.py            # Query LangGraph 图定义
│   ├── query_nodes.py            # Query LangGraph 节点实现
│   ├── query_state.py            # QueryGraphState：图状态模型
│   ├── query_task_adapter.py     # QueryRequest/ChatRequest → TaskSpec
│   └── tasks/
│       ├── task_orchestrator.py  # TaskWorkflowOrchestrator：组装 Task LangGraph
│       ├── document_analysis_graph.py  # DocumentAnalysis LangGraph
│       ├── document_analysis_nodes.py  # 节点实现
│       ├── document_analysis_skill.py  # Skill 实现
│       ├── document_summary_skill.py   # Skill 实现
│       ├── skill.py              # TaskSkill + TaskSkillRegistry
│       └── builtin_skills.py     # 内置 Skill 元数据
│
├── agents/                       # [Capability] Agent + Tool Surface
│   ├── runtime.py               # AgentRuntime：任务运行时入口
│   ├── memory.py                # TaskMemory：任务记忆统一读写
│   ├── subagents.py             # SubAgentRegistry + SubAgentRuntime：受控子代理
│   ├── planner.py               # TaskPlanner：任务计划
│   └── tools/
│       ├── registry.py          # ToolRegistry：工具注册/查找/执行
│       ├── base.py              # BaseTool / ToolSchema / ToolContext：工具基类
│       ├── defaults.py          # build_runtime_rag_tools()：默认 RAG 工具集
│       ├── rag_tools.py         # rag_* 工具族（5 个）
│       ├── command_tools.py     # ShellCommandTool / RepositoryCommandTool
│       ├── repository_tools.py  # 仓库工具（list/search/read）
│       ├── database_tools.py    # 数据库工具（list/describe/query）
│       ├── api_contract_tools.py  # API 契约工具
│       ├── artifact_tools.py     # 报告工具（draft/review/finalize）
│       ├── analysis_tools.py    # 分析工具（extract_key_points / extract_risks）
│       ├── weather_tools.py     # 天气工具（2 个）
│       ├── finance_tools.py     # 金融工具（2 个）
│       ├── news_tools.py        # 新闻工具（2 个）
│       ├── currency_tools.py    # 汇率工具（2 个）
│       ├── calculator_tools.py  # 安全计算器（1 个）
│       ├── datetime_tools.py    # 时间工具（2 个）
│       ├── geocoding_tools.py   # 地理编码（2 个）
│       ├── url_fetch_tools.py   # 网页抓取（1 个）
│       ├── translation_tools.py # 翻译工具（2 个）
│       ├── chart_tools.py       # 图表生成（1 个）
│       └── web_search_tools.py  # 联网搜索（1 个）
│
├── capabilities/                # [Domain] 领域能力实现（每个 capability 一个目录）
│   ├── knowledge/               # KnowledgeCapability：知识检索（RAG）
│   ├── repository/              # RepositoryCapability：仓库文件浏览
│   ├── database/                # DatabaseCapability：数据库查询
│   ├── api_contract/            # ApiContractCapability：API 契约发现
│   ├── artifact/                # ArtifactCapability：产物管理
│   ├── weather/                 # WeatherCapability：天气查询
│   ├── finance/                 # FinanceCapability：金融数据
│   ├── news/                    # NewsCapability：新闻聚合
│   ├── currency/                # CurrencyCapability：汇率转换
│   ├── calculator/              # CalculatorCapability：安全计算
│   ├── datetime/                # DateTimeCapability：时间日期
│   ├── geocoding/               # GeocodingCapability：地理编码
│   ├── url_fetch/               # UrlFetchCapability：网页抓取
│   ├── translation/             # TranslationCapability：翻译
│   ├── chart/                   # ChartCapability：图表生成
│   └── web_search/              # WebSearchCapability：联网搜索
│
├── rag/                          # [Domain] RAG 域内实现
│   ├── facade.py               # RagFacade：知识能力唯一门面
│   ├── vector_store.py          # ChromaClientFactory：ChromaDB 向量库工厂
│   ├── ingestion.py             # 文档导入与索引
│   ├── retrieval.py             # 检索服务
│   ├── query_engine.py          # RAG 查询引擎
│   ├── observability.py         # TraceRecorder：链路追踪
│   └── llamaindex_components.py # LlamaIndex 集成
│
├── models/                       # [Data] 数据模型
│   ├── task.py                 # TaskSpec / TaskRun / TaskDetail
│   ├── query.py                # QueryRequest / QueryResponse / ChatRequest
│   ├── runtime_contracts.py    # MemoryRecord / PromptSpec / ResultContract
│   └── artifact.py             # Artifact / EvidencePack
│
├── core/                         # [Infra] 核心基础设施
│   ├── config.py               # Settings：全局配置（Pydantic）
│   ├── logging.py              # 日志配置
│   ├── errors.py               # 异常处理注册
│   └── auth.py                 # 认证中间件
│
tests/                            # 单元测试
scripts/                          # 评测脚本（RAGAS / benchmark / regression / trend）
docs/                             # 项目文档
├── 架构.md                       # 本项目完整分层架构与模块通信总览
├── agent-platform-architecture.md # Agent Platform v6 架构设计（Mode + Capability）
├── agent-platform-roadmap.md     # 实施路线图
├── harness-unification-refactor.md # Harness 统一重构文档
├── memory-system-redesign.md     # 记忆系统改造方案
└── architecture/
    ├── harness-runtime-contracts.md # Harness 运行时接口契约
    └── ...
```

---

## 🎯 核心概念

### Mode（执行模式 = 怎么做）

三种执行模式，应对不同场景：

| Mode | 说明 | 适用场景 |
|------|------|----------|
| `chat` | 用户输入 → 意图识别 → 匹配 Capability → 直接执行 → 返回结果 | 快速问答、简单请求、日常对话 |
| `plan` | 用户输入 → 意图识别 → 生成计划 → 用户确认 → 按计划执行 | 复杂任务、需要用户审核的场景 |
| `autopilot` | 用户输入 → 意图识别 → 自动执行 → 完成后询问下一步 | 长时间任务、批处理、持续交互 |

### Capability（能力 = 会做什么）

系统内置的 Capability：

| Capability | 说明 | 状态 | 依赖基础设施 |
|------------|------|------|--------------|
| `chat` | 通用对话 | ✅ 现成 | LLM |
| `document_analysis` | 文档深度分析 | ✅ 已有 | knowledge + repository |
| `document_summary` | 文档摘要 | ✅ 已有 | knowledge |
| `code_review` | 代码审查 | ✅ 已实现 | repository |
| `data_analysis` | 数据分析 | ✅ 已实现 | database |
| `web_search` | 联网搜索 | ✅ 已实现 | httpx (DuckDuckGo) |

### Infrastructure（基础设施 = 用什么做）

| 基础设施 | 说明 | 对应能力 |
|----------|------|----------|
| `knowledge` | 知识库检索 (RAG) | document_analysis, document_summary |
| `repository` | 文件系统浏览 | code_review, document_analysis |
| `database` | SQLite 数据库查询 | data_analysis |
| `api_contract` | API 契约发现 | 代码分析 |
| `artifact` | 产物管理 | 所有任务 |
| `sandbox_execute` | 沙盒命令执行 | （预留） |
| `web_fetch` | 网页抓取 | web_search |

---

## 🧠 记忆系统架构

项目采用**五级分层记忆模型**，完整实现了 `working/session/run/semantic/profile` 五层：

| Scope | 生命周期 | 写入者 | 存储 | 说明 |
|-------|---------|--------|------|------|
| **working** | 单 step 内，完成后清除 | Tool calls、ReAct | TaskRun 内存 | 当前步骤临时工作集 |
| **session** | 单次对话，持久化 | `SessionManager.add_message()` | SQLite + memory_records | 同一 session 内的连续交互 |
| **run** | 单次 TaskRun 的全过程 | Tool calls、反思 | TaskRun payload | 单次运行的过程记忆 |
| **semantic** | 跨 run 复用，不自动删除 | `MemoryCommitGate.commit_to_semantic()` | 独立 semantic_memory 表 | 高信任度结论，跨任务复用 |
| **profile** | 用户级别，长期稳定 | `UserProfileService` | 独立 user_profiles 表 | 用户偏好、语言、输出格式等 |

### 信任等级阶梯

```
未验证(unverified) → 暂定(provisional) → 已验证(verified) → 最终(final)
```

### Scope 晋升路径

```
working ──step完成──▶ run ──任务成功──▶ semantic
  ↑                    │                    │
  │                    │ 冲突               │ 跨任务复用
  │                    ▼                    ▼
  │               conflict_refs       ContextBundle
  │               stale=True           注入
```

### 模块职责

| 模块 | 文件 | 职责 |
|------|------|------|
| `TaskMemory` | `app/agents/memory.py` | 记忆统一读写入口 |
| `SessionManager` | `app/services/session_manager.py` | 会话管理（持久化到 SQLite） |
| `UserProfileService` | `app/services/user_profile_service.py` | 用户画像 CRUD + 偏好推断 |
| `MemoryCommitGate` | `app/services/memory_commit_gate.py` | 信任提升 + scope 晋升 + 冲突检测 |
| `MemoryHook` | `app/harness/core/trace_hook.py` | EventBus 事件 → 写入记忆 |
| `SQLiteStateStore` | `app/services/sqlite_store.py` | SQLite 持久化 |

---

## 🔌 模块间通信链路

### 1. Query 请求完整调用链

```text
HTTP POST /api/v1/query
  ↓
app/api/v1/endpoints/query.py::query()
  ↓
app/services/query_service.py::query()
  ↓
app/workflows/query_orchestrator.py::QueryWorkflowOrchestrator.query()
  ├─ build_query_task_spec()       ← QueryRequest → TaskSpec (Entry 适配)
  ├─ init_query_graph_state()      ← 初始化图状态
  ↓
app/workflows/query_graph.py::build_query_graph().invoke(state)  ← LangGraph 执行
  ├─ check_guardrails              ← stage: GuardrailStage
  ├─ rewrite_query                 ← stage: RewriteStage
  ├─ expand_queries                ← 查询扩展（Multi-Query / HyDE）
  ├─ lookup_cache                  ← 语义缓存检查
  ├─ retrieve_evidence             ← stage: RetrieveEvidenceStage
  │   └─ ExecutionHarness.run_tool('rag_retrieve_evidence', ...)
  │       ├─ EventBus.before_tool()  ← 发射事件
  │       ├─ GuardrailEngine.validate_tool_call()  ← 护栏检查
  │       ├─ PolicyEngine.check_tool()             ← 权限检查
  │       ├─ SandboxEngine.assess()                ← 沙盒决策
  │       ├─ ToolExecutor.execute()               ← 执行（timeout/retry/circuit_breaker）
  │       └─ EventBus.after_tool()   ← 发射事件 → MemoryHook 写入记忆
  ├─ compress_context             ← 上下文压缩
  ├─ grounded_answer               ← stage: GroundedAnswerStage
  │   └─ ExecutionHarness.run_tool('rag_grounded_answer', ...)
  ├─ self_reflect                  ← stage: ReflectionStage (Corrective RAG)
  └─ orchestration_next_route      ← 路由决策
  ↓
持久化 QueryRun 到 InMemoryState / SQLite
  ↓
返回 QueryResponse(answer, citations, ...)
```

### 2. Document Analysis Task 完整调用链

```text
HTTP POST /api/v1/tasks/document-analysis
  ↓
app/api/v1/endpoints/tasks.py::create_task()
  ↓
app/services/task_service.py::create_task()
  ↓
app/agents/memory.py::TaskMemory.create_task()
  ↓
task 入队（status=queued）→ TaskWorker 后台轮询
  ↓
app/task_worker.py::TaskWorker.run_next()
  ↓
app/agents/runtime.py::AgentRuntime.execute()
  ↓
app/workflows/tasks/task_orchestrator.py::TaskWorkflowOrchestrator.run()
  ├─ skill = skill_registry.get(task.request.task_type)
  ├─ state = skill.build_initial_state(task)
  ↓
app/workflows/tasks/document_analysis_graph.py::build_document_analysis_graph().invoke(state)
  ├─ plan                       ← stage: PlanStage
  ├─ collect_document_context   ← stage: CollectDocumentContextStage
  │   └─ Tool: rag_load_document_context
  ├─ retrieve_evidence          ← stage: RetrieveEvidenceStage
  │   └─ Tool: rag_retrieve_evidence / rag_retrieve_graph_evidence
  ├─ analyze                    ← stage: AnalyzeStage
  │   └─ Tool: extract_key_points / extract_risks
  ├─ draft_report               ← stage: DraftReportStage
  │   └─ Tool: draft_report
  ├─ review_report              ← stage: ReviewStage
  │   └─ Tool: review_report
  ├─ revise_report              ← stage: ReviseStage
  ├─ finalize_report            ← stage: FinalizeStage
  │   └─ Tool: finalize_report (高风险 → process_isolated 沙盒)
  ↓
持久化 TaskResult 到 InMemoryState / SQLite
  ↓
MemoryCommitGate.commit_to_semantic()  ← 高信任度结论晋升到 semantic
  ↓
返回 TaskResult(final_artifact, summary, ...)
```

### 3. 统一 Agent API 调用链（新增）

```text
HTTP POST /api/v1/agent/chat (SSE 流式)
  ↓
app/api/v1/endpoints/agent.py::chat()
  ↓
app/services/agent_service.py::AgentService.process()
  ├─ 1. intent_matcher.match(message)  ← 意图识别 → Capability
  │   output: CapabilityMatch(name, confidence)
  ├─ 2. 根据 mode 选择执行路径
  │   ├─ mode=chat: 直接执行 → SSE 输出
  │   ├─ mode=plan: 生成计划 → 返回计划等待确认 → 确认后执行
  │   └─ mode=autopilot: 自动执行 → 完成后询问下一步
  ├─ 3. capability → workflow 路由
  │   ├─ chat: 直接 LLM 回答
  │   └─ document_analysis: 调用 DocumentAnalysisWorkflow
  └─ 4. 每个节点/步骤通过 EventBus 发射 SSE 事件
```

SSE 事件协议：

```
event: intent
data: {"type":"intent","capability":"document_analysis","confidence":0.85}

event: plan              ← plan 模式独有
data: {"steps":[{"id":1,"name":"收集文档上下文",...},...]}

event: plan_confirmed  ← plan 模式用户确认后
event: step_start
data: {"step_id":1,"name":"收集文档上下文"}

event: tool_call
data: {"tool":"rag_retrieve_evidence","args":{...}}

event: tool_result
data: {"duration_ms":800}

event: delta
data: {"content":"系统的核心架构是..."}

event: step_end
event: completed
data: {"task_id":"task-xxx","duration_ms":3500}
```

### 4. 工具执行治理链（ExecutionHarness）

每次工具调用都会走完整治理链：

```text
ExecutionHarness.run_tool(name, payload, workflow_state, context_bundle)
  │
  ├─ 1. EventBus.emit_before_tool()          ← 发射 before_tool 事件
  │
  ├─ 2. GuardrailEngine.validate_tool_call()  ← 护栏检查（注入/Prompt 攻击）
  │    └─ 不通过 → 抛 ToolExecutionError
  │
  ├─ 3. PolicyEngine.check_tool()             ← 权限/策略检查
  │    └─ 不通过 → 抛 permission_error
  │
  ├─ 4. SandboxEngine.assess()                ← 沙盒决策
  │    └─ risk_level=high → process_isolated 隔离执行
  │
  ├─ 5. ToolExecutor.execute()                ← 执行（含 timeout/retry/circuit_breaker）
  │    ├─ 成功 → 返回 result
  │    └─ 失败 → FallbackHandler.apply() 或 抛异常
  │
  ├─ 6. EventBus.emit_after_tool()            ← 发射 after_tool 事件
  │
  └─ 7. ExecutionHooks.record_execution()     ← Trace + Memory 写入
```

### 5. EventBus 事件路由（治理横切）

所有生命周期事件通过 EventBus 广播，Hook 监听并处理：

```text
HarnessKernel.run()
  → event_bus.before_stage(ws, stage_name='retrieve')
    → HookRegistry.emit(EventPayload(event='before_stage', ...))
      ├─ TraceHook.handle(event)         ← 通配符监听 → trace.record()
      ├─ MemoryHook.handle(event)        ← 特定事件监听 → TaskMemory.append_memory_record()
      └─ (其他注册的 hook)
```

支持 22 种事件类型：

- **Stage 生命周期**: `BEFORE_STAGE` / `AFTER_STAGE` / `STAGE_FAILED`
- **Tool 生命周期**: `BEFORE_TOOL` / `AFTER_TOOL` / `TOOL_FAILED`
- **ReAct 生命周期**: `BEFORE_REACT_TURN` / `AFTER_REACT_TURN` / `REACT_EXCEEDED_MAX_TURNS`
- **Checkpoint 生命周期**: `BEFORE_CHECKPOINT` / `AFTER_CHECKPOINT`
- **Request/Run 生命周期**: `RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED`
- **Recovery 生命周期**: `RECOVERY_INITIATED` / `RECOVERY_COMPLETED` / `RECOVERY_FAILED`
- **Context 生命周期**: `CONTEXT_BUILT` / `CONTEXT_TRIM`

### 6. 容器启动装配顺序

`AppContainer.__init__()` 严格按依赖顺序装配：

```text
AppContainer.__init__(settings)
  ├─ 1. 创建基础 Infra
  │   ├─ self.settings
  │   ├─ self.state = InMemoryState()
  │   ├─ self.persistence = SQLiteStateStore(settings)
  │   ├─ self.persistence.load_into(self.state)  ← 持久化恢复到内存
  │   ├─ self.trace = TraceRecorder()
  │   ├─ self.event_bus = EventBus()
  │   └─ self.event_bus.register(TraceHook(trace=self.trace))
  ├─ 2. 创建 LLM 和向量库
  │   ├─ self.llm = build_llm(settings)
  │   ├─ self.vector_store = ChromaClientFactory(settings)
  │   └─ self.model_router = ModelRouter()
  ├─ 3. 创建 RAG 基础服务
  │   ├─ self.graph_service
  │   ├─ self.retrieval
  │   ├─ self.semantic_cache
  │   └─ self.ingestion
  ├─ 4. 创建 Domain Capability
  │   ├─ self.knowledge_capability
  │   ├─ self.rag_facade
  │   ├─ self.repository_capability
  │   ├─ self.api_contract_capability
  │   ├─ self.artifact_capability
  │   ├─ self.database_capability
  │   └─ 外部服务: weather / finance / news / ... → 存入 self.external_services 字典
  ├─ 5. 创建 Agent Platform 新服务
  │   ├─ self.config_store
  │   ├─ self.mcp_manager
  │   ├─ self.auth_manager
  │   ├─ self.llm_router  ← LLM 按用途路由
  │   ├─ self.llm_config_manager
  │   ├─ self.skill_manager
  │   ├─ self.capability_registry
  │   ├─ self.intent_matcher  ← 意图匹配
  │   └─ self.plan_generator / self.plan_executor
  ├─ 6. 创建记忆系统
  │   ├─ self.task_memory = TaskMemory(self.state, self.persistence)
  │   └─ self.event_bus.register(MemoryHook(memory=self.task_memory))
  ├─ 7. 创建 SessionManager（依赖 task_memory）
  │   └─ self.session_manager = SessionManager(state, persistence, task_memory)
  ├─ 8. 创建 AgentService
  ├─ 9. 注册 SubAgent 到 SubAgentRegistry
  ├─ 10. 注册所有 Tool 到 ToolRegistry
  │   ├─ RAG 工具 (5 个)
  │   ├─ 仓库/数据库/API/产物/分析 工具
  │   ├─ 命令工具 (shell/repository)
  │   └─ 外部服务工具 (weather/finance/news/...)
  ├─ 11. 创建治理组件
  │   ├─ self.guardrail_engine
  │   ├─ self.policy_engine
  │   └─ self.sandbox_engine
  ├─ 12. 创建 Orchestrator
  │   ├─ self.task_orchestrator
  │   └─ self.query_orchestrator
  ├─ 13. 创建 AgentRuntime
  ├─ 14. 创建 TaskWorker（后台轮询）
  ├─ 15. 创建业务服务
  │   ├─ collection_service
  │   ├─ document_service
  │   ├─ query_service
  │   ├─ task_service
  │   ├─ eval_service
  │   └─ feedback_service
  └─ 16. 如果配置启用，启动后台 worker
```

---

## 🛠️ 快速开始

### 1. 环境准备

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env`，配置你的 LLM API Key：

```bash
# OpenAI 配置
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-xxx
EMBED_MODEL=text-embedding-3-small
EMBED_API_KEY=sk-xxx

# 可选外部服务 API Key（对应工具需要）
WEATHER_API_KEY=xxx
NEWS_API_KEY=xxx
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 启动服务

```bash
uvicorn app.main:app --reload
```

服务启动后，访问 http://localhost:8000/docs 查看 API 文档。

### 4. Docker Compose 启动（可选）

```bash
docker compose up --build
```

会同时启动 `app`、`task-worker`、`chroma` 三个服务。

---

## 📡 API 概览

### 统一 Agent 入口（推荐）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/agent/chat` | 统一 Agent 对话（支持 SSE 流式） |

请求示例：

```json
{
  "message": "分析 demo 集合中的架构文档",
  "mode": "chat",
  "session_id": "sess-xxx",
  "capabilities": null,
  "collection_name": "demo",
  "stream": true
}
```

### 查询与对话

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/query` | 单次问答 |
| `POST` | `/api/v1/query/stream` | 流式问答 |
| `POST` | `/api/v1/chat` | 多轮对话 |
| `POST` | `/api/v1/chat/stream` | 流式多轮对话 |

### 任务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/tasks/document-analysis` | 创建文档分析任务 |
| `GET` | `/api/v1/tasks` | 列出所有任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 获取任务详情 |
| `POST` | `/api/v1/tasks/{task_id}/retry` | 重试任务 |

### 文档与知识库

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/collections` | 创建知识库集合 |
| `GET` | `/api/v1/collections` | 列出集合 |
| `POST` | `/api/v1/documents/upload` | 上传文档 |
| `GET` | `/api/v1/documents/{doc_id}` | 获取文档 |

### 能力发现

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/capabilities` | 列出所有 Capability |
| `GET` | `/api/v1/capabilities/{name}` | 获取 Capability 详情 |

### 评测

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/eval/ragas/compare` | RAG 策略对比评测 |
| `POST` | `/api/v1/eval/tasks/document-analysis/benchmark` | 文档分析 benchmark |
| `GET` | `/api/v1/eval/tasks/document-analysis/dashboard/latest` | 最新评测看板 |

### 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/health` | 健康检查 + 运行时统计 |
| `GET` | `/api/v1/metrics` | Prometheus 格式指标 |

---

## 🔧 可用工具列表

系统当前内置 **~40 个工具**，覆盖多个领域：

### RAG 知识工具

| 工具 | 说明 |
|------|------|
| `rag_load_document_context` | 加载指定文档上下文 |
| `rag_retrieve_evidence` | 检索相关证据 |
| `rag_retrieve_graph_evidence` | 图谱增强检索（GraphRAG） |
| `rag_grounded_answer` | 基于证据生成回答 |
| `rag_grounded_query` |  grounded 问答完整链路 |

### 代码仓库工具

| 工具 | 说明 |
|------|------|
| `list_repository_files` | 列出仓库文件 |
| `read_repository_file` | 读取文件内容 |
| `search_repository` | 搜索代码模式 |

### 数据库工具

| 工具 | 说明 |
|------|------|
| `list_database_tables` | 列出数据库表 |
| `describe_database_table` | 查看表结构 |
| `query_database` | 执行 SQL 查询 |

### API 契约工具

| 工具 | 说明 |
|------|------|
| `list_api_contracts` | 列出 API 契约 |
| `search_api_contract_operations` | 搜索 API 操作 |
| `read_api_contract` | 读取契约详情 |

### 分析报告工具

| 工具 | 说明 |
|------|------|
| `extract_key_points` | 提取关键点 |
| `extract_risks` | 提取风险点 |
| `draft_report` | 起草报告 |
| `review_report` | 审查报告 |
| `finalize_report` | 定稿报告 |

### 命令工具

| 工具 | 说明 |
|------|------|
| `shell_command` | 在沙盒中执行 Shell 命令 |
| `repository_command` | 在仓库目录执行命令 |

### 外部数据工具

| 工具 | 说明 |
|------|------|
| `get_current_weather` | 获取当前天气 |
| `get_weather_forecast` | 获取天气预报 |
| `get_stock_quote` | 获取股票报价 |
| `get_historical_prices` | 获取历史价格 |
| `get_latest_news` | 获取最新新闻 |
| `search_news` | 搜索新闻 |
| `convert_currency` | 货币汇率转换 |
| `get_exchange_rates` | 获取汇率列表 |
| `geocode_address` | 地址转坐标 |
| `reverse_geocode` | 坐标转地址 |
| `fetch_webpage` | 抓取网页内容 |
| `translate_text` | 翻译文本 |
| `detect_language` | 检测语言 |
| `calculate` | 安全计算器 |
| `get_current_time` | 获取当前时间 |
| `get_date_info` | 获取日期信息 |
| `generate_chart` | 生成图表 |
| `web_search` | 联网搜索 |

---

## ⚙️ 配置

### 环境变量（层级 1）

核心配置：

```bash
# 应用
APP_NAME=Lania Agent Platform
API_PREFIX=/api/v1
HOST=0.0.0.0
PORT=8000

# LLM
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=
LLM_BASE_URL=
EMBED_MODEL=text-embedding-3-small
EMBED_API_KEY=
USE_LOCAL_MODEL_FALLBACK=true

# 功能开关
ENABLE_CONTEXT_COMPRESSION=true
ENABLE_SEMANTIC_CACHE=true
ENABLE_PROMPT_GUARDRAILS=true
ENABLE_PII_REDACTION=true
ENABLE_CROSS_ENCODER_RERANK=false
ENABLE_SELF_RAG_RETRY=false

# 任务 Worker
ENABLE_EMBEDDED_TASK_WORKER=true
TASK_WORKER_POLL_INTERVAL_SECONDS=1
TASK_WORKER_LEASE_SECONDS=1800
TASK_WORKER_MAX_WORKERS=1

# 语义缓存
SEMANTIC_CACHE_SIMILARITY_THRESHOLD=0.94
SEMANTIC_CACHE_TTL_SECONDS=86400
SEMANTIC_CACHE_MAX_ENTRIES_PER_COLLECTION=500

# 上下文压缩
CONTEXT_COMPRESSION_MAX_CHUNKS=4
CONTEXT_COMPRESSION_MAX_SENTENCES=8
CONTEXT_COMPRESSION_MAX_CHARS=1600

# 认证
ENABLE_AUTH=false
LANIA_DEFAULT_API_KEY=dev-key-123
LANIA_DEFAULT_ROLE=admin

# 外部服务 API Key
WEATHER_API_KEY=
NEWS_API_KEY=
TRANSLATION_API_KEY=
```

完整配置见 `app/core/config.py`。

### 运行时配置（层级 2）

通过管理 API 可在运行时配置：

- `llm.routes.*` — LLM 按用途路由（chat/intent/plan/corrective/extraction/...）
- `features.*` — 功能开关（guardrails/semantic_cache/pii_redaction/...）
- `rag.*` — RAG/检索参数（default_top_k/similarity_threshold/...）
- `agent.*` — Agent 行为参数（default_mode/default_capability/max_tool_calls/...）
- `sandbox.*` — 沙盒配置（provider/timeout_seconds/max_output_bytes/...）

### CLI 本地配置（层级 3）

`~/.lania/agent-config.json` 存储 CLI 用户偏好：

```json
{
  "backend_url": "http://localhost:8000",
  "api_key": "sk-xxx",
  "default_collection": "default",
  "default_mode": "chat",
  "output_mode": "human"
}
```

---

## 📊 评测与回归

项目内置完整的评测体系：

### 运行本地 RAGAS 评测

```bash
.venv/bin/python scripts/run_ragas_eval.py \
  --dataset-path data/eval/accuracy_regression_eval.json \
  --collection-name demo \
  --use-graph-rag \
  --graph-max-hops 2 \
  --top-k 5
```

### 运行回归基线

```bash
.venv/bin/python scripts/run_regression_baseline.py \
  --dataset-path data/eval/accuracy_regression_eval.json \
  --collection-name demo
```

### 生成准确率报告

```bash
.venv/bin/python scripts/run_accuracy_report.py \
  --dataset-path data/eval/accuracy_regression_eval.json \
  --collection-name demo \
  --fail-on-gate-fail
```

### 生成趋势报告

```bash
.venv/bin/python scripts/run_accuracy_trend_report.py \
  --input-dir data/eval/results \
  --limit 10
```

### 完整回归流水线

```bash
.venv/bin/python scripts/run_regression_pipeline.py \
  --dataset-path data/eval/accuracy_regression_eval.json \
  --collection-name demo \
  --trend-limit 10 \
  --fail-on-gate-fail
```

---

## 🧪 运行测试

```bash
# 运行所有测试
python -m unittest discover tests

# 运行特定测试
python -m unittest tests.test_harness_runtime
python -m unittest tests.test_query_orchestrator
python -m unittest tests.test_context_harness_v2
python -m unittest tests.test_sqlite_persistence
```

---

## 类型检查

项目已提供 `pyright` 和 `mypy` 配置：

```bash
# pyright
npx pyright

# mypy
.venv/bin/mypy
```

---

## 📐 设计原则

### 原则 1：每类能力只有一个主管理层

| 能力 | 管理归属 | 注册方式 |
|------|----------|----------|
| session / memory / context | State Layer | `TaskMemory` / `ContextHarness` |
| skill / recipe / stage | Workflow Layer | `TaskSkillRegistry` / `RecipeRegistry` |
| tool / command / sub-agent | Capability Layer | `ToolRegistry` / `SubAgentRegistry` |
| permission / policy / hook / audit | Governance Layer | `PolicyEngine` / `HookRegistry` |
| tech-stack | Infra Layer | `Settings` / `Container` |

### 原则 2：扩展靠继承，执行靠组合

```python
# 通过继承扩展
class BaseRecipe      # 新增 task 类型时继承
class BaseStage       # 新增 step 类型时继承
class BaseTool        # 新增能力时继承
class BaseSubAgent    # 新增受控子代理时继承

# 通过组合执行
recipe = QueryRecipe(stages=[GuardrailStage(), RewriteStage(), ...])
kernel.run(recipe, state, ctx)    # 组合执行由 kernel 驱动
EventBus + HookRegistry           # 治理组合由事件路由
```

### 原则 3：Harness > Agent

- Budget / Permission / Guardrail / Sandbox 由 Harness 控制
- Agent 只负责任务推进
- ReAct 只用于 step 内局部动作选择

### 原则 4：领域能力不反穿 Harness

```text
正确路径：Stage → Tool → Facade → Domain Service
错误路径：Stage → 直接调 KnowledgeCapability / RagQueryEngine
```

### 原则 5：治理不散落在 Stage 中

```text
好的方式：Stage 只发射事件 → EventBus → HookRegistry → TraceHook/MemoryHook
不好的方式：Stage 内部自己调 trace.record() + 自己写 TaskMemory + 自己判断权限
```

---

## 🧩 设计理由：为什么这么设计

> 本节解释每个架构决策的背景、问题、权衡和选择理由。如果你要理解"为什么长这样"而不是"长什么样"，从这里开始。

---

### 一、为什么是 Harness-first，而不是 RAG-first？

**背景问题**：项目最初是 "Personal RAG App" —— 一个标准的 RAG 问答应用。随着能力扩展，我们逐步加入了 weather、finance、repository、database、command 等工具，发现了一个根本问题：

```
传统 RAG 应用：
  query → RAG Query Engine → 检索 → 生成 → 回答
  (所有请求都走同一条管道，RAG 是主轴)

问题：
  - 天气查询不需要 RAG，但管道假设了"检索"是必经步骤
  - 代码审查、数据库查询、图表生成都无法简单嵌入
  - 每种新能力都需要在 RAG 管道里打补丁
  - 治理（权限、护栏、沙盒）散落在 RAG 管道的各个角落
```

**决策**：将 RAG 降级为一种能力，在它之上建立一个通用的 Task 执行框架（Harness）。

**为什么这样更好**：
- RAG 只是 `rag_*` 工具族，和 `weather_*`、`database_*`、`command_*` 平级
- 新增能力不需要修改现有管道，只需注册新 Tool + 新 Recipe
- 治理逻辑（护栏、权限、沙盒）统一在 Harness 层执行，不再散落

**你不这样做会怎样**：RAG 管道会越来越臃肿，每次加新能力都要在管道里加 `if capability == 'weather': ...` 分支，最终变成无法维护的"上帝管道"。

---

### 二、为什么是六层架构？

**每一层解决一个独立问题，且只解决一个问题**：

| 层 | 解决的问题 | 如果不分层会怎样 |
|----|-----------|-----------------|
| **Entry** | 外部请求格式（HTTP/SSE）与内部 TaskSpec 的适配 | API 端点直接调 workflow，换传输协议要改所有端点 |
| **State** | 跨 step / run / session 的数据生命周期 | 每个 stage 自己管理数据，状态散落，无法 checkpoint/replay |
| **Workflow** | 任务按什么顺序推进 | 硬编码执行顺序，新增任务类型要改 kernel 代码 |
| **Capability** | 有哪些可执行的能力 | 每次加工具都要改治理层和执行层 |
| **Governance** | 什么能做、做到什么程度 | 每个 tool 自己判断权限，同一条规则在 40 个 tool 里重复实现 |
| **Infra** | 底层运行底座 | 换 LLM Provider 或向量库要改所有上层代码 |

**关键边界**：
- 上层不依赖下层的具体实现，只依赖接口
- 跨层通信通过明确定义的契约（`TaskSpec`、`ContextBundle`、`MemoryRecord`、`EventPayload`）
- 每层内部的改动不应影响其他层

---

### 三、为什么所有请求都先映射为 TaskSpec？

**当前做法**：

```text
QueryRequest → TaskSpec
ChatRequest  → TaskSpec
TaskRequest  → TaskSpec
  ↓
HarnessKernel.run(task_spec)  ← 只认 TaskSpec，不认 QueryRequest/ChatRequest
```

**如果不这样做**：`HarnessKernel` 需要理解三种不同的请求格式，内部充满 `if isinstance(request, QueryRequest): ...` 分支。每新增一种入口（如 CLI、WebSocket），就要改 kernel。

**为什么 TaskSpec 是合适的统一格式**：

```python
class TaskSpec:
    task_id: str           # 唯一标识
    task_type: str         # 'grounded_query' | 'session_chat' | 'document_analysis'
    objective: str         # 自然语言任务描述
    input_payload: dict    # 携带的原始数据
    run_budget: RunBudget  # 预算约束
    steps: list[StepSpec]  # 步骤声明
```

- `task_type` 让 Recipe 路由知道该用哪个工作流
- `objective` 让每个 stage 都理解"要做什么"
- `run_budget` 统一控制资源消耗
- `input_payload` 携带所有原始请求数据，不丢失信息

**核心原则**：Adapter 只做投影，不执行业务逻辑。`QueryRequestAdapter.to_task_spec()` 只做字段映射，不调 RAG、不写状态。

---

### 四、为什么用 EventBus + Hook 做治理横切？

**问题场景**：一个 tool 调用需要经过以下治理检查：

```
护栏检查 → 权限检查 → 沙盒决策 → 执行 → 失败兜底 → Trace 记录 → 记忆写入
```

**如果不用 EventBus**：每个 stage 内部都要手动调用 `guardrail.validate()`、`policy.check()`、`trace.record()`、`memory.append()`。同样一段治理逻辑在 10+ 个 stage 里重复出现。

**当前的 EventBus 方案**：

```text
HarnessKernel.run()
  → event_bus.before_stage(ws, stage_name='retrieve')
    → HookRegistry.emit(EventPayload(...))
      ├─ TraceHook → trace.record('before_stage', ...)     ← 通配符监听
      ├─ MemoryHook → TaskMemory.append_memory_record(...)  ← 特定事件监听
      └─ (其他 hook)
```

**为什么这样更好**：
- Stage 只发射事件，不感知治理逻辑
- 新增治理策略（如审计日志、计费扣减）只需注册新 Hook，不改任何 Stage 代码
- Hook 可独立测试、独立开关、独立排序
- 通配符 `'all'` 注册让 TraceHook 自动捕获所有事件，一行代码不写

**22 种事件类型的设计意图**：

| 事件类别 | 事件 | 为什么需要 |
|---------|------|-----------|
| Stage 生命周期 | before/after/failed | 记录每个阶段的耗时、成功/失败 |
| Tool 生命周期 | before/after/failed | 记录每次工具调用，用于审计和计费 |
| ReAct 生命周期 | before/after/exceeded | 监控 ReAct 循环，防止无限循环 |
| Checkpoint 生命周期 | before/after | 在 checkpoint 前后做数据一致性校验 |
| Run 生命周期 | started/completed/failed | 任务级别的成功率统计 |
| Recovery 生命周期 | initiated/completed/failed | 恢复策略的效果追踪 |
| Context 生命周期 | built/trim | 上下文构建质量的观测 |

---

### 五、为什么用 LangGraph 做工作流编排？

**背景**：项目最初有两条执行路径——`classic`（手写编排）和 `langgraph`（图编排）。经过重构后，LangGraph 成为唯一的编排引擎。

**为什么选 LangGraph 而不是手写编排**：

| 维度 | 手写编排 | LangGraph |
|------|---------|-----------|
| 可视化 | 代码即流程，难理解 | StateGraph 图结构，一目了然 |
| 条件路由 | 大量 if/else | `add_conditional_edges` 显式声明 |
| Checkpoint | 手写，易遗漏 | 框架自动，每个节点后都可 checkpoint |
| 重试/回退 | 手写 try/except | 框架原生支持 |
| 并行执行 | 手写 asyncio | `Send()` API 原生支持 |
| 人机交互 | 手写中断逻辑 | `interrupt()` 内置支持 |

**LangGraph 在项目中的角色**：它是"图执行器"，不是"业务建模工具"。

```text
HarnessKernel 是"业务编排层"——决定任务推进顺序
       ↓
Orchestrator 内部用 Recipe 表达流程，实际执行调 LangGraph compiled graph
       ↓
LangGraph 是"图执行器"——执行节点和路由
       ↓
graph 内部每个 node 对应一个或多个 Stage 语义
```

**关键边界**：Node 是 graph 的实现细节，不是对外建模对象。外部代码只和 Recipe/Stage 交互，不直接操作 graph node。

---

### 六、为什么 ToolRegistry 和 ToolExecutor 是分开的？

**分开之前的问题**：一个 `ToolRegistry` 同时负责注册、查找、schema 暴露、执行、超时、重试、熔断、trace、audit。单个类 500+ 行，每加一个"执行特性"就要改注册代码。

**分开之后**：

```text
ToolRegistry           ToolExecutor
├─ register()           ├─ execute()
├─ get()                │   ├─ timeout
├─ describe()           │   ├─ retry
├─ list_descriptions()  │   ├─ circuit_breaker
└─ (只负责注册/查找)     │   ├─ sandbox
                        │   └─ trace
                        └─ (只负责执行治理)
```

**为什么这样更好**：
- Registry 是"工具字典"，只回答"有哪些工具、怎么调用"
- Executor 是"执行策略"，回答"怎么安全地执行一个工具"
- 两者独立演进：加新工具不改执行策略，加新执行策略不改注册逻辑
- 测试更简单：测试 Registry 不需要 mock sandbox，测试 Executor 不需要构造完整工具集

---

### 七、为什么 CommandTool 是 Tool 的子类，而不是独立体系？

**诱惑**：Shell 命令执行和 RAG 检索看起来完全不同——不同的风险等级、不同的沙盒模式、不同的输入输出。很容易为 Command 单独建一套 registry + executor + policy + audit。

**为什么不做独立体系**：

```text
如果 Command 独立：

  RAG Tool 走：ToolRegistry → ToolExecutor → PolicyEngine → GuardrailEngine → Trace
  Command 走： CommandRegistry → CommandRunner → ??? → ??? → ???
                            ↑ 第二套 registry、第二套 executor、第二套 policy
                            问题：权限检查在哪？审计日志在哪？熔断在哪？

当前做法：

  Command 走：ToolRegistry → ToolExecutor → PolicyEngine → GuardrailEngine → Trace
              ↑ 同一套管道，只是 risk_level='high', sandbox_mode='process_isolated'
```

**CommandTool 的差异化通过属性实现，不通过架构实现**：

```python
class ShellCommandTool(BaseCommandTool):
    name = 'shell_command'
    risk_level = 'high'                     # ← 属性驱动差异
    sandbox_mode = 'process_isolated'       # ← 属性驱动差异
    timeout_ms = 30000                      # ← 属性驱动差异
    # 但走的是同一套 ToolExecutor 管道
```

**核心原则**：不让命令长出第二套 registry + executor + policy + audit。

---

### 八、为什么 RAG 通过 Facade + ToolAdapter 暴露，而不是直接调用？

**问题场景**：Workflow 层需要执行检索。两种做法：

```text
A) 直接调用（错误）：
   RetrieveEvidenceStage.run():
       result = self.knowledge_capability.retrieve(query, top_k=5)
       # ↑ stage 直接依赖 KnowledgeCapability 内部实现

B) 通过 Tool + Facade（正确）：
   RetrieveEvidenceStage.run():
       result = self.execution_harness.run_tool('rag_retrieve_evidence', payload)
       # ↑ stage 只依赖 Tool 名称，不感知内部实现
```

**为什么 A 不好**：
- 如果 KnowledgeCapability 改为远程调用，所有 stage 代码都要改
- 如果检索逻辑需要加缓存、加降级、加审计，stage 代码全部要改
- 治理检查（护栏、权限、沙盒）在 stage 层被绕过，直接调 capability

**为什么 B 好**：
- `RagFacade` 收口了 RAG 域内的全部复杂度（检索、生成、图谱、重排）
- `rag_*` 工具是 RAG 对外的唯一契约面，内部实现可以整体替换
- 工具调用走 ExecutionHarness 治理链，护栏/权限/沙盒/审计一个不落

**扩展规则**：新增任何领域能力（repository、database、weather、sandbox_execute），都必须遵循：

```
Domain Service → Facade → ToolAdapter → Tool → ToolExecutor → Stage
```

不允许 Stage 直接调 Facade 或 Domain Service。

---

### 九、为什么是五级记忆模型，而不是简单的一级缓存？

**一级缓存的局限**：

```text
简单方案：一个 dict 存所有历史消息
  memory = {"用户喜欢 Markdown 格式", "上次分析了架构文档", "今天天气是晴天"}
  
问题：
  1. 用户偏好和天气信息混在一起，生命周期完全不同
  2. 重启丢失所有记忆
  3. 无法区分"临时观察"和"已验证结论"
  4. 跨任务的知识无法复用
```

**五级模型的每级都解决一个特定问题**：

| Scope | 解决的问题 | 为什么需要独立一层 |
|-------|-----------|-------------------|
| **working** | 当前 step 的临时工作集 | 防止 step 之间数据污染，上一个 step 的中间产物不应影响下一个 step |
| **session** | 对话连续性 | 用户说"继续刚才的话题"，需要知道刚才聊了什么。重启后仍需恢复 |
| **run** | 任务审计与回放 | Checkpoint 机制依赖完整的 run 级记忆链。出问题时可以回放整个任务 |
| **semantic** | 跨任务知识复用 | 上一次文档分析发现的"模块 A 依赖模块 B"这个结论，下次分析时可以直接用 |
| **profile** | 用户个性化 | 语言偏好、输出格式、风险容忍度，这些是与具体任务无关的用户特征 |

**为什么 trust_level 有四级阶梯**：

```
unverified → provisional → verified → final

unverified: Tool 刚返回的原始结果，可能是幻觉
provisional: 经过单个来源确认
verified:   3+ 个不同来源交叉验证
final:      24h 内无冲突，真正可信
```

**为什么需要 MemoryCommitGate**：不是所有 run 级记忆都值得晋升到 semantic。只有 `status=completed` + `trust_level=verified/final` 的记录才能进入长期记忆。这防止了失败任务的错误结论污染知识库。

---

### 十、为什么 SessionManager 要持久化到 SQLite？

**改造前**：`SessionManager` 是一个纯内存 `dict`，重启丢失全部会话。

```python
# 改造前
class SessionManager:
    _sessions: dict[str, Session] = {}  # 重启 = 丢失
```

**为什么这样不够**：
- 用户和 Agent 聊了 10 轮，重启后 Agent 完全不记得，体验极差
- 会话数据无法跨 worker 进程共享
- 无法做会话分析和审计

**改造后**：双写模式——InMemoryState 做热缓存，SQLite 做持久化。

```python
async def save(self, session: Session):
    self._state.sessions[session.id] = payload   # 内存热缓存
    self._persistence.upsert_session(session.id, payload)  # SQLite 持久化
```

**为什么是双写而不是纯 SQLite**：
- 高频读写时纯 SQLite 有性能瓶颈
- InMemoryState 保证当前进程内的读写无延迟
- SQLite 保证重启后数据不丢失

---

### 十一、为什么 ExecutionHarness 的治理链是 7 步，且顺序固定？

**治理链的 7 步设计**：

```text
1. EventBus.before_tool()      ← 先发射事件，让审计 hook 开始计时
2. GuardrailEngine.validate()   ← 护栏先跑：检测注入攻击、敏感信息泄露
3. PolicyEngine.check()         ← 权限再跑：用户是否有权调用这个工具
4. SandboxEngine.assess()       ← 沙盒决策：高风险工具是否需要进程隔离
5. ToolExecutor.execute()       ← 真正执行：timeout / retry / circuit_breaker
6. EventBus.after_tool()        ← 执行后发射事件
7. ExecutionHooks.record()      ← Trace + Memory 写入
```

**为什么这个顺序是固定的**：

| 步骤 | 为什么在这个位置 | 换顺序会怎样 |
|------|-----------------|-------------|
| 1. EventBus | 最先发射，让审计 hook 记录完整耗时 | 放在后面 → 审计漏掉 guardrail 阶段的耗时 |
| 2. Guardrail | 护栏优先于权限 | 先检查权限再检查护栏 → 注入攻击请求通过了权限检查 |
| 3. Policy | 权限在沙盒之前 | 先创建沙盒再检查权限 → 为无权限的用户创建了沙盒进程 |
| 4. Sandbox | 沙盒在执行之前 | 先执行再沙盒 → 高风险命令已经在宿主机上跑了 |
| 5. Execute | 执行在中间 | 这是唯一真正"做事"的步骤 |
| 6. EventBus | 执行后发射 | 放在 7 之前 → hook 可以拿到执行结果 |
| 7. Record | 最后写入 | 放在 6 之后 → Trace/Memory 包含完整的事件数据 |

**原则**：治理链的设计是"先检查，再执行，最后记录"，不是"先执行，再检查"。

---

### 十二、为什么 Recipe 是声明式组合，而不是命令式编码？

**两种方式对比**：

```python
# A) 命令式（不好）
class QueryWorkflow:
    def run(self, state):
        state = self.guardrail(state)
        state = self.rewrite(state)
        state = self.retrieve(state)
        state = self.answer(state)
        return state
    # 问题：每新增一个 stage 要改 run() 方法

# B) 声明式（当前做法）
class QueryRecipe(BaseRecipe):
    name = 'query'
    task_type = 'query'
    def __init__(self):
        super().__init__(stages=[
            GuardrailStage(),
            RewriteStage(),
            RetrieveEvidenceStage(),
            GroundedAnswerStage(),
            ReflectionStage(),
            FinalizeStage(),
        ])
    # 新增 stage 只需在列表里加一行
```

**为什么声明式更好**：
- Recipe 表达"做什么阶段"，不表达"怎么做"
- 阶段顺序改一行代码，不需要改执行逻辑
- 同一个 kernel 可以跑任何 Recipe，kernel 代码不随业务变化
- 可以在运行时动态替换某些 stage（如 mock 测试、A/B 实验）

---

### 十三、为什么 RuntimeContext 是薄容器，不是大而全的 God Object？

**RuntimeContext 只放稳定运行时依赖**：

```python
class RuntimeContext(BaseModel):
    request_id: str
    task_id: str | None
    run_id: str | None
    trace: Any | None
    event_bus: Any | None
    tool_registry: Any | None
    tool_executor: Any | None
    subagent_runtime: Any | None
    policy: Any | None
    guardrail: Any | None
    extensions: dict[str, Any]   # 扩展点，不放领域数据
```

**不放什么**：
- ❌ 不放 `knowledge_capability`（领域能力通过 Tool 调用，不通过 Context）
- ❌ 不放 `rag_facade`（同上）
- ❌ 不放 `weather_service`（同上）
- ❌ 不放 `collection_name`（属于 TaskSpec，不属于运行时上下文）
- ❌ 不放 `current_step`（属于 State，不属于 Context）

**为什么这样限制**：
- 如果 RuntimeContext 变成"万能口袋"，每个 stage 都能拿到任何依赖
- 结果就是 stage 绕过治理层直接调 capability，架构分层被架空
- 薄容器强制 stage 只能通过 Tool 调用能力，确保治理链不被绕过

**领域数据去哪了**：
- `collection_name`、`doc_ids` → TaskSpec.input_payload
- 当前步骤信息 → State（HarnessState / QueryGraphState）
- 用户偏好 → ContextBundle（由 ContextHarness 构建后注入）

---

### 十四、为什么基类（BaseRecipe/BaseStage/BaseTool）只服务契约一致性？

**基类的职责边界**：

```python
# BaseTool 只提供：
# ✓ 稳定元数据（name, version, timeout_ms, risk_level）
# ✓ 通用 trace/log helper
# ✗ 不负责 registry 注册（由 ToolRegistry 负责）
# ✗ 不负责 capability 构建（由 Container 负责）
# ✗ 不负责 fallback 策略（由 FallbackHandler 负责）
# ✗ 不负责执行链编排（由 ToolExecutor 负责）
```

**如果不限制基类职责**：基类会逐渐吞掉其他模块的职责，最终变成"隐式框架"——看起来是继承了一个简单基类，实际背后跑了一整套隐式逻辑。这种框架的调试和测试成本极高。

**哪些对象允许继承，哪些不允许**：

| 允许继承（扩展点） | 不允许继承（骨架/门面） | 原因 |
|-------------------|----------------------|------|
| `BaseRecipe` | `HarnessKernel` | Kernel 是运行时骨架，继承后长成隐式框架 |
| `BaseStage` | `ToolExecutor` | Executor 是执行策略，需要行为一致 |
| `BaseTool` | `ToolRegistry` | Registry 是基础设施，全局唯一 |
| `BaseSubAgent` | `RagFacade` | Facade 是稳定门面，内部可替换 |
| `BaseContextProvider` | `RuntimeEntry` | 入口应统一，不可被扩展 |

---

### 十五：总结：为什么这套架构能支撑未来扩展

```
                    新增 Tool         新增 Capability     新增治理规则
                    ─────────        ──────────────     ────────────
HarnessKernel       不变 ✅           不变 ✅             不变 ✅
ToolExecutor        不变 ✅           不变 ✅             不变 ✅
PolicyEngine        不变 ✅           不变 ✅             不变 ✅
GuardrailEngine     不变 ✅           不变 ✅             不变 ✅
EventBus            不变 ✅           不变 ✅             不变 ✅
ToolRegistry        注册新 Tool ✅    不变 ✅             不变 ✅
RecipeRegistry      不变 ✅           注册新 Recipe ✅     不变 ✅
HookRegistry        不变 ✅           不变 ✅             注册新 Hook ✅

需要改的：          1 个文件          3-4 个文件          1 个文件
```

**核心设计目标**：**新增扩展只需注册，不需改骨架**。Recipe/Stage/Recipe 定义了"做什么"，Kernel/Executor/EventBus 定义了"怎么做"。两者解耦后，新增"做什么"不需要动"怎么做"。

---

## 📖 扩展指南

### 新增一个 Capability（任务类型）需要改什么？

```text
1. 在 app/capabilities/ 下新增领域能力实现（如果需要）
2. 在 app/workflows/tasks/ 下新增 {name}_skill.py
   → 定义 Skill 类，实现 build_initial_state()
3. 在 app/workflows/tasks/ 下新增 {name}_graph.py / {name}_nodes.py
   → 定义 LangGraph StateGraph 和节点
4. 修改 app/workflows/tasks/builtin_skills.py
   → 注册 Skill 元数据
5. 在 app/capabilities/registry.py 中注册 CapabilityDefinition
6. 在 app/container.py 中完成装配
```

**不需要改**：`HarnessKernel`、`ToolExecutor`、`PolicyEngine`、`GuardrailEngine`

### 新增一个 Tool 需要改什么？

```text
1. 如果需要外部 API，在 app/capabilities/ 下写 Capability 实现
2. 在 app/agents/tools/ 下写 Tool 子类
   → 继承 BaseTool，实现 run() 方法，定义 input_model / output_model
3. 在 app/container.py 中注册工具（加到 self.task_tool_registry.register() 列表）
```

**不需要改**：`HarnessKernel`、`Stage`、`ToolExecutor`、`PolicyEngine`

### 新增一个治理 Hook 需要改什么？

```text
1. 实现 RuntimeHook 协议（class MyHook: name + handle(event)）
2. 在 container.py 中注册到 EventBus：self.event_bus.register(MyHook(), event=HookEvent.BEFORE_TOOL)
```

**不需要改**：`HarnessKernel`、`Stage` 核心代码

---

## 📚 项目文档

| 文档 | 位置 | 说明 |
|------|------|------|
| **架构总览** | [`架构.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/架构.md) | 完整分层架构、模块职责、数据主链、通信链路 |
| **Agent Platform 设计** | [`docs/agent-platform-architecture.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/agent-platform-architecture.md) | Mode + Capability 新模型设计 |
| **实施路线图** | [`docs/agent-platform-roadmap.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/agent-platform-roadmap.md) | 分阶段实施计划 |
| **Harness 统一重构** | [`docs/harness-unification-refactor.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/harness-unification-refactor.md) | 双流水线统一重构方案 |
| **记忆系统改造** | [`docs/memory-system-redesign.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/memory-system-redesign.md) | 五层记忆系统设计 |
| **Harness 运行时契约** | [`docs/architecture/harness-runtime-contracts.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/architecture/harness-runtime-contracts.md) | 稳定接口/扩展接口/兼容层界定 |

---

## 🗺️ 开发路线图

当前状态：**v6.0 Agent Platform 核心框架已完成**

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | Harness 内核统一重构 + 记忆系统改造 | ✅ 完成 |
| Phase 2 | CapabilityRegistry + IntentMatcher + AgentService + 统一 Agent API | ✅ 完成 |
| Phase 3 | LlmRouter 按用途路由 + 运行时配置管理 | ✅ 完成 |
| Phase 4 | Plan 模式 + Autopilot 模式 | 🏗️ 进行中 |
| Phase 5 | Sandbox 命令执行 + Coding Agent | 📋 待开始 |
| Phase 6 | Data Analysis Agent + Web 前端 | 📋 待开始 |

---

## 📄 许可证

MIT

---
