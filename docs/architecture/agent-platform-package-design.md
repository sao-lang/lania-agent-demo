# Agent 平台包设计方案

> 将 `app/agent_platform/` 从子目录改造为独立 Python 包 `agent-platform`，
> 上层应用通过导入包、调用方法的方式使用平台能力。

---

## 一、架构概览

```
┌─────────────────────────────────────────────────────┐
│                  应用层 (Application)                │
│                                                     │
│  my-coding-agent/                                   │
│  ├── main.py          ← 暴露 HTTP/SSE 端点           │
│  ├── .lania/agents/   ← Agent 定义                  │
│  ├── tools/           ← 自定义工具                   │
│  └── frontend/        ← UI                          │
│                                                     │
│  from agent_platform import AgentPlatformContainer    │
│  container = AgentPlatformContainer(settings)        │
│  container.agent_service.process(...)                │
└──────────────────────┬──────────────────────────────┘
                       │ 依赖
┌──────────────────────▼──────────────────────────────┐
│              agent-platform（pip 包）                  │
│                                                      │
│  agent_platform/                                     │
│  ├── container.py     ← AgentPlatformContainer       │
│  ├── agents/brain/    ← AgentLoop / 执行引擎          │
│  ├── services/        ← 所有平台服务                   │
│  ├── harness/         ← guardrails / policy / hooks   │
│  ├── capabilities/    ← 工具能力注册                  │
│  ├── models/          ← 数据模型                      │
│  └── core/            ← 配置 / 认证 / 日志             │
└──────────────────────────────────────────────────────┘
```

---

## 二、核心扩展点（Plugin Protocol）

平台定义接口，用户提供实现。内置零依赖的默认实现，不强制用户选框架。

### 2.1 LLM Protocol — 模型供应商

```python
# agent_platform/llm/protocol.py
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    usage: dict[str, Any] | None = None


@dataclass
class StreamChunk:
    type: str  # "delta" | "tool_call_delta" | "stop"
    text: str = ""
    tool_call: dict[str, Any] | None = None


class LLM(Protocol):
    """LLM 供应商接口。用户可实现此协议接入任意模型。"""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> ChatResponse:
        """同步调用。返回完整响应。"""

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用。逐块返回。"""


# 内置默认实现：OpenAI，零额外依赖
class OpenAILLM:
    """只依赖 httpx 的 OpenAI 兼容实现。"""

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None: ...
```

**用户接入其他模型：**

```python
from agent_platform.llm.protocol import LLM, ChatResponse
from anthropic import AsyncAnthropic

class AnthropicLLM(LLM):
    def __init__(self, api_key: str):
        self._client = AsyncAnthropic(api_key=api_key)

    async def chat(self, messages, tools=None):
        resp = await self._client.messages.create(
            model="claude-3-opus", messages=messages,
        )
        return ChatResponse(content=resp.content[0].text)

container = AgentPlatformContainer(
    settings=settings,
    llm=AnthropicLLM(api_key="sk-ant-..."),
)
```

---

### 2.2 Storage Backend — 持久化存储

```python
# agent_platform/store/protocol.py
from typing import Any, Protocol


class StateStore(Protocol):
    """持久化存储接口。平台内置 SQLite 实现。"""

    # ── 生命周期 ──
    def load_into(self, state: Any) -> None: ...
    def ping(self) -> str: ...

    # ── Task ──
    def get_task(self, task_id: str) -> dict | None: ...
    def upsert_task(self, payload: dict) -> None: ...
    def list_tasks(self) -> list[dict]: ...
    def claim_next_task(self, worker_id: str, lease_seconds: int) -> dict | None: ...
    def touch_task_heartbeat(self, task_id: str, worker_id: str, lease_seconds: int) -> dict | None: ...

    # ── TaskRun ──
    def get_task_run(self, run_id: str) -> dict | None: ...
    def upsert_task_run(self, record: dict) -> None: ...
    def list_task_runs(self) -> list[dict]: ...

    # ── Artifact ──
    def get_artifact(self, artifact_id: str) -> dict | None: ...
    def upsert_artifact(self, payload: dict) -> None: ...
    def list_artifacts(self) -> list[dict]: ...
    def list_artifacts_for_task(self, task_id: str) -> list[dict]: ...

    # ── Session ──
    def get_session(self, session_id: str) -> dict | None: ...
    def upsert_session(self, session_id: str, payload: dict) -> None: ...
    def delete_session(self, session_id: str) -> None: ...

    # ── UserProfile ──
    def get_user_profile(self, user_id: str) -> dict | None: ...
    def upsert_user_profile(self, user_id: str, payload: dict) -> None: ...

    # ── AgentDef ──
    def get_agent_def(self, agent_id: str) -> dict | None: ...
    def upsert_agent_def(self, payload: dict) -> None: ...
    def list_agent_defs(self) -> list[dict]: ...
    def delete_agent_def(self, agent_id: str) -> None: ...

    # ── Skill ──
    def get_skill(self, skill_id: str) -> dict | None: ...
    def upsert_skill(self, payload: dict) -> None: ...
    def list_skills(self) -> list[dict]: ...
    def delete_skill(self, skill_id: str) -> None: ...
    def upsert_skill_rule(self, payload: dict) -> None: ...
    def list_skill_rules(self) -> list[dict]: ...
    def delete_skill_rule(self, rule_id: str) -> None: ...

    # ── Prompt ──
    def get_prompt(self, prompt_id: str) -> dict | None: ...
    def upsert_prompt(self, payload: dict) -> None: ...
    def list_prompts(self) -> list[dict]: ...
    def delete_prompt(self, prompt_id: str) -> None: ...

    # ── MCP ──
    def get_mcp_server(self, mcp_id: str) -> dict | None: ...
    def upsert_mcp_server(self, payload: dict) -> None: ...
    def list_mcp_servers(self) -> list[dict]: ...
    def delete_mcp_server(self, mcp_id: str) -> None: ...

    # ── Consent ──
    def get_consent(self, user_id: str, tool_name: str) -> dict | None: ...
    def save_consent(self, user_id: str, tool_name: str, payload: dict) -> None: ...

    # ── PolicyProfile ──
    def list_policy_profiles(self) -> list[dict]: ...


# 内置默认实现：SQLite
class SQLiteStateStore:
    """平台自带的 SQLite 实现。""" ...
```

