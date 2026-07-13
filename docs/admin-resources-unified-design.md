# 管理面资源统一设计方案

## Context

当前项目有四种用户可配置资源（Agents、Skills、Prompts、MCPs），它们分别由不同的 Manager 管理，使用不同的存储方式（`ConfigStore` 通用 KV 表 vs 内存），且均未深度集成到运行时执行流程中。需要统一设计这四类资源的持久化、传参和按需加载方案。

## 设计原则

1. **统一存储模式**：全部走 `SQLiteStateStore` 的 `{id} TEXT PRIMARY KEY, payload TEXT NOT NULL` JSON 模式
2. **统一 ID 生成**：`{prefix}-{uuid4().hex[:12]}` 格式
3. **统一 API 风格**：RESTful CRUD + 批量导入
4. **按需加载**：运行时通过 ID 从 DB 加载，支持缓存

---

## 一、四类资源对比

| 维度 | Agent | Skill | Prompt | MCP |
|------|-------|-------|--------|-----|
| 用途 | 定义 AI 行为（指令+工具+模型） | 定义可复用指令集（含子规则） | 定义提示词模板 | 定义外部工具服务器连接 |
| 主键 | `agent_id` | `skill_id` | `prompt_id` | `mcp_id` |
| ID 前缀 | `agt-` | `sk-` | `prt-` | `mcp-` |
| 版本 | ✅ | ✅ | ✅ | ❌（连接配置不需版本） |
| 子表 | 无（扁平结构） | `skill_rules` (1:N) | 无（扁平结构） | 无（纯内存工具列表） |
| 运行时加载 | AgentService 根据请求中的 agent_id 加载 | Capability/Tool 执行时按需加载 | RAG 流程中按 name 获取 | 请求时 connect，工具调用时查找 |

---

## 二、数据库表设计

### 全部走 `SQLiteStateStore` 统一模式

**`skills` 表（已有设计，确认）：**

```sql
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
-- payload: {id, name, version, description, instructions, task_types, tools, source, created_at, updated_at}
```

**`skill_rules` 表（已有设计，确认）：**

```sql
CREATE TABLE IF NOT EXISTS skill_rules (
    rule_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
-- payload: {id, skill_id, name, apply_to, content, order, created_at}
```

**`agent_defs` 表（新建）：**

```sql
CREATE TABLE IF NOT EXISTS agent_defs (
    agent_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
-- payload: {id, name, display_name, description, instructions, skills, allowed_tools, model, temperature, max_turns, is_default, version, created_at, updated_at}
```

**`prompts` 表（新建）：**

```sql
CREATE TABLE IF NOT EXISTS prompts (
    prompt_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
-- payload: {id, name, description, template, variables, is_builtin, version, created_at, updated_at}
```

**`mcp_servers` 表（新建）：**

```sql
CREATE TABLE IF NOT EXISTS mcp_servers (
    mcp_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
-- payload: {id, name, server_type, url, command, args, enabled, status, tools_count, error, created_at, updated_at}
```

### SQLiteStateStore 新增方法

每种资源提供 4 个基础方法 + 1 个列表方法：

```python
# Skills
upsert_skill(record) / delete_skill(skill_id) / get_skill(skill_id) / list_skills()
upsert_skill_rule(record) / delete_skill_rule(rule_id) / list_skill_rules()

# Agents
upsert_agent_def(record) / delete_agent_def(agent_id) / get_agent_def(agent_id) / list_agent_defs()

# Prompts
upsert_prompt(record) / delete_prompt(prompt_id) / get_prompt(prompt_id) / list_prompts()

# MCPs
upsert_mcp_server(record) / delete_mcp_server(mcp_id) / get_mcp_server(mcp_id) / list_mcp_servers()
```

---

## 三、Pydantic 模型设计

### Agent

```python
class AgentDefinition(BaseModel):
    id: str = Field(default_factory=lambda: f"agt-{uuid4().hex[:12]}")
    name: str                                    # 唯一标识名
    display_name: str = ""                       # 前端展示名
    description: str = ""
    instructions: str = ""                       # 系统提示词
    skills: list[str] = Field(default_factory=list)  # 绑定的 skill name 列表
    allowed_tools: list[str] | None = None       # 工具白名单
    model: str | None = None                     # 指定模型
    temperature: float = 0.7
    max_turns: int = 10
    is_default: bool = False
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

### Skill

```python
class SkillRule(BaseModel):
    id: str = Field(default_factory=lambda: f"skr-{uuid4().hex[:12]}")
    skill_id: str
    name: str                                    # 如 "00-base"
    apply_to: str = "**/*"
    content: str = ""
    order: int = 0
    created_at: datetime = Field(default_factory=datetime.now)

