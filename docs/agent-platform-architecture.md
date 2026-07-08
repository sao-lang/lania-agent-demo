# Lania Agent 平台架构设计

> 版本: 当前（Mode + Capability 模型）  
> 日期: 2026-07-04

---

## 目录

1. [核心模型](#1-核心模型)
2. [整体架构](#2-整体架构)
3. [Mode — 执行模式](#3-mode--执行模式)
4. [Capability — Agent 能力](#4-capability--agent-能力)
5. [Infrastructure — 基础设施](#5-infrastructure--基础设施)
6. [统一 Agent API](#6-统一-agent-api)
7. [CLI 设计](#7-cli-设计)
8. [Web 前端设计](#8-web-前端设计)
9. [管理配置面](#9-管理配置面)
10. [认证与权限](#10-认证与权限)
11. [LLM 按用途路由](#11-llm-按用途路由)
12. [系统配置参考](#12-系统配置参考)
13. [实施路线](#13-实施路线)

---

## 1. 核心模型

### 1.1 三维分类

```
Mode（模式 = 怎么做）
  chat       直接响应
  plan       先出计划 → 确认 → 执行
  autopilot  自动执行到完成为止

Capability（能力 = 会做什么）
  chat              通用对话（默认）
  document_analysis  文档分析
  document_summary   文档摘要
  code_review        代码审查
  data_analysis      数据分析
  web_search        联网搜索
  ...

Infrastructure（基础设施 = 用什么做）
  knowledge     知识库检索
  repository    文件系统
  database      数据库
  api_contract  API 契约
  artifact      产物管理
```

### 1.2 用户视角

```
用户: "分析 demo 集合中的架构文档"

Agent 内部处理:
  1. mode = chat         (用户没指定，默认)
  2. capability = document_analysis  (意图识别)
  3. infrastructure 依赖:
     - knowledge (RAG 检索)
     - repository (读文件)
  4. 执行 → 返回结果
```

```
用户: "--plan 审查 app/harness/ 下的代码"

Agent 内部处理:
  1. mode = plan         (用户指定)
  2. capability = code_review
  3. 先生成计划 → 等确认 → 执行
```

### 1.3 旧模型 → 新模型映射

```
旧 API                         新模型
──────────────────────────────────────────────────
POST /api/v1/query              mode=chat   + capability=自动识别
POST /api/v1/chat               mode=chat   + capability=自动识别
POST /api/v1/query/stream       mode=chat   + stream=true
POST /api/v1/chat/stream        mode=chat   + stream=true
POST /api/v1/tasks              mode=chat   + capability=指定
  { task_type: "document_analysis" }        { capability: "document_analysis" }
```

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户交互层                                    │
│                                                                     │
│  ┌─────────────────────┐      ┌─────────────────────┐              │
│  │  CLI (lania-cli-v2) │      │  Web 前端 (React)    │              │
│  │                     │      │                     │              │
│  │  lan agent "..."    │      │  Chat 界面          │              │
│  │  lan agent --plan   │      │  模式选择           │              │
│  │  lan agent --auto   │      │  能力管理           │              │
│  └─────────┬───────────┘      └──────────┬──────────┘              │
│            │                             │                         │
│            └──────────┬──────────────────┘                         │
│                       │                                            │
│              POST /api/v1/agent/chat                               │
│         { message, mode, capabilities, session_id }               │
└───────────────────────┼─────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       Agent Runtime (后端)                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  AgentService (核心编排)                                     │   │
│  │                                                             │   │
│  │  1. 解析 mode → 决定执行流程                                │   │
│  │     chat:    直接执行                                        │   │
│  │     plan:    生成计划 → 等待确认 → 执行                      │   │
│  │     autopilot: 自动循环执行 → 询问下一步                     │   │
│  │                                                             │   │
│  │  2. 识别意图 → 匹配 Capability                              │   │
│  │     chat               → 直接 LLM                           │   │
│  │     document_analysis  → DocumentAnalysisWorkflow           │   │
│  │     code_review        → CodingReviewWorkflow               │   │
│  │     data_analysis      → DataAnalysisWorkflow               │   │
│  │     ...                                                     │   │
│  │                                                             │   │
│  │  3. 执行 → 产生 SSE 事件流                                  │   │
│  │     step_start / tool_call / delta / completed              │   │
│  └─────────────────────────┬───────────────────────────────────┘   │
│                            │                                       │
│  ┌─────────────────────────┴───────────────────────────────────┐   │
│  │  Capability Registry                                       │   │
│  │                                                             │   │
│  │  chat             → LLM 直接回答 (无 Workflow)              │   │
│  │  document_analysis → DocumentAnalysisWorkflow + 子 Agent   │   │
│  │  document_summary  → DocumentSummaryWorkflow                │   │
│  │  code_review       → CodeReviewCapability (已实现)           │   │
│  │  data_analysis     → DataAnalysisCapability (已实现)          │   │
│  │  web_search        → WebSearchCapability (已实现)           │   │
│  └─────────────────────────┬───────────────────────────────────┘   │
│                            │                                       │
│  ┌─────────────────────────┴───────────────────────────────────┐   │
│  │  Infrastructure Layer (已有)                                │   │
│  │                                                             │   │
│  │  ToolRegistry | Harness Kernel | Policy | Guardrail         │   │
│  │  knowledge | repository | database | api_contract | artifact│   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Mode — 执行模式

### 3.1 三种模式

```
mode: chat
  用户输入 → 意图识别 → 匹配 Capability → 直接执行 → 返回结果
  ─────────────────────────────────────────────────────
  适合: 快速问答、简单请求、日常对话

mode: plan
  用户输入 → 意图识别 → 匹配 Capability → 生成计划 → 返回计划给用户
  → (用户确认) → 按计划逐步执行 → 返回结果
  ─────────────────────────────────────────────────────
  适合: 复杂任务、需要用户审核的场景

mode: autopilot
  用户输入 → 意图识别 → 匹配 Capability → 自动执行 → 完成后询问下一步
  → 用户继续输入 → 继续执行...
  ─────────────────────────────────────────────────────
  适合: 长时间任务、批处理、需要持续交互的场景
```

### 3.2 Plan 模式的执行流程

```
用户: "--plan 分析 demo 集合"

AgentService (mode=plan):
  1. 意图识别 → document_analysis
  2. 生成计划:
     ┌─────────────────────────────────┐
     │ 📋 执行计划                     │
     │                                 │
     │ 1/4 收集文档上下文              │
     │     → rag_load_document_context │
     │                                 │
     │ 2/4 检索证据                    │
     │     → rag_retrieve_evidence     │
     │     → rag_retrieve_graph_ev.    │
     │                                 │
     │ 3/4 提取关键发现                │
     │     → extract_key_points        │
     │                                 │
     │ 4/4 生成分析报告                │
     │     → draft_report              │
     └─────────────────────────────────┘
  3. SSE 返回计划 → CLI/Web 展示给用户
  4. 用户确认 → 开始执行
  5. 执行过程中逐步骤 SSE 推送
```

### 3.3 Autopilot 模式

```
用户: "--autopilot 审查整个项目的代码"

AgentService (mode=autopilot):
  1. 意图识别 → code_review
  2. 开始自动执行
  3. 审查完一个模块后，自动询问:
     "已审查 app/harness/，要继续审查 app/agents/ 吗？"
  4. 用户继续 → 继续执行
  5. 用户说"够了" → 停止
```

### 3.4 模式切换

用户可在对话中随时切换模式：

```bash
# CLI
/mode plan          # 切换到 plan 模式
/mode autopilot     # 切换到 autopilot
/mode chat          # 切回 chat
```

```python
# API 层面
PUT /api/v1/agent/session/{id}/mode
{"mode": "plan"}
```

---

## 4. Capability — Agent 能力

### 4.1 完整能力列表

| Capability | 说明 | 状态 | 依赖的 Workflow | 依赖的基础设施 |
|-----------|------|------|----------------|--------------|
| `chat` | 通用对话 | ✅ 现成 | 无（直接 LLM） | LLM |
| `document_analysis` | 文档深度分析 | ✅ 已有 | DocumentAnalysisWorkflow | knowledge + repository |
| `document_summary` | 文档摘要 | ✅ 已有 | DocumentSummaryWorkflow | knowledge |
| `code_review` | 代码审查 | ✅ 已实现 | CodeReviewCapability | repository |
| `data_analysis` | 数据分析 | ✅ 已实现 | DataAnalysisCapability | database |
| `web_search` | 联网搜索 | ✅ 已实现 | WebSearchCapability | httpx (DuckDuckGo) |

### 4.2 Capability 定义模型

```python
# app/capabilities/registry.py (新增)

class CapabilityDefinition(BaseModel):
    """Capability 定义。"""
    name: str                           # document_analysis
    display_name: str                   # 文档分析
    description: str                    # 对文档进行深度分析...
    workflow_type: str | None = None    # 对应的 Workflow 类型
    requires: list[str] = []            # 依赖的基础设施
    tools: list[str] = []               # 可用的工具列表
    prompt_instructions: str = ""       # Capability 的指令前缀
    is_default: bool = False            # 是否默认启用


class CapabilityRegistry:
    """Capability 注册表。"""

    def __init__(self):
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._workflows: dict[str, type] = {}

    def register(self, capability: CapabilityDefinition, workflow_cls=None):
        self._capabilities[capability.name] = capability
        if workflow_cls:
            self._workflows[capability.name] = workflow_cls

    def get(self, name: str) -> CapabilityDefinition: ...
    def list(self) -> list[CapabilityDefinition]: ...
    def match_intent(self, message: str) -> list[tuple[str, float]]:
        """根据用户输入匹配最合适的 Capability。"""
        ...
```

### 4.3 意图匹配

```python
# AgentService 意图识别

class IntentMatcher:
    """将用户自然语言匹配到 Capability。"""

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    async def match(self, message: str, history: list) -> CapabilityMatch:
        """识别用户意图，返回最匹配的 Capability。"""

        # 1. 关键词快速匹配
        if any(kw in message for kw in ["分析", "总结", "审查", "评估"]):
            # 需要进一步判断是文档分析还是代码审查
            if any(kw in message for kw in ["代码", "代码审查", "review"]):
                return CapabilityMatch("code_review", confidence=0.8)
            return CapabilityMatch("document_analysis", confidence=0.7)

        if any(kw in message for kw in ["搜索", "查一下", "网上"]):
            return CapabilityMatch("web_search", confidence=0.8)

        # 2. 默认: 通用对话
        return CapabilityMatch("chat", confidence=0.5)
```

### 4.4 Capability → Workflow 路由

```python
class CapabilityRouter:
    """Capability 到 Workflow 的路由。"""

    WORKFLOW_MAP = {
        "document_analysis": "app.workflows.tasks.document_analysis_graph",
        "document_summary": "app.workflows.tasks.document_summary_graph",
        "code_review": "app.workflows.tasks.coding_review_graph",
        "data_analysis": "app.workflows.tasks.data_analysis_graph",
        "chat": None,  # 直接 LLM，无需 Workflow
    }

    async def route(self, capability: str, context: dict) -> AsyncIterator[AgentEvent]:
        if capability == "chat":
            # 通用对话：直接 LLM 调用
            async for event in self._direct_llm(context):
                yield event
        else:
            # 有 Workflow 的能力：走 Harness
            workflow = self._load_workflow(capability)
            async for event in workflow.execute(context):
                yield event
```

### 4.5 Capability 管理 API

```python
GET    /capabilities                    # 列出所有 Capability
GET    /capabilities/{name}             # 查看详情
POST   /capabilities/{name}/enable      # 启用
POST   /capabilities/{name}/disable     # 禁用
```

---

## 5. Infrastructure — 基础设施

### 5.1 现有基础设施（全部复用）

| 基础设施 | 说明 | 对应 Capability |
|---------|------|----------------|
| `knowledge` | 知识库检索 (RAG) | document_analysis, document_summary |
| `repository` | 文件系统浏览 | code_review, document_analysis |
| `database` | SQLite 数据库查询 | data_analysis |
| `api_contract` | API 契约发现 | 代码分析 |
| `artifact` | 产物管理 | 所有任务 |

### 5.2 预留基础设施（部分已实现）

| 基础设施 | 说明 | 对应 Capability | 状态 |
|---------|------|----------------|------|
| `sandbox_execute` | 沙盒命令执行 | code_review, data_analysis | 🏗️ command_tools 已实现，非独立 Infra |
| `web_fetch` | 网页抓取 | web_search | 🏗️ web_search 直接使用 httpx，未抽成独立 Infra |

---

## 6. 统一 Agent API

### 6.1 唯一的入口

```python
POST /api/v1/agent/chat

{
  "message": "分析 demo 集合中的架构文档",

  "mode": "chat",                   # chat | plan | autopilot
  "session_id": "sess-xxx",          # 会话 ID

  "capabilities": null,              # null = 自动识别
  "collection_name": "demo",
  "agent_name": "default",
  "model": "gpt-4o",
  "stream": true
}
```

### 6.2 SSE 事件流

```
event: intent
data: {"type":"intent","capability":"document_analysis","confidence":0.85}

# plan 模式独有
event: plan
data: {"type":"plan","steps":[
  {"id":1,"name":"收集文档上下文","tool":"rag_load_document_context"},
  {"id":2,"name":"检索证据","tool":"rag_retrieve_evidence"},
  {"id":3,"name":"提取关键发现","tool":"extract_key_points"},
  {"id":4,"name":"生成报告","tool":"draft_report"}
]}

# plan 模式下，用户确认后继续
event: plan_confirmed
data: {"type":"plan_confirmed"}

# 执行事件
event: step_start
data: {"type":"step_start","step_id":1,"name":"收集文档上下文"}

event: tool_call
data: {"type":"tool_call","tool":"rag_retrieve_evidence","args":{...}}

event: tool_result
data: {"type":"tool_result","tool":"rag_retrieve_evidence","duration_ms":800}

event: delta
data: {"type":"delta","content":"系统的核心架构是..."}

event: step_end
data: {"type":"step_end","step_id":1,"status":"completed"}

event: completed
data: {"type":"completed","task_id":"task-xxx","duration_ms":3500}
```

### 6.3 旧 API 兼容

```python
# 旧的 query 端点 → 内部转调 AgentService
@router.post("/query")
async def query(request: QueryRequest):
    result = await agent_service.process(
        message=request.query,
        mode="chat",
        collection_name=request.collection_name,
        capability="document_analysis",  # 强制用文档分析
    )
    return result

# 旧的 chat 端点 → 同上
@router.post("/chat")
async def chat(request: ChatRequest):
    result = await agent_service.process(
        message=request.message,
        mode="chat",
        session_id=request.session_id,
        # capability 自动识别
    )
    return result
```

---

## 7. CLI 设计

### 7.1 命令树

```
lan agent                           # 交互 REPL (mode=chat, 自动识别能力)
lan agent -p "问题"                 # 一次性命令
lan agent --plan "复杂任务"         # plan 模式
lan agent --autopilot "持续任务"    # autopilot 模式

lan agent config
  set url/key/mcp-config
  show
  test

lan agent query <问题>              # 快捷方式 = mode=chat 自动识别
  [-c, --collection]
  [--stream]

lan agent capability                # 能力管理
  list                              # 列出所有 Capability
  enable <name>                     # 启用
  disable <name>                    # 禁用

lan agent task *                    # 任务管理 (兼容旧接口)

lan agent doc / collection / eval   # 文档/集合/评测管理

lan agent config llm / skill / agent-def / prompt / system / mcp
```

### 7.2 CLI 交互示例

```
$ lan agent
🔮 Lania Agent (模式: chat | 能力: 自动)

  ▶ 分析 demo 集合中的架构文档

  ── 意图识别: document_analysis (置信度: 0.85) ──

  📋 1/4 收集文档上下文
     → 正在读取 3 个文档...
  📋 2/4 检索证据
     → 找到 6 条相关证据
  📋 3/4 提取关键发现
     → 识别出 4 条核心发现
  📋 4/4 生成分析报告

  ## 核心架构分析...(Markdown)

  ── 完成 (3.2s) ──

  ▶
```

```
$ lan agent --plan "审查 app/harness/ 的代码"

  📋 执行计划:
     1/5 列出目录文件
     2/5 读取核心模块
     3/5 运行静态分析
     4/5 审查安全漏洞
     5/5 生成审查报告

  ▶ 确认执行? (Y/n) y

  📋 1/5 列出目录文件...
  ...
```

### 7.3 Slash 命令

```
/mode <chat|plan|autopilot>   切换模式
/capability list              查看当前可用能力
/capability <name>            指定使用某个能力
/help                         帮助
/quit                         退出
```

---

## 8. Web 前端设计

### 8.1 页面

```
/chat            Agent 对话
  ├── 模式选择器 (chat | plan | autopilot)
  ├── 消息列表 (含 Capability 标识)
  ├── Plan 展示卡片 (plan 模式)
  ├── 执行时间线
  └── 输入框

/capabilities   能力管理
  ├── 能力列表 (启用/禁用)
  └── 能力详情

/tasks           任务管理
/collections     集合与文档
/eval            评测
/settings        设置
```

### 8.2 Chat 页布局

```
┌──────────────────────────────────────────────┐
│  🔮 Lania Agent                              │
│  模式: [chat ▼]  能力: [自动 ▼]              │
├──────────────────────────────────────────────┤
│                                              │
│  用户: 分析 demo 集合中的架构文档             │
│                                              │
│  ┌─ Agent ──────────────────────────────┐    │
│  │ 📋 意图: document_analysis           │    │
│  │                                      │    │
│  │ 📋 1/4 收集文档上下文                │    │
│  │    → 正在读取...                     │    │
│  │                                      │    │
│  │ 📋 2/4 检索证据                      │    │
│  │    🔧 rag_retrieve_evidence 0.8s    │    │
│  │    🔧 rag_retrieve_graph...  1.2s   │    │
│  │                                      │    │
│  │ ## 核心架构分析...                    │    │
│  └──────────────────────────────────────┘    │
│                                              │
│  ▶ [___________________________________]    │
│       [chat ▼] [发送]                       │
└──────────────────────────────────────────────┘
```

---

## 9. 管理配置面

沿用之前版本的六个管理模块，增加 Capability 管理：

| 模块 | API |
|------|-----|
| **LLM 配置** | `GET/POST /admin/llm/*` |
| **Skill 管理** | `GET/POST /admin/skills/*` |
| **Agent 定义** | `GET/POST /admin/agents/*` |
| **提示词管理** | `GET/PUT /admin/prompts/*` |
| **MCP 配置** | `POST/GET /admin/mcp/*` |
| **系统设置** | `GET/PUT /admin/settings/*` |
| **Capability 管理** | `GET /capabilities`, `POST /capabilities/{name}/enable` |

---

## 10. 认证与权限

### 10.1 设计原则

- API Key 认证，简洁且适合 CLI/Web
- 角色权限控制，区分管理员和普通用户
- API Key 通过 CLI/Web 配置，存入 SQLite

### 10.2 认证流程

```
CLI: lan agent config set key sk-xxx
  ↓ 保存到 ~/.lania/agent-config.json
  ↓ 每次请求带上 Authorization: Bearer sk-xxx

Web: 登录页输入 API Key
  ↓ 存 localStorage
  ↓ 每次请求带上 Authorization: Bearer sk-xxx

后端:
  ┌──────────────────────────────┐
  │  请求 → AuthMiddleware       │
  │    ├─ /health → 放行         │
  │    ├─ /auth/login → 放行     │
  │    └─ 其他 → 校验 API Key   │
  │       ├─ 匹配 → 放行         │
  │       └─ 不匹配 → 401        │
  └──────────────────────────────┘
```

### 10.3 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/auth/login` | 用 API Key 登录，返回 token |
| `POST` | `/api/v1/auth/verify` | 验证 token 有效性 |
| `GET` | `/api/v1/auth/profile` | 获取当前用户/角色信息 |
| `GET` | `/api/v1/admin/users` | 用户列表 (admin only) |
| `POST` | `/api/v1/admin/users` | 创建用户 (admin only) |

### 10.4 角色权限矩阵

| 权限 | admin | user | readonly |
|------|-------|------|----------|
| Agent 对话 | ✅ | ✅ | ✅ |
| 任务管理 | ✅ | ✅ | ✅ |
| 文档/集合管理 | ✅ | ✅ | ❌ |
| LLM 配置 | ✅ | ❌ | ❌ |
| Skill 管理 | ✅ | ❌ | ❌ |
| Agent 定义 | ✅ | ❌ | ❌ |
| 提示词管理 | ✅ | ❌ | ❌ |
| MCP 配置 | ✅ | ❌ | ❌ |
| 系统设置 | ✅ | ❌ | ❌ |
| 用户管理 | ✅ | ❌ | ❌ |

### 10.5 数据模型

```python
# SQLite 表: api_keys
# id, key_hash, name, role, enabled, created_at

# SQLite 表: users
# id, name, role, api_key_id, created_at

DEFAULT_API_KEY = env("LANIA_API_KEY", "dev-key-123")
DEFAULT_ROLE = "admin"  # 开发环境默认 admin
```

### 10.6 CLI 配置

```bash
lan agent config set key sk-xxx        # 设置 API Key
lan agent config show                  # 查看当前配置（隐藏 key）
lan agent status                       # 查看连接状态 + 当前角色
```


## 11. LLM 按用途路由

### 11.1 问题

整个系统有 16 个组件依赖 LLM，但不同用途需要的模型能力差异很大：

| 用途 | 需要的能力 | 推荐模型 | Token 消耗 |
|------|-----------|---------|-----------|
| `chat` | 对话/回答生成 | gpt-4o, claude-3-opus | 🔴 高 |
| `analysis` | 文档分析/报告 | gpt-4o, claude-3-opus | 🔴 高 |
| `intent` | 意图识别 | gpt-4o-mini | 🟢 极低 |
| `plan` | 计划生成 | gpt-4o-mini | 🟢 低 |
| `corrective` | Corrective RAG 自检 | gpt-4o-mini | 🟢 低 |
| `extraction` | 图谱实体抽取 | gpt-4o-mini | 🟡 中 |
| `expansion` | 多查询扩展 / HyDE | gpt-4o-mini | 🟢 低 |
| `eval` | Ragas 评测 | gpt-4o-mini | 🟡 中 |

目前所有用途共享同一个 LLM 实例，无法按需配置。

### 11.2 设计方案

用途 → Provider + Model 的映射表，存在 SQLite `config_store` 中：

```python
# 命名空间: llm_route
# key: 用途名称
# value: { "provider": "openai", "model": "gpt-4o-mini" }

LLM_ROUTE_DEFAULTS = {
    "chat":        { "provider": "openai", "model": "gpt-4o" },
    "analysis":    { "provider": "openai", "model": "gpt-4o" },
    "intent":      { "provider": "openai", "model": "gpt-4o-mini" },
    "plan":        { "provider": "openai", "model": "gpt-4o-mini" },
    "corrective":  { "provider": "openai", "model": "gpt-4o-mini" },
    "extraction":  { "provider": "openai", "model": "gpt-4o-mini" },
    "expansion":   { "provider": "openai", "model": "gpt-4o-mini" },
    "eval":        { "provider": "openai", "model": "gpt-4o-mini" },
}
```

### 11.3 API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/v1/admin/llm/routes` | 列出所有用途的路由 |
| `GET` | `/api/v1/admin/llm/routes/{purpose}` | 查看单个用途 |
| `PUT` | `/api/v1/admin/llm/routes/{purpose}` | 设置单个用途的路由 |
| `POST` | `/api/v1/admin/llm/routes/reset` | 恢复默认路由 |

### 11.4 CLI 配置

```bash
# 配置各用途的模型
lan agent config llm route chat gpt-4o
lan agent config llm route intent gpt-4o-mini
lan agent config llm route corrective gpt-4o-mini

# 查看当前路由
lan agent config llm routes
# ┌──────────────┬──────────┬──────────────┐
# │ Purpose      │ Provider │ Model        │
# ├──────────────┼──────────┼──────────────┤
# │ chat         │ openai   │ gpt-4o       │
# │ intent       │ openai   │ gpt-4o-mini  │
# │ corrective   │ openai   │ gpt-4o-mini  │
# │ extraction   │ ollama   │ qwen2.5:7b   │
# └──────────────┴──────────┴──────────────┘
```

### 11.5 后端实现要点

```python
class LlmRouter:
    """LLM 路由 - 按用途返回对应的 LLM 实例。"""

    def __init__(self, config_store: ConfigStore):
        self._store = config_store
        self._instances: dict[str, Any] = {}  # 用途 → LLM 实例缓存

    async def get_llm(self, purpose: str) -> Any:
        """获取指定用途的 LLM 实例。"""
        if purpose in self._instances:
            return self._instances[purpose]

        # 从配置读取
        route = await self._store.get("llm_route", purpose)
        if not route:
            route = LLM_ROUTE_DEFAULTS.get(purpose, LLM_ROUTE_DEFAULTS["chat"])

        # 构建 LLM 实例
        llm = self._build_llm(route["provider"], route["model"])
        self._instances[purpose] = llm
        return llm

    async def set_route(self, purpose: str, provider: str, model: str):
        """设置用途的路由。"""
        await self._store.set("llm_route", purpose, {
            "provider": provider, "model": model,
        })
        # 清除缓存，下次重建
        self._instances.pop(purpose, None)

    def _build_llm(self, provider: str, model: str) -> Any:
        """根据 provider + model 构建 LLM 实例。"""
        if provider == "openai":
            from llama_index.llms.openai import OpenAI
            return OpenAI(model=model, api_key=...)
        elif provider == "anthropic":
            ...
```

### 11.6 组件接入方式

各组件不再直接持有 `self._llm`，而是持有 `LlmRouter`：

```python
# Before:
class AnswerService:
    def __init__(self, llm):        # 一个 LLM 实例
        self._llm = llm

# After:
class AnswerService:
    def __init__(self, llm_router):  # LLM 路由器
        self._llm_router = llm_router

    async def answer(self, query):
        llm = await self._llm_router.get_llm("chat")  # 获取 chat 用途的 LLM
        ...

    async def corrective_check(self, answer):
        llm = await self._llm_router.get_llm("corrective")  # 获取 corrective 用途的 LLM
        ...
```


## 12. 系统配置参考

### 12.1 配置体系总览

系统有 70+ 个可配置参数，分为三个配置层级：

```
层级 1: 环境变量 (.env)         → 启动时加载，适合基础设施
层级 2: 管理 API (运行时)       → ConfigStore → SQLite，适合业务参数
层级 3: CLI 本地配置            → ~/.lania/agent-config.json，适合用户偏好
```

当前仅层级 1 完整，层级 2 部分实现，层级 3 待建设。
以下列出所有应暴露为层级 2（运行时 API 可配）的参数。

### 12.2 LLM / 模型

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `llm.providers` | `list` | `[openai]` | Provider 列表 |
| `llm.active_provider` | `string` | `openai` | 当前激活的 Provider |
| `llm.active_model` | `string` | `gpt-4o-mini` | 当前激活的模型 |
| `llm.embed_model` | `string` | `text-embedding-3-small` | Embedding 模型 |
| `llm.fallback_enabled` | `bool` | `true` | 无 Key 时本地降级 |
| `llm.routes.*` | `object` | 见 §11 | 按用途路由 |

CLI:

```bash
lan agent config llm set openai gpt-4o sk-xxx
lan agent config llm set-embed text-embedding-3-small
lan agent config llm route chat gpt-4o
lan agent config llm route intent gpt-4o-mini
```

API:

```
GET    /admin/llm/providers
POST   /admin/llm/providers
PUT    /admin/llm/active?provider=openai&model=gpt-4o
GET    /admin/llm/routes
PUT    /admin/llm/routes/{purpose}?model=gpt-4o-mini
```

### 12.3 功能开关

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `features.guardrails` | `bool` | `true` | 输入/输出护栏 |
| `features.context_compression` | `bool` | `true` | 检索结果压缩 |
| `features.semantic_cache` | `bool` | `true` | 语义缓存 |
| `features.pii_redaction` | `bool` | `true` | PII 脱敏 |
| `features.cross_encoder_rerank` | `bool` | `false` | Cross-Encoder 重排 |
| `features.self_rag_retry` | `bool` | `false` | Corrective RAG 重试 |
| `features.plan_mode` | `bool` | `true` | plan 执行模式 |
| `features.autopilot_mode` | `bool` | `true` | autopilot 执行模式 |
| `features.mcp` | `bool` | `true` | MCP 集成 |

CLI:

```bash
lan agent config system set features.semantic_cache false
lan agent config system set features.cross_encoder_rerank true
lan agent config system list
```

API:

```
GET    /admin/settings/features
PUT    /admin/settings/features/{key}?value=true
```

### 12.4 RAG / 检索

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `rag.default_top_k` | `int` | `5` | 默认检索数量 |
| `rag.similarity_threshold` | `float` | `0.94` | 语义缓存阈值 |
| `rag.cache_ttl_seconds` | `int` | `86400` | 缓存过期 |
| `rag.cache_max_entries` | `int` | `500` | 每集合最大缓存 |
| `rag.compression_max_chunks` | `int` | `4` | 压缩最大块数 |
| `rag.compression_max_sentences` | `int` | `8` | 压缩最大句子 |
| `rag.compression_max_chars` | `int` | `1600` | 压缩最大字符 |
| `rag.self_retry_count` | `int` | `1` | 自检重试次数 |
| `rag.self_min_confidence` | `float` | `0.65` | 自检最低置信度 |
| `rag.parent_context_max_chars` | `int` | `1800` | 父块回填上限 |

CLI:

```bash
lan agent config system set rag.default_top_k 10
lan agent config system set rag.similarity_threshold 0.9
```

### 12.5 文档解析

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `ingestion.chunking_strategy` | `enum` | `fixed` | `fixed` / `semantic` |
| `ingestion.chunk_size` | `int` | `800` | 分块大小 |
| `ingestion.chunk_overlap` | `int` | `100` | 分块重叠 |
| `ingestion.max_file_bytes` | `int` | `50MB` | 单文件上限 |
| `ingestion.ocr_languages` | `string` | `eng+chi_sim` | OCR 语言 |
| `ingestion.noise_cleanup` | `bool` | `true` | 导入去噪 |
| `ingestion.metadata_enrichment` | `bool` | `true` | 元数据增强 |

### 12.6 Agent 行为

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `agent.default_mode` | `enum` | `chat` | `chat` / `plan` / `autopilot` |
| `agent.default_capability` | `string` | `auto` | 默认能力 |
| `agent.default_collection` | `string` | `default` | 默认集合 |
| `agent.default_language` | `string` | `zh-CN` | 默认语言 |
| `agent.max_tool_calls_per_step` | `int` | `5` | 单步最大工具调用 |
| `agent.tool_timeout_ms` | `int` | `30000` | 工具超时 |
| `agent.tool_retry_attempts` | `int` | `1` | 工具重试次数 |

CLI:

```bash
lan agent config system set agent.default_mode plan
lan agent config system set agent.max_tool_calls_per_step 10
```

### 12.7 任务 Worker

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `worker.poll_interval` | `float` | `1.0` | 轮询间隔(秒) |
| `worker.lease_seconds` | `int` | `1800` | 任务租约 |
| `worker.max_workers` | `int` | `1` | 并发 worker 数 |
| `worker.max_concurrent_tasks` | `int` | `5` | 最大并发任务 |

### 12.8 安全 / 沙盒

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `sandbox.provider` | `enum` | `local_process` | `local_process` / `docker` |
| `sandbox.timeout_seconds` | `int` | `15` | 执行超时 |
| `sandbox.circuit_breaker_threshold` | `int` | `3` | 熔断阈值 |
| `sandbox.circuit_breaker_cooldown` | `int` | `30` | 熔断冷却(秒) |
| `sandbox.high_risk_tools` | `list` | `[finalize_report]` | 高风险工具 |
| `sandbox.allow_local_fallback` | `bool` | `true` | 远程降级 |

### 12.9 数据工具

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `tools.repository_max_results` | `int` | `20` | 文件搜索上限 |
| `tools.repository_max_lines` | `int` | `80` | 文件读取行数 |
| `tools.database_max_rows` | `int` | `100` | 查询行数上限 |
| `tools.database_max_entries` | `int` | `100` | 列表条目上限 |

### 12.10 评测

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `eval.use_query_rewrite` | `bool` | `true` | 是否改写查询 |
| `eval.multi_query_count` | `int` | `3` | 多查询数量 |
| `eval.use_hybrid_retrieval` | `bool` | `false` | 混合检索 |
| `eval.use_graph_rag` | `bool` | `false` | 图谱 RAG |
| `eval.use_corrective_rag` | `bool` | `false` | Corrective RAG |
| `eval.graph_max_hops` | `int` | `1` | 图谱最大跳数 |

### 12.11 CLI 本地配置

以下配置存储在 `~/.lania/agent-config.json`，不涉及后端：

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `backend_url` | `string` | `http://localhost:8000` | 后端地址 |
| `api_key` | `string` | — | 认证密钥 |
| `default_collection` | `string` | `default` | 默认集合 |
| `default_mode` | `enum` | `chat` | 默认模式 |
| `output_mode` | `enum` | `human` | `human` / `json` |
| `timeout_seconds` | `int` | `60` | 请求超时 |
| `mcp_config_path` | `string` | — | MCP 配置文件路径 |

CLI:

```bash
lan agent config set url http://localhost:8000
lan agent config set key sk-xxx
lan agent config set output_mode json
lan agent config show
```


## 13. 实施路线

### Phase 4 (2-3 周): 认证 + LLM 路由 + 系统配置 ✅ 已完成

```
后端:
├── CapabilityRegistry + 定义 (chat, document_analysis, document_summary)
├── IntentMatcher (关键词匹配)
├── AgentService (mode=chat 实现)
├── 统一 POST /api/v1/agent/chat (SSE 流式)
├── 旧 API 兼容 (query/chat → 内部转调)
└── McpManager (MCP 客户端)

CLI:
├── lania-plugins-command-agent crate
│   ├── api_client, handler, config, output
│   └── 命令: agent (REPL), query, capability, task, doc
└── 验证: lan agent "分析..." → 自动识别 → 执行 → 输出
```

### Phase 5 (2-3 周): Sandbox 执行 + Coding Agent ✅ 已完成

```
后端:
├── SandboxExecuteCapability (三层安全策略)
│   ├── base.py: CommandSecurityPolicy / CommandExecutionRequest / Result
│   ├── service.py: LocalSandboxExecuteCapability (subprocess 沙盒)
│   └── 三级策略工厂: sandboxed / restricted / standard
├── command_tools.py 重构
│   ├── ShellCommandTool / RepositoryCommandTool 委托 sandbox_execute
│   └── 网络隔离 (环境变量阻断 HTTP/HTTPS)
├── CodingCapability (6 阶段工作流)
│   ├── Plan → CollectCodeContext → RunAnalysis → Analyze → DraftReview → Finalize
│   └── 实际执行 lint/静态分析 (pyflakes, mypy)
├── coding_tools: ExtractCodeIssuesTool / RunCodeAnalysisTool
└── AgentService 注册 CodingCapability 为 provider
```

### Phase 6 (2-3 周): 新 Capability + 管理面 🏗️ 部分完成（Capability 已实现，Web 前端待开始）

```
后端:
├── code_review Capability (沙盒执行)
├── data_analysis Capability
├── web_search Capability
├── 管理 API (LLM/Skill/Agent/Prompt/MCP/System)
└── Capability 管理 API

CLI:
├── config / skill / agent-def / prompt 命令
├── capability list/enable/disable
└── 完整管理能力

Web:
├── 设置页 (所有管理面)
├── Capability 管理页
├── 任务管理页
└── 评测 Dashboard
```

---

## 总结

```
旧模型:                       新模型:
task_type → 是什么             Capability → 会做什么
query/chat → 不同端点          Mode → 怎么做
                               Infrastructure → 用什么做

统一 API: POST /api/v1/agent/chat { message, mode, capabilities }
CLI: lan agent / lan agent --plan / lan agent --autopilot
Web: Chat 页 + 模式选择 + 能力管理
```