**用户接入 Redis/Postgres：**

```python
from agent_platform.store.protocol import StateStore
import asyncpg

class PostgresStateStore(StateStore):
    def __init__(self, dsn: str):
        self._pool = asyncpg.create_pool(dsn)
    ...

container = AgentPlatformContainer(
    settings=settings,
    store=PostgresStateStore(dsn="postgres://..."),
)
```

---

### 2.3 Tool Lifecycle Hook — 工具执行钩子

**统一设计：平台层只认 `ToolHook` 协议，两种使用方式共享同一个内部接口。**

```python
# agent_platform/hooks/protocol.py
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


HOOK_EVENTS = Literal[
    "before_tool",      # 工具执行前（可阻断）
    "after_tool",       # 工具执行后（只读）
    "tool_failed",      # 工具执行失败
    "before_react_turn",# ReAct 轮次开始
    "after_react_turn", # ReAct 轮次结束
    "run_started",      # 一次 Agent 执行开始
    "run_completed",    # 一次 Agent 执行完成
    "run_failed",       # 一次 Agent 执行失败
]

@dataclass
class HookDecision:
    """Hook 执行结果。allow=False 可阻断后续执行。"""
    allow: bool = True
    reason: str = ""
    override_result: Any | None = None
    audit_log: dict[str, Any] | None = None


class ToolHook(Protocol):
    """工具执行钩子。平台内部统一使用此协议。"""

    async def on_event(
        self,
        event: HOOK_EVENTS,
        payload: dict[str, Any],
    ) -> HookDecision:
        """事件触发时调用。payload 包含事件上下文（tool_name, args, result 等）。"""
        return HookDecision()
```

#### 方式一：文件式（面向最终用户）

用户在 `.lania/hooks/` 下写 YAML 配置文件，无需写代码：

```yaml
# .lania/hooks/audit-all-tools.yaml
on: after_tool
action: audit
target: all
params:
  channel: database
```

```yaml
# .lania/hooks/block-dangerous-commands.yaml
on: before_tool
action: block
target:
  tools: [shell_execute, execute_batch]
  risk_levels: [critical]
params:
  reason: "高危命令已被系统策略禁止"
```

平台内置 `FileHookRunner` 加载并执行这些配置：

```python
# 平台内置实现
class FileHookRunner:
    """读取 .lania/hooks/*.yaml，解析为 ToolHook 协议。"""
    def __init__(self, hooks_dir: str | Path):
        self._loader = HookLoader(hooks_dir)
        self._engine = HookActionEngine()

    async def on_event(self, event, payload):
        matched_hooks = self._loader.match(event, payload)
        for hook in matched_hooks:
            result = await self._engine.execute(hook.action, payload)
            if not result.allowed:
                return HookDecision(allow=False, reason=result.reason)
        return HookDecision()
```

#### 方式二：编程式（面向开发者）

开发者实现 `ToolHook` 协议，注册到容器：

```python
from agent_platform.hooks.protocol import ToolHook, HookDecision

# 审计日志
class AuditLogger:
    async def on_event(self, event, payload):
        if event in ("before_tool", "after_tool"):
            log_audit(user=payload.get("user_id"), event=event, data=payload)
        return HookDecision()

# 速率限制
class RateLimiter:
    async def on_event(self, event, payload):
        if event == "before_tool":
            user = payload.get("user_id", "anonymous")
            if self._is_rate_limited(user):
                return HookDecision(allow=False, reason="请求过于频繁")
        return HookDecision()

# 注册
container.register_tool_hook(FileHookRunner("./lania/hooks"))
container.register_tool_hook(AuditLogger())
container.register_tool_hook(RateLimiter())
```

#### 两种方式的关系

```
                    ToolHook 协议
                   ┌─────────────┐
                   │  on_event()  │
                   └──────┬──────┘
                          │ 实现
              ┌───────────┴───────────┐
              │                       │
     FileHookRunner           AuditLogger / RateLimiter / ...
   (.lania/hooks/*.yaml)      (Python 代码)
              │                       │
              │ 内部使用               │
     HookLoader + HookActionEngine    │
```

| 维度 | 文件式 | 编程式 |
|---|---|---|
| **用户** | 非开发者（填写 YAML） | 开发者（写 Python） |
| **能力** | log / block / audit / notify / throttle | 任意 Python 逻辑 |
| **存储** | `.lania/hooks/` 文件系统 | 代码仓库 |
| **适用场景** | 审计、阻断、限流等标准治理 | 自定义监控、动态策略、第三方集成 |

#### 执行顺序

所有注册的 `ToolHook` 按注册顺序依次执行。任何一个返回 `allow=False` 则阻断后续执行：

```python
async def _execute_hooks(self, event, payload):
    for hook in self._tool_hooks:
        decision = await hook.on_event(event, payload)
        if not decision.allow:
            return decision  # 阻断，不继续执行后续 hook
    return HookDecision(allow=True)
```