class SkillDefinition(BaseModel):
    id: str = Field(default_factory=lambda: f"sk-{uuid4().hex[:12]}")
    name: str
    version: int = 1
    description: str = ""
    instructions: str = ""
    task_types: list[str] = Field(default_factory=list)
    tools: list[str] | None = None
    source: Literal["builtin", "file", "api"] = "api"
    rules: list[SkillRule] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

### Prompt

```python
class PromptTemplate(BaseModel):
    id: str = Field(default_factory=lambda: f"prt-{uuid4().hex[:12]}")
    name: str
    description: str = ""
    template: str = ""                           # 含 {variable} 占位符
    variables: list[str] = Field(default_factory=list)
    is_builtin: bool = False
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

### MCP

```python
class McpServerConfig(BaseModel):
    id: str = Field(default_factory=lambda: f"mcp-{uuid4().hex[:12]}")
    name: str
    server_type: Literal["url", "stdio"] = "url"
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    enabled: bool = True
    status: str = "disconnected"                 # connected | disconnected | error
    tools_count: int = 0
    error: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
```

---

## 四、API 设计（统一风格）

### Agents API: `prefix="/admin/agents"`

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| `GET` | `/admin/agents` | - | `list[AgentDefinition]` |
| `POST` | `/admin/agents` | `AgentDefinition`（不含 id） | `AgentDefinition`（含 id, version=1） |
| `GET` | `/admin/agents/{agent_id}` | - | `AgentDefinition` |
| `PUT` | `/admin/agents/{agent_id}` | `AgentDefinition`（覆盖） | `AgentDefinition`（version+1） |
| `DELETE` | `/admin/agents/{agent_id}` | - | `{"status": "ok"}` |
| `POST` | `/admin/agents/{agent_id}/activate` | - | `{"status": "ok", "default_agent": name}` |

### Skills API: `prefix="/admin/skills"`

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| `GET` | `/admin/skills` | - | `list[SkillDefinition]`（含 rules） |
| `POST` | `/admin/skills` | `SkillCreateRequest` | `SkillDefinition` |
| `GET` | `/admin/skills/{skill_id}` | - | `SkillDefinition` |
| `PUT` | `/admin/skills/{skill_id}` | `SkillCreateRequest` | `SkillDefinition`（version+1） |
| `DELETE` | `/admin/skills/{skill_id}` | - | `{"status": "ok"}` |
| `POST` | `/admin/skills/import` | `{"path": "..."}` 或 `{"format": "json", "data": {...}}` | `SkillDefinition` |

### Prompts API: `prefix="/admin/prompts"`

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| `GET` | `/admin/prompts` | - | `list[PromptTemplate]` |
| `POST` | `/admin/prompts` | `PromptTemplate`（不含 id） | `PromptTemplate` |
| `GET` | `/admin/prompts/{prompt_id}` | - | `PromptTemplate` |
| `PUT` | `/admin/prompts/{prompt_id}` | `PromptTemplate` | `PromptTemplate`（version+1） |
| `DELETE` | `/admin/prompts/{prompt_id}` | - | `{"status": "ok"}` |
| `POST` | `/admin/prompts/{prompt_id}/reset` | - | `PromptTemplate`（恢复内置默认） |

### MCPs API: `prefix="/admin/mcp"`

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| `GET` | `/admin/mcp/servers` | - | `list[McpServerConfig]` |
| `POST` | `/admin/mcp/servers` | `McpServerConfig`（不含 id） | `McpServerConfig` |
| `GET` | `/admin/mcp/servers/{mcp_id}` | - | `McpServerConfig` |
| `PUT` | `/admin/mcp/servers/{mcp_id}` | `McpServerConfig` | `McpServerConfig` |
| `DELETE` | `/admin/mcp/servers/{mcp_id}` | - | `{"status": "ok"}` |
| `POST` | `/admin/mcp/servers/{mcp_id}/connect` | - | `{"status": "ok", "tools_count": N}` |
| `POST` | `/admin/mcp/servers/{mcp_id}/disconnect` | - | `{"status": "ok"}` |
| `GET` | `/admin/mcp/tools` | - | `list[McpToolDef]` |

---

## 五、运行时按需加载设计

### 核心思路：两阶段加载 + Token 预算控制

**问题：** 系统可能同时存在 10 个 Skill、5 个 MCP Server、20 个 Prompt、5 个 Agent。如果全部加载到每次请求的上下文窗口，token 消耗会爆炸。

**解法：** 每次请求只加载一个 Agent 的上下文，Skill 采用两阶段加载（先加载路由表，再按需加载规则），Prompt 和 MCP 工具按需调用。

```
每次请求的资源范围（Scope）：
┌────────────────────────────────────────────────────────────┐
│ 请求 { agent_id, message, mcp_config? }                    │
│                                                            │
│ Agent（1个）── 用户选择的那个，或默认 Agent                  │
│   ├─ Skills（N个）── Agent 绑定的，两阶段加载                │
│   │   ├─ Phase 1: 全量加载 instructions（路由表，轻量）      │
│   │   └─ Phase 2: LLM 按需选规则（token 预算内）             │
│   ├─ Tools ── Agent.allowed_tools 白名单                    │
│   │   ├─ 内置工具（code_review, shell, ...）                │
│   │   └─ MCP 工具（连接时自动注册）                          │
│   ├─ Prompts ── 按需渲染，不预加载                          │
│   └─ Model ── Agent 指定或默认                              │
│                                                            │
│ 不在范围内的：其他 Agent、其他 Skill、未连接的 MCP Server     │
│ → 这些完全不会进入上下文，token 消耗为 0                       │
└────────────────────────────────────────────────────────────┘
```

### Token 预算管理（正确设计：扩展清单 + 按需加载）

**核心原则：** 给大模型一份"菜单"（名字+描述），大模型自己决定需要哪些扩展，按需调用工具加载。

**四类扩展的处理方式：**

| 扩展类型 | 在清单中？ | 谁加载？ | 加载什么？ |
|---------|---------|--------|----------|
| **Skill** | ✅ 名字+描述 | 大模型调用 `load_extension` | SKILL.md 路由表 → 大模型再调用 `load_rule` 加载具体规则 |
| **MCP** | ✅ 名字+描述 | 大模型调用 `load_extension` | 连接 MCP Server，返回可用工具列表 |
| **Agent** | ✅ 名字+描述 | 大模型调用 `load_extension` | Agent 指令 |
| **Prompt** | ❌ 不在清单中 | 系统处理 | 系统渲染模板后传给大模型 |

**工作流程：**

```
┌─────────────────────────────────────────────────────────────────────┐
│ 系统提示词（固定，~1K tokens，每次发送）                              │
│                                                                      │
│ Agent 指令: "你是编码助手..."                                         │
│                                                                      │
│ ## 可用扩展                                                          │
│ ### Skills                                                           │
│ - `ai-coding-rules`: 编码规则 (TS/Python/Rust/Go)                    │
│ - `debug-tools`: 调试排查流程                                         │
│ ### MCP 工具                                                         │
│ - `github-mcp`: url 连接                                              │
│                                                                      │
│ 使用 load_extension(name, type) 加载扩展，type: skill | mcp | agent   │
│                                                                      │
│ 清单 = ~50 tokens/扩展，10 个扩展 ≈ 500 tokens                        │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 用户: "帮我写 Python 代码"                                           │
│                                                                      │
│ 大模型思考：需要编码规则 → 调用 load_extension("ai-coding-rules",     │
│           "skill") → 获得 SKILL.md 路由表 → 决定需要 Python 规则      │
│           → 调用 load_rule("ai-coding-rules", "10-python")            │
│           → 获得 Python 规则内容                                       │
│                                                                      │
│ 未选中的 11 个规则文件 + 其他 skill → 0 token 消耗                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Token 消耗对比：**

