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

---

## 八、执行顺序

```
第一周：阶段一（修路）
├─ 1. N1-N2: 实现 _state.py + _store.py
├─ 2. M3-M51: 修复全部 import 路径
├─ 3. M52-M53: 处理 FrontmatterParser + workflows 引用
└─ 4. 验证: python -c "from app.agent_platform import *" 不报错

第二周：阶段二（瘦身）
├─ 1. D1: 删除 api/ 目录
├─ 2. M2: 更新 main.py
├─ 3. D2-D7: 删除旧文件
└─ 4. 验证: 启动应用，对话功能正常

第三周：阶段三（提取）
├─ 1. N3-N4: 创建 AgentPlatformContainer + PlatformSettings
├─ 2. M1: 改造 AppContainer 组合 AgentPlatformContainer
├─ 3. 编写 pyproject.toml
└─ 4. 验证: pip install 后能在新项目中使用
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