#### ToolHook 与 ToolSandbox 的执行联动

`before_tool` hook 和 `ToolSandbox` 是两层独立的防护，它们按固定顺序配合：

```
StepExecutor.execute_step(tool_call)
  │
  ├─ 1. before_tool hook（所有注册的 hook 依次执行）
  │     ├─ 通过 → 继续
  │     └─ 阻断 → 返回 HookDecision(allow=False)，不执行工具
  │
  ├─ 2. 检查工具在白名单中（agent_def.allowed_tools）
  │     ├─ 在白名单中 → 继续
  │     └─ 不在 → 拒绝执行
  │
  ├─ 3. 检查工具 risk_level
  │     ├─ low    → StepExecutor._tool_registry.run()   # 内联执行
  │     ├─ medium → asyncio.to_thread(...)               # 线程隔离
  │     ├─ high   → ToolSandbox.run_in_sandbox(...)      # 沙箱隔离
  │     └─ critical → 拒绝执行（须审批）
  │
  ├─ 4. after_tool hook（只读审计）
  │
  └─ 5. tool_failed hook（仅在工具抛出异常时触发）
```

**关键规则**：
- before_tool hook 阻断后，Sandbox 不会被执行——避免不必要的资源开销
- risk_level 的默认值来自工具定义，但 `agent_def.risk_level_overrides` 可以按 agent 类型覆盖
- after_tool hook 是只读的，不能阻断或修改结果
- tool_failed hook 可以触发告警、写入错误日志、或执行降级策略
```

---

### 2.4 Observability Export — 可观测导出

```python
# agent_platform/observability/protocol.py
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Span:
    """一次 Agent 执行的一个阶段。"""
    name: str
    start_time: float
    end_time: float
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class AgentTrace:
    """一次完整的 Agent 执行追踪。"""
    trace_id: str
    session_id: str
    agent_name: str
    spans: list[Span]
    total_tokens: int
    total_latency_ms: float
    tool_calls: int
    errors: int


class TraceExporter(Protocol):
    """追踪数据导出接口。用户可接入 Prometheus/LangSmith/OpenTelemetry 等。"""

    async def export(self, trace: AgentTrace) -> None: ...


# 内置默认实现：Console 日志
class ConsoleExporter:
    async def export(self, trace: AgentTrace) -> None:
        print(f"[trace] {trace.agent_name} | tokens={trace.total_tokens} | "
              f"latency={trace.total_latency_ms:.0f}ms | errors={trace.errors}")


# 用户接入 Prometheus
from prometheus_client import Counter, Histogram

class PrometheusExporter:
    def __init__(self):
        self._tool_calls = Counter("agent_tool_calls", "...")
        self._latency = Histogram("agent_latency_ms", "...")

    async def export(self, trace: AgentTrace) -> None:
        self._tool_calls.inc(trace.tool_calls)
        self._latency.observe(trace.total_latency_ms)


# 用户接入 OpenTelemetry
from opentelemetry import trace

class OTelExporter:
    async def export(self, trace_data: AgentTrace) -> None:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("agent_run") as span:
            span.set_attribute("agent", trace_data.agent_name)
            span.set_attribute("tokens", trace_data.total_tokens)


container.register_trace_exporter(PrometheusExporter())
```

---

### 2.5 扩展点汇总

| 扩展点 | 协议 | 内置实现 | 谁需要实现 |
|---|---|---|---|
| **LLM** | `LLM` | `OpenAILLM` | 想换模型供应商的人 |
| **Storage** | `StateStore` | `SQLiteStateStore` | 生产环境需要高可用存储的人 |
| **Tool Hook** | `ToolHook` | `FileHookRunner`（加载 `.lania/hooks/`） | 需要审计/日志/自定义阻断逻辑的人 |
| **Observability** | `TraceExporter` | `ConsoleExporter` | 需要监控/告警/性能分析的人 |

`AgentPlatformContainer` 接受所有扩展点作为构造参数或注册方法：

```python
container = AgentPlatformContainer(
    settings=settings,
    llm=AnthropicLLM(api_key="..."),           # 扩展点 1
    store=PostgresStateStore(dsn="..."),         # 扩展点 2
)
container.register_tool_hook(AuditLogger())     # 扩展点 3
container.register_trace_exporter(OTelExporter())  # 扩展点 4

### 2.6 Agent 类型系统 — 工具可见性与权限模型

Agent 类型决定了 LLM 能「看到」什么和能「调用」什么。这是让 LLM 不跑偏的第一道闸门。

```
Agent 定义 (AgentDef)
├── agent_type: "coding" | "document_analysis" | "research" | "chat"
├── allowed_tools: [read_file, search_code, ...]    ← LLM 只能看到这些工具
├── risk_level_overrides: {shell_execute: "critical"} ← 覆盖工具默认风险等级
├── max_turns: 50                                    ← 预算上限
├── default_system_prompt: "你是 Coding Agent..."    ← 角色设定
└── hooks: [audit_all, block_dangerous]              ← 生效的钩子
```

**规则**：

1. `AgentLoop.run()` 只向 LLM 暴露 `agent_def.allowed_tools` 中的工具
2. 即使 LLM 构造了不在白名单中的工具名，`StepExecutor` 会直接拒绝执行
3. 未指定 `allowed_tools` 时，使用该 `agent_type` 的默认工具集
4. 应用层可以在注册容器时注入自定义的 `agent_type → tools` 映射