| 场景 | 全量加载（旧方案） | 清单+按需（新方案） | 节省 |
|------|-----------------|-----------------|------|
| 10 个 skill，每个 13 条规则 | ~150K tokens | 清单 500 + 按需加载 2K | **98%** |
| 只写 Python 代码 | 全量规则（含 TS/Go/Rust） | 只加载 Python 规则 | **90%+** |
| 不需要任何 skill | 150K tokens 浪费 | 500 tokens | **99.7%** |

**关键实现：**

- `ExtensionCatalog.build_catalog()` → 构建轻量清单
- `LoadExtensionTool` / `LoadRuleTool` → 注册为 LLM 可调用的工具
- 大模型通过 tool calling 机制按需加载，内容进入对话历史
- 加载后的内容在对话历史中，后续轮次可引用，不需重复加载

```
每次请求的上下文窗口分配：
┌──────────────────────────────────────────────────────┐
│ 总预算: ~128K tokens (以 GPT-4o 为例)                 │
│                                                      │
│ 系统提示词固定部分:    ~2K  (Agent.instructions)      │
│ Skill 路由表:          ~3K  (所有 skill 的 instructions)│
│ Skill 规则内容:        ~8K  (Phase 2 按需加载，有上限)  │
│ 工具描述:              ~2K  (allowed_tools + MCP)     │
│ 对话历史:              ~20K (滑动窗口)                 │
│ 用户消息 + 附件:        ~10K                          │
│ LLM 输出预留:          ~8K                           │
│ ──────────────────────────────────────────────────── │
│ 剩余预算:              ~75K (用于工具调用结果等)       │
└──────────────────────────────────────────────────────┘
```

