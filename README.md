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
  用户 → TaskSpec → Orchestrator → LangGraph 工作流
    → ExecutionHarness.run_tool(rag_*/repo_*/database_*/...) → Facade → Domain Service
```

**核心变化**：

1. **RAG 降级为能力** — RAG 只是众多 Tool 之一，与 repository / database / api_contract 平级，不再是产品主轴
2. **Harness 是顶层** — 治理（policy / guardrail / budget / sandbox / hook）由 Harness 统一控制，Agent 只负责任务推进
3. **ReAct 是局部的** — 只在单 step 内运行有界循环（max_turns=3），不接管系统治理

---

## 🏗️ 整体架构

系统采用**六层 + 大脑架构**，从外到内依次是：

```text
┌────────────────────────────────────────────────────────────┐
│                 Entry Layer (入口层)                        │
│   API / Service / Worker 入口、请求适配为 TaskSpec           │
├────────────────────────────────────────────────────────────┤
│                  State Layer (状态层)                        │
│   Memory / Context / Checkpoint / Artifact Lineage          │
├────────────────────────────────────────────────────────────┤
│                 Workflow Layer (工作流层)                    │
│   LangGraph 编排 / Orchestrator / Skill / Step Lifecycle   │
├────────────────────────────────────────────────────────────┤
│              ════ Brain Layer (大脑层) ════                 │
│   IntentRecognizer / ModeRouter / AgentLoop                │
│   StepExecutor / SafetyEngine                              │
│   ← 大脑在 Harness 中：大脑决定怎么调，Harness 约束怎么执行 → │
├────────────────────────────────────────────────────────────┤
│                Capability Layer (能力层)                     │
│   Tool / SubAgent / Facade / CatalogTool                    │
│   CustomizationEngine                                      │
├────────────────────────────────────────────────────────────┤
│                Governance Layer (治理层)                     │
│   Policy / Guardrail / EventBus Hook / Sandbox / Budget      │
│   SafetyEngine（可插拔安全策略 7 种）                        │
├────────────────────────────────────────────────────────────┤
│                  Infra Layer (基础设施层)                    │
│   LLM Provider / Vector Store / SQLite / Sandbox Worker     │
└────────────────────────────────────────────────────────────┘
```

### 核心执行主链

```text
请求 → Entry → TaskSpec → Orchestrator → LangGraph 节点
  → [Brain Layer] IntentRecognizer → ModeRouter → AgentLoop
    → StepExecutor（含 SafetyEngine 检查）
      → ExecutionHarness.run_tool()
                                                    ↓
                                               EventBus ──→ HookRegistry → Trace/Memory/Checkpoint
           (每次 step/tool 事件都广播，治理逻辑通过 Hook 横向切面)
```

**一句话主链**：`TaskSpec → LangGraph → Brain → ExecutionHarness → Tool → Facade`

这条链之外不应有第二条平行主链。LangGraph 是唯一的工作流引擎，Brain Layer 是感知与决策中心。

---

## 📁 项目目录结构

```text
app/
├── main.py                       # FastAPI 应用入口
├── container.py                  # [Assembly] 依赖容器，所有组件按顺序装配

├── api/                          # [Entry] HTTP 接口层
│   └── v1/endpoints/
│       ├── query.py              # query / chat / stream 接口
│       ├── tasks.py              # 任务 CRUD 接口
│       ├── agent.py              # 统一 Agent 对话入口
│       ├── documents.py          # 文档上传/导入
│       ├── collections.py        # 知识库集合管理
│       ├── sessions.py           # 会话管理
│       ├── eval.py               # 评测接口
│       ├── feedback.py           # 反馈接口
│       ├── health.py             # 健康检查/metrics
│       ├── capabilities.py       # Capability 列表
│       ├── admin_agents.py       # Agent 定义管理
│       ├── admin_hooks.py        # Hook 配置管理
│       ├── admin_instructions.py # 系统指令管理
│       ├── admin_file_instructions.py # 文件指令管理
│       └── admin_*/              # 其他管理后台接口（LLM/Skill/Prompt/MCP）

├── services/                     # [Entry+Infra] 业务服务入口
│   ├── query_service.py          # query/chat 请求入口服务
│   ├── task_service.py           # task 任务入口服务
│   ├── agent_service.py          # 统一 Agent 服务（处理 mode/capability）
│   ├── customization_engine.py   # 定制化原语引擎
│   ├── extension_catalog.py      # 扩展清单（LLM 可浏览）
│   ├── frontmatter_parser.py     # YAML frontmatter 解析
│   ├── instructions_manager.py   # 系统指令管理器
│   ├── file_instruction_manager.py # 文件级指令管理器
│   ├── hook_loader.py            # FileHook JSON 加载
│   ├── hook_adapter.py           # FileHook → RuntimeHook 适配
│   ├── hook_actions.py           # Hook 动作执行引擎
│   ├── consent_store.py          # 用户确认记录存储
│   ├── session_manager.py        # 会话管理器（持久化到 SQLite）
│   ├── document_service.py       # 文档导入服务
│   ├── collection_service.py     # 集合管理服务
│   ├── task_dispatcher.py        # 任务队列分发器
│   ├── sqlite_store.py           # SQLite 持久化存储
│   ├── state.py                  # InMemoryState 进程内缓存
│   ├── semantic_cache.py         # 语义缓存服务
│   ├── eval_service.py           # 评测服务（RAGAS / benchmark）
│   ├── feedback_service.py       # 反馈收集服务
│   ├── graph_service.py          # GraphRAG 图谱服务
│   ├── memory_commit_gate.py     # 记忆提交门（trust 提升 + semantic 晋升）
│   ├── user_profile_service.py   # 用户画像服务
│   ├── intent_matcher.py         # 意图匹配到 Capability
│   ├── llm_router.py             # LLM 按用途路由
│   ├── plan_generator.py         # plan 模式生成计划
│   ├── plan_executor.py          # plan 模式执行计划
│   └── auth_manager.py           # 认证管理

├── harness/                      # [Brain + Governance] 大脑与治理层
│   ├── brain/                    # 大脑层 — 感知 + 决策
│   │   ├── agent_loop.py         # AgentLoop：LLM 驱动工具调用循环
│   │   ├── intent_recognizer.py  # 统一意图识别（双层策略）
│   │   ├── mode_router.py        # 模式路由（chat/plan/autopilot）
│   │   ├── step_executor.py      # 步骤执行器 + SafetyEngine 安全检查
│   │   └── models.py             # 大脑层数据模型
│   ├── safety/                   # 安全引擎 — 可插拔策略体系
│   │   ├── engine.py             # SafetyEngine + SafetyPolicy ABC
│   │   └── policies/             # 内置安全策略（7 种）
│   ├── execution.py              # ExecutionHarness facade：完整工具执行治理链入口
│   ├── context.py                # ContextHarness facade：上下文构建入口
│   ├── policy.py                 # PolicyEngine：权限策略检查
│   ├── guardrails.py             # GuardrailEngine：安全护栏
│   ├── sandbox.py                # ToolSandbox：工具级沙盒
│   ├── reflection.py             # ReflectionHarness：反思评估
│   ├── recovery.py               # RecoveryManager：失败恢复
│   ├── prompting.py              # PromptBuilder：提示词构造
│   ├── model_router.py           # ModelRouter：模型路由
│   ├── react_runtime.py          # BoundedLocalReActRuntime：有界局部 ReAct
│   ├── hooks.py                  # HookRegistry + EventBus：事件总线（22 种事件）
│   ├── trace_hook.py             # TraceHook / MemoryHook：事件→Trace/Memory
│   ├── models.py                 # ContextBundle / ToolExecutionResult / ...
│   └── components/               # 治理组件
│       ├── execution_hooks.py    # ExecutionHooks：运行时摘要 + EventBus 转发
│       ├── execution_policy.py   # ExecutionPolicyResolver
│       ├── fallback_handler.py   # FallbackHandler：失败兜底
│       ├── tool_executor.py      # ToolExecutor：timeout/retry/circuit_breaker
│       └── context_builders.py   # TaskContextBuilder / QueryContextBuilder

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

├── agents/                       # [Capability] Agent + Tool Surface
│   ├── runtime.py                # AgentRuntime：任务运行时入口
│   ├── memory.py                 # TaskMemory：任务记忆统一读写
│   ├── subagents.py              # SubAgentRegistry + SubAgentRuntime：受控子代理
│   ├── planner.py                # TaskPlanner：任务计划
│   └── tools/
│       ├── registry.py           # ToolRegistry：工具注册/查找/执行
│       ├── base.py               # BaseTool / ToolSchema / ToolContext：工具基类
│       ├── defaults.py           # build_runtime_rag_tools()：默认 RAG 工具集
│       ├── catalog_tools.py      # load_extension / load_rule 工具
│       ├── rag_tools.py          # rag_* 工具族（5 个）
│       ├── command_tools.py      # ShellCommandTool / RepositoryCommandTool
│       ├── repository_tools.py   # 仓库工具（list/search/read）
│       ├── database_tools.py     # 数据库工具（list/describe/query）
│       ├── api_contract_tools.py # API 契约工具
│       ├── artifact_tools.py     # 报告工具（draft/review/finalize）
│       ├── analysis_tools.py     # 分析工具（extract_key_points / extract_risks）
│       ├── weather_tools.py      # 天气工具（2 个）
│       ├── finance_tools.py      # 金融工具（2 个）
│       ├── news_tools.py         # 新闻工具（2 个）
│       ├── currency_tools.py     # 汇率工具（2 个）
│       ├── calculator_tools.py   # 安全计算器（1 个）
│       ├── datetime_tools.py     # 时间工具（2 个）
│       ├── geocoding_tools.py    # 地理编码（2 个）
│       ├── url_fetch_tools.py    # 网页抓取（1 个）
│       ├── translation_tools.py  # 翻译工具（2 个）
│       ├── chart_tools.py        # 图表生成（1 个）
│       └── web_search_tools.py   # 联网搜索（1 个）

├── capabilities/                 # [Domain] 领域能力实现（每个 capability 一个目录）
│   ├── knowledge/                # KnowledgeCapability：知识检索（RAG）
│   ├── repository/               # RepositoryCapability：仓库文件浏览
│   ├── database/                 # DatabaseCapability：数据库查询
│   ├── api_contract/             # ApiContractCapability：API 契约发现
│   ├── artifact/                 # ArtifactCapability：产物管理
│   ├── weather/                  # WeatherCapability：天气查询
│   ├── finance/                  # FinanceCapability：金融数据
│   ├── news/                     # NewsCapability：新闻聚合
│   ├── currency/                 # CurrencyCapability：汇率转换
│   ├── calculator/               # CalculatorCapability：安全计算
│   ├── datetime/                 # DateTimeCapability：时间日期
│   ├── geocoding/                # GeocodingCapability：地理编码
│   ├── url_fetch/                # UrlFetchCapability：网页抓取
│   ├── translation/              # TranslationCapability：翻译
│   ├── chart/                    # ChartCapability：图表生成
│   └── web_search/               # WebSearchCapability：联网搜索

├── rag/                          # [Domain] RAG 域内实现
│   ├── facade.py                 # RagFacade：知识能力唯一门面
│   ├── vector_store.py           # ChromaClientFactory：ChromaDB 向量库工厂
│   ├── ingestion.py              # 文档导入与索引
│   ├── retrieval.py              # 检索服务
│   ├── query_engine.py           # RAG 查询引擎
│   ├── observability.py          # TraceRecorder：链路追踪
│   └── llamaindex_components.py  # LlamaIndex 集成

├── models/                       # [Data] 数据模型
│   ├── task.py                   # TaskSpec / TaskRun / TaskDetail
│   ├── query.py                  # QueryRequest / QueryResponse / ChatRequest
│   ├── runtime_contracts.py      # MemoryRecord / PromptSpec / ResultContract
│   ├── artifact.py               # Artifact / EvidencePack
│   └── customization.py          # 定制化原语 Schema（Frontmatter 定义）

├── core/                         # [Infra] 核心基础设施
│   ├── config.py                 # Settings：全局配置（Pydantic）
│   ├── logging.py                # 日志配置
│   ├── errors.py                 # 异常处理注册
│   └── auth.py                   # 认证中间件

├── .agents/                      # [Config] 定制化原语文件
│   ├── AGENTS.md                 # 系统指令
│   ├── instructions/             # 文件级指令（*.instructions.md）
│   ├── prompts/                  # Prompt 模板（*.prompt.md）
│   ├── skills/                   # Skill 定义（SKILL.md + rules/）
│   ├── agents/                   # Agent 定义（*.agent.md）
│   └── hooks/                    # Hook 配置（*.json）

├── config/                       # [Config]
│   ├── safety.yaml               # 安全策略配置
│   └── harness-policy-profiles.yaml

tests/                            # 单元测试
├── test_brain_models.py          # 大脑层模型测试
├── test_intent_recognizer.py     # 意图识别测试
├── test_mode_router.py           # 模式路由测试
├── test_agent_loop.py            # Agent Loop 测试
├── test_step_executor.py         # 步骤执行器测试
├── test_safety_engine.py         # 安全引擎测试（含 7 策略）
├── test_customization_primitives.py # 定制化原语测试
└── ...                           # 其他已有测试

scripts/                          # 工具脚本
├── generate_primitive.py         # 原语文件生成器
└── ...                           # 其他评测脚本