```python
# 应用层注册 agent 类型
container.register_agent_type("coding", default_tools=[
    read_file_tool, search_code_tool, run_test_tool,
])
container.register_agent_type("document", default_tools=[
    retrieve_docs_tool, summarize_tool, extract_entities_tool,
])

# 创建 agent 实例时自动继承类型默认工具集
container.create_agent({
    "name": "my-coding-agent",
    "agent_type": "coding",
    "allowed_tools": ["read_file", "search_code", "run_test"],
})
```

**为什么这是第一道闸门**：LLM 只能从它看到的工具列表中做选择。如果 coding agent 看不到 `database_query` 工具，它就不可能调用数据库。这比任何运行时检查都更根本。
```

## 三、`AgentPlatformContainer` — 唯一入口

### 3.1 构造方法

| 属性 | 类型 | 用途 |
|---|---|---|
| `.settings` | `PlatformSettings` | 平台配置（只读） |
| `.state` | `InMemoryState` | 运行时状态（热缓存） |
| `.store` | `SQLiteStateStore` | 持久化存储 |
| `.tool_registry` | `ToolRegistry` | 工具注册表 |
| `.agent_service` | `AgentService` | **Agent 对话主入口** |
| `.agent_loop` | `AgentLoop` | Brain 执行循环 |
| `.session_manager` | `SessionManager` | 会话管理 |
| `.customization_engine` | `CustomizationEngine` | 原语加载引擎 |
| `.skill_manager` | `SkillManager` | Skill 管理 |
| `.agent_def_manager` | `AgentDefManager` | Agent 定义管理 |
| `.prompt_manager` | `PromptManager` | 提示词管理 |
| `.mcp_manager` | `McpManager` | MCP 工具管理 |
| `.llm_config_manager` | `LlmConfigManager` | LLM 配置管理 |
| `.system_settings_manager` | `SystemSettingsManager` | 系统设置管理 |
| `.auth_manager` | `AuthManager` | 认证管理 |
| `.safety_engine` | `SafetyEngine` | 安全引擎 |
| `.consent_store` | `ConsentStore` | 用户确认存储 |
| `.brain_context_manager` | `BrainContextManager` | 上下文管理 |
| `.step_executor` | `StepExecutor` | 步骤执行器 |
| `.event_bus` | `EventBus` | 事件总线 |

### 2.3 公开方法

```python
class AgentPlatformContainer:
    # ── 生命周期 ──────────────────────────────────────

    def start(self, start_worker: bool = False):
        """启动平台后台服务（worker/调度器）。"""

    def shutdown(self):
        """释放后台资源。"""

    def register_default_tools(self):
        """注册平台内置工具（天气/金融/日历/计算器等 20+ 工具）。"""

    def register_tool(self, tool: AgentTool):
        """注册单个自定义工具。"""

    def register_external_services(self, services: dict[str, Any]):
        """注入应用层提供的外部服务（RAG、数据库等）。"""

    # ── 原语管理（原 admin API，现为方法调用）─────────

    # Agent 定义
    def list_agents(self) -> list[AgentDefinition]: ...
    def get_agent(self, agent_id: str) -> AgentDefinition | None: ...
    def create_agent(self, payload: dict) -> AgentDefinition: ...
    def update_agent(self, agent_id: str, payload: dict) -> AgentDefinition: ...
    def delete_agent(self, agent_id: str) -> None: ...
    def set_default_agent(self, agent_id: str) -> None: ...

    # Skill
    def list_skills(self) -> list[SkillSpec]: ...
    def get_skill(self, skill_id: str) -> SkillSpec | None: ...
    def create_skill(self, payload: dict) -> SkillSpec: ...
    def update_skill(self, skill_id: str, payload: dict) -> SkillSpec: ...
    def delete_skill(self, skill_id: str) -> None: ...

    # Prompt
    def list_prompts(self) -> list[PromptTemplate]: ...
    def get_prompt(self, prompt_id: str) -> PromptTemplate | None: ...
    def create_prompt(self, payload: dict) -> PromptTemplate: ...
    def update_prompt(self, prompt_id: str, payload: dict) -> PromptTemplate: ...
    def delete_prompt(self, prompt_id: str) -> None: ...
    def render_prompt(self, name: str, **variables) -> str: ...
    def reset_prompt(self, prompt_id: str) -> None: ...

    # LLM 配置
    def list_llm_providers(self) -> list[LlmProvider]: ...
    def set_llm_provider(self, name: str, config: dict) -> LlmProvider: ...
    def delete_llm_provider(self, name: str) -> None: ...
    def test_llm_connection(self, name: str) -> bool: ...
    def get_active_llm(self) -> str: ...
    def set_active_llm(self, name: str) -> None: ...
    def set_llm_route(self, purpose: str, provider: str) -> None: ...

    # MCP Server
    def list_mcp_servers(self) -> list[McpServerConfig]: ...
    def create_mcp_server(self, payload: dict) -> McpServerConfig: ...
    def delete_mcp_server(self, mcp_id: str) -> None: ...
    def connect_mcp(self, mcp_id: str) -> bool: ...
    def disconnect_mcp(self, mcp_id: str) -> None: ...
    def list_mcp_tools(self) -> list[dict]: ...

    # 系统设置
    def get_settings(self) -> dict: ...
    def get_setting(self, key: str) -> Any: ...
    def set_setting(self, key: str, value: Any) -> None: ...

    # Instructions
    def create_instruction(self, payload: dict) -> Instruction: ...
    def list_instructions(self) -> list[Instruction]: ...
    def delete_instruction(self, instruction_id: str) -> None: ...

    # Hooks
    def list_hooks(self) -> list[HookDefinition]: ...
    def create_hook(self, payload: dict) -> HookDefinition: ...
    def delete_hook(self, hook_id: str) -> None: ...

    # ── Agent 交互 ────────────────────────────────────

    async def process_chat(
        self,
        message: str,
        session_id: str | None = None,
        agent_name: str | None = None,
        mode: str = "auto",
    ) -> AsyncIterator[AgentEvent]:
        """核心对话入口。返回 SSE 事件流。"""

    async def execute_command(
        self,
        message: str,
        session_id: str | None = None,
    ) -> str:
        """同步执行一条命令，返回结果文本。"""