**Skill 规则加载的 Token 控制策略：**

| 策略 | 描述 |
|------|------|
| **Phase 1 全量加载路由表** | 每个 Skill 只加载其 `instructions`（SKILL.md 正文），不含 rules 内容。这相当于"目录"，告诉 LLM 有哪些规则可用。通常每个 Skill 的路由表只有 200-500 tokens |
| **Phase 2 按需加载规则** | LLM 根据任务类型判断需要哪些规则，系统只加载匹配的规则文件。例如 "写 Python" → 只加载 `10-python.instructions.md`，不加载 `01-typescript`、`08-dart` 等 |
| **规则预算上限** | 单次请求最多加载 8K tokens 的规则内容，超出部分按优先级裁剪 |
| **规则优先级** | 0. 基础规则（`00-base`）始终加载；1. 任务类型匹配的规则（如 Python 任务 → Python 规则）；2. 其他规则不加载 |

### 多 Agent / 多 Skill 的选择与调度机制

**核心设计原则：Agent 是顶层选择器，LLM 在限定范围内自主决策。**

```
                          ┌─────────────────────────┐
                          │    用户请求到达           │
                          │  {agent_id, message}     │
                          └───────────┬─────────────┘
                                      │
                          ┌───────────▼─────────────┐
                          │  Agent 选择（谁处理？）    │
                          │                          │
                          │  agent_id 指定 → 直接加载  │
                          │  无 agent_id → 加载默认    │
                          │  无默认     → 内置兜底     │
                          └───────────┬─────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                  ▼
              ┌──────────┐    ┌──────────────┐   ┌──────────────┐
              │  Agent A  │    │   Agent B    │   │   Agent C    │
              │  (编码)   │    │  (数据分析)   │   │  (通用对话)   │
              │ skills:   │    │ skills:      │   │ skills: []   │
              │  - ai-    │    │  - data-     │   │ tools: null  │
              │   coding  │    │   analysis   │   │ model: gpt   │
              │ tools:    │    │ tools:       │   │              │
              │  [shell,  │    │  [sql,chart] │   │              │
              │   code]   │    │ model: gpt4  │   │              │
              └─────┬─────┘    └──────┬───────┘   └──────┬───────┘
                    │                 │                   │
                    ▼                 ▼                   ▼
              ┌──────────────────────────────────────────────┐
              │         构建该 Agent 的运行时上下文             │
              │                                              │
              │  系统提示词 = Agent.instructions               │
              │             + 各 Skill 的 instructions        │
              │             + 各 Skill 的 rules 内容           │
              │                                              │
              │  可用工具 = Agent.allowed_tools ∩ 全局工具     │
              │           + MCP 连接提供的工具                  │
              │                                              │
              │  LLM 模型 = Agent.model ?? 默认模型            │
              └──────────────────────┬───────────────────────┘
                                     │
                                     ▼
              ┌──────────────────────────────────────────────┐
              │        LLM 自主决策（在限定范围内）             │
              │                                              │
              │  "用户让我审查代码"                             │
              │  → 我加载了 ai-coding-rules skill             │
              │  → SKILL.md 告诉我：代码审查时加载              │
              │    rules/00-base + rules/01-typescript        │
              │  → 我需要调用 code_review 工具                  │
              │  → 这个工具在 allowed_tools 白名单中 ✓          │
              │  → 执行                                           │
              └──────────────────────────────────────────────┘
```