docs/                             # 项目文档
├── 架构.md                       # 本项目完整分层架构与模块通信总览
├── intent-recognition-module-design.md # 意图识别模块设计文档（2026-07-13 新增）
├── agent-customization-primitives-design.md # 定制化原语系统设计文档（2026-07-13 新增）
├── admin-resources-unified-design.md # 管理后台统一设计文档（2026-07-13 新增）
├── skill-storage-refactor-plan.md # Skill 存储重构计划（2026-07-13 新增）
├── agent-platform-architecture.md # Agent Platform 架构设计
├── agent-platform-roadmap.md     # 实施路线图
├── memory-system-redesign.md     # 记忆系统改造方案
├── architecture/
│   └── README.md                 # 架构文档索引
└── operations/
    └── remote-provider-runbook.md # 远程 Provider 运维手册
```

---

## 🎯 核心概念

### Mode（执行模式 = 怎么做）

三种执行模式，应对不同场景。**Mode 是 `AgentChatRequest` 的字段，只存在于 Agent 接口（`POST /agent`）中**，传统 RAG 接口（`POST /query`、`POST /chat`、`POST /tasks`）没有 Mode 概念。

| Mode | 说明 | 适用场景 |
|------|------|----------|
| `chat` | 用户输入 → 意图识别 → 匹配 Capability → 直接执行 → 返回结果 | 快速问答、简单请求、日常对话 |
| `plan` | 用户输入 → 意图识别 → 生成计划 → 用户确认 → 按计划执行 | 复杂任务、需要用户审核的场景 |
| `autopilot` | 用户输入 → 意图识别 → 自动执行 → 完成后询问下一步 | 长时间任务、批处理、持续交互 |

### 请求类型（请求数据模型 = 请求长什么样）

**四种请求数据模型**，对应不同的 API 端点和业务场景。它们各自耦合到特定领域，不是通用概念：

| 请求模型 | API 端点 | 所属域 | 用途 | 是否带 Mode |
|----------|---------|--------|------|------------|
| `QueryRequest` | `POST /query` | **RAG 域** | 单轮检索问答（带 RAG 能力开关） | ❌ 无 |
| `ChatRequest` | `POST /chat` | **RAG 域** | 多轮会话问答（继承 QueryRequest，必须带 session_id） | ❌ 无 |
| `TaskRequest` | `POST /tasks` | **任务域** | 创建长时文档分析任务 | ❌ 无 |
| `AgentChatRequest` | `POST /agent` | **Agent 域** | 统一 Agent 对话入口 | ✅ 有（chat/plan/autopilot） |

> `QueryRequest` / `ChatRequest` 是 RAG 域的专属模型，由 `QueryService` → `QueryWorkflowOrchestrator` 管道处理，不经过 Agent 平台。

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

---

## 🧠 Brain Layer（大脑层）— 新增（2026-07-13）

> **核心命题**：大脑在 Harness 中。大脑决定怎么调用，Harness 约束怎么执行。

大脑层由三个子系统组成：**感知层**（IntentRecognizer）+ **决策层**（AgentLoop）+ **安全层**（SafetyEngine）。

### 感知层：IntentRecognizer

双层策略意图识别：

1. **QuickHeuristicClassifier**（<1ms）— 规则引擎，覆盖约 80% 常见情况
2. **LLMIntentClassifier**（~200ms）— LLM 处理复杂/边缘情况

输出 `IntentDecision`（knowledge_sources, complexity, risk_level, suggested_mode），一次性设定整场会话上下文。

### 决策层：AgentLoop + StepExecutor

`AgentLoop` 是 LLM 驱动的多轮工具调用循环：LLM 决定 → `StepExecutor` 执行 → 结果回传。

`StepExecutor` 负责：读取工具风险声明 → `SafetyEngine` 执行前检查 → 执行（服务端沙箱/客户端下发）→ `SafetyEngine` 输出检查 → 用户确认（需同意时）。

### 安全层：SafetyEngine

5 层可插拔安全策略体系，通过 `config/safety.yaml` 配置：

| 策略 | 检查内容 |
|------|---------|
| 数据销毁防护 | 防止破坏性操作（rm/drop/delete） |
| 数据外泄防护 | 防止敏感数据泄露 |
| 权限提升防护 | 防止越权行为 |
| 远程代码执行防护 | 防止恶意代码执行 |
| 会话风险评估 | 会话级风险检测 |
| 系统篡改防护 | 防止系统配置篡改 |
| 工具输出内容安全 | 工具输出内容安全审查 |

```python
class SafetyPolicy(ABC):
    name: str
    def check(self, context: SafetyContext) -> SafetyDecision: ...