```

---

## 四、存储层设计

### 3.1 `InMemoryState` — 运行时热缓存

```python
@dataclass
class InMemoryState:
    tasks: dict[str, TaskMemoryEntry] = field(default_factory=dict)
    task_runs: dict[str, TaskRunRecord] = field(default_factory=dict)
    artifacts: dict[str, ArtifactMemoryEntry] = field(default_factory=dict)
    collections: dict[str, Any] = field(default_factory=dict)
    sessions: dict[str, Any] = field(default_factory=dict)
    query_runs: dict[str, Any] = field(default_factory=dict)
```

- 纯数据容器，无方法
- 平台模块通过 `container.state.X` 读写
- `container.store.load_into(container.state)` 在启动时从 SQLite 恢复

### 3.2 `SQLiteStateStore` — 持久化存储

统一存储所有平台数据的 SQLite 实现，31 个方法分 10 类：

| 分类 | 方法 |
|---|---|
| 生命周期 | `__init__(db_path)`, `load_into(state)`, `ping()` |
| Task | `get_task`, `upsert_task`, `list_tasks`, `claim_next_task`, `touch_task_heartbeat` |
| TaskRun | `get_task_run`, `upsert_task_run`, `list_task_runs` |
| Artifact | `get_artifact`, `upsert_artifact`, `list_artifacts`, `list_artifacts_for_task` |
| Session | `get_session`, `upsert_session`, `delete_session` |
| UserProfile | `get_user_profile`, `upsert_user_profile` |
| AgentDef | `get_agent_def`, `upsert_agent_def`, `list_agent_defs`, `delete_agent_def` |
| Skill | `get_skill`, `upsert_skill`, `list_skills`, `delete_skill`, `upsert_skill_rule`, `list_skill_rules`, `delete_skill_rule` |
| Prompt | `get_prompt`, `upsert_prompt`, `list_prompts`, `delete_prompt` |
| MCP | `get_mcp_server`, `upsert_mcp_server`, `list_mcp_servers`, `delete_mcp_server` |
| Consent | `get_consent`, `save_consent` |
| PolicyProfile | `list_policy_profiles` |

---

## 五、使用示例

### 4.1 最简集成

```python
from agent_platform import AgentPlatformContainer
from agent_platform.settings import PlatformSettings

settings = PlatformSettings(llm_api_key="sk-xxx", llm_model="gpt-4o")
container = AgentPlatformContainer(settings)
container.register_default_tools()

# 单次对话
async for event in container.process_chat(
    message="帮我分析这个项目",
    session_id="session-1",
    agent_name="coding-agent",
):
    print(event)
```

### 4.2 带外部服务

```python
container.register_external_services({
    "rag": my_rag_service,
    "database": my_db_service,
})
```

### 4.3 自定义工具注册

```python
container.register_tool(MyCustomTool())
```

### 4.4 管理原语

```python
# 查询已注册的 Agent 定义
agents = container.list_agents()

# 创建一个新的 Agent 定义
container.create_agent({
    "name": "security-review",
    "model": "gpt-4o",
    "allowed_tools": ["read_file", "search_repository"],
})

# 设置默认 LLM
container.set_active_llm("gpt-4o")
```

### 4.5 FastAPI 集成

```python
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from agent_platform import AgentPlatformContainer

app = FastAPI()
container = AgentPlatformContainer(settings)

@app.post("/v1/chat")
async def chat_endpoint(request: ChatRequest):
    return StreamingResponse(
        container.process_chat(
            message=request.message,
            session_id=request.session_id,
            agent_name=request.agent_name,
        ),
        media_type="text/event-stream",
    )

@app.get("/v1/admin/agents")
async def list_agents():
    return container.list_agents()
```

---

## 六、主应用容器改造

### 5.1 当前（改造前）

```python
# app/container.py
class AppContainer:
    def __init__(self, settings):
        self.state = InMemoryState()
        self.store = SQLiteStateStore(...)
        self.trace = TraceRecorder()
        # ... 200 行代码混在一起
        self.retrieval = RagRetrievalService(...)
        self.rag_system = RagContainer(...)
        self.weather_capability = WeatherCapability(...)
        # agent_platform 服务也在同一个 __init__ 里
        self.agent_service = AgentService(...)
        self.agent_loop = AgentLoop(...)
```

### 5.2 改造后

```python
# app/container.py
class AppContainer:
    def __init__(self, settings):
        # 共享基础设施
        self.state = InMemoryState()
        self.store = SQLiteStateStore(settings.resolved_data_dir / "app.sqlite3")
        self.trace = TraceRecorder()

        # RAG 系统
        self.rag_system = RagContainer(
            settings=RagSettings.from_app_settings(settings),
        )

        # Agent 平台（核心）
        self.platform = AgentPlatformContainer(
            settings=settings,
            state=self.state,
            store=self.store,
            llm=self.platform_llm,
        )

        # 注册平台内置工具
        self.platform.register_default_tools()

        # 注入 RAG 系统等外部服务
        self.platform.register_external_services({
            "rag_system": self.rag_system,
        })

        # 注册独立 RAG 工具
        self.platform.register_tool(RagSystemRetrieveTool())
        self.platform.register_tool(RagSystemQueryTool())
        self.platform.register_tool(RagSystemIngestTool())