**各层选择逻辑详解：**

| 层级 | 谁决定 | 决策依据 | 例子 |
|------|--------|---------|------|
| **Agent 选择** | 用户/前端 | 用户从下拉列表选，或 API 传 `agent_id`，或无指定时用 `is_default=true` 的 Agent | 用户选了 "编码助手" Agent |
| **Skill 加载** | 系统（自动） | Agent 定义中 `skills: ["ai-coding-rules", "project-coder"]` → 全部加载，Skill 自己的 `instructions` 里写了何时用哪个 rule | ai-coding-rules 的 SKILL.md 写了 "TypeScript 相关 → 加载 rules/01-typescript" |
| **Rule 选择** | LLM | Skill 的 instructions 告诉 LLM 判断规则，LLM 根据任务类型决定加载哪些 rule 文件 | "帮我写个 Python 脚本" → LLM 看到 `10-python.instructions.md` 匹配 → 遵循 Python 规则 |
| **工具选择** | LLM | `allowed_tools` 白名单限定了可用工具范围，LLM 在范围内选择 | `allowed_tools: ["shell", "code_review"]` → LLM 只能用这两个 |
| **MCP 工具** | 系统 + LLM | 请求中携带 `mcp_config` 时自动连接，工具注册后 LLM 可调用 | 连接了 GitHub MCP Server → LLM 可调用 `github:create_issue` |
| **模型选择** | 系统（自动） | `Agent.model` 有值用 Agent 的，否则用系统默认模型 | Agent 指定了 `claude-4` → 用 Claude |

**多 Agent 并存场景：**

```
用户 A: "帮我审查这段 TypeScript 代码"
  → 前端选了 "代码审查 Agent" (agent_id=agt-001)
  → Agent 绑定了 skills: ["ai-coding-rules"]
  → tools: ["code_review", "extract_issues"]
  → model: "gpt-4o"

用户 B: "分析这个 CSV 数据的趋势"
  → 前端选了 "数据分析 Agent" (agent_id=agt-002)
  → Agent 绑定了 skills: ["data-analysis"]
  → tools: ["sql", "chart", "extract_key_points"]
  → model: "gpt-4o"

两个 Agent 同时存在，互不干扰，各自有独立的：
  - 系统提示词（instructions + skills 内容）
  - 工具白名单（allowed_tools）
  - LLM 模型
```

**Skill 内的规则路由（LLM 自主判断）：**

以 `ai-coding-rules` 为例，它的 `SKILL.md` 写了：

```markdown
## 何时加载什么规则文件
- TypeScript / TSX 相关代码：加载 rules/01-typescript.instructions.md
- Python 相关：加载 rules/10-python.instructions.md
- 调试问题：加载 rules/12-debug.instructions.md
```

这些 rules 全量注入到系统提示词中，LLM 看到后自己判断：
- 用户说 "写 Python" → LLM 自动遵循 `10-python.instructions.md` 的规则
- 用户说 "修 TypeScript bug" → LLM 自动遵循 `01-typescript.instructions.md` + `12-debug.instructions.md`

**不需要额外写"选择逻辑"代码**——LLM 天然具备上下文理解能力，Skill 的 instructions 就是路由规则。

### 具体流程

**1. Agent 加载（AgentService.process）—— 两阶段**