```

### 关键文件

| 文件 | 职责 |
|------|------|
| `app/harness/brain/intent_recognizer.py` | 统一意图识别 |
| `app/harness/brain/agent_loop.py` | LLM 驱动工具调用循环 |
| `app/harness/brain/mode_router.py` | 模式路由 |
| `app/harness/brain/step_executor.py` | 步骤执行 + 安全检查 |
| `app/harness/brain/models.py` | 大脑层数据模型 |
| `app/harness/safety/engine.py` | SafetyEngine |
| `app/harness/safety/policies/*.py` | 7 个内置安全策略 |

---

## 📦 Agent 定制化原语系统 — 新增（2026-07-13）

将 IDE 领域的 AI Agent 定制化原语映射到后端 Agent 运行时，构建一套完整的 **文件驱动 + API 可管理** 的扩展体系。

### 支持的 7 种原语

| 原语 | 文件形态 | 管理器 |
|------|---------|--------|
| **Instructions** | `.agents/AGENTS.md` | `InstructionsManager` |
| **File Instructions** | `.agents/instructions/*.instructions.md` | `FileInstructionManager` |
| **Prompts** | `.agents/prompts/*.prompt.md` | `PromptManager` |
| **Skills** | `.agents/skills/<name>/SKILL.md` | `SkillManager` + `ExtensionCatalog` |
| **Custom Agents** | `.agents/agents/*.agent.md` | `AgentDefManager` |
| **Hooks** | `.agents/hooks/*.json` | `HookLoader` → `HookAdapter` → `HookRegistry` |
| **MCP Servers** | `.agents/mcp.json` | `McpManager` |

### 核心机制

- **CustomizationEngine**（`app/services/customization_engine.py`）：Agent 初始化时自动扫描 `.agents/` 目录，加载所有原语并组装运行时上下文
- **ExtensionCatalog**（`app/services/extension_catalog.py`）：给 LLM 提供轻量级扩展清单，通过 `load_extension` / `load_rule` 工具按需加载
- **FrontmatterParser**（`app/services/frontmatter_parser.py`）：统一解析 YAML frontmatter + body
- **Hook 系统**：JSON 配置的 FileHook → `RuntimeHook` 协议适配，支持 log/block/audit/notify/throttle/mutate_payload 等动作

### 关键文件

| 文件 | 职责 |
|------|------|
| `app/services/customization_engine.py` | 定制化原语引擎 |
| `app/services/extension_catalog.py` | 扩展清单 |
| `app/services/frontmatter_parser.py` | YAML frontmatter 解析 |
| `app/services/hook_loader.py` / `hook_adapter.py` / `hook_actions.py` | Hook 系统 |
| `app/services/consent_store.py` | 用户确认记录存储 |
| `app/models/customization.py` | 定制化原语 Schema |
| `app/agents/tools/catalog_tools.py` | load_extension / load_rule 工具 |

---

## 🔬 各层工作原理详解

> 以下每一节都以**一次真实请求的代码执行路径**为主线，逐层追踪数据如何流入、流转、流出。

---

### 1. Entry Layer（入口层）— 怎么工作的

**职责**：把外部 HTTP 请求路由到对应的领域管道。系统有**三条独立入口路径**，每条耦合到不同领域：

```text
POST /query ──→ QueryRequest  ──→ QueryService.query()     ──→ RAG 管道（QueryWorkflowOrchestrator）
POST /chat  ──→ ChatRequest   ──→ QueryService.chat()      ──→ RAG 管道（同上）
POST /tasks ──→ TaskRequest   ──→ TaskService.create_task()──→ 文档分析管道（TaskDispatcher → AgentRuntime）
POST /agent ──→ AgentChatRequest → AgentService.process()  ──→ Agent 平台（Mode + Capability 路由）
```

> **关键认知**：`QueryRequest` / `ChatRequest` / `TaskRequest` 是 RAG 域和任务域的请求模型，**入口层本身不是一个独立层**——它只是 API 端点 + Service 薄路由的组合。真正的入口逻辑（校验、适配、投影）由各领域 Service 内部完成。

#### 代码执行路径（以 RAG 查询为例）

以 `POST /api/v1/query` 为例，追踪 `QueryRequest` 如何进入 RAG 管道：

**第一步：FastAPI 端点接收请求**

[app/api/v1/endpoints/query.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/api/v1/endpoints/query.py) 第 33-45 行：

```python
@router.post('/query', response_model=QueryResponse)
async def query(payload: QueryRequest, request: Request) -> QueryResponse:
    container = get_container(request)            # 1. 从请求中获取依赖容器
    return container.query_service.query(payload) # 2. 委托给 QueryService
```

端点只做两件事：解析 JSON → Pydantic 模型，然后委托给 Service。

**第二步：QueryService 薄层路由**

[app/services/query_service.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/services/query_service.py) 第 104-113 行：

```python
def query(self, payload: QueryRequest) -> QueryResponse:
    return self.query_engine.query(payload)  # 直接转发给 RAG QueryWorkflowOrchestrator
```

`QueryService` 是 RAG 域的专属服务，不执行业务逻辑，只做路由转发。它的 `query_engine` 就是 `QueryWorkflowOrchestrator`。

**第三步：QueryWorkflowOrchestrator 进入 RAG 管道**

[app/workflows/query_orchestrator.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/workflows/query_orchestrator.py) — 导入 `QueryRequest`、`ChatRequest`、`build_runtime_rag_tools`、`RagQueryEngine`，深度耦合 RAG 域。其 `query()` 方法组装 Query LangGraph，按 LangGraph 图执行 RAG 检索+生成完整流程。

**关键设计**：`QueryService` 和 `QueryWorkflowOrchestrator` 都是 RAG 域的组成部分，不是独立于 RAG 之外的通用的"入口层"。不同入口路径对应不同领域管道，互不干扰。

#### 入口层通信总结

```
┌─ RAG 域入口 ───────────────────────────────────────────────────────┐
│ POST /query  → QueryRequest  → QueryService  → QueryWorkflowOrchestrator │
│ POST /chat   → ChatRequest   → QueryService  → QueryWorkflowOrchestrator │
│                                    ↑ 以上全部耦合 RAG 域              │
└────────────────────────────────────────────────────────────────────┘

┌─ 任务域入口 ───────────────────────────────────────────────────────┐
│ POST /tasks  → TaskRequest  → TaskService  → TaskDispatcher        │
│                                              → AgentRuntime         │
│                                    ↑ 以上全部耦合文档分析任务域        │
└────────────────────────────────────────────────────────────────────┘

┌─ Agent 域入口 ──────────────────────────────────────────────────────┐
│ POST /agent  → AgentChatRequest  → AgentService.process()          │
│    mode=chat      → _handle_chat_mode()      直接执行               │
│    mode=plan      → _handle_plan_mode()      生成计划→等待确认→执行  │
│    mode=autopilot → _handle_autopilot_mode() 自动执行→询问下一步     │
│                                    ↑ 以上全部耦合 Agent 平台域        │
└────────────────────────────────────────────────────────────────────┘
```

#### 入口层优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **Pydantic 请求校验** | `app/api/v1/endpoints/query.py` | 请求到达时自动校验字段类型/必填/范围，无效请求在入口层就被拦截，不进入后续流程 |
| **TaskSpec 统一投影** | `app/workflows/query_task_adapter.py` | 3 种外部请求格式 → 1 种内部格式，Workflow 层只认 `TaskSpec`，消除分支判断 |
| **SSE 流式响应** | `app/api/v1/endpoints/query.py` `stream_query()` | 长回答分批推送（delta 事件），用户不用等全部生成完才能看到内容 |
| **心跳保活** | 同上，heartbeat 事件 | 长连接期间每 15s 发送心跳，防止代理/网关超时断开 |
| **薄 Service 层** | `app/services/query_service.py` | Service 不做业务逻辑，只转发给 Orchestrator，减少调用链上的中间状态 |

---

### 2. State Layer（状态层）— 怎么工作的

**职责**：管理跨 step / run / session 的数据生命周期。每个 step 开始时构建最小上下文切片（ContextBundle），step 结束时写回记忆（MemoryRecord）。

#### 五级记忆模型

| 级别 | 生命周期 | 存储 | 写入触发 |
|------|---------|------|---------|
| **working** | 单 step 内，完成后清除 | 进程内存 | Tool 调用、ReAct 观察 |
| **run** | 单次 TaskRun 全过程 | SQLite + 内存 | 每个 step 完成时 |
| **session** | 同一 session 连续交互 | SQLite | SessionManager.add_message() |
| **semantic** | 跨 run 复用，不自动删除 | SQLite 独立表 | MemoryCommitGate（需 trust≥verified） |
| **profile** | 用户级别，长期稳定 | SQLite 独立表 | UserProfileService |

#### 代码执行路径：一个 step 的上下文构建与记忆写入

**Step 开始前——构建 ContextBundle：**

`ContextHarness.build_context(workflow_state, step_id)` 按 6 步流水线构建：

```
Gather（收集）→ Filter（过滤敏感/过期）→ Rank（按相关度排序）
  → Compress（超 token 上限时压缩）→ Budget（最终 token 预算控制）→ Package（打包输出）
```

产出的 `ContextBundle` 包含 `state_slice`、`evidence_slice`、`artifact_slice`、`memory_slice`、`tool_options` 等字段，每个 slice 都标注了来源（`source_summary`）和可靠性（`reliability_summary`）。

**Step 执行中——EventBus 驱动记忆写入：**

每次 Tool 调用完成后，`ExecutionHarness` 在 `finally` 块中（[execution.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/harness/execution.py) 第 226-258 行）：

1. 构造 `ToolExecutionResult`（包含 tool_name, status, latency_ms, retries 等）
2. 调用 `self.hooks.record_execution(workflow_state, step_id, execution)` → 写入 TaskMemory
3. EventBus 发射 `AFTER_TOOL` 事件 → `MemoryHook.handle()` 自动追加 `MemoryRecord`

**双写模式**：`InMemoryState`（热缓存，进程内读写无延迟）+ `SQLiteStateStore`（持久化，重启不丢失）。启动时 `persistence.load_into(state)` 从 SQLite 恢复到内存。

#### 状态层通信总结

```
State Layer ← Workflow Layer
  ContextHarness.build_context() ← Orchestrator 在每个 step 前调用

State Layer ← Governance Layer  
  MemoryHook 监听 EventBus 事件 → 自动写入 TaskMemory
  EventBus 发射事件时携带 workflow_state → MemoryHook 从中提取 task_id

State Layer → Infra Layer
  TaskMemory 内部通过 SQLiteStateStore 持久化
  InMemoryState 作为热缓存，SQLite 作为持久化
```

#### 状态层优化策略

> 记忆系统的**选择 → 压缩 → 预算 → 门控**四层优化已在 [🧠 记忆系统架构](#-记忆系统架构) 详细展开，此处不重复。以下补充状态层独有的优化：

| 优化 | 位置 | 效果 |
|------|------|------|
| **双写缓存** | `TaskMemory` → `InMemoryState` + `SQLiteStateStore` | 内存热缓存（读延迟 0）+ SQLite 持久化（重启不丢），启动时 `load_into()` 恢复 |
| **滑动窗口上限** | `app/agents/memory.py` 各 `append` 方法 | 每种记忆类型硬上限（200/100/50），追加后自动截断，防止内存无限增长 |
| **Checkpoint 断点续传** | `app/workflows/query_orchestrator.py` | LangGraph checkpoint 保存每个 step 完成后的状态，支持 resume/replay/recover |
| **语义记忆跨任务注入** | `app/harness/components/context_builders.py` | 高信任度语义记忆自动注入到新任务的 ContextBundle，实现跨任务知识复用 |
| **信任等级门控** | `app/services/memory_commit_gate.py` | 只有 trust≥verified 的记忆才能晋升为 semantic，防止不可靠结论污染长期记忆 |

---

### 3. Workflow Layer（工作流层）— 怎么工作的

**职责**：决定任务按什么步骤推进。**LangGraph 是唯一的工作流引擎**，Orchestrator 负责组装 LangGraph 图并驱动执行。

#### 架构设计：RAG 的双重身份

RAG 在系统中以两种身份运行：

```
RAG 作为独立应用（/query、/chat 端点）:
  QueryService → QueryWorkflowOrchestrator → LangGraph(Query Graph) → RagFacade（直接调用）
  ★ 不经过 ExecutionHarness，RAG 管道内部直接使用 RagFacade

RAG 作为注册 Tool（/agent、/tasks 端点）:
  AgentService/TaskService → Orchestrator → LangGraph 节点 → ExecutionHarness.run_tool("rag_*")
  ★ 所有 Tool 通过 ToolRegistry 统一调用，Harness 不知道具体 Capability 是什么
```

#### 代码执行路径

**第一步：Orchestrator 组装 LangGraph 图**

[app/workflows/query_orchestrator.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/workflows/query_orchestrator.py) 的 `query()` 方法：

```python
def query(self, payload: QueryRequest) -> QueryResponse:
    task_spec = build_query_task_spec(payload)         # 1. 构建 TaskSpec
    state = init_query_graph_state(payload)             # 2. 初始化图状态
    graph = build_query_graph()                         # 3. 构建 LangGraph 图
    final_state = graph.invoke(state)                   # 4. 执行图
    return build_response(final_state)                  # 5. 构建响应
```

**第二步：LangGraph 图执行**

每个节点是独立的处理函数，条件路由由图的 conditional edges 决定。每个节点内部通过 `ExecutionHarness.run_tool()` 调用工具，治理链自动生效：

```text
LangGraph 节点函数（如 retrieve_evidence）
  │
  ├─ ContextHarness.build_context(workflow_state, step_id)  ← 构建上下文
  │
  ├─ ExecutionHarness.run_tool('rag_retrieve_evidence', payload, ws, ctx_bundle)
  │    │
  │    ├─ EventBus.before_tool()          ← 发射 before_tool 事件
  │    ├─ GuardrailEngine.validate_tool_call()  ← 护栏检查
  │    ├─ PolicyEngine.check_tool()             ← 权限检查
  │    ├─ SandboxEngine.assess()                ← 沙盒决策
  │    ├─ ToolExecutor.execute()                ← 执行（含 timeout/retry/circuit_breaker）
  │    ├─ EventBus.after_tool()           ← 发射 after_tool 事件
  │    └─ ExecutionHooks.record_execution()     ← Trace + Memory 写入
  │
  └─ 返回更新后的 state
```

#### Query 工作流的 LangGraph 图结构

实际代码在 [app/workflows/query_graph.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/workflows/query_graph.py) 的 `build_query_graph()` 中，核心设计是 **`dispatch_query_step` 路由器**：

```
__entry__ → check_guardrails
              ├─ blocked → blocked_response → finalize
              └─ dispatch → dispatch_query_step ← 核心路由器
                              ├─ load_session_context → 回到 dispatch
                              ├─ rewrite_query        → 回到 dispatch
                              ├─ expand_queries       → 回到 dispatch
                              ├─ lookup_cache
                              │    ├─ hit → cache_hit_response → finalize
                              │    └─ miss → 回到 dispatch
                              ├─ retrieve_evidence    → 回到 dispatch
                              ├─ compress_context     → 回到 dispatch
                              ├─ grounded_answer      → 回到 dispatch
                              ├─ self_reflect
                              │    ├─ retry → retry_retrieve → retrieve_evidence
                              │    ├─ rewrite → rewrite_answer → dispatch
                              │    └─ pass → 回到 dispatch
                              ├─ persist_session → finalize
                              └─ finalize → END
```

`dispatch_query_step` 类似 CPU 的取指-执行循环——每个 Step 节点执行完后都回到它，由它决定下一个要执行的 step。

**Task 工作流（以 DocumentAnalysis 为例）：**

```
plan → collect_document_context → retrieve_evidence → analyze
  → draft_report → review_report → revise_report → finalize_report
```

#### Step 生命周期管理

`app/workflows/step_lifecycle.py` 提供共享的 Step 生命周期 helper，query 和 task 两侧共用：

- `step_started` / `step_completed` / `step_failed` 事件
- `checkpoint` 创建（在关键步骤后自动保存）
- `RunEvent` 记录（进入 TaskRun 数据面）

#### 关键原则

- **LangGraph 是唯一的工作流引擎**——不在其上叠加 Recipe/Stage/HarnessKernel 抽象
- **Orchestrator 是组装者**——负责创建 LangGraph 图、注入依赖、驱动执行
- **RAG 是独立应用 + 也是 Tool**——`/query` `/chat` 走自己的 RAG 管道；同时 RAG 能力注册为 Tool 供 Agent 平台调用
- **ExecutionHarness 只依赖 ToolRegistry**——不 import 任何具体 Capability
- 新增任务类型时写 Skill + LangGraph 图，不改 Orchestrator 骨架
- 节点不直接调 `trace.record()` 或写内存——这些通过 EventBus 由 hook 处理

#### 工作流层通信总结

```
Workflow Layer ← Entry Layer
  Orchestrator 接收 TaskSpec → 初始化 QueryGraphState

Workflow Layer → State Layer
  ContextHarness.build_context() ← 每个 step 开始前
  TaskMemory.append_memory_record() ← 每个 step 结束后

Workflow Layer → Capability Layer
  LangGraph 节点通过 ExecutionHarness.run_tool(name, payload) 调用 Tool
  节点绝不直接调 Facade 或 Domain Service

Workflow Layer → Governance Layer
  ExecutionHarness 在每次 tool 调用前后自动发射 EventBus 事件
  节点开发者不需要手动调用任何治理逻辑
```

#### 工作流层优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **LangGraph Checkpoint 断点续传** | `app/workflows/query_orchestrator.py` | 每个 step 完成后自动保存 checkpoint，支持 resume/replay/recover 三种恢复模式 |
| **条件路由跳过** | `app/workflows/query_graph.py` `dispatch_query_step` | 路由器根据状态决定下一步：缓存命中跳过检索、护栏通过跳过 blocked 分支，减少不必要的 step 执行 |
| **LangGraph 节点自动包裹治理** | `app/harness/execution.py` `run_tool()` | 每个 Tool 调用自动走 7 步治理链，节点开发者无需手动调 trace/memory |
| **并发能力** | `app/workflows/query_graph.py` `build_query_graph()` | LangGraph 支持并行节点（如同时执行扩展查询和缓存检查），通过 `add_node` + `add_edge` 实现 |
| **Route 决策可解释** | `app/workflows/query_graph.py` 条件路由 | 每次条件路由的决策结果记录在 state 中，可追溯"为什么走了这个分支" |

---

### 4. Capability Layer（能力层）— 怎么工作的

**职责**：提供上层唯一稳定的能力调用面。所有可执行能力统一表现为 Tool。

#### 完整调用链：LangGraph 节点 → ExecutionHarness → ToolExecutor → Tool → Facade → Domain

以 `rag_retrieve_evidence` 为例，一次 Tool 调用的完整代码执行路径：

**第一段：LangGraph 节点发起调用**

```python
# LangGraph 节点函数内部
result = execution_harness.run_tool(
    'rag_retrieve_evidence',
    payload={'query': '...', 'collection_name': 'demo', 'top_k': 5},
    workflow_state=ws,
    context_bundle=ctx_bundle,
)
```

**第二段：ExecutionHarness.run_tool() —— 治理链入口**

[app/harness/execution.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/harness/execution.py) 第 138-225 行，按顺序执行：

```python
def run_tool(self, name, payload, workflow_state, context_bundle):
    # 1. 护栏检查：工具名是否在白名单？payload 是否包含注入攻击？
    tool_decision = self.guardrail_engine.validate_tool_call(name, payload, allowed_tools)
    
    # 2. 权限检查：当前用户是否有权调用此工具？
    policy_decision = self.policy_engine.check_tool(task_request, name, payload)
    
    # 3. 沙盒决策：高风险工具 → 进程隔离，低风险 → 进程内执行
    sandbox_decision = self.sandbox_engine.assess(tool_name=name, ...)
    
    # 4. 执行：timeout / retry / circuit_breaker
    outcome = self.tool_executor.execute(name, payload, ...)
    
    # 5. finally 块：记录结果到 TaskMemory + Trace
    self.hooks.record_execution(workflow_state, step_id, execution)
```

**第三段：ToolExecutor.execute() —— 执行保障**

- **timeout**：超时自动中断，抛 `ToolExecutionError(code='timeout')`
- **retry**：根据 `runtime_policy.max_attempts` 自动重试
- **circuit_breaker**：连续失败超过阈值后自动熔断，拒绝后续调用

**第四段：ToolRegistry.run() —— 查找 + 校验 + 执行**

```python
def run(self, name, payload, context):
    tool = self._tools[name]                              # 1. 查找工具实例
    validated_input = tool.input_model(**payload)          # 2. 校验输入 schema
    result = tool.run(validated_input, context)            # 3. 执行工具
    validated_output = tool.output_model.model_validate(result)  # 4. 校验输出 schema
    return validated_output
```

**第五段：Tool → Facade → Domain Service**

```python
# RagRetrieveEvidenceTool.run()
def run(self, payload, context):
    return context.rag.retrieve_evidence(...)
    #     ↑ RagFacade.retrieve_evidence()
    #         ↑ KnowledgeCapability.retrieve_evidence()
    #             ↑ RagRetrievalService.retrieve()
    #                 ↑ ChromaDB 向量搜索
```

#### 工具注册机制

启动时在 [app/container.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/container.py) 中一次性注册 (~40 个工具)，运行时按名称查找。每个 Tool 声明了：
- `input_model` / `output_model`：Pydantic schema，自动校验
- `risk_level`：low / medium / high，决定沙盒策略
- `sandbox_mode`：inline / process_isolated
- `retry_policy`：重试次数和间隔

#### 关键约束

**LangGraph 节点绝不直接调 Facade 或 Domain Service，必须通过 `ExecutionHarness.run_tool()`**。这保证了治理链（护栏 → 权限 → 沙盒 → 执行 → 兜底）不被绕过。

#### 能力层通信总结

```
Capability Layer ← Workflow Layer
  LangGraph 节点通过 ExecutionHarness.run_tool(name, payload) 调用

Capability Layer → Governance Layer
  ExecutionHarness 内部串行调用 Guardrail → Policy → Sandbox 引擎
  EventBus 自动发射 before_tool / after_tool / tool_failed 事件

Capability Layer → State Layer
  Tool 执行结果通过 ToolRegistry → TaskMemory 写入
  ToolContext 提供 state, retrieval, trace, task_memory 等依赖

Capability Layer → Infra Layer
  Tool 通过 ToolContext 访问 LLM, VectorStore, Settings
  Domain Service 通过 Capability 访问外部 API
```

#### 能力层优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **ToolExecutor 超时/重试/熔断** | `app/harness/components/tool_executor.py` | 超时自动中断；根据 `retry_policy` 自动重试；连续失败 N 次后熔断，拒绝后续调用 |
| **Tool 输入输出 Schema 校验** | `app/agents/tools/registry.py` `run()` | 执行前校验 payload 是否符合 `input_model`，执行后校验 result 是否符合 `output_model`，防止类型错误传播 |
| **Facade 模式解耦** | `app/rag/facade.py` 等 | 每个 Domain 域一个 Facade，域内实现可整体替换，上层代码不改 |
| **Tool 元数据驱动** | `app/agents/tools/base.py` `BaseTool` | 每个 Tool 声明 `risk_level`/`sandbox_mode`/`retry_policy`/`timeout_ms`，治理层自动适配 |
| **一次性注册** | `app/container.py` `__init__()` | ~40 个工具在启动时一次性注册到 `ToolRegistry`，运行时按名称 O(1) 查找，无动态注册开销 |
| **ToolContext 依赖注入** | `app/agents/tools/base.py` `ToolContext` | Tool 通过 `ToolContext` 访问 state/retrieval/trace/task_memory/llm，不直接依赖具体实现 |

---

### 5. Governance Layer（治理层）— 怎么工作的

**职责**：决定什么能做、做到什么程度、失败后怎么收口。治理层通过 **EventBus + Hook** 实现横向切面，通过 **串行治理引擎** 实现每次 Tool 调用的安全检查。

#### 治理层的两大机制

**机制一：EventBus + Hook 注册表（被动监听）**

`EventBus` 封装在 [app/harness/hooks.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/harness/hooks.py)，定义了 22 种事件类型：

| 事件类别 | 事件 | 触发时机 |
|---------|------|---------|
| Step 生命周期 | `BEFORE_STEP` / `AFTER_STEP` / `STEP_FAILED` | LangGraph 节点的 step 包裹函数 |
| Tool 生命周期 | `BEFORE_TOOL` / `AFTER_TOOL` / `TOOL_FAILED` | ExecutionHarness.run_tool() |
| ReAct 生命周期 | `BEFORE_REACT_TURN` / `AFTER_REACT_TURN` / `REACT_EXCEEDED_MAX_TURNS` | BoundedLocalReActRuntime |
| Run 生命周期 | `RUN_STARTED` / `RUN_COMPLETED` / `RUN_FAILED` | Orchestrator |
| Checkpoint | `BEFORE_CHECKPOINT` / `AFTER_CHECKPOINT` | Checkpoint 写入前后 |
| Recovery | `RECOVERY_INITIATED` / `RECOVERY_COMPLETED` / `RECOVERY_FAILED` | RecoveryManager |
| Context | `CONTEXT_BUILT` / `CONTEXT_TRIM` | ContextHarness |

**事件从发射到消费的完整路径：**

```
ExecutionHarness / Orchestrator 发射事件
  │
  event_bus.before_stage(ws, stage_name='retrieve_evidence')
  │   → EventBus.emit(HookEvent.BEFORE_STAGE, ws, stage_name='retrieve_evidence')
  │       → 构造 EventPayload(event='before_stage', workflow_state=ws, payload={...})
  │           → HookRegistry.emit(payload)
  │               │
  │               ├─ 通配符 handler 先跑:
  │               │   TraceHook.handle(payload)
  │               │     → trace.record('before_stage', {hook_event, payload, metadata})
  │               │
  │               └─ 特定事件 handler 后跑:
  │                   MemoryHook.handle(payload)
  │                     → TaskMemory.append_memory_record(MemoryRecord(...))
  │                     → 清理 working memory（在 AFTER_STAGE/STAGE_FAILED/AFTER_REACT_TURN 时）
```

**关键设计**：LangGraph 节点开发者只需写节点函数，事件发射和 Hook 消费全由框架自动完成。新增治理策略（如审计日志、计费扣减）只需注册新 Hook，不改任何节点代码。

**机制二：串行治理引擎（主动检查）**

每次 Tool 调用时，`ExecutionHarness.run_tool()` 内部按固定顺序串行执行 5 步检查：

```
步骤 1: EventBus.before_tool()          ← 最先发射，让审计 hook 记录完整耗时
步骤 2: GuardrailEngine.validate_tool_call()  ← 护栏优先：检查注入攻击、敏感信息
步骤 3: PolicyEngine.check_tool()       ← 权限检查：用户是否有权调用此工具
步骤 4: SandboxEngine.assess()          ← 沙盒决策：高风险 → 进程隔离
步骤 5: ToolExecutor.execute()          ← 执行：timeout/retry/circuit_breaker
         └─ 失败 → FallbackHandler.apply()  ← 兜底：降级/跳过
步骤 6: EventBus.after_tool()           ← 执行后发射，hook 可拿到执行结果
步骤 7: ExecutionHooks.record_execution() ← 最后写入 Trace/Memory
```

**为什么顺序固定？**

| 顺序 | 如果换顺序会怎样 |
|------|-----------------|
| Guardrail 在 Policy 之前 | 先检查权限 → 注入攻击请求通过了权限检查 |
| Policy 在 Sandbox 之前 | 先创建沙盒 → 为无权限用户创建了沙盒进程 |
| Sandbox 在 Execute 之前 | 先执行 → 高风险命令已在宿主机上跑了 |
| EventBus 在最前/最后 | 放在后面 → 审计漏掉 guardrail 阶段的耗时 |

**原则：先检查，再执行，最后记录。**

#### 三层沙盒体系

1. **ToolSandbox（工具级）**：高风险工具 → `process_isolated`（子进程隔离），低风险 → `inline`（进程内执行）。支持远程 sandbox worker 和本地熔断回退。
2. **ContextSandbox（上下文级）**：控制 sub-agent/tool 能看到的 state/evidence/artifact/memory 范围。
3. **CapabilitySandbox（能力级）**：按 capability 类型预设默认限制。

#### 治理层通信总结

```
Governance Layer → 所有层（横向切面，贯穿不穿透）
  EventBus 事件在 Workflow/Capability 执行时由框架自动发射
  Hook 监听事件 → 写入 Trace/Memory/Checkpoint
  治理引擎（Guardrail/Policy/Sandbox）在每次 Tool 调用时串行执行

Governance Layer ← Workflow Layer
  ExecutionHarness 在每次 tool 调用前后自动发射 EventBus 事件
  Orchestrator 通过 ExecutionHarness 间接触发治理链

Governance Layer ← Capability Layer
  ExecutionHarness 是治理链的入口，每次 Tool 调用都走完整 7 步治理链
```

#### 治理层优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **7 步治理链固化顺序** | `app/harness/execution.py` `run_tool()` | 先检查（Guardrail→Policy→Sandbox）再执行（ToolExecutor）最后记录（EventBus→Hooks），顺序不可变，保证安全检查不绕过 |
| **EventBus 通配符 + 特定事件双模式** | `app/harness/hooks.py` `HookRegistry` | 通配符 handler（TraceHook）监听所有事件，特定 handler（MemoryHook）只监听关注的事件，灵活组合 |
| **Fallback 三层兜底** | `app/harness/components/fallback_handler.py` | Tool 失败时：fallback（备选逻辑）→ degrade（降级返回）→ skip_with_gap（跳过并标记缺口），逐级降级 |
| **三层沙盒隔离** | `app/harness/sandbox.py` | ToolSandbox（工具级进程隔离）+ ContextSandbox（上下文裁剪）+ CapabilitySandbox（能力级约束），纵深防御 |
| **ModelRouter 按用途路由** | `app/harness/model_router.py` | 不同用途（chat/intent/plan/corrective/extraction）路由到不同模型，避免一个模型包打天下 |
| **22 种事件全覆盖** | `app/harness/hooks.py` `HookEvent` | 覆盖 Step/Tool/ReAct/Checkpoint/Run/Recovery/Context 全生命周期，无监控盲区 |

---

### 6. Infra Layer（基础设施层）— 怎么工作的

**职责**：提供 LLM Provider、向量库、SQLite 持久化、Sandbox Worker 等运行底座。上层通过接口访问，不依赖具体实现。

#### 组件清单

| 组件 | 实现 | 上层通过什么访问 |
|------|------|----------------|
| LLM Provider | OpenAI API（可替换） | `build_llm(settings)` 工厂 → `ModelRouter` 按用途路由 |
| Vector Store | ChromaDB | `ChromaClientFactory` → `RagRetrievalService` |
| 持久化 | SQLite | `SQLiteStateStore` → `TaskMemory` / `SessionManager` |
| 链路追踪 | TraceRecorder | `TraceHook` 自动写入 |
| 语义缓存 | SemanticCacheService | `RagQueryEngine` |
| 沙盒执行 | 子进程隔离 / 远程 HTTP | `ToolSandbox.execute_isolated()` |

#### 容器启动装配顺序

[app/container.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/container.py) 的 `AppContainer.__init__()` 严格按依赖顺序装配（第 106-304 行）：

```
1. Infra 基础:  Settings → InMemoryState → SQLiteStateStore → TraceRecorder → EventBus
                 → EventBus.register(TraceHook)  ← 最先注册，通配符监听所有事件

2. LLM + 向量库: build_llm() → ChromaClientFactory → ModelRouter

3. RAG 服务:    GraphService → RagRetrievalService → SemanticCacheService → RagIngestionService

4. Domain Capability: KnowledgeCapability → RagFacade
                      Repository / ApiContract / Artifact / Database
                      Weather / Finance / News / Currency / ...

5. Agent 平台:  ConfigStore → LlmRouter → CapabilityRegistry → IntentMatcher
               → PlanGenerator / PlanExecutor

6. 记忆系统:    TaskMemory → EventBus.register(MemoryHook)  ← 在 TaskMemory 之后注册

7. 治理组件:    GuardrailEngine → PolicyEngine → SandboxEngine

8. 编排器:      QueryWorkflowOrchestrator → TaskWorkflowOrchestrator

9. 业务服务:    QueryService / TaskService / CollectionService / DocumentService
```

#### 关键原则

**上层不依赖具体实现**：`RagRetrievalService` 依赖的是 `ChromaClientFactory` 接口，不是 ChromaDB 具体 API。换 Milvus 只需改 Factory，上层代码不变。

**双写模式**：`InMemoryState`（热缓存）+ `SQLiteStateStore`（持久化）。启动时 `persistence.load_into(state)` 恢复。

#### 基础设施层通信总结

```
Infra Layer → 所有上层
  通过 Container 依赖注入，上层通过接口访问
  LLM → 注入到 Orchestrator / ExecutionHarness
  VectorStore → 注入到 RagRetrievalService
  SQLite → 注入到 TaskMemory / SessionManager

Infra Layer ← 上层
  不直接回调上层，通过返回值和异常传递结果
```

#### 基础设施层优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **Container 依赖注入** | `app/container.py` `AppContainer` | 按依赖顺序装配（Infra→RAG→Capability→Agent→Governance→Orchestrator），单例复用，避免重复创建 |
| **LLM Provider 可替换** | `app/rag/llamaindex_components.py` `build_llm()` | 通过 Settings 切换 Provider/Model，不改代码 |
| **VectorStore 可替换** | `app/rag/vector_store.py` `ChromaClientFactory` | Factory 模式封装，换 Milvus 只需改 Factory |
| **SQLite 双写模式** | `app/services/sqlite_store.py` + `app/services/state.py` | InMemoryState（热缓存）+ SQLite（持久化），兼顾性能与可靠性 |
| **TraceRecorder 全链路追踪** | `app/rag/observability.py` | 每次 Tool 调用、Stage 执行、检索结果都有 trace 记录，可出 RAGAS 评测报告 |
| **后台 Worker 并行处理** | `app/services/task_dispatcher.py` | 任务队列 + Worker 租约机制，支持多 Worker 并行消费，超时自动回收 |
| **ConfigStore 统一配置** | `app/services/config_store.py` | LLM/Skill/Agent/Prompt/MCP 配置统一存储在 SQLite 中，支持运行时热更新 |

---

## 🤖 ReAct 工作原理详解

> ReAct（Reasoning + Acting）是 Agent 在**单个 step 内**进行局部推理和动作选择的机制。在本项目中，ReAct 是**有界、局部的**——只在单 step 内运行有限轮循环（默认 1 轮，上限 8 轮），不接管系统级治理。

### ReAct 在整体架构中的位置

```
Workflow Layer
  │
  Stage（如 RetrieveEvidenceStage）
    │
    ├─ 简单场景：直接调用 ExecutionHarness.run_tool('rag_retrieve_evidence', ...)
    │              └─ 单次 Tool 调用，走完整治理链
    │
    └─ 复杂场景：需要多步推理（如"先检索→不够→再查图谱→审阅"）
        │
        ▼
    BoundedLocalReActRuntime（局部 ReAct 运行时）
        │
        ├─ initialize(step, context) → 创建 ReActState
        ├─ next_action(state, context) → 决策下一步做什么
        ├─ 执行选中的动作 → ExecutionHarness.run_tool(action, ...)
        └─ observe(state, action, observation) → 记录这一轮
            (循环最多 max_turns 轮)
```

### 代码执行路径（逐行追踪）

`BoundedLocalReActRuntime` 位于 [app/harness/react_runtime.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/harness/react_runtime.py)。

**第一步：初始化**

```python
def initialize(self, step: StepSpec, context: ContextBundle) -> ReActState:
    return ReActState(
        step_id=step.step_id,
        objective=step.objective,           # 当前 step 的目标
        allowed_tools=step.allowed_tools,   # 白名单：哪些工具可用
        max_turns=step.max_turns,           # 默认 1，上限 8
    )
```

**第二步：决策——next_action()**

```python
def next_action(self, state: ReActState, context: ContextBundle) -> str | None:
    # 终止条件
    if state.stop_reason is not None or len(state.turns) >= state.max_turns:
        return None                         # 停止

    allowed_tools = state.allowed_tools or list(context.tool_options)
    if not allowed_tools:
        return None                         # 没有可用工具，停止

    # 启发式决策
    if len(allowed_tools) == 1:
        return allowed_tools[0]             # 只有一个工具，直接用

    if context.memory_slice.get('missing_aspects') and 'retrieve_graph_evidence' in allowed_tools:
        return 'retrieve_graph_evidence'    # 证据缺口 → 走图谱检索

    if context.evidence_slice and 'review_report' in allowed_tools:
        return 'review_report'              # 有证据 → 走审阅

    return allowed_tools[0]                 # 默认：第一个工具
```

**第三步：观察——observe()**

```python
def observe(self, state, *, action, observation, success, stop_reason):
    turn = ReActTurn(
        turn_index=len(state.turns),
        action=action,
        reason=self._reason_for_action(state, action),  # 可解释性
        observation=observation or {},
    )
    state.turns.append(turn)
    if success:
        state.stop_reason = 'success_criteria_satisfied'
    elif len(state.turns) >= state.max_turns:
        state.stop_reason = 'max_turns_reached'
    return state
```

### ReAct 内部的通信模式

ReAct 运行时内部通过 **EventBus** 与治理层/状态层通信，通过 **ExecutionHarness** 与能力层通信：

```
ReAct 循环的每一轮：

1. next_action() 决策
     │
2. EventBus.before_react_turn(ws, step_id, turn_index, action)
     │  → TraceHook 记录: trace.record('before_react_turn', {...})
     │  → MemoryHook 写入: TaskMemory.append_memory_record(working)
     │
3. ExecutionHarness.run_tool(action, payload, ws, ctx_bundle)
     │  → 走完整 7 步治理链（护栏→权限→沙盒→执行→兜底→记录）
     │  → 返回 Tool 执行结果
     │
4. EventBus.after_react_turn(ws, step_id, turn_index, action, observation, success)
     │  → TraceHook 记录 trace
     │  → MemoryHook 写入 run memory + 清理 working memory
     │
5. observe() 记录这一轮 → 判断是否继续
     │
     ├─ success → 停止
     ├─ max_turns 耗尽 → EventBus.react_exceeded_max_turns()
     │    → 审计 Hook 记录异常
     └─ 否则 → 回到步骤 1
```

### ReAct 的关键约束

1. **有界性**：`max_turns` 默认 1，上限 8。防止无限循环
2. **局部性**：只在单 step 内运行，不跨 step
3. **可解释性**：每轮记录 `reason`（为什么选这个动作）
4. **不绕过治理**：ReAct 内部调用 Tool 仍然走 `ExecutionHarness.run_tool()` 完整治理链
5. **可观测性**：通过 EventBus 事件全程可追踪

#### ReAct 优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **有界 max_turns** | `app/harness/react_runtime.py` `ReActState.max_turns` | 默认 1 轮，上限 8 轮，防止无限循环消耗 token 和时间 |
| **启发式动作选择** | `app/harness/react_runtime.py` `next_action()` | 按优先级决策：单工具直接用 → 证据缺口走图谱 → 有证据走审阅 → 默认第一个工具，避免 LLM 调用开销 |
| **ReActTurn 可解释记录** | `app/harness/react_runtime.py` `ReActTurn` | 每轮记录 `action` + `reason` + `observation`，事后可追溯"为什么选了这个动作" |
| **EventBus 三事件监控** | `app/harness/react_runtime.py` → `BEFORE_REACT_TURN` / `AFTER_REACT_TURN` / `REACT_EXCEEDED_MAX_TURNS` | 每轮开始/结束发射事件，超限发射告警事件，Hook 可做限流/审计 |
| **不绕过治理链** | `app/harness/react_runtime.py` 内部调 `ExecutionHarness.run_tool()` | ReAct 每次选动作后仍走完整 7 步治理链，护栏/权限/沙盒一个不落 |
| **stop_reason 终态标记** | `app/harness/react_runtime.py` `ReActState.stop_reason` | 明确区分停止原因（success / max_turns_reached / no_allowed_tools），便于监控和调试 |

---

## 📚 RAG 工作原理详解

> RAG（Retrieval-Augmented Generation）是本平台最核心的能力之一。从用户问题到最终答案，RAG 经过一条完整的 **8 步处理管道**，每一步都由 LangGraph 图的一个节点执行。

### RAG 在整体架构中的位置

RAG 是 Workflow Layer 中 Query LangGraph 图定义的一系列节点：

```
用户问题
  │
  ▼
Entry Layer → TaskSpec → Workflow Layer
  │
  Query LangGraph 节点:
  ├─ 1. check_guardrails       ← 安全检查（不属于 RAG 管道）
  ├─ 2. rewrite_query          ← 查询改写
  ├─ 3. expand_queries         ← 查询扩展（Multi-Query / HyDE）
  ├─ 4. lookup_cache           ← 语义缓存检查
  ├─ 5. retrieve_evidence      ← 证据检索（RAG 核心）
  ├─ 6. compress_context       ← 上下文压缩
  ├─ 7. grounded_answer        ← 基于证据生成回答
  ├─ 8. self_reflect           ← 反思评估（Corrective RAG）
  └─ 9. finalize               ← 收尾
```

### 代码执行路径：逐节点追踪

**节点 1: 查询改写（rewrite_query）**

输入：原始用户问题（如 "请问session怎么用呢"）
处理：规则改写（去语气词、同义词替换、领域词扩展、去重）
输出：标准化查询（如 "session 使用"）

**节点 2: 查询扩展（expand_queries）**

可选策略：Multi-Query（一个查询变多个角度）、HyDE（生成假设文档再检索）、Multi-Rewrite（多个改写候选）

**节点 3: 语义缓存检查（lookup_cache）**

检查相似问题是否已缓存：相似度阈值 0.94 → 命中直接返回，TTL 86400 秒 → 过期自动淘汰

**节点 4: 证据检索（retrieve_evidence）—— RAG 核心**

这是 RAG 管道中最重要的节点。节点内部通过 `ExecutionHarness.run_tool('rag_retrieve_evidence', ...)` 调用，走完整治理链后到达 `RagRetrievalService.retrieve()`。

**检索内部的多路召回与融合流程：**

```
RagRetrievalService.retrieve(question, collection_name, top_k, ...)
  │
  ├─ 稠密召回 (Dense): question → Embedding → ChromaDB 向量相似度搜索
  │   适用：语义相似但关键词不同
  │
  ├─ 词法召回 (Lexical): question → 关键词提取 → BM25 搜索
  │   适用：精确匹配关键词（可选，use_hybrid_retrieval=true）
  │
  ├─ 图谱召回 (Graph): question → 实体识别 → 图谱关系遍历
  │   适用：实体间关系很重要（可选，use_graph_rag=true）
  │
  ├─ RRF 融合: score = Σ 1/(k + rank)，k=60
  │   将三路召回结果按倒数排名融合
  │
  ├─ 目标聚合: 同一 chunk_id 的多个命中 → 合并为一条
  │   多路命中（content + query_hint + title_summary）→ 加分奖励
  │
  ├─ Cross-Encoder 重排: 用 cross-encoder 模型对候选精排
  │
  ├─ 长上下文重组: 高分结果放首尾，低分放中间
  │   缓解 LLM "Lost in the Middle" 问题
  │
  ├─ 父块回填 (Small-to-Big): 从子块查找父块，替换为完整上下文
  │
  └─ 最终 top_k 引用列表 → 返回 CitationItem[]
```

**节点 5: 上下文压缩（compress_context）**

超 token 预算时压缩或裁剪检索结果。

**节点 6: Grounded Answer（grounded_answer）**

通过 `ExecutionHarness.run_tool('rag_grounded_answer', ...)` → `RagFacade.grounded_answer()` → `KnowledgeCapability.grounded_answer()` → LLM 基于证据生成回答并标注引用。

**节点 7: 反思评估（self_reflect）—— Corrective RAG**

```
检查答案是否有据可依:
  ├─ 答案有据 → 通过，进入 finalize
  ├─ 答案无据 → retry_retrieve → 回到节点 4 重新检索
  └─ 答案部分有据 → rewrite_answer → 改写答案
```

**节点 8: 收尾（finalize）**

持久化 QueryRun，返回 `QueryResponse(answer, citations, ...)`。

### RAG 内部的通信模式

RAG 管道中的每个节点通过以下方式与其他组件通信：

```
RAG 节点（如 retrieve_evidence）
  │
  ├─ 与 Workflow Layer 通信:
  │   节点函数返回 state_update → LangGraph 合并状态
  │   dispatch_query_step 条件路由 → 决定下一个节点
  │
  ├─ 与 Governance Layer 通信:
  │   ExecutionHarness.run_tool() 内部走 7 步治理链
  │
  ├─ 与 Capability Layer 通信:
  │   ExecutionHarness.run_tool('rag_retrieve_evidence', payload, ws, ctx_bundle)
  │     → RagFacade.retrieve_evidence()
  │       → KnowledgeCapability.retrieve_evidence()
  │         → RagRetrievalService.retrieve()
  │
  ├─ 与 State Layer 通信:
  │   ContextHarness.build_context() → ContextBundle（step 开始前）
  │   ExecutionHarness 在 finally 块中写入 ToolExecutionResult → TaskMemory
  │
  └─ 与 Infra Layer 通信:
      RagRetrievalService → ChromaDB（向量搜索）
      KnowledgeCapability → LLM（生成回答）
      TraceRecorder（全程追踪）
```

### RAG 的 Facade 模式

`RagFacade`（[app/rag/facade.py](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/app/rag/facade.py)）是 RAG 域对外的**唯一稳定门面**：

```python
class RagFacade:
    def retrieve_evidence(self, request, trace_context=None):
        return self.knowledge.retrieve_evidence(request, trace_context=trace_context)

    def grounded_answer(self, request, trace_context=None):
        return self.knowledge.grounded_answer(request, trace_context=trace_context)
```

**关键约束**：RAG 域内实现（RagRetrievalService、KnowledgeCapability）可以整体替换，只要 `RagFacade` 接口不变，上层代码不需要任何改动。节点绝不直接调 `RagFacade`，必须通过 `ExecutionHarness.run_tool()` 走完整治理链。

#### RAG 优化策略

**管道级优化：**

| 优化 | 位置 | 效果 |
|------|------|------|
| **查询改写去噪** | `app/rag/query_engine_parts/` `QueryPreprocessService` | 规则改写（去语气词、同义词替换、领域词扩展、去重），提高检索命中率 |
| **查询扩展 (Multi-Query / HyDE)** | `app/rag/query_engine.py` `expand_queries` | 一个查询变多个角度，或生成假设文档再检索，扩大召回覆盖面 |
| **语义缓存** | `app/services/semantic_cache.py` | 相似度 ≥0.94 命中缓存直接返回，TTL 86400s，大幅降低重复查询成本和延迟 |
| **Corrective RAG 反思** | `app/rag/query_engine.py` `self_reflect` | 生成答案后检查是否有据可依：无据 → 重试检索，部分有据 → 改写答案，提高答案准确性 |
| **会话自动摘要** | `app/rag/query_engine.py` `AUTO_SUMMARY_TRIGGER=8` | 会话消息超过 8 条自动生成摘要，保留最近 4 条原文，防止上下文爆炸 |

**检索级优化：**

| 优化 | 位置 | 效果 |
|------|------|------|
| **多路召回 (Dense + Lexical + Graph)** | `app/rag/retrieval.py` `retrieve()` | 稠密召回（语义相似）+ 词法召回（关键词匹配）+ 图谱召回（实体关系），三路互补 |
| **RRF 融合** | `app/rag/retrieval.py` `retrieve_multi()` | `score = Σ 1/(60 + rank)`，将三路召回按倒数排名融合，单路强结果不会被稀释 |
| **目标聚合** | `app/rag/retrieval.py` 内部 | 同 chunk_id 的多个命中合并为一条，多路命中（content+query_hint+title）加分奖励 |
| **Cross-Encoder 重排** | `app/rag/retrieval.py` `use_rerank=True` | 用 Cross-Encoder 模型对候选精排，比向量相似度更准确 |
| **长上下文重组（首尾夹心）** | `app/rag/retrieval.py` `use_long_context_reorder=True` | 高分结果放首尾，低分放中间，缓解 LLM "Lost in the Middle" 问题 |
| **父块回填（Small-to-Big）** | `app/rag/retrieval.py` `use_parent_chunk_retrieval=True` | 从子块查找父块，用完整上下文替换子块文本，提供更丰富的上下文 |
| **问题导向索引** | `app/rag/retrieval.py` `use_question_oriented_index=True` | 用问题向量检索问题索引，再映射到答案块，提高问答场景的检索精度 |
| **GraphRAG 实体关系遍历** | `app/rag/retrieval.py` `use_graph_rag=True` | 实体识别 → 图谱关系遍历（max_hops），按实体类型过滤，补充结构化知识 |

**Facade 级优化：**

| 优化 | 位置 | 效果 |
|------|------|------|
| **Facade 单入口** | `app/rag/facade.py` `RagFacade` | RAG 域对外的唯一门面，域内实现可整体替换，上层代码零改动 |
| **Trace 上下文传递** | `app/rag/facade.py` `trace_context` 参数 | 每次调用携带 trace 上下文，全链路可追踪到具体检索/生成步骤 |

---

## 🧩 核心子系统详解

> 以下子系统是六层架构之外的重要横向模块，各自独立运作，通过统一的 Harness 治理链与各层通信。

---

### 1. Agent Platform — Mode + Intent + Plan 系统

**一句话**：把用户自然语言输入识别为 Capability，按 Mode 决定执行流程，路由到对应执行器。

#### 内部工作流程

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    Agent Platform 执行流程                            │
│                                                                     │
│  用户输入 (AgentChatRequest)                                         │
│      │                                                              │
│      ▼                                                              │
│  1. 获取/创建 Session (session_manager)                              │
│     │  session.mode = request.mode  (chat / plan / autopilot)        │
│     ▼                                                              │
│  2. MCP 工具连接 (mcp_manager.connect)  ← 可选                       │
│     │  yield AgentEvent.tool_call("mcp:server:tool")                │
│     ▼                                                              │
│  3. 意图识别 (intent_matcher)                                        │
│     │  ┌────────────────────────────────────────────────┐          │
│     │  │ 第一层：关键词匹配 (KEYWORD_RULES)                │          │
│     │  │  "分析文档" → document_analysis (0.75)          │          │
│     │  │  "代码审查" → code_review (0.85)               │          │
│     │  │  "搜索"     → web_search (0.80)                │          │
│     │  │  命中 → IntentMatch(capability, confidence)     │          │
│     │  ├────────────────────────────────────────────────┤          │
│     │  │ 第二层：LLM 分类 (关键词未命中时)                │          │
│     │  │  LLM 从已启用的 capabilities 中选择最匹配的      │          │
│     │  │  命中 → IntentMatch(capability, 0.6)            │          │
│     │  ├────────────────────────────────────────────────┤          │
│     │  │ 兜底：chat (0.5)                                │          │
│     │  └────────────────────────────────────────────────┘          │
│     │  yield AgentEvent.intent(capability, confidence)             │
│     ▼                                                              │
│  4. 按 Mode 分支执行                                                │
│     │                                                              │
│     ├─ mode=chat ──────────────────────────────────────────┐       │
│     │  _handle_chat_mode()                                  │       │
│     │  → 直接路由到 Capability Provider                     │       │
│     │  → 返回 SSE 事件流                                    │       │
│     │                                                       │       │
│     ├─ mode=plan ──────────────────────────────────────────┐       │
│     │  _handle_plan_mode()                                  │       │
│     │  → PlanGenerator.generate() 生成计划                  │       │
│     │  → yield AgentEvent(type="plan", steps=[...])         │       │
│     │  → 存入 session.context["current_plan"]               │       │
│     │  → 等待客户端 POST /agent/plan/confirm 确认            │       │
│     │                                                       │       │
│     └─ mode=autopilot ─────────────────────────────────────┐       │
│        _handle_autopilot_mode()                             │       │
│        → PlanGenerator.generate() 生成计划                  │       │
│        → PlanExecutor.execute() 自动执行                    │       │
│        → yield AgentEvent.ask_user("还需要我做什么？")      │       │
│                                                             │       │
│  5. 保存会话 → yield AgentEvent.completed()                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### 通信方式

```
AgentService
  ├── intent_matcher.match(message, history) → IntentMatch
  │     └── 通信协议：Python 函数调用，同步返回
  ├── plan_generator.generate(message, capability, context) → Plan
  │     └── 通信协议：Python async 函数调用
  ├── plan_executor.execute(plan, capability, context) → AsyncIterator[AgentEvent]
  │     └── 通信协议：Python async generator (SSE 事件流)
  ├── session_manager.get_or_create() / save()
  │     └── 通信协议：Python async 函数调用
  └── mcp_manager.connect(config) → list[ToolDef]
        └── 通信协议：Python async 函数调用
```

#### 优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **关键词匹配优先** | `app/services/intent_matcher.py` `KEYWORD_RULES` | 6 组关键词规则，命中率 80%+ 的请求不走 LLM，零延迟 |
| **防误匹配规则** | `app/services/intent_matcher.py` 第 79-82 行 | 排除"分析文档"与"代码审查"的交叉误匹配（如"分析代码"不匹配 document_analysis） |
| **LLM 分类兜底** | `app/services/intent_matcher.py` `_llm_match()` | 关键词未命中时调用 LLM，只返回已启用的 capability 名称，防止幻觉 |
| **Plan 存入 Session 等待确认** | `app/services/agent_service.py` 第 224-231 行 | Plan 模式不自动执行，存入 session.context 等待用户确认，避免错误操作 |
| **Capability 路由优先级** | `app/services/agent_service.py` `_route_to_capability()` | Provider → Workflow → 兜底 chat，按优先级查找，匹配到即停止 |
| **SSE 事件流统一** | `app/services/agent_service.py` `process()` | 所有模式输出统一为 `AsyncIterator[AgentEvent]`，客户端只需处理一种事件格式 |

---

### 2. SubAgent 系统

**一句话**：为复杂任务提供 4 个受控子代理，每个代理有严格的白名单约束，通过 Handoff 机制交接。

#### 架构

```text
┌─────────────────────────────────────────────────────────────────────┐
│                       SubAgent 系统架构                              │
│                                                                     │
│  Task Workflow (Stage)                                              │
│      │                                                              │
│      │ SubAgentHandoff(step_limit, budget_limit, sandbox_profile)    │
│      ▼                                                              │
│  SubAgentRuntime.execute(agent_name, action, payload)               │
│      │                                                              │
│      ├─ EvidenceAgent (证据收集代理)                                  │
│      │   ├─ 动作: collect_evidence / supplement_evidence             │
│      │   ├─ 白名单: rag_retrieve_evidence, rag_grounded_answer       │
│      │   └─ 产出: EvidencePack (证据包)                              │
│      │                                                              │
│      ├─ ReportingAgent (报告生成代理)                                 │
│      │   ├─ 动作: draft_artifact                                    │
│      │   ├─ 白名单: rag_retrieve_evidence, rag_grounded_answer       │
│      │   └─ 产出: ReportArtifactContent (报告内容)                    │
│      │                                                              │
│      ├─ ReviewAgent (审查代理)                                       │
│      │   ├─ 动作: review_draft / revise_draft                       │
│      │   ├─ 白名单: rag_retrieve_evidence, repo_search               │
│      │   └─ 产出: ReviewResult (审查结果) + decision (finalize/revise)│
│      │                                                              │
│      └─ ContractAgent (契约发现代理)                                  │
│          ├─ 动作: discover_contracts / inspect_contract              │
│          ├─ 白名单: api_list_contracts, api_read_contract             │
│          └─ 产出: ApiContractDocument[] (API 契约文档)               │
│                                                                     │
│  每个子代理执行时：                                                   │
│    1. _ensure_allowed() 校验工具白名单                               │
│    2. _run_tool() 在白名单通过后执行工具                              │
│    3. memory.record_sub_agent_run() 记录执行摘要到 TaskMemory         │
│    4. trace.record() 记录链路追踪                                    │
└─────────────────────────────────────────────────────────────────────┘
```

#### 通信方式

```
SubAgentRuntime
  ├── SubAgentRegistry → 按名称查找 RegisteredSubAgent
  │     └── 协议：Python 接口（Protocol），返回 RegisteredSubAgent
  ├── sub_agent.execute(action, payload) → BaseModel
  │     └── 协议：Pydantic BaseModel 输入/输出
  └── sub_agent → TaskMemory → SQLiteStateStore
        └── 协议：EventBus 写入（MemoryHook）
```

#### 优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **工具白名单约束** | `app/agents/subagents.py` `_ensure_allowed()` | 每个子代理只能调用白名单内的工具，越权调用直接抛 RuntimeError |
| **Handoff 契约** | `app/agents/subagents.py` `SubAgentHandoff` | 明确定义 step_limit(≤8) / budget_limit(≤32) / sandbox_profile，防止失控 |
| **静态 Schema 声明** | `app/agents/subagents.py` `SubAgentSchema` | 每个子代理公开能力描述，LLM 根据 Schema 选择子代理，不依赖内部实现 |
| **执行摘要记录** | `app/agents/subagents.py` `ControlledSubAgent._run_tool()` | 每次执行后记录 selected_tools 到 TaskMemory，可追溯"用了哪些工具" |
| **Trace 字段约束** | `app/agents/subagents.py` `trace_fields` | 只记录关键字段（task_id, agent_name, action, allowed_tools, selected_tools），不记录敏感数据 |
| **子代理复用** | `app/container.py` 单例注册 | 4 个子代理在容器启动时创建一次，任务间复用 |

---

### 3. Task Worker 后台任务系统

**一句话**：将"创建任务"与"执行任务"解耦，支持同步执行（测试）和后台 Worker 队列消费（生产）两种模式。

#### 内部工作流程

```text
┌─────────────────────────────────────────────────────────────────────┐
│                    Task Worker 系统架构                               │
│                                                                     │
│  TaskService.create_task()                                          │
│      │                                                              │
│      ▼                                                              │
│  TaskDispatcher.submit(task)                                        │
│      │                                                              │
│      ├─ InlineTaskDispatcher (测试模式)                               │
│      │   → 当前线程直接 runtime.run(task_id)                         │
│      │                                                              │
│      └─ PersistentTaskDispatcher (生产模式)                           │
│          → 任务置为 queued 状态                                       │
│          → wake_callback() 唤醒 worker                               │
│                                                                     │
│  TaskWorker (后台线程)                                                │
│      │                                                              │
│      │  _run_loop(worker_id):                                       │
│      │  ┌─────────────────────────────────────────────────┐        │
│      │  │ while not stop:                                  │        │
│      │  │   1. claim_next_task(worker_id, lease_seconds)   │        │
│      │  │      │  原子领取：先到先得，加租约锁               │        │
│      │  │      │  无任务 → wait(poll_interval) + 继续循环   │        │
│      │  │      ▼                                           │        │
│      │  │   2. touch_task_heartbeat(task_id, lease)        │        │
│      │  │      │  执行前续租，防止执行超时被误判为失联      │        │
│      │  │      ▼                                           │        │
│      │  │   3. runtime.run(task_id)                        │        │
│      │  │      │  执行任务，异常被 catch 不中断 worker       │        │
│      │  │      ▼                                           │        │
│      │  │   4. 循环回到步骤 1                               │        │
│      │  │   stop_event 触发 → 退出循环                      │        │
│      │  └─────────────────────────────────────────────────┘        │
│      │                                                              │
│      │  max_workers 控制并发线程数                                    │
│      │  poll_interval_seconds 控制轮询间隔                           │
│      │  lease_seconds 控制任务租约时长                               │
│      │  wake_event 用于即时唤醒                                    │
└─────────────────────────────────────────────────────────────────────┘
```

#### 通信方式

```
TaskService
  └── TaskDispatcher.submit(task)
        ├── InlineTaskDispatcher  → AgentRuntime.run() [同步]
        └── PersistentTaskDispatcher → wake_callback() → TaskWorker

TaskWorker
  └── TaskMemory.claim_next_task(worker_id, lease) → TaskDetail | None
        └── 协议：Python 函数调用，通过 SQLite 实现原子领取
```

#### 优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **PersistentTaskDispatcher 与 TaskWorker 解耦** | `app/services/task_dispatcher.py` | 提交和消费分离，Dispatcher 只管排队，Worker 只管消费，各自独立伸缩 |
| **租约机制** | `app/services/task_dispatcher.py` `claim_next_task` + `lease_seconds` | 任务被 Worker 认领后加租约锁，超时自动释放，防止 Worker 崩溃导致任务丢失 |
| **执行前续租** | `app/services/task_dispatcher.py` 第 172 行 `touch_task_heartbeat` | 认领后立即续租一次，降低"认领-执行"间隙的误抢占概率 |
| **多 Worker 并行** | `app/services/task_dispatcher.py` `max_workers` | 一个 Worker 实例可启动多个线程并行消费，提高吞吐 |
| **异常隔离** | `app/services/task_dispatcher.py` 第 174 行 `except Exception` | 单任务执行失败不中断 Worker 线程，继续处理下一个任务 |
| **唤醒机制** | `app/services/task_dispatcher.py` `wake` / `_wake_event` | 新任务提交后立即唤醒等待中的 Worker，减少轮询延迟 |
| **双模式切换** | `app/services/task_dispatcher.py` InlineTaskDispatcher | 测试环境用 Inline（同步执行），生产环境用 PersistentTaskDispatcher，零配置切换 |

---

### 4. Document Analysis 文档分析工作流

**一句话**：为文档深度分析任务提供的完整 LangGraph 工作流，包含 12 个节点和条件路由。

#### 工作流结构

```text
┌─────────────────────────────────────────────────────────────────────┐
│              Document Analysis LangGraph 工作流                       │
│                                                                     │
│  load_task ──────────────────────────────────────────────┐          │
│      │  加载任务详情                                        │          │
│      ▼                                                    │          │
│  plan_task ───────────────────────────────────────────────┤          │
│      │  TaskPlanner 生成执行计划                            │          │
│      ▼                                                    │          │
│  dispatch_plan_step ──────────────────────────────────────┤          │
│      │  根据计划步骤分发到对应节点                           │          │
│      │                                                    │          │
│      ├─→ collect_document_context                         │          │
│      │     ContextHarness 构建文档上下文                    │          │
│      │     → 回到 dispatch_plan_step                       │          │
│      │                                                    │          │
│      ├─→ retrieve_evidence                                │          │
│      │     ExecutionHarness.run_tool('rag_retrieve_evidence')│       │
│      │     → 回到 dispatch_plan_step                       │          │
│      │                                                    │          │
│      ├─→ handle_evidence_gap                              │          │
│      │     证据不足时补证据 (SubAgent)                       │          │
│      │     → 回到 dispatch_plan_step                       │          │
│      │                                                    │          │
│      ├─→ analyze                                          │          │
│      │     分析证据 + 提取关键点/风险                       │          │
│      │     → 回到 dispatch_plan_step                       │          │
│      │                                                    │          │
│      ├─→ draft_artifact                                   │          │
│      │     ReportingAgent 生成草稿                          │          │
│      │     → review_artifact                               │          │
│      │                                                    │          │
│      ├─→ review_artifact                                  │          │
│      │     ReviewAgent 审查草稿                             │          │
│      │     ├─ decision=finalize → finalize                 │          │
│      │     └─ decision=revise → revise_artifact            │          │
│      │                                                    │          │
│      ├─→ revise_artifact                                  │          │
│      │     ReviewAgent 修订草稿                             │          │
│      │     → review_artifact (再次审查)                     │          │
│      │                                                    │          │
│      ├─→ evaluate_exit_criteria                           │          │
│      │     EvaluationHarness 评估退出条件                   │          │
│      │     → 回到 dispatch_plan_step 或 finalize           │          │
│      │                                                    │          │
│      └─→ finalize ────────────────────────────────────────┘          │
│             最终交付                                                │
│                                                                     │
│  所有节点通过 _wrap_task_node() 包裹：                               │
│    - 成功：创建 checkpoint (checkpoint_step_id)                      │
│    - 失败：on_node_error 上报异常，写回运行态                        │
└─────────────────────────────────────────────────────────────────────┘
```

#### 通信方式

```
DocumentAnalysisGraph
  ├── LangGraph StateGraph 编排
  │     └── 协议：LangGraph StateGraph
  ├── ExecutionHarness.run_tool(name, payload) → ToolExecutionResult
  │     └── 协议：7 步治理链
  ├── ContextHarness.build(step, context) → ContextBundle
  │     └── 协议：选择 → 压缩 → 预算
  ├── SubAgentRuntime.execute(agent, action, payload) → BaseModel
  │     └── 协议：Pydantic I/O
  └── EvaluationHarness.evaluate(step, context) → EvaluationResult
        └── 协议：Python 函数调用
```

#### 优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **12 个节点 + 条件路由** | `app/workflows/tasks/document_analysis_graph.py` | 根据计划步骤和审查结果动态路由，不执行不需要的节点 |
| **_wrap_task_node 统一包裹** | `app/workflows/tasks/document_analysis_graph.py` 第 54-80 行 | 每个节点自动获得 checkpoint + 异常处理能力，无需节点开发者关心 |
| **Checkpoint 断点续传** | `app/workflows/tasks/document_analysis_graph.py` `checkpoint_step_id` | 每个节点完成后自动创建 checkpoint，支持暂停/恢复/重放 |
| **审查-修订循环** | `app/workflows/tasks/document_analysis_graph.py` review → revise → review | 审查不满意自动修订再审查，最多循环直到通过或超限 |
| **退出条件评估** | `app/workflows/tasks/document_analysis_graph.py` `evaluate_exit_criteria` | 独立节点评估是否满足退出条件，避免过早交付或无限循环 |

---

### 5. Semantic Cache 语义缓存

**一句话**：对相似度 ≥0.94 的重复查询直接返回缓存结果，避免重复检索和 LLM 调用。

#### 内部工作流程

```text
┌─────────────────────────────────────────────────────────────────────┐
│                   Semantic Cache 工作流程                             │
│                                                                     │
│  查询请求到达                                                        │
│      │                                                              │
│      ▼                                                              │
│  lookup_cache(collection_name, question)                             │
│      │                                                              │
│      │  1. 计算问题向量 (embed_model)                                 │
│      │  2. 在 collection 的向量缓存中 KNN 搜索                        │
│      │  3. 找到最高相似度候选                                        │
│      │                                                              │
│      ├─ 相似度 ≥ 0.94 + 未过期 (TTL 86400s)                          │
│      │   → 返回缓存结果 (QueryResponse)                              │
│      │   → trace.record('cache_hit')                                │
│      │   → 跳过检索和 LLM 生成                                       │
│      │                                                              │
│      └─ 相似度 < 0.94 或已过期                                       │
│          → 返回 None (缓存未命中)                                    │
│          → 继续正常检索流程                                          │
│                                                                     │
│  store_cache(collection_name, question, result)                      │
│      │  检索完成后存入缓存                                           │
│      │  持久化到 SQLite (persistence)                                │
│      │  trace.record('cache_store')                                 │
└─────────────────────────────────────────────────────────────────────┘
```

#### 通信方式

```
SemanticCacheService
  ├── embed_model → 向量化查询文本
  │     └── 协议：Python 方法调用
  ├── InMemoryState → 热缓存（向量索引）
  │     └── 协议：Python dict 操作
  └── SQLiteStateStore → 持久化缓存
        └── 协议：sqlite3 操作
```

#### 优化策略

| 优化 | 位置 | 效果 |
|------|------|------|
| **高阈值 0.94** | `app/services/semantic_cache.py` | 只有几乎相同的查询才命中，防止语义相近但意图不同的查询被误缓存 |
| **TTL 86400s (24h)** | `app/services/semantic_cache.py` | 过期自动淘汰，防止缓存污染 |
| **按 Collection 隔离** | `app/services/semantic_cache.py` `collection_name` 参数 | 不同知识库的缓存互不干扰，跨知识库的高相似度查询不会误命中 |
| **双写缓存** | `app/services/semantic_cache.py` → InMemoryState + SQLite | 内存热缓存（KNN 搜索快）+ SQLite 持久化（重启不丢） |
| **Trace 记录** | `app/services/semantic_cache.py` `cache_hit` / `cache_store` | 可统计缓存命中率，评估缓存效果 |

---

### 6. 其他子系统

以下子系统相对轻量，以表格形式列出核心职责和优化策略：

#### Skill 系统

| 维度 | 说明 |
|------|------|
| **核心文件** | `app/services/skill_manager.py`, `app/workflows/tasks/builtin_skills.py` |
| **职责** | 定义可复用的任务技能（TaskSkill），每个 Skill 绑定到一个 TaskWorkflow |
| **关键优化** | `TaskSkillRegistry` 统一注册/查找，`ConfigStore` 持久化 Skill 元数据，支持运行时热更新 |

#### Prompt 管理

| 维度 | 说明 |
|------|------|
| **核心文件** | `app/services/prompt_manager.py` |
| **职责** | 提示词版本化管理，支持回滚和 A/B 测试 |
| **关键优化** | `PromptManager` 通过 `ConfigStore` 持久化，支持运行时切换而不重启 |

#### MCP 集成

| 维度 | 说明 |
|------|------|
| **核心文件** | `app/services/mcp_manager.py` |
| **职责** | 连接外部 MCP Server，发现并注册外部工具 |
| **关键优化** | `McpManager.connect()` 按需连接，工具发现后自动注册到 ToolRegistry，失败时优雅降级不影响主流程 |

---

## 🔌 层间通信完整链路

### 通信矩阵总览

| 从 → 到 | 数据类型 | 协议/方式 | 方向 |
|---------|---------|-----------|------|
| Entry → Workflow | `TaskSpec + StepSpec` | Python 函数调用 | 单向 |
| Workflow → State | `ContextBundle` | `ContextHarness.build_context()` | 双向 |
| Workflow → Capability | `tool_name + payload dict` | `ExecutionHarness.run_tool()` | 单向（调用） |
| Capability → State | `ToolExecutionResult` | `ToolRegistry → TaskMemory` | 单向（写入） |
| Stage → EventBus | `HookEvent + workflow_state` | `EventBus.before_stage()` / `after_stage()` | 单向（发射） |
| EventBus → Hook | `EventPayload` | `HookRegistry.emit()` → `RuntimeHook.handle()` | 单向（消费） |
| Hook → State | `MemoryRecord` | `MemoryHook → TaskMemory.append()` | 单向（写入） |
| Hook → Trace | `event payload dict` | `TraceHook → TraceRecorder.record()` | 单向（写入） |
| Container → ToolContext | `external_services` dict | 依赖注入 | 单向（注入） |

### 一次完整 Query 请求的跨层调用链

```
HTTP POST /api/v1/query  { "question": "session怎么用", "collection_name": "demo" }
  │
  ▼
[Entry]  query.py:query(payload)
  │  FastAPI 解析 JSON → QueryRequest
  │  container.query_service.query(payload)
  ▼
[Entry]  query_service.py:query(payload)
  │  orchestrator.query(payload)  ← 薄层路由
  ▼
[Workflow] query_orchestrator.py:query(payload)
  │  build_query_task_spec(payload) → TaskSpec
  │  init_query_graph_state(payload) → QueryGraphState
  │  build_query_graph().invoke(state)  ← LangGraph 执行
  ▼
[Workflow] LangGraph 图执行（每个节点是 Stage）:
  │
  ├─ check_guardrails
  │   └─ [Governance] EventBus.before_stage → TraceHook → trace.record()
  │
  ├─ rewrite_query
  │   └─ "session怎么用" → "session 使用"
  │
  ├─ retrieve_evidence
  │   │  [Governance] EventBus.before_stage
  │   │  [Capability] ExecutionHarness.run_tool('rag_retrieve_evidence', ...)
  │   │     ├─ [Governance] Guardrail.validate_tool_call()
  │   │     ├─ [Governance] Policy.check_tool()
  │   │     ├─ [Governance] Sandbox.assess()
  │   │     ├─ [Capability] ToolExecutor.execute()
  │   │     │    └─ [Capability] ToolRegistry.run()
  │   │     │         └─ [Capability] RagRetrieveEvidenceTool.run()
  │   │     │              └─ [Domain] RagFacade.retrieve_evidence()
  │   │     │                   └─ [Domain] KnowledgeCapability.retrieve_evidence()
  │   │     │                        └─ [Infra] RagRetrievalService.retrieve()
  │   │     │                             ├─ 稠密召回 (ChromaDB)
  │   │     │                             ├─ 词法召回 (可选)
  │   │     │                             ├─ 图谱召回 (可选)
  │   │     │                             ├─ RRF 融合
  │   │     │                             ├─ Cross-Encoder 重排
  │   │     │                             └─ 父块回填
  │   │     ├─ [Governance] EventBus.after_tool
  │   │     │    → [State] MemoryHook → TaskMemory.append_memory_record()
  │   │     └─ [Governance] ExecutionHooks.record_execution()
  │   │          → [State] TaskMemory 写入 ToolExecutionResult
  │   └─ [Governance] EventBus.after_stage
  │
  ├─ grounded_answer
  │   └─ [Domain] RagFacade.grounded_answer()
  │        └─ [Infra] LLM 基于证据生成回答
  │
  ├─ self_reflect
  │   └─ Corrective RAG: 答案有据 → 通过
  │
  └─ finalize
       └─ [State] 持久化 QueryRun → 返回 QueryResponse
```

### 隐式治理事件流（与上述调用链并行存在）

```
RUN_STARTED
  ├─ check_guardrails → rewrite_query
  ├─ retrieve_evidence
  │    ├─ BEFORE_TOOL(rag_retrieve_evidence)
  │    │    ├─ Guardrail.validate → Policy.check → Sandbox.assess
  │    │    ├─ ToolExecutor.execute() → 成功
  │    │    └─ (或) TOOL_FAILED → FallbackHandler
  │    └─ AFTER_TOOL(rag_retrieve_evidence)
  ├─ grounded_answer
  ├─ self_reflect
  └─ finalize
RUN_COMPLETED
```

每个事件都被 `TraceHook`（通配符监听）和 `MemoryHook`（特定事件监听）消费，自动写入 Trace 和 TaskMemory。**节点开发者不需要手动调用任何治理逻辑。**

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
| `MemoryHook` | `app/harness/trace_hook.py` | EventBus 事件 → 写入记忆 |
| `SQLiteStateStore` | `app/services/sqlite_store.py` | SQLite 持久化 |

### 记忆系统优化策略

> 记忆系统的核心优化思路是 **"不把所有记忆都塞给 LLM"**——通过 **选择 → 压缩 → 预算 → 门控** 四层漏斗，确保进入 LLM 上下文的只有高质量、高相关的最少记忆。

**第一层：选择（SelectionEngine）** — 按相关性和新鲜度精选

| 来源类型 | 策略 | 权重 | 位置 |
|---------|------|------|------|
| Evidence（证据） | 组合策略 | 相关性 0.7 + 新鲜度 0.3 | `app/harness/selection.py` |
| Memory（记忆） | 组合策略 | 相关性 0.5 + 新鲜度 0.5 | 同上 |
| Artifact（产物） | 组合策略 | 相关性 0.6 + 新鲜度 0.4 | 同上 |
| State（状态） | 纯新鲜度 | 最新的优先 | 同上 |

相关性计算：取当前 step 的 intent（目标），与每条记忆的 summary/text 做中英文 token 匹配 + 语义重叠度计算，低于阈值的直接丢弃。

**第二层：压缩（CompressionEngine）** — 三层压缩链

```
去重（MD5 哈希）→ 主题聚类（关键词归类合并）→ 句子截断（只保留前 N 句）
```

| 压缩策略 | 做法 | 文件 | 效果 |
|---------|------|------|------|
| **Deduplication** | 文本 MD5 哈希去重 | `app/harness/compression.py` | 相同内容只保留一条，避免重复浪费 token |
| **ThematicClustering** | 按 风险/财务/合同/技术/流程 关键词归类，同主题合并 | 同上 | 减少碎片化，提高信息密度 |
| **SentenceTruncation** | 每条只保留前 N 句（默认 3），总字符不超过上限 | 同上 | 控制单条记忆长度 |
| **Hierarchical** | 以上三层组合，顺序执行 | 同上 | 逐步压缩，先粗后细 |

**第三层：预算（TokenBudgetEngine）** — 按权重分配 + 超预算裁剪

```
默认权重：Evidence 40% > State 25% > Artifact 20% > Memory 15%

超预算时裁剪顺序（从低优先级开始裁）：
  Memory → Artifact → State → Evidence（证据最优先保留）
```

| 功能 | 文件 | 效果 |
|------|------|------|
| 权重分配 | `app/harness/budgeting.py` `allocate_budget()` | 按来源类型权重 + 优先级加成分配 token 预算 |
| 超预算裁剪 | `app/harness/budgeting.py` `enforce_budget()` | 从低优先级开始逐层裁：删尾部列表项、截断字符串、删字典非核心字段 |
| Token 估算 | `app/harness/budgeting.py` `estimate_tokens()` | `tokens ≈ chars / 4`，快速估算无需调 LLM |

**第四层：门控（MemoryCommitGate）** — 信任提升 + Scope 晋升 + 冲突检测

| 机制 | 文件 | 规则 |
|------|------|------|
| 信任提升 | `app/services/memory_commit_gate.py` `auto_promote()` | unverified → provisional（有非空 summary）；provisional → verified（3+ 条相同 summary）；verified → final（24h 无冲突） |
| Scope 晋升 | `app/services/memory_commit_gate.py` `commit_to_semantic()` | 任务完成后，trust≥verified 的 run 记忆 → semantic，同 summary 去重 |
| 冲突检测 | `app/services/memory_commit_gate.py` `resolve_conflicts()` | 同 scope 同 kind 但不同 summary → 标记冲突，互相添加 `conflict_refs`，阻碍信任提升 |

**辅助优化：**

| 优化 | 位置 | 效果 |
|------|------|------|
| **滑动窗口硬上限** | `app/agents/memory.py` 各 `append` 方法 | memory_records≤200, task_memory≤100, artifact_memory≤50, reflections≤50, tool_calls≤100, revisions≤20, sub_agent_runs≤50 |
| **按 Step 定制策略** | `app/harness/context_policy.py` `for_step()` | 不同 Step 不同 token 预算和压缩参数（draft_artifact 给 12000 token，finalize 只给 8000） |
| **语义记忆跨任务注入** | `app/harness/components/context_builders.py` | 高信任度 semantic 记忆自动注入到新任务 ContextBundle，Task 工作流最多 20 条，Query 工作流最多 10 条 |
| **双写缓存** | `TaskMemory` → `InMemoryState` + `SQLiteStateStore` | 内存热缓存（读延迟 0）+ SQLite 持久化（重启不丢） |
| **EventBus 驱动自动写入** | `app/harness/trace_hook.py` `MemoryHook` | 每次 Tool/Step 完成自动通过 EventBus 事件写入记忆，无需手动调用 |

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
| skill / workflow / step | Workflow Layer | `TaskSkillRegistry` / LangGraph 图 |
| tool / command / sub-agent | Capability Layer | `ToolRegistry` / `SubAgentRegistry` |
| permission / policy / hook / audit | Governance Layer | `PolicyEngine` / `HookRegistry` |
| tech-stack | Infra Layer | `Settings` / `Container` |

### 原则 2：扩展靠继承，执行靠组合

```python
# 通过继承扩展
class BaseTool        # 新增能力时继承
class BaseSubAgent    # 新增受控子代理时继承
class TaskSkill       # 新增任务类型时继承

# 通过组合执行
# Orchestrator 组装 LangGraph 图 → graph.invoke(state)
EventBus + HookRegistry           # 治理组合由事件路由
```

### 原则 3：Harness > Agent

- Budget / Permission / Guardrail / Sandbox 由 Harness 控制
- Agent 只负责任务推进
- ReAct 只用于 step 内局部动作选择

### 原则 4：领域能力不反穿 Harness

```text
正确路径：LangGraph 节点 → Tool → Facade → Domain Service
错误路径：LangGraph 节点 → 直接调 KnowledgeCapability / RagQueryEngine
```

### 原则 5：治理不散落在节点中

```text
好的方式：节点只发射事件 → EventBus → HookRegistry → TraceHook/MemoryHook
不好的方式：节点内部自己调 trace.record() + 自己写 TaskMemory + 自己判断权限
```

---

## 🧩 设计理由：为什么这么设计

> 本节解释每个架构决策的背景、问题、权衡和选择理由。

---

### 一、为什么是 Harness-first，而不是 RAG-first？

**背景问题**：项目最初是 "Personal RAG App"。随着能力扩展，逐步加入了 weather、finance、repository、database、command 等工具，发现了一个根本问题：

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

---

### 三、为什么所有请求都先映射为 TaskSpec？

**当前做法**：

```text
QueryRequest → TaskSpec
ChatRequest  → TaskSpec
TaskRequest  → TaskSpec
  ↓
Orchestrator.run(task_spec)  ← 只认 TaskSpec，不认 QueryRequest/ChatRequest
```

**如果不这样做**：`Orchestrator` 需要理解三种不同的请求格式，内部充满 `if isinstance(request, QueryRequest): ...` 分支。

**核心原则**：Adapter 只做投影，不执行业务逻辑。

---

### 四、为什么用 EventBus + Hook 做治理横切？

**如果不用 EventBus**：每个节点内部都要手动调用 `guardrail.validate()`、`policy.check()`、`trace.record()`、`memory.append()`。同样一段治理逻辑在 10+ 个节点里重复出现。

**当前的 EventBus 方案**：

```text
Orchestrator / ExecutionHarness
  → event_bus.emit(HookEvent.BEFORE_STEP, ws, step_name='retrieve')
    → HookRegistry.emit(EventPayload(...))
      ├─ TraceHook → trace.record('before_step', ...)     ← 通配符监听
      ├─ MemoryHook → TaskMemory.append_memory_record(...)  ← 特定事件监听
      └─ (其他 hook)
```

**为什么这样更好**：
- 节点只发射事件，不感知治理逻辑
- 新增治理策略（如审计日志、计费扣减）只需注册新 Hook，不改任何节点代码
- Hook 可独立测试、独立开关、独立排序

---

### 五、为什么用 LangGraph 做工作流编排？

| 维度 | 手写编排 | LangGraph |
|------|---------|-----------|
| 可视化 | 代码即流程，难理解 | StateGraph 图结构，一目了然 |
| 条件路由 | 大量 if/else | `add_conditional_edges` 显式声明 |
| Checkpoint | 手写，易遗漏 | 框架自动，每个节点后都可 checkpoint |
| 重试/回退 | 手写 try/except | 框架原生支持 |
| 并行执行 | 手写 asyncio | `Send()` API 原生支持 |
| 人机交互 | 手写中断逻辑 | `interrupt()` 内置支持 |

**关键边界**：Node 是 graph 的实现细节，不是对外建模对象。外部代码只和 Orchestrator / LangGraph 图交互，不直接操作 graph node。

---

### 六、为什么 ToolRegistry 和 ToolExecutor 是分开的？

```text
ToolRegistry           ToolExecutor
├─ register()           ├─ execute()
├─ get()                │   ├─ timeout
├─ describe()           │   ├─ retry
├─ list_descriptions()  │   ├─ circuit_breaker
└─ (只负责注册/查找)     │   └─ sandbox
                        └─ (只负责执行治理)
```

- Registry 是"工具字典"，只回答"有哪些工具、怎么调用"
- Executor 是"执行策略"，回答"怎么安全地执行一个工具"
- 两者独立演进：加新工具不改执行策略，加新执行策略不改注册逻辑

---

### 七、为什么 CommandTool 是 Tool 的子类，而不是独立体系？

**如果 Command 独立**：

```
  RAG Tool 走：ToolRegistry → ToolExecutor → PolicyEngine → GuardrailEngine → Trace
  Command 走： CommandRegistry → CommandRunner → ??? → ??? → ???
                     ↑ 第二套 registry、第二套 executor、第二套 policy
                     问题：权限检查在哪？审计日志在哪？熔断在哪？
```

**当前做法**：Command 走同一套管道，只是 `risk_level='high'`, `sandbox_mode='process_isolated'`。

**核心原则**：不让命令长出第二套 registry + executor + policy + audit。

---

### 八、为什么 RAG 通过 Facade + ToolAdapter 暴露，而不是直接调用？

```text
A) 直接调用（错误）：
   节点直接调 knowledge_capability.retrieve()
   ↑ 节点直接依赖 KnowledgeCapability 内部实现

B) 通过 Tool + Facade（正确）：
   节点通过 ExecutionHarness.run_tool('rag_retrieve_evidence', payload)
   ↑ 节点只依赖 Tool 名称，不感知内部实现
```

**为什么 B 好**：
- 治理检查（护栏、权限、沙盒）在节点层被绕过 → B 走完整治理链
- 检索逻辑需要加缓存、加降级、加审计 → B 在 Facade 层统一处理
- 领域实现整体替换 → B 只要 Facade 接口不变

---

### 九：总结：为什么这套架构能支撑未来扩展

```
                    新增 Tool         新增 Capability     新增治理规则
                    ─────────        ──────────────     ────────────
Orchestrator         不变 ✅           不变 ✅             不变 ✅
ToolExecutor         不变 ✅           不变 ✅             不变 ✅
PolicyEngine         不变 ✅           不变 ✅             不变 ✅
GuardrailEngine      不变 ✅           不变 ✅             不变 ✅
EventBus             不变 ✅           不变 ✅             不变 ✅
ToolRegistry         注册新 Tool ✅    不变 ✅             不变 ✅
HookRegistry         不变 ✅           不变 ✅             注册新 Hook ✅

需要改的：          1 个文件          3-4 个文件          1 个文件
```

**核心设计目标**：**新增扩展只需注册，不需改骨架**。

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

**不需要改**：`ExecutionHarness`、`ToolExecutor`、`PolicyEngine`、`GuardrailEngine`

### 新增一个 Tool 需要改什么？

```text
1. 如果需要外部 API，在 app/capabilities/ 下写 Capability 实现
2. 在 app/agents/tools/ 下写 Tool 子类
   → 继承 BaseTool，实现 run() 方法，定义 input_model / output_model
3. 在 app/container.py 中注册工具（加到 self.task_tool_registry.register() 列表）
```

**不需要改**：`ExecutionHarness`、`ToolExecutor`、`PolicyEngine`

### 新增一个治理 Hook 需要改什么？

```text
1. 实现 RuntimeHook 协议（class MyHook: name + handle(event)）
2. 在 container.py 中注册到 EventBus：self.event_bus.register(MyHook(), event=HookEvent.BEFORE_TOOL)
```

**不需要改**：`ExecutionHarness`、Orchestrator 核心代码

---

## 📚 项目文档

| 文档 | 位置 | 说明 |
|------|------|------|
| **架构总览** | [`架构.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/架构.md) | 完整分层架构、模块职责、数据主链、通信链路 |
| **Agent Platform 设计** | [`docs/agent-platform-architecture.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/agent-platform-architecture.md) | Mode + Capability 新模型设计 |
| **实施路线图** | [`docs/agent-platform-roadmap.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/agent-platform-roadmap.md) | 分阶段实施计划 |
| **记忆系统改造** | [`docs/memory-system-redesign.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/memory-system-redesign.md) | 五层记忆系统设计 |
| **能力管理设计** | [`docs/architecture/agent-capability-management-design.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/architecture/agent-capability-management-design.md) | 能力归口与管理 |
| **能力与 Harness 集成** | [`docs/architecture/harness-capability-integration.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/architecture/harness-capability-integration.md) | 能力如何接入 Harness |
| **运维手册** | [`docs/operations/remote-provider-runbook.md`](file:///e:/vsc-workspace/lania-zip/lania-agent-demo/docs/operations/remote-provider-runbook.md) | 远程 Provider 运维 |

---

## 🗺️ 开发路线图

当前状态：**Agent Platform 核心框架已完成，Plan/Autopilot 模式已实现**

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | Harness 内核统一重构 + 记忆系统改造 | ✅ 完成 |
| Phase 2 | CapabilityRegistry + IntentMatcher + AgentService + 统一 Agent API | ✅ 完成 |
| Phase 3 | LlmRouter 按用途路由 + 运行时配置管理 | ✅ 完成 |
| Phase 4 | Plan 模式 + Autopilot 模式 | ✅ 完成 |
| Phase 5 | Sandbox 命令执行 + Coding Agent | ✅ 完成 |
| Phase 6 | Data Analysis Agent + Web 前端 | 🏗️ 部分完成（data_analysis ✅，Web 前端 📋） |

---

## 📄 许可证

MIT