```

---

## 七、文件改动清单

### 6.1 新增文件

| # | 路径 | 内容 |
|---|---|---|
| N1 | `app/agent_platform/services/_state.py` | `InMemoryState` |
| N2 | `app/agent_platform/services/_store.py` | `SQLiteStateStore` |
| N3 | `app/agent_platform/container.py` | `AgentPlatformContainer` |
| N4 | `app/agent_platform/settings.py` | `PlatformSettings`（从 `core/config.py` 抽取平台相关字段） |

### 6.2 修改文件

| # | 文件 | 改动 |
|---|---|---|
| M1 | `app/container.py` | 组合 `AgentPlatformContainer`，移除平台服务的直接初始化 |
| M2 | `app/main.py` | 移除平台 API 路由挂载（由应用层自己决定暴露什么） |
| M3-M37 | `app/agent_platform/**/*.py` 35+ 个文件 | `from app.models.*` → `from app.agent_platform.models.*` |
| M38-M51 | `app/agent_platform/**/*.py` 14+ 个文件 | `from app.services.*` → `from app.agent_platform.services._state` / `_store` |
| M52 | `app/agent_platform/agents/memory.py` | `FrontmatterParser` 引用处理 |
| M53 | 3 个引用 `app.workflows.*` 的文件 | 清理或替换 |
| M54 | `app/agent_platform/services/agent_service.py` | 移除 Legacy Path（`_process_legacy`、`_resolve_mode`、`_handle_*_mode`、`execute_command`、`execute_plan` 等关键词驱动方法），仅保留 Brain 路径（`IntentRecognizer` → `ModeRouter` → `AgentLoop`） |
| M55 | `app/container.py` | 移除 `IntentMatcher` 的创建和注入，移除 `plan_generator`/`plan_executor`/`repository`/`database` 等旧依赖向 `AgentService` 的注入 |

### 6.3 删除文件

| # | 路径 | 原因 |
|---|---|---|
| D1 | `app/agent_platform/api/` 完整目录（7 个文件） | 平台不暴露 HTTP 端点 |
| D2 | `app/agent_platform/agents/subagents.py` | 旧文档分析子 Agent |
| D3 | `app/agent_platform/agents/planner.py` | 旧 TaskPlanner |
| D4 | `app/agent_platform/agents/runtime.py` | 旧 AgentRuntime |
| D5 | `app/agent_platform/agents/artifacts.py` | 旧产物处理 |
| D6 | `app/agent_platform/task_worker.py` | 旧任务 Worker |
| D7 | `app/agent_platform/runtime_contract_adapters.py` | 旧契约适配（引用已删除的 workflow） |
| D8 | `app/agent_platform/services/intent_matcher.py` | 关键词驱动意图匹配器，已被 `IntentRecognizer`（LLM 驱动）替代 |

---

## 八、执行顺序

```
第一周：阶段一（修路）
├─ 1. N1-N2: 实现 _state.py + _store.py
├─ 2. M3-M51: 修复全部 import 路径
├─ 3. M52-M53: 处理 FrontmatterParser + workflows 引用
└─ 4. 验证:
   ├─ python -c "from app.agent_platform.agents.brain import IntentRecognizer, ModeRouter, AgentLoop" 不报错
   ├─ python -c "from app.agent_platform.services._state import InMemoryState" 不报错
   └─ python -c "from app.agent_platform.services._store import SQLiteStateStore" 不报错

第二周：阶段二（瘦身）
├─ 1. D1: 删除 api/ 目录
├─ 2. M2: 更新 main.py
├─ 3. M54-M55: 移除 Legacy Path（agent_service.py + container.py）
├─ 4. D2-D8: 删除旧文件（含 intent_matcher.py）
└─ 5. 验证:
   ├─ 旧 API 端点（/api/v1/agent/command、/api/v1/admin/agents 等）返回 404
   ├─ POST /api/v1/agent/chat 返回 SSE 流，对话功能正常
   └─ container.agent_service.process() 走 Brain 路径（可通过日志确认 IntentRecognizer 被调用）

第三周：阶段三（提取）
├─ 1. N3-N4: 创建 AgentPlatformContainer + PlatformSettings
├─ 2. M1: 改造 AppContainer 组合 AgentPlatformContainer
├─ 3. 编写 pyproject.toml
└─ 4. 验证:
   ├─ pip install -e . 安装成功
   ├─ 在新目录中执行最简集成示例（from agent_platform import AgentPlatformContainer）不报错
   ├─ container.process_chat("hello") 返回事件流
   └─ container.list_agents() 返回空列表（API 方法调用正常）