```python
async def process(self, request: AgentChatRequest):
    # ── Phase 0: 确定 Agent（1个） ──
    agent_def = await self._resolve_agent(request.agent_id)
    # 只加载一个 Agent，其他 Agent 完全不进入上下文
    
    # ── Phase 1: 轻量加载（路由表 + 工具描述）──
    # Skill 路由表：只加载 instructions，不含 rules 正文
    skill_routing_table = await self._build_skill_routing_table(agent_def)
    # 工具描述：只加载白名单内的工具名 + 描述
    tool_descriptions = await self._build_tool_descriptions(agent_def)
    # MCP 工具：连接时注册，工具描述自动加入
    if request.mcp_config:
        await self._mcp_manager.connect(request.mcp_config)
    
    system_prompt = self._assemble_system_prompt(
        agent_def.instructions,      # ~2K tokens
        skill_routing_table,          # ~3K tokens (路由表，不含 rules)
        tool_descriptions,            # ~2K tokens
    )
    # 此时系统提示词约 7K tokens，远低于 128K 上限
    
    # ── Phase 2: 按需加载规则（LLM 决定）──
    # LLM 看到路由表后，在对话中判断需要哪些规则
    # 例如：LLM 回复 "我需要加载 Python 编码规则"
    # → 系统调用 _load_skill_rules(["ai-coding-rules/10-python"])
    # → 只追加 ~1K tokens，而非全部 13 个规则文件
```

**2. Skill 两阶段加载**

```python
class SkillManager:
    # Phase 1: 构建路由表（轻量，不含规则正文）
    async def build_routing_table(self, skill_names: list[str]) -> str:
        """只加载 instructions（路由表），不加载 rules 正文。
        
        输出示例：
        ## Skill: ai-coding-rules
        这是一个通用的 AI 编码规则 skill。
        可用规则：00-base (基础), 01-typescript, 10-python, 12-debug
        何时加载：TypeScript → 01-typescript; Python → 10-python; ...
        """
        parts = []
        for name in skill_names:
            skill = await self.get_by_name(name)
            if skill:
                # 只加载 instructions + 规则列表摘要
                rule_names = [r.name for r in skill.rules]
                parts.append(
                    f"## Skill: {skill.name}\n"
                    f"{skill.instructions}\n"
                    f"可用规则: {', '.join(rule_names)}\n"
                )
        return "\n\n".join(parts)
    
    # Phase 2: 按需加载规则（LLM 决定后加载）
    async def load_rules(
        self, 
        requests: list[tuple[str, str]],  # [(skill_name, rule_name), ...]
        max_tokens: int = 8000,
    ) -> str:
        """按需加载指定规则，有 token 预算上限。
        
        Args:
            requests: 要加载的规则列表，如 [("ai-coding-rules", "10-python")]
            max_tokens: 最大 token 预算，超出后按优先级裁剪
        
        Returns:
            拼接后的规则内容。
        """
        loaded = []
        total_chars = 0
        char_budget = max_tokens * 3  # 粗略估算：1 token ≈ 3 字符
        
        for skill_name, rule_name in requests:
            skill = await self.get_by_name(skill_name)
            if not skill:
                continue
            for rule in skill.rules:
                if rule.name == rule_name:
                    chunk = f"\n## Rule: {rule.name}\n{rule.content}\n"
                    if total_chars + len(chunk) > char_budget:
                        break  # 超出预算，停止加载
                    loaded.append(chunk)
                    total_chars += len(chunk)
                    break
        
        return "\n".join(loaded)
```

**3. Prompt 按需加载（不预加载，用时才取）**

```python
class PromptManager:
    async def render(self, name: str, **variables) -> str:
        """按 name 加载模板并填充变量。
        
        Prompt 完全不预加载到系统提示词中。
        只在具体工作流步骤需要时才调用 render()。
        """
        tpl = await self.get_by_name(name)
        if not tpl:
            tpl = _BUILTIN_TEMPLATES.get(name)
        if not tpl:
            raise ValueError(f"Prompt '{name}' not found")
        return tpl.template.format(**variables)
```

**4. MCP 按需连接（工具描述自动注册，LLM 按需调用）**

```python
class McpManager:
    async def get_or_connect(self, mcp_id: str) -> list[McpToolDef]:
        """按 ID 加载 MCP 配置并连接。
        
        MCP 工具描述会自动加入工具列表，但只有 LLM 决定调用时
        才会实际执行工具，不会消耗额外 token 在未使用的工具上。
        """
        config = await self._persistence.get_mcp_server(mcp_id)
        if not config:
            raise ValueError(f"MCP server '{mcp_id}' not found")
        if config["name"] in self._servers:
            return [t for t in self._tools.values() if t.server == config["name"]]
        return await self.connect({"mcpServers": {config["name"]: config}})
```

**5. 四种资源的 Token 消耗对比**

| 资源类型 | 加载方式 | Token 消耗 | 何时加载 |
|---------|---------|-----------|---------|
| **Agent** | 全量加载 | ~2K | 请求开始时 |
| **Skill 路由表** | 全量加载 | ~3K（只有 instructions） | Phase 1 |
| **Skill 规则** | 按需加载 | 0~8K（有预算上限） | Phase 2，LLM 决定 |
| **Prompt** | 按需渲染 | 0~2K（用时才取） | 工作流步骤中 |
| **MCP 工具描述** | 连接时注册 | ~0.2K/工具 | 连接时 |
| **MCP 工具调用** | LLM 按需调用 | 仅工具结果占用 | 对话中 |
| **其他 Agent** | 不加载 | **0** | 永远不会 |
| **其他 Skill** | 不加载 | **0** | 永远不会 |
| **未连接 MCP** | 不加载 | **0** | 永远不会 |

**关键结论：** 系统中可以注册 100 个 Skill、50 个 Agent、30 个 MCP Server，但每次请求只会实际消耗 ~7K（固定）+ 0~8K（按需规则）+ 工具调用结果 ≈ 最多 20K tokens 用于资源加载，其余 100K+ 留给对话和工具调用。**未选中的资源完全零消耗。**

---

## 六、改造实施顺序

### 阶段 1：数据层（纯新增，无破坏性）

1. `app/services/sqlite_store.py` — 新增 `skills`, `skill_rules`, `agent_defs`, `prompts`, `mcp_servers` 五张表 + 对应 CRUD 方法

### 阶段 2：模型层

2. `app/models/admin.py` — 重设计四类模型，增加 `id` 和 `version` 字段

### 阶段 3：Manager 层（逐个改造）

3. `app/services/skill_manager.py` — 重写，切换到 `SQLiteStateStore`，支持文件导入
4. `app/services/agent_def_manager.py` — 重写，切换到 `SQLiteStateStore`，增加 version
5. `app/services/prompt_manager.py` — 重写，切换到 `SQLiteStateStore`，保留内置模板
6. `app/services/mcp_manager.py` — 重写，增加 `SQLiteStateStore` 持久化

### 阶段 4：API 层

7. `app/api/v1/endpoints/admin_skills.py` — 更新端点
8. `app/api/v1/endpoints/admin_agents.py` — 更新端点
9. `app/api/v1/endpoints/admin_prompts.py` — 更新端点
10. `app/api/v1/endpoints/admin_mcp.py` — 更新端点

### 阶段 5：运行时集成

11. `app/services/agent_service.py` — 集成 Agent/Skill/Prompt 按需加载
12. `app/container.py` — 更新所有 Manager 初始化，传入 `persistence` 参数

### 阶段 6：迁移

13. 各 Manager 的 `__init__` 中实现 `ConfigStore` → 新表的一次性迁移逻辑

---

## 七、验证

```bash
# 启动应用
.venv/bin/python -m uvicorn app.main:app --reload

# Agent CRUD
curl -X POST http://localhost:8000/api/v1/admin/agents \
  -H "Content-Type: application/json" \
  -d '{"name":"coder","instructions":"You are a coding assistant","skills":["ai-coding-rules"]}'

# Skill 导入
curl -X POST http://localhost:8000/api/v1/admin/skills/import \
  -H "Content-Type: application/json" \
  -d '{"path": ".agents/skills/ai-coding-rules"}'

# Prompt 创建
curl -X POST http://localhost:8000/api/v1/admin/prompts \
  -H "Content-Type: application/json" \
  -d '{"name":"my-prompt","template":"Hello {name}","variables":["name"]}'

# MCP 服务器注册
curl -X POST http://localhost:8000/api/v1/admin/mcp/servers \
  -H "Content-Type: application/json" \
  -d '{"name":"my-server","server_type":"url","url":"http://localhost:8080/sse"}'

# 运行时：使用指定 Agent 对话
curl -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"agt-xxx","message":"帮我审查这段代码"}'
```