```

---

## 九、待接入能力（代码已存在，需接入平台）

以下功能代码已存在于 `app/agent_platform/` 中，但尚未在 `AgentPlatformContainer` 中接线。按优先级分三波接入。

### 9.1 第一波：核心安全 + 记忆

| # | 功能 | 代码位置 | 接入方式 |
|---|---|---|---|
| 1 | **MemoryCommitGate** | `services/memory_commit_gate.py` | `AgentPlatformContainer` 中实例化，注入 `BrainContextManager._memory_gate` |
| 2 | **UserProfileService** | `services/user_profile_service.py` | `AgentPlatformContainer` 中实例化，注入 `BrainContextManager._profile` |
| 3 | **PolicyEngine → StepExecutor** | `harness/policy.py` → `agents/brain/step_executor.py` | `StepExecutor.execute_step()` 中调 `policy_engine.evaluate()` |
| 4 | **GuardrailEngine → StepExecutor** | `harness/guardrails.py` → `agents/brain/step_executor.py` | `StepExecutor` 中调 `guardrail_engine.check_input/output()` |
| 5 | **ConsentStore 持久化** | `agents/brain/consent_store.py` | `get_consent/save_consent` 委托给 `store` |

```python
# AgentPlatformContainer 中接线示意
self.memory_commit_gate = MemoryCommitGate(
    state=self.state, store=self.store,
    session_manager=self.session_manager,
    user_profile_service=self.user_profile_service,
    llm=self.llm,
)
self.brain_context_manager = BrainContextManager(
    customization_engine=self.customization_engine,
    memory_commit_gate=self.memory_commit_gate,    # ← 接入
    user_profile_service=self.user_profile_service,  # ← 接入
    llm=self.llm,
)
```

### 9.2 第二波：可观测 + 执行增强

| # | 功能 | 代码位置 | 接入方式 |
|---|---|---|---|
| 6 | **EventBus 触发** | `harness/hooks.py` → `agents/brain/agent_loop.py` | `AgentLoop.run()` 中调 `event_bus.emit()` |
| 7 | **ToolSandbox 风险分级** | `harness/sandbox.py` → `agents/brain/step_executor.py` | 按 risk_level 决定 inline/thread/process 隔离 |
| 8 | **重试 + 熔断** | `agents/brain/circuit_breaker.py` | `StepExecutor._execute_on_server()` 加指数退避重试 |
| 9 | **委派工具注册** | `agents/tools/delegation_tools.py` | `register_default_tools()` 中加入 |

```python
# StepExecutor 中风险分级执行示意
async def _execute_on_server(self, tool_call, session):
    risk_level = getattr(tool_def, 'risk_level', 'low')

    if risk_level == "low":
        result = self._tool_registry.run(...)
    elif risk_level == "medium":
        result = await asyncio.to_thread(self._tool_registry.run, ...)
    else:  # high / critical
        result = await self._sandbox.run_in_sandbox(...)
```

### 9.3 第三波：治理 + 优化

| # | 功能 | 代码位置 | 接入方式 |
|---|---|---|---|
| 10 | **预算管理** | `agents/brain/models.py` → `agent_loop.py` | 完善 `AgentBudget`，从 `agent_def` 读取配置 |
| 11 | **速率限制** | `services/rate_limiter.py` | 通过 `ToolHook` 协议注册，不侵入核心逻辑 |
| 12 | **Agent 缓存** | `services/agent_cache.py` | `StepExecutor` 中查/写缓存，避免重复工具调用 |

```python
# 预算管理接入示意
budget = AgentBudget(
    max_steps=agent_def.max_turns if agent_def else self.MAX_TURNS,
    max_tool_calls=agent_def.max_tool_calls if agent_def else 16,
)
for turn in range(budget.max_steps):
    if budget.exceeded:
        yield AgentEvent.error(f"预算超限")
        return
```

### 9.4 不从平台接入的（应用层或外部能力）

| 功能 | 原因 |
|---|---|
| **P3-2 事件驱动触发** | cron/webhook 触发器，属于应用层调度 |
| **P3-3 Agent 评估** | 评测工具，属于测试/CI 范畴 |
| **P3-5 Agent 测试沙箱** | 同上 |
| **P3-6 人机协作** | 审批流/转人工，业务相关 |
| **P3-7 记忆污染防护** | 通过 `ToolHook` 可实现，不强制内置 |
| **P3-8 接地性** | 方案不同（要求 LLM 主动引用），不影响核心循环 |
| **P2-4 租户隔离** | 部署层关切，不是平台核心 |
| **P2-9 健康监控** | OPS 范畴，应用层自己决定 |

### 9.5 接入顺序总图

```
第一波（安全+记忆）          第二波（可观测+执行）       第三波（治理+优化）
─────────────────           ─────────────────        ─────────────────
MemoryCommitGate            EventBus 触发             预算管理
UserProfileService          ToolSandbox 分级           速率限制
PolicyEngine               重试+熔断                   Agent 缓存
GuardrailEngine             委派工具注册
ConsentStore 持久化
        │                        │                        │
        ▼                        ▼                        ▼
                    AgentPlatformContainer
```

### 9.6 每波接入的优先级理由

| 波次 | 功能 | 不接入的风险 | 接入的收益 |
|---|---|---|---|
| **第一波** | MemoryCommitGate | LLM 能在一次对话中写入错误的用户偏好或虚假记忆，污染后续所有对话；记忆永久驻留 working memory 永不清理 | 每次 memory commit 前由 LLM 审核，只有高置信度信息被持久化；working memory 定期 GC |
| **第一波** | UserProfileService | 平台不认识用户，无法区分不同用户的偏好、权限和上下文；每个对话都是"匿名模式" | 按用户记忆偏好，Agent 可个性化响应；per-user 权限边界确保用户不会越权操作 |
| **第一波** | PolicyEngine | LLM 可以自由决策执行任何操作，没有安全策略把关。即使 hook 能阻断，也缺乏结构化策略管理 | 策略集中管理（YAML），按 agent 类型/用户角色/操作风险分级控制，比 hook 的 if-else 更可维护 |
| **第一波** | GuardrailEngine | LLM 可能输出暴力、涉政、PII 等内容，没有输入/输出过滤层 | 输入 guardrail 拦截恶意 prompt 注入，输出 guardrail 过滤敏感内容，双向保护 |
| **第一波** | ConsentStore 持久化 | 用户确认记录只存在内存中，服务重启后所有 consent 丢失，高风险工具需要反复确认 | consent 持久化到 SQLite，重启后恢复，用户只需确认一次 |
| **第二波** | EventBus 触发 | AgentLoop 执行过程没有可观测性，调试困难 | 每次 tool_call、hook 阻断、mode_switch 都发出事件 |
| **第二波** | ToolSandbox 分级 | 所有工具内联执行，高风险工具（shell、文件删除等）没有隔离保障 | 低风险内联、中风险线程、高风险沙箱，执行失败不影响主进程 |
| **第二波** | 重试+熔断 | 网络抖动或 LLM 超时导致工具调用失败，不做重试直接向用户报错 | 指数退避重试 3 次，连续失败触发熔断，避免级联故障 |
| **第二波** | 委派工具注册 | 每次新增工具需要改代码、重新注册，扩展成本高 | 通过 MCP 或 register_tool() 动态注册，运行时即可生效 |
| **第三波** | 预算管理 | LLM 可能无限循环工具调用，tokens 和耗时不可控 | max_steps、max_tool_calls、max_cost 三层预算，超限主动中止 |
| **第三波** | 速率限制 | 同一用户/工具可能被高频调用，LLM 不考虑调用频率 | 滑动窗口限流，超限返回 429，LLM 收到后自行降速 |
| **第三波** | Agent 缓存 | 相同工具调用重复执行（如 list_files('/src')） | 按工具名+参数哈希缓存结果，TTL 内直接返回，减少 LLM 等待时间 |

**为什么第一波先于第二波**：没有安全（PolicyEngine + GuardrailEngine）的情况下开放可观测和沙箱，相当于先装监控再装锁。记忆污染（MemoryCommitGate）不先解决，后续的优化都在错误的数据上做。

**为什么第三波最后**：预算、限流、缓存都是优化层，底层安全+执行稳定后才需要。如果 LLM 在第三波之前就跑偏了，速率限制和缓存加速都没有意义。

---

## 十、后续补充清单

以下按优先级排列，待分批填充到文档对应章节中。

### 第一批：架构层

| # | 插入位置 | 内容 |
|---|---|---|
| D1 | 1.3 防护链（Defense Chain） | 9 层执行链路图，每层标注决策者（workflow/agent）、代码位置、数据来源、失效兜底关系 |
| D2 | 新增 1.4 防护链统一接口 | ChainLink 协议 + DefenseChain 执行器 + ChainContext 数据对象，各层适配示例 |

### 第二批：功能缺失

| # | 插入位置 | 内容 |
|---|---|---|
| D3 | 新增章节：测试基础设施 | 分层测试策略表 + FauxLLM 设计 + TestContainerBuilder + 覆盖率目标 |
| D4 | 新增章节：Context Compaction | Compactor 协议 + 两种内置策略（summarize/prune）+ 集成到 AgentLoop |

### 第三批：约束优化

| # | 插入位置 | 内容 |
|---|---|---|
| D5 | 防护链 → 新增 Constraint Escalation 小节 | block_type（soft/hard）+ 用户确认 bypass + 结构化错误反馈到 LLM + 三层规则表 |
| D6 | 防护链 → 新增 9 层冗余表 | 每层可能漏放什么、被哪层兜底 |

### 第四批：小优化

| # | 插入位置 | 内容 |
|---|---|---|
| D7 | 扩展点 → Tool DI | ToolContext[Generic[T]] 泛型改造，提升类型安全 |
| D8 | Agent 类型系统 → 继承关系 | 强化 AgentDef.agent_type 如何继承类型默认工具集 |
| D9 | 工具定义 → execution_mode | ToolDef 加 execution_mode: sequential/parallel 字段 |
| D10 | AgentPlatformContainer → 预算参数 | max_steps、max_tool_calls 从第三波待接入提升为构造参数 |

### 第五批：复杂场景扩展

| # | 插入位置 | 内容 |
|---|---|---|
| D11 | 新增章节：Replan 循环 | 在 AgentLoop 中增加 _should_replan() 判断（意外发现/工具失败/新方向/计划耗尽四种触发条件），以及 TaskDecomposer 将复杂任务分解为 3-5 个可并行子任务 |
| D12 | 新增章节：AgentOrchestrator | 管理子 agent 完整生命周期的编排器，支持 spawn()（串行）和 spawn_parallel()（并行）；SubAgentDef 独立配置工具白名单/system prompt/budget；SubAgentResult 结构化返回协议（status/summary/artifacts/suggestions） |
| D13 | 已有 DelegationTool → 扩展为多 agent 委派 | 现有 delegation_tools.py 中增加 DelegateTool，主 agent 通过普通工具调用即可委派子任务 |
| D14 | 新增章节：三层记忆架构 | FactMemory（结构化事实）、EpisodicMemory（向量检索对话历史）、EntityMemory（实体关系图）；MemoryCommitGate 升级为从对话中自动抽取三层次信息 |

### 第六批：现有架构强化

| # | 插入位置 | 内容 |
|---|---|---|
| D15 | 防护链 → StepExecutor 中增加 replan 触发点 | replan 检测点插入防护链的 post_exec 层 |
| D16 | AgentType → 子 agent 继承规则 | 定义子 agent 默认继承父级的哪些配置（防护链/记忆/策略），哪些可以覆盖 |
