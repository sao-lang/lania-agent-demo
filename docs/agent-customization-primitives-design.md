# Agent 定制化原语系统设计

> 将 IDE 领域的 AI Agent 定制化原语（Instructions / File Instructions / Prompts / Skills / Custom Agents / Hooks / MCP Server）映射到后端 Agent 运行时，构建一套完整的、文件驱动 + API 可管理的扩展体系。

---

## 目录

1. [现状盘点](#1-现状盘点)
2. [总体架构](#2-总体架构)
3. [原语详解](#3-原语详解)
4. [执行模型](#4-执行模型)
5. [文件目录规范](#5-文件目录规范)
6. [数据模型](#6-数据模型)
7. [架构合规性分析](#7-架构合规性分析)
8. [实施路线图](#8-实施路线图)

---

## 1. 现状盘点

当前项目已有大量基础设施，以下是完整映射评估：

| 原语 | IDE 形态 | 项目已有程度 | 关键差异 |
|------|----------|-------------|---------|
| **Instructions** | `.github/copilot-instructions.md` / `AGENTS.md` | ❌ 未实现 | 只有 `session.context` 字段，无文件加载机制 |
| **File Instructions** | `.github/instructions/*.instructions.md` + `applyTo` | ⚡ 部分 | Skill rules 已有 `apply_to` 字段，但无独立文件扫描器 |
| **Prompts** | `.github/prompts/*.prompt.md` + `/` 触发 | ✅ `PromptManager` | 通过 API CRUD，缺文件导入 |
| **Skills** | `.github/skills/<name>/SKILL.md` + rules/ | ✅ `SkillManager` + `ExtensionCatalog` | 已完整实现文件导入 |
| **Custom Agents** | `.github/agents/*.agent.md` | ⚡ `AgentDefManager` | 通过 API CRUD，缺 `agent.md` 文件导入 |
| **Hooks** | `.github/hooks/*.json` + JSON Schema | ⚡ `EventBus` + `HookRegistry` | 只有代码注册，缺文件配置 |
| **MCP Server** | `mcpServers` 配置 + 标准协议 | ✅ `McpManager` | 已完整实现 |

**核心缺失**：缺少一个 **`CustomizationEngine`** 来统一加载 `.agents/` 目录下所有原语，在 Agent 初始化时自动组装。

---

## 2. 总体架构

### 2.1 架构分层

```text
┌──────────────────────────────────────────────────────────────────┐
│                     API 管理层                                     │
│     FastAPI 端点 (admin/*)：CRUD + 文件导入 + 预览                  │
├──────────────────────────────────────────────────────────────────┤
│                     原语管理层                                      │
│  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌─────────────────┐  │
│  │ InstManager│ │FileInstMgr │ │PromptMgr │ │  SkillManager   │  │
│  └────────────┘ └────────────┘ └──────────┘ └─────────────────┘  │
│  ┌────────────┐ ┌────────────┐ ┌─────────────────────────┐      │
│  │AgentDefMgr │ │HookManager │ │      McpManager         │      │
│  └────────────┘ └────────────┘ └─────────────────────────┘      │
├──────────────────────────────────────────────────────────────────┤
│                     CustomizationEngine                            │
│     `.agents/` 目录扫描 → 文件解析 → 缓存管理 → 运行时组装        │
├──────────────────────────────────────────────────────────────────┤
│                     运行时注入层                                     │
│     AgentService / SessionManager / ExecutionHarness               │
│     ↓ 注入到 ToolContext / System Prompt / LLM Messages            │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 核心设计原则

1. **文件优先，API 补充**：`.agents/` 目录是事实来源（source of truth），API 写入到 DB
2. **目录即注册表**：在 `.agents/` 中放一个文件 = 注册一个原语
3. **懒加载**：轻量清单始终在系统提示词中，完整内容按需加载（已有 `ExtensionCatalog`)
4. **双层存储**：内置（代码硬编码） + 用户自定义（文件/DB），用户覆盖内置
5. **YAML Frontmatter**：所有原语文件使用标准 frontmatter 元数据

### 2.3 `.agents/` 目录结构

```text
.agents/
├── AGENTS.md                          ← 项目级系统指令（始终加载）
├── instructions/                      ← 文件指令（按 applyTo 匹配）
│   ├── python.instructions.md
│   ├── sql.instructions.md
│   └── api-design.instructions.md
├── prompts/                           ← 快捷提示模板（按 / 触发或自动匹配）
│   ├── code-review.prompt.md
│   ├── bug-analysis.prompt.md
│   └── data-schema.prompt.md
├── skills/                            ← 已有技能（多步骤工作流）
│   ├── ai-coding-rules/
│   ├── debug-tools/
│   └── project-coder/
├── agents/                            ← 自定义 Agent 定义
│   ├── code-reviewer.agent.md
│   └── data-analyst.agent.md
├── hooks/                             ← 硬约束钩子（自动触发）
│   ├── pre-tool-execution.json
│   └── post-tool-execution.json
└── mcp-servers.json                   ← MCP Server 注册
```

---

## 3. 原语详解

### 3.1 Instructions（系统指令）

**文件**：`.agents/AGENTS.md`
**触发**：始终加载到 Agent 会话的系统提示词中
**本质**：项目通用的行为准则，LLM 不可绕过

**文件格式**：

```markdown
# Project Guidelines

- 所有 API 响应必须是 JSON 格式
- 代码审查时优先检查安全性
- 不得直接修改生产数据库
```

**实现方式**：

```python
class InstructionsManager:
    """系统指令管理器。

    加载 .agents/AGENTS.md，始终注入 Agent 系统提示词。
    支持多级继承：项目级 → 租户级 → Agent 级。
    """

    def __init__(self, agents_dir: Path) -> None:
        self._agents_dir = agents_dir

    def load_project_instructions(self) -> str:
        """加载项目级系统指令。"""
        path = self._agents_dir / "AGENTS.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def build_system_prompt(
        self,
        agent_def: AgentDefinition | None = None,
        extra_instructions: str = "",
    ) -> str:
        """组装完整系统提示词。

        优先级（高 → 低）：
        1. extra_instructions（请求级）
        2. agent_def.instructions（Agent 级）
        3. AGENTS.md（项目级）
        """
        parts = [self.load_project_instructions()]
        if agent_def and agent_def.instructions:
            parts.append(agent_def.instructions)
        if extra_instructions:
            parts.append(extra_instructions)
        return "\n\n".join(p for p in parts if p)
```

**注入点**：`SessionManager.get_or_create()` → `AgentService` 组装 System Message

---

### 3.2 File Instructions（文件级指令）

**文件**：`.agents/instructions/*.instructions.md`
**触发**：当 Agent 操作匹配 `applyTo` 模式的文件时自动注入
**本质**：特定文件的针对性约束

**文件格式**：

```markdown
---
applyTo: "app/**/*.py"
name: python-standards
---

# Python 编码规范

- 所有 public 函数必须有类型注解
- 使用 `from __future__ import annotations`
- 异常必须显式捕获，不得使用 bare `except:`
```

**实现方式**：

```python
@dataclass
class FileInstruction:
    name: str
    apply_to: str       # glob 模式，如 "app/**/*.py"
    content: str

class FileInstructionManager:
    """文件指令管理器。"""

    def __init__(self, instructions_dir: Path) -> None:
        self._dir = instructions_dir
        self._instructions: list[FileInstruction] = []

    def load_all(self) -> None:
        """扫描并加载所有 .instructions.md 文件。"""
        self._instructions = []
        if not self._dir.exists():
            return
        for fpath in sorted(self._dir.glob("*.instructions.md")):
            frontmatter, body = self._parse_frontmatter(fpath.read_text())
            self._instructions.append(FileInstruction(
                name=frontmatter.get("name", fpath.stem),
                apply_to=frontmatter.get("applyTo", "**/*"),
                content=body.strip(),
            ))

    def match(self, file_path: str) -> list[FileInstruction]:
        """返回匹配给定文件路径的所有指令。"""
        return [
            inst for inst in self._instructions
            if Path(file_path).match(inst.apply_to)
        ]
```

**注入点**：`ToolContext` 构建时或 `ExecutionHarness.run_tool()` 中，当工具涉及文件操作时注入。

**复用 Skill Rule 机制**：已有的 `SkillRule.apply_to` 和 `FileInstruction.apply_to` 语义完全一致。File Instruction 本质上就是无 Skill 归属的独立规则。

---

### 3.3 Prompts（快捷提示模板）

**文件**：`.agents/prompts/*.prompt.md`
**触发**：用户输入 `/name` 触发，或意图匹配自动推荐
**本质**：参数化的单次任务模板

**当前状态**：`PromptManager` 已有双层存储 + 变量插值
**缺失**：文件导入（`import_from_file`）+ `/` 触发路由

**文件格式**：

```markdown
---
name: code-review
description: 对指定代码进行审查
variables:
  - files
---

# 代码审查

请审查以下文件的代码：

{files}

关注点：
1. 安全性漏洞
2. 性能问题
3. 代码可维护性
```

**扩展 PromptManager**：

```python
class PromptManager:
    # ... 已有 CRUD ...

    async def import_from_file(self, file_path: str) -> PromptTemplate:
        """从 .prompt.md 文件导入 Prompt。"""
        path = Path(file_path)
        frontmatter, body = parse_frontmatter(path.read_text(encoding="utf-8"))

        request = PromptCreateRequest(
            name=frontmatter.get("name", path.stem),
            description=frontmatter.get("description", ""),
            template=body.strip(),
            variables=frontmatter.get("variables", []),
        )
        return await self.create(request)

    def render(self, template_id: str, variables: dict[str, str]) -> str:
        """渲染模板。"""
        tpl = self.get(template_id)
        return tpl.template.format(**variables)
```

---

### 3.4 Skills（技能）

**文件**：`.agents/skills/<name>/SKILL.md` + `rules/*.instructions.md`
**触发**：`load_extension("skill_name", "skill")` 工具调用
**本质**：多步骤工作流 + 附带规则资源

**当前状态**：✅ 已完整实现
- `SkillManager` 支持文件导入 + API CRUD + 双层存储
- `ExtensionCatalog` 提供懒加载 + 路由表
- `SkillRule` 已有 `applyTo` 匹配

**关键文件**：

| 文件 | 位置 | 职责 |
|------|------|------|
| [`SkillManager`](app/services/skill_manager.py) | 核心 CRUD + 文件导入 | |
| [`ExtensionCatalog`](app/services/extension_catalog.py) | 懒加载 + 路由 | |
| [`LoadExtensionTool` / `LoadRuleTool`](app/agents/tools/catalog_tools.py) | LLM 可调用的加载工具 | |
| [`TaskSkill`/`TaskSkillRegistry`](app/workflows/tasks/skill.py) | LangGraph 工作流 Skill | |

**无需改动**，现有设计已是最终形态。

---

### 3.5 Custom Agents（自定义 Agent）

**文件**：`.agents/agents/*.agent.md`
**触发**：Agent 选择器切换 / 子 Agent 调用
**本质**：独立的 AI 身份 + 权限边界

**当前状态**：`AgentDefManager` 已有 API CRUD + `AgentDefinition` 模型
**缺失**：`.agent.md` 文件导入 + Schema 校验

**文件格式**：

```markdown
---
name: code-reviewer
display_name: 代码审查员
description: 专门负责代码审查的 Agent
model: gpt-4o
temperature: 0.3
max_turns: 15
allowed_tools:
  - list_repository_files
  - read_repository_file
  - search_repository
  - extract_code_issues
  - run_code_analysis
skills:
  - ai-coding-rules
  - debug-tools
---

# Code Reviewer Instructions

你是一个资深的代码审查员。你的职责：
1. 阅读和理解代码
2. 发现潜在问题（安全性、性能、可维护性）
3. 给出具体的改进建议
4. 生成审查报告

## 审查规范

- 优先检查安全漏洞
- 关注边界条件和错误处理
- 确保代码符合项目编码规范
- 用中文输出审查结果
```

**扩展 AgentDefManager**：

```python
class AgentDefManager:
    # ... 已有 CRUD ...

    async def import_from_file(self, file_path: str) -> AgentDefinition:
        """从 .agent.md 文件导入 Agent 定义。

        Frontmatter 解析为 AgentDefinition 的元数据字段，
        正文 body 作为 instructions。
        """
        path = Path(file_path)
        frontmatter, body = parse_frontmatter(path.read_text(encoding="utf-8"))

        request = AgentCreateRequest(
            name=frontmatter.get("name", path.stem),
            display_name=frontmatter.get("display_name", ""),
            description=frontmatter.get("description", ""),
            instructions=body.strip(),
            skills=frontmatter.get("skills", []),
            allowed_tools=frontmatter.get("allowed_tools"),
            model=frontmatter.get("model"),
            temperature=frontmatter.get("temperature", 0.7),
            max_turns=frontmatter.get("max_turns", 10),
        )
        return await self.create(request)
```

**运行时选择**：

```python
class AgentService:
    async def _resolve_agent(self, session: Session) -> AgentDefinition | None:
        """解析当前会话使用的 Agent 定义。"""
        if session.agent_name:
            return await self._agent_def_manager.get_by_name(session.agent_name)
        return await self._agent_def_manager.get_default()

    async def _build_system_prompt(self, agent: AgentDefinition | None) -> str:
        """组装系统提示词。"""
        instructions_manager = InstructionsManager(Path(".agents"))
        return instructions_manager.build_system_prompt(agent_def=agent)
```

**工具隔离**：通过 `PolicyEngine` 实现，限制 Agent 只能使用 `allowed_tools` 列表中的工具。

---

### 3.6 Hooks（生命周期钩子）

**文件**：`.agents/hooks/*.json`
**触发**：Agent 生命周期事件自动触发
**本质**：确定性的拦截/扩展脚本

**当前状态**：`EventBus` + `HookRegistry` 已有代码级 Hook
**缺失**：文件级 Hook 配置 + Hook 脚本执行器

**文件格式**：

```json
{
  "name": "dangerous-tool-guard",
  "description": "拦截危险命令执行",
  "events": ["before_tool", "before_stage"],
  "conditions": {
    "tool_names": ["shell_command", "command"]
  },
  "actions": [
    {
      "type": "log",
      "params": {
        "level": "warn",
        "message": "检测到危险工具调用: ${tool_name}"
      }
    },
    {
      "type": "block",
      "params": {
        "reason": "禁止在生产环境执行 shell 命令"
      }
    }
  ]
}
```

**Hook 执行引擎**：

```python
@dataclass
class HookAction:
    type: Literal["log", "block", "notify", "audit", "custom_script"]
    params: dict[str, Any]

@dataclass
class FileHook:
    name: str
    events: list[HookEvent]
    conditions: dict[str, Any]
    actions: list[HookAction]

class FileHookLoader:
    """从 .agents/hooks/ 加载文件 Hook。"""

    def load_all(self, hooks_dir: Path) -> list[FileHook]:
        hooks = []
        for fpath in hooks_dir.glob("*.json"):
            data = json.loads(fpath.read_text())
            hooks.append(FileHook(
                name=data["name"],
                events=[HookEvent(e) for e in data["events"]],
                conditions=data.get("conditions", {}),
                actions=[HookAction(**a) for a in data["actions"]],
            ))
        return hooks

class HookRuntimeAdapter:
    """将 FileHook 适配为 RuntimeHook 协议。"""

    def __init__(self, hook: FileHook, engine: HookActionEngine) -> None:
        self.name = hook.name
        self._hook = hook
        self._engine = engine

    def handle(self, event: EventPayload) -> None:
        if not self._matches_conditions(event):
            return
        for action in self._hook.actions:
            self._engine.execute(action, event)
```

**注入点**：在 `EventBus` 初始化时注册 FileHook 适配器。

```python
class AppContainer:
    def _register_file_hooks(self) -> None:
        hooks_dir = Path(self.settings.agents_dir) / "hooks"
        loader = FileHookLoader()
        for file_hook in loader.load_all(hooks_dir):
            adapter = HookRuntimeAdapter(file_hook, HookActionEngine())
            self.event_bus.register(adapter)
```

---

### 3.7 MCP Server（外部工具协议）

**文件**：`.agents/mcp-servers.json`
**触发**：通过 `tools: [server/*]` 注入工具列表
**本质**：外部动态注册的工具箱

**当前状态**：✅ `McpManager` 已实现 URL/STDIO 连接、工具发现和调用

**文件格式**：

```json
{
  "mcpServers": {
    "github": {
      "type": "url",
      "url": "http://localhost:3100/mcp",
      "description": "GitHub API 工具"
    },
    "database-analyzer": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "mcp_db_analyzer"],
      "description": "数据库 Schema 分析"
    }
  }
}
```

**自动注册流程**：

```python
class AppContainer:
    async def _load_mcp_servers(self) -> None:
        """从文件加载并连接 MCP Server。"""
        config_path = Path(self.settings.agents_dir) / "mcp-servers.json"
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text())
        tools = await self.mcp_manager.connect(config)
        # 将 MCP 工具注册到 ToolRegistry
        for tool in tools:
            self.task_tool_registry.register(McpAgentToolAdapter(tool))
```

---

## 4. 执行模型

本章是全篇的核心，定义所有定制化原语如何在运行时**实际执行**。

### 4.1 总览：双层执行模型

系统分为 **控制面（Control Plane）** 和 **数据面（Data Plane）** 两层执行：

```text
控制面（启动时一次性）
┌──────────────────────────────────────────────────────────┐
│ CustomizationEngine.initialize()                          │
│  ├─ 扫描 .agents/ 目录                                   │
│  ├─ 同步 Skills → SkillManager                          │
│  ├─ 同步 Agents → AgentDefManager                        │
│  ├─ 同步 Prompts → PromptManager                         │
│  ├─ 连接 MCP → McpManager → ToolRegistry                 │
│  ├─ 注册 FileHooks → EventBus                            │
│  └─ 加载 FileInstructions → FileInstructionManager       │
└──────────────────────────────────────────────────────────┘
         │
         ▼ 每个会话
数据面（每次请求）
┌──────────────────────────────────────────────────────────┐
│ AgentService.process()                                    │
│  → CustomizationEngine.build_session_context()            │
│  → 构建 System Message (含指令 + 扩展清单)               │
│  → LLM 推理（或 PlanGenerator + PlanExecutor）            │
│  → ExecutionHarness.run_tool() ← 含 Hook 拦截             │
│  → 结果返回                                              │
└──────────────────────────────────────────────────────────┘
```

---

### 4.2 Hook 生命周期引擎（核心设计）

Hook 系统由三个核心部分组成：

```text
事件源（Event Sources）
  Workflow Orchestrator   →  Stage 生命周期
  ExecutionHarness        →  Tool 生命周期
  ToolExecutor            →  执行生命周期
  CustomizationEngine     →  会话生命周期
       │
       ▼
EventBus (发布-订阅总线)
  已有 16 个 HookEvent 枚举
  支持通配符注册 ('all')
  支持多 handler 顺序执行
       │
       ▼
Handler 链（按注册顺序执行）
  TraceHook (always)      →  记录 trace
  MemoryHook (always)     →  写入/清理记忆
  FileHook (按条件匹配)   →  JSON 配置的拦截/审计
  CustomHook (代码注册)   →  业务自定义
```

#### 4.2.1 完整事件发射图谱

这是所有 HookEvent 在代码中的实际发射点，标注了哪个文件、哪行、哪个条件：

```text
请求初始化阶段
══════════════════════════════════════════════════════════
RUN_STARTED         → [app/harness/execution.py] when? ❌ 缺失
                           需在 ExecutionHarness.run_tool() 入口处发射
CONTEXT_BUILT       → [app/harness/context.py] ContextHarness.build_context() 末尾

工具执行阶段（一次 run_tool 调用）
══════════════════════════════════════════════════════════
┌─ 0. EventBus.emit(BEFORE_TOOL)       ← [execution.py] 需新增
│     ↓ Hook 可以在这里 block 或 audit
├─ 1. GuardrailEngine.validate_tool_call() 
├─ 2. PolicyEngine.check_tool()
├─ 3. SandboxEngine.assess()
├─ 4. ToolExecutor.execute()
│     └─ 内部重试循环:
│         ├─ 尝试 1
│         ├─ 尝试 2 (if retry)
│         └─ ...
├─ 5. ToolExecutionError? → TOOL_FAILED
│     ↓
└─ 6. EventBus.emit(AFTER_TOOL)        ← [execution.py] 目前仅调用 hooks.emit_after_tool()
      ↓
  ExecutionHooks.record_execution()    ← trace 记录
  ExecutionHooks.record_runtime_summary() ← memory 记录

ReAct 步骤阶段
══════════════════════════════════════════════════════════
BEFORE_REACT_TURN   → [harness/components/react_runtime.py] 每步循环顶部
AFTER_REACT_TURN    → [同上] 每步循环末尾
REACT_EXCEEDED_MAX_TURNS → [同上] 超步数时

Stage 阶段（TaskWorkflow 内部）
══════════════════════════════════════════════════════════
BEFORE_STAGE        → [workflows/tasks/*_nodes.py] 每个 stage 函数入口
AFTER_STAGE         → [同上] 每个 stage 函数返回前
STAGE_FAILED        → [同上] catch 中

Checkpoint 阶段
══════════════════════════════════════════════════════════
BEFORE_CHECKPOINT   → [workflows/tasks/*_nodes.py] checkpoint 前
AFTER_CHECKPOINT    → [同上] checkpoint 后

请求结束阶段
══════════════════════════════════════════════════════════
RUN_COMPLETED       → [execution.py] run_tool 成功返回前 ❌ 缺失
RUN_FAILED          → [execution.py] run_tool 异常抛出前 ❌ 缺失
```

#### 4.2.2 需要在现有代码中补充的事件发射点

当前 `ExecutionHarness.run_tool()` **没有发射任何 EventBus 事件**。`ExecutionHooks` 定义了 `emit_before_tool()` / `emit_after_tool()` / `emit_tool_failed()` 方法但从未被调用。

> **这不是新增设计，而是修复已有的缺口。** 项目 [`架构.md`](架构.md) 3.5 节的"理想状态"描述中明确标注了 `EventBus.emit_before_tool()` 在治理链第 1 步、`EventBus.emit_after_tool()` 在治理链第 6 步。实际代码没有跟上架构文档。本设计补上事件发射，使代码对齐架构描述。

```python
# 修复后的 ExecutionHarness.run_tool() 事件注入点
class ExecutionHarness:
    def run_tool(self, name, payload, workflow_state, context_bundle, ...):
        # ── 发射 BEFORE_TOOL 事件 ──
        self.hooks.emit_before_tool(workflow_state, name, payload)

        try:
            # 治理链: guardrail → policy → sandbox
            tool_decision = self.guardrail_engine.validate_tool_call(...)
            policy_decision = self.policy_engine.check_tool(...)
            sandbox_decision = self.sandbox_engine.assess(...)

            # 执行（含重试/熔断）
            outcome = self.tool_executor.execute(name, payload, ...)

            # ── 发射 AFTER_TOOL 事件（成功路径）──
            execution = ToolExecutionResult(status='ok', ...)
            self.hooks.emit_after_tool(workflow_state, execution)
            return outcome.result

        except ToolExecutionError as exc:
            # ── 发射 TOOL_FAILED 事件（失败路径）──
            self.hooks.emit_tool_failed(workflow_state, name, str(exc))
            ...

        finally:
            # 始终记录摘要
            self.hooks.record_execution(workflow_state, step_id, execution)
            self.hooks.record_runtime_summary(workflow_state, runtime_summary)
```

#### 4.2.3 Hook Action 类型与执行语义

每个 FileHook 可以包含多个 action，按声明顺序执行：

| Action 类型 | 作用 | 对主流程的影响 | 参数 |
|-------------|------|---------------|------|
| `log` | 记录日志 | 不阻断 | `level`, `message`（支持模板变量） |
| `audit` | 写入审计记录到 DB | 不阻断 | `category`, `detail` |
| `notify` | 发送通知（Webhook/事件） | 不阻断 | `channel`, `template` |
| `block` | 终止工具执行 | 阻断抛出异常 | `reason`, `error_code` |
| `throttle` | 限流（速率限制） | 可阻断 | `max_calls`, `window_sec` |
| `mutate_payload` | 修改工具入参 | 不阻断 | `path`, `value` |
| `custom_script` | 执行自定义 Python 脚本 | 取决于脚本 | `module`, `function`, `args` |

**Action 的顺序与生命周期**：

```python
from app.agents.tools.base import ToolExecutionError


class HookActionEngine:
    """Hook 动作执行引擎。"""

    def execute(
        self,
        actions: list[HookAction],
        event: EventPayload,
    ) -> HookActionResult:
        """按顺序执行动作，block 类动作会立即中止后续动作。"""
        result = HookActionResult(allowed=True, reason="", audit_log=None)

        for action in actions:
            # 模板变量替换：${tool_name}, ${payload.field} 等
            resolved_params = self._resolve_template(action.params, event)

            if action.type == "log":
                self._execute_log(resolved_params)

            elif action.type == "block":
                result.allowed = False
                result.reason = resolved_params.get("reason", "Blocked by hook")
                result.error_code = resolved_params.get(
                    "error_code", "hook_blocked"
                )
                break  # block 立即中止后续 actions

            elif action.type == "audit":
                self._execute_audit(resolved_params, event)

            elif action.type == "notify":
                self._execute_notify(resolved_params)  # fire-and-forget

            elif action.type == "mutate_payload":
                self._execute_mutate(resolved_params, event)  # 直接修改 event.payload

            elif action.type == "custom_script":
                script_result = self._execute_script(resolved_params, event)
                if script_result.get("block"):
                    result.allowed = False
                    result.reason = script_result.get("reason", "Blocked by script")
                    break

            elif action.type == "throttle":
                allowed = self._check_rate_limit(resolved_params, event)
                if not allowed:
                    result.allowed = False
                    result.reason = "Rate limit exceeded"
                    result.error_code = "rate_limited"
                    break

        # block 动作通过 ToolExecutionError 传递给调用方。
        # 复用现有异常类型使 execution.py 的 except ToolExecutionError 自然捕获，
        # 符合"治理链中所有阻断走 ToolExecutionError"的架构风格，不需要新增异常继承体系。
        if not result.allowed:
            raise ToolExecutionError(
                code=result.error_code or 'hook_blocked',
                message=f"Hook blocked: {result.reason}",
                error_type='permission_error',
                default_action='abort',
                details={
                    'hook_name': event.payload.get('_hook_name', 'unknown'),
                },
            )

        return result
```

#### 4.2.4 Hook 条件匹配引擎

条件决定了 Hook 是否在特定事件上触发：

```json
{
  "conditions": {
    "tool_names": ["shell_command", "command"],
    "tool_names_exclude": ["shell_command_readonly"],
    "payload_match": {
      "command": "rm -rf *"
    },
    "stage_names": ["analyze", "draft"],
    "sandbox_modes": ["inline"],
    "risk_levels": ["high"],
    "rate_limit": {
      "max_calls": 10,
      "window_seconds": 60
    }
  }
}
```

条件评估逻辑：

```python
class HookConditionEvaluator:
    """评估 Hook 触发条件是否满足。

    支持条件类型：
    - tool_names: 白名单（任意匹配即触发）
    - tool_names_exclude: 黑名单（匹配则跳过）
    - payload_match: 负载字段精确匹配
    - stage_names: Stage 名称匹配
    - sandbox_modes: 沙盒模式匹配
    - risk_levels: 风险等级匹配
    """

    def matches(self, conditions: dict, event: EventPayload) -> bool:
        """所有条件都满足才返回 True（AND 语义）。"""
        payload = event.payload

        # 工具名称白名单
        tool_names = conditions.get("tool_names")
        if tool_names:
            actual_tool = payload.get("tool_name", "")
            if not any(self._glob_match(pattern, actual_tool) for pattern in tool_names):
                return False

        # 工具名称黑名单（排除项）
        excluded = conditions.get("tool_names_exclude", [])
        actual_tool = payload.get("tool_name", "")
        if any(self._glob_match(pattern, actual_tool) for pattern in excluded):
            return False

        # Payload 字段匹配
        payload_match = conditions.get("payload_match", {})
        actual_payload = payload.get("payload_preview", payload.get("payload", {}))
        for key, expected_val in payload_match.items():
            if isinstance(actual_payload, dict):
                if actual_payload.get(key) != expected_val:
                    return False

        # Stage 匹配
        stage_names = conditions.get("stage_names")
        if stage_names:
            actual_stage = payload.get("stage_name", payload.get("step_name", ""))
            if actual_stage not in stage_names:
                return False

        # 风险等级
        risk_levels = conditions.get("risk_levels")
        if risk_levels:
            actual_risk = payload.get("risk_level", "low")
            if actual_risk not in risk_levels:
                return False

        return True
```

#### 4.2.5 Hook 执行顺序与优先级

Hook 按**类型 + 注册顺序**执行，不可绕过：

```python
class PriorityHookRegistry(HookRegistry):
    """分层 Hook 注册表，保证执行顺序。"""

    HOOK_PRIORITY = {
        "system_guard": 0,      # 系统内置防护（最高优先级）
        "file_hook": 1,         # JSON 文件配置的 Hook
        "code_hook": 2,         # 代码注册的普通 Hook
        "trace_hook": 3,        # Trace 记录（最低优先级，不影响执行）
        "memory_hook": 3,       # 记忆记录
    }

    def emit(self, event: EventPayload) -> None:
        """按优先级分层发射事件。

        低优先级 Hook 即使失败也不影响主流程。
        高优先级 Hook（guard/file_hook）的 block 动作会阻断执行。
        """
        for priority_level in sorted(self._layers.keys()):
            handlers = self._layers[priority_level]
            for handler in handlers:
                try:
                    handler.handle(event)
                except ToolExecutionError as exc:
                    if exc.code.endswith('hook_blocked'):
                        raise  # 仅 hook block 阻断传播到调用方
                    # 其他 ToolExecutionError 按优先级处理
                    if priority_level <= 1:
                        raise
                except Exception:
                    if priority_level <= 1:
                        raise  # guard/file_hook 异常必须中断
                    # trace/memory hook 异常仅记录，不中断
```

#### 4.2.6 Hook 错误处理策略

| Handler 类型 | 自身异常 | Block 动作 | 推荐优先级 |
|-------------|---------|-----------|-----------|
| System Guard（代码注册） | 阻断主流程 | 阻断 | 0 |
| File Hook（JSON 配置） | 阻断主流程 | 阻断 | 1 |
| Code Hook（业务扩展） | 日志记录，不阻断 | 阻断 | 2 |
| Trace Hook | 吞掉异常 | N/A | 3 |
| Memory Hook | 吞掉异常 | N/A | 3 |

---

### 4.3 完整的工具执行流水线（7 阶段模型）

> **治理链顺序说明**：File Instructions 注入（阶段 2）放在 guardrail（阶段 1）之后、policy（阶段 3）之前。原因：File Instructions 是**数据注入**（提供文件级规则上下文给 LLM/工具），而非**权限决策**——它不影响"是否允许执行"的判断。放在 guardrail 之后，确保护栏参数校验已完成；放在 policy 之前，确保后续权限判断可参考文件级上下文。这与当前治理链的设计语义一致。

这是 **ExecutionHarness.run_tool()** 一次调用的完整执行流水线，标注了每个阶段原语的参与位置：

```text
阶段 0: BEFORE_TOOL 事件
────────────────────────────────────────────────────
  EventBus.emit(BEFORE_TOOL, tool_name, payload)
  ├── TraceHook        → 记录 trace
  ├── MemoryHook       → 写入工作记忆
  ├── FileHook (audit) → 审计日志写入 DB
  ├── FileHook (block) → 检查条件，阻断危险工具
  └── FileHook (log)   → 日志记录
        │
        ▼ 未被阻断则继续

阶段 1: Guardrail 护栏检查
────────────────────────────────────────────────────
  GuardrailEngine.validate_tool_call()
  ├── 参数校验（input_schema 验证）
  └── 护栏规则检查（敏感词、PII 等）
        │
        ▼ 通过则继续

阶段 2: File Instructions 注入
────────────────────────────────────────────────────
  FileInstructionManager.match(tool_name, payload)
  ├── 检查工具操作的文件路径（如 read_file, search_repo）
  ├── 匹配 applyTo glob 模式
  └── 将匹配的指令注入到 ToolContext.file_instructions
        │
        ▼ FileInstructions 随 ToolContext 传入工具

阶段 3: Policy 权限检查
────────────────────────────────────────────────────
  PolicyEngine.check_tool()
  ├── Agent.allowed_tools 白名单（如设置了）
  └── PolicyProfile 策略检查（针对 task 模式）
        │
        ▼ 允许则继续

阶段 4: Sandbox 沙盒决策
────────────────────────────────────────────────────
  SandboxEngine.assess()
  ├── 工具声明风险等级
  └── 上下文风险加权 → 决定 inline / thread_isolated / process_isolated
        │
        ▼ 决策通过

阶段 5: Tool 执行（含重试/熔断/超时）
────────────────────────────────────────────────────
  ToolExecutor.execute()
  ├── CircuitBreaker 检查 → 熔断开启则抛出
  ├── 重试循环（最多 max_attempts 次）
  │   ├── ThreadPoolExecutor 提交
  │   ├── 超时控制
  │   └── 失败 → backoff → 重试
  └── 结果/异常返回

阶段 6: AFTER_TOOL / TOOL_FAILED 事件
────────────────────────────────────────────────────
  if 成功:
    EventBus.emit(AFTER_TOOL, tool_name, status, latency_ms)
    ├── TraceHook     → 记录执行成功
    ├── MemoryHook    → 写入任务记忆
    └── FileHook      → 后处理审计
  else:
    EventBus.emit(TOOL_FAILED, tool_name, error)
    └── FallbackHandler → 执行降级逻辑

阶段 7: 后处理（无论成功/失败）
────────────────────────────────────────────────────
  始终执行:
    ExecutionHooks.record_execution()     → trace
    ExecutionHooks.record_runtime_summary() → memory
    ├── 写入 TaskMemory
    └── 清理 working memory（MemoryHook）
```

---

### 4.4 原语解析顺序与优先级

当一个请求到达时，多个原语的指令可能同时生效。解析顺序决定了最终的 System Message 内容：

```text
优先级 0（最高）  ← 请求级
  Request-level Instructions
  （用户请求中附带的额外指令）

优先级 1         ← Agent 级
  AgentDefinition.instructions
  （.agent.md 的 body 部分）

优先级 2         ← 项目级
  AGENTS.md（项目通用行为准则）

优先级 3（最低）  ← 系统内置
  System default instructions
  （代码硬编码的默认系统提示词）
```

**合并策略**：所有层级按优先级**拼接**，而非覆盖。高优先级指令拼接在低优先级之后（即越靠近用户的消息越有效）。

#### 工具白名单解析顺序

```text
1. AgentDefinition.allowed_tools        → 精确指定允许的工具列表
2. PolicyProfile 中的工具约束           → 按任务类型的策略约束
3. ToolRegistry 全部工具                → 无限制（默认）
```

当 `allowed_tools` 为 `None` 时表示无限制。设为 `[]` 表示禁止所有工具。

#### File Instruction 匹配优先级

```text
1. Skill rules（.agents/skills/<name>/rules/*.md）
   └── applyTo 匹配 + skill_name 匹配
2. File Instructions（.agents/instructions/*.instructions.md）
   └── applyTo 匹配（独立文件）
3. 同一文件匹配到多条时，按文件名字典序拼接
```

---

### 4.5 File Instructions 运行时注入机制

File Instructions 在工具执行时动态匹配并注入到 `ToolContext`，让 LLM 能看到"操作这个文件时需要遵守的规则"。

```python
@dataclass
class ToolContext:
    # ... 已有字段 ...

    # ── 新增：文件级指令 ──
    file_instructions: list[FileInstruction] = field(default_factory=list)
```

**注入时机**：在 `ExecutionHarness.run_tool()` 的阶段 2 中

```python
class ExecutionHarness:
    def __init__(self, ..., file_instruction_manager=None):
        self._file_inst_mgr = file_instruction_manager

    def run_tool(self, name, payload, workflow_state, context_bundle, ...):
        # 阶段 0: BEFORE_TOOL event
        self.hooks.emit_before_tool(workflow_state, name, payload)

        # 阶段 1: Guardrail
        tool_decision = self.guardrail_engine.validate_tool_call(...)

        # ── 阶段 2: File Instructions 注入 ──
        file_instructions = []
        if self._file_inst_mgr:
            # 从 payload 中推断操作的文件路径
            file_paths = self._extract_file_paths(name, payload)
            for fp in file_paths:
                file_instructions.extend(self._file_inst_mgr.match(fp))
        # 稍后注入到 ToolContext
        context_bundle.file_instructions = file_instructions

        # 继续后续阶段...
```

**`ToolExecutor._tool_context()` 的修改**：

```python
class ToolExecutor:
    def _tool_context(self, workflow_state, tool_call_id) -> ToolContext:
        # 从 context_bundle 中提取 file_instructions
        file_instructions = workflow_state.get("_file_instructions", [])

        return ToolContext(
            # ... 已有字段 ...
            file_instructions=file_instructions,  # 新增
        )
```

**工具实现中使用**：

```python
class ReadRepositoryFileTool:
    def run(self, payload, context: ToolContext) -> BaseModel:
        # file_instructions 已经注入到 context
        if context.file_instructions:
            # 可以作为额外 context 传递给 LLM
            pass
        # 正常执行...
        return ReadFileOutput(content=content, ...)
```

---

### 4.6 Custom Agent 运行时切换与工具隔离

```text
请求进入
  │
  ├─ 检查 session.agent_name
  │   ├─ 有值 → AgentDefManager.get_by_name(agent_name)
  │   └─ 无值 → AgentDefManager.get_default()
  │
  ├─ 加载 AgentDefinition
  │   ├─ instructions      → 注入 System Prompt
  │   ├─ allowed_tools     → PolicyEngine 白名单
  │   ├─ skills            → ExtensionCatalog 限定清单
  │   └─ model/temperature → LLM 配置覆盖
  │
  └─ 构建 System Prompt
      ├─ AGENTS.md
      ├─ Agent.instructions
      └─ ExtensionCatalog（仅列出 agent.skills 限定的扩展）
```

**PolicyEngine 白名单实现**：

```python
class PolicyEngine:
    def check_tool(
        self,
        tool_name: str,
        agent: AgentDefinition | None = None,
    ) -> PolicyDecision:
        """检查工具是否在 Agent 白名单内。"""
        if agent and agent.allowed_tools is not None:
            if tool_name not in agent.allowed_tools:
                return PolicyDecision(
                    allowed=False,
                    reason=f"Agent '{agent.name}' 不允许使用工具 '{tool_name}'",
                )
        return PolicyDecision(allowed=True)
```

---

### 4.7 MCP 工具的执行适配

MCP 工具通过 `McpAgentToolAdapter` 适配为标准的 `AgentTool` 协议，透明地通过 `ToolRegistry` 执行：

```python
class McpAgentToolAdapter:
    """将 MCP 工具适配为 AgentTool 协议。"""

    def __init__(self, mcp_tool: McpToolDef) -> None:
        self.name = mcp_tool.name
        self._mcp_tool = mcp_tool
        self.input_model = self._build_input_model()
        self.output_model = ToolOutputEnvelope
        self.version = "mcp-v1"
        self.timeout_ms = 30000
        self.retry_policy = ToolRetryPolicy(max_attempts=1)
        self.trace_fields = ["server", "name"]

    def run(self, payload: BaseModel, context: ToolContext) -> BaseModel:
        """通过 McpManager 调用远程工具。"""
        server_name = self._mcp_tool.server
        mcp_manager = context.services.get("mcp_manager")
        if not mcp_manager:
            raise ToolExecutionError(...)

        result = asyncio.run(
            mcp_manager.call_tool(server_name, self.name, payload.model_dump())
        )
        return ToolOutputEnvelope(data=result)
```

执行路径：`ToolRegistry → McpAgentToolAdapter.run() → McpManager.call_tool() → 远程 MCP Server`

---

### 4.8 Skills 的运行时执行

Skills 有两种执行模式：

**模式 A：作为扩展内容（大部分场景）**
```text
LLM 在 System Message 中看到扩展清单
  → 按需调用 load_extension("skill_name", "skill")
  → 收到 SKILL.md instructions + 规则路由表
  → 按需调用 load_rule("skill_name", "rule_name")
  → 收到完整规则内容
  → 在后续工具调用中自然遵循这些规则
```

**模式 B：作为工作流 TaskSkill（任务编排场景）**
```text
TaskWorkflowOrchestrator 根据 task_type 查找 TaskSkill
  → TaskSkillRegistry.get(task_type)
  → Skill.build_initial_state(task)  → 构造初始 workflow state
  → LangGraph 按图执行各 stage
  → 每个 stage 内可能调用 ExecutionHarness.run_tool()
```

---

### 4.9 完整的事件时间线（一次典型请求的完整生命周期）

```text
时间 →  AgentService.process()
  │
  │  RUN_STARTED           ← [CustomizationEngine]
  │    执行: TraceHook, FileHook(audit)
  │
  │  CustomizationEngine.build_session_context()
  │    ├─ InstructionsManager.build_system_prompt()
  │    └─ ExtensionCatalog.build_catalog()
  │
  │  SessionManager.get_or_create()
  │    └─ 创建或恢复会话
  │
  │  CONTEXT_BUILT         ← [ContextHarness]
  │
  ├─ 如果 mode=plan:
  │    PlanGenerator.generate()
  │    → 用户确认
  │    → PlanExecutor.execute()
  │      └─ 每个步骤调用 run_tool()
  │
  ├─ 如果 mode=chat/autopilot:
  │    LLM.generate(messages)
  │    → LLM 返回 tool_calls 或 text
  │    → 循环直到完成
  │
  │  ┌── [每次 tool_call 循环] ─────────────────────────┐
  │  │ ① BEFORE_TOOL         ← [ExecutionHarness]       │
  │  │    └─ 文件 Hook 评估条件, 可能 block              │
  │  │ ② Guardrail 检查                                  │
  │  │ ③ FileInstructions 注入                           │
  │  │ ④ Policy 检查 (allowed_tools)                    │
  │  │ ⑤ Sandbox 评估                                    │
  │  │ ⑥ ToolExecutor.execute()                          │
  │  │    ├─ CircuitBreaker 检查                          │
  │  │    ├─ 超时/重试/熔断控制                           │
  │  │    └─ ToolContext 携带 file_instructions           │
  │  │ ⑦ AFTER_TOOL / TOOL_FAILED                       │
  │  │    └─ TraceHook, MemoryHook, FileHook              │
  │  │ ⑧ record_execution + record_runtime_summary        │
  │  └──────────────────────────────────────────────────┘
  │
  │  RUN_COMPLETED / RUN_FAILED
  │    执行: TraceHook, FileHook(audit)
  │
  ▼  返回给用户
```

---

### 4.10 Skills 的运行时懒加载

```text
LLM 收到轻量清单（~50 tokens/项）：
  可用扩展:
    - ai-coding-rules: AI 编码规则
    - debug-tools: 通用调试工具

LLM 决定需要 → 调用 load_extension("debug-tools", "skill")
  ↓
ExtensionCatalog._load_skill("debug-tools")
  → 返回 SKILL.md 的完整 instructions + 路由表
  ↓
LLM 读取路由表 → 调用 load_rule("debug-tools", "02-ts-debug")
  ↓
ExtensionCatalog._load_rule("debug-tools", "02-ts-debug")
  → 返回 rules/02-ts-debug.instructions.md 的完整内容
```

---

## 5. 文件目录规范

### 5.1 标准化 File Schema

所有原语文件遵循统一的 frontmatter 规范：

```yaml
---
# 通用字段
name: string                   # 唯一名称
description: string            # 描述（用于清单）
type: instructions|prompt|skill|agent|hook|mcp

# 类型特定字段
applyTo: string                # instructions: 文件匹配 glob
variables: [string]            # prompt: 模板变量列表
model: string                  # agent: 指定 LLM 模型
temperature: float             # agent: 温度参数
allowed_tools: [string]        # agent: 工具白名单
skills: [string]              # agent: 绑定的 Skill
events: [string]              # hook: 监听事件
conditions: dict              # hook: 触发条件
---
```

### 5.2 文件命名规则

| 原语 | 文件模式 | 位置 |
|------|---------|------|
| Instructions | `AGENTS.md` | `.agents/` |
| File Instructions | `*.instructions.md` | `.agents/instructions/` |
| Prompts | `*.prompt.md` | `.agents/prompts/` |
| Skills | `SKILL.md` + `rules/*.md` | `.agents/skills/<name>/` |
| Custom Agents | `*.agent.md` | `.agents/agents/` |
| Hooks | `*.json` | `.agents/hooks/` |
| MCP Servers | `mcp-servers.json` | `.agents/` |

### 5.3 配置项

在 `Settings` 中新增：

```python
class Settings(BaseSettings):
    # ... 已有配置 ...

    # ── 定制化原语配置 ──
    agents_dir: str = ".agents"                     # 原语根目录
    auto_import_agents: bool = True                 # 启动时自动导入 .agent.md
    auto_import_skills: bool = True                 # 启动时自动导入 SKILL.md
    auto_import_prompts: bool = True                # 启动时自动导入 .prompt.md
    auto_connect_mcp: bool = True                   # 启动时自动连接 MCP
    enable_file_hooks: bool = True                  # 启用文件 Hook
    enable_file_instructions: bool = True           # 启用文件指令匹配
```

---

## 6. 数据模型

### 6.1 CustomizationEngine（新增）

```python
class CustomizationEngine:
    """定制化原语引擎——统一加载和组装所有原语。

    职责：
    - 扫描 .agents/ 目录加载所有原语文件
    - 提供按类型、按名称的查询
    - 构建会话级系统提示词
    - 与各 Manager 同步（双向同步策略）
    """

    def __init__(
        self,
        agents_dir: Path,
        skill_manager: SkillManager,
        agent_def_manager: AgentDefManager,
        prompt_manager: PromptManager,
        mcp_manager: McpManager,
        event_bus: EventBus,
        file_instruction_manager: FileInstructionManager,
        instructions_manager: InstructionsManager,
        settings: Settings,
    ) -> None:
        self._agents_dir = agents_dir
        self._skill_manager = skill_manager
        self._agent_def_manager = agent_def_manager
        self._prompt_manager = prompt_manager
        self._mcp_manager = mcp_manager
        self._event_bus = event_bus
        self._file_inst_mgr = file_instruction_manager
        self._inst_mgr = instructions_manager
        self._settings = settings

    async def initialize(self) -> None:
        """应用启动时初始化——扫描文件并同步到各 Manager。"""
        if self._settings.auto_import_skills:
            await self._sync_skills()
        if self._settings.auto_import_agents:
            await self._sync_agents()
        if self._settings.auto_import_prompts:
            await self._sync_prompts()
        if self._settings.auto_connect_mcp:
            await self._sync_mcp_servers()
        if self._settings.enable_file_hooks:
            self._sync_hooks()
        if self._settings.enable_file_instructions:
            self._file_inst_mgr.load_all()

    async def build_session_context(
        self,
        session: Session,
        agent_name: str | None = None,
    ) -> SessionContext:
        """为会话构建完整上下文。"""
        # 1. 解析 Agent 定义
        agent_def = None
        if agent_name:
            agent_def = await self._agent_def_manager.get_by_name(agent_name)
        if not agent_def:
            agent_def = await self._agent_def_manager.get_default()

        # 2. 构建系统提示词
        system_prompt = self._inst_mgr.build_system_prompt(agent_def)

        # 3. 构建扩展清单
        catalog = ""
        if self._settings.enable_extension_catalog:
            catalog = await self._build_catalog(agent_def)

        # 4. 解析工具白名单
        allowed_tools = agent_def.allowed_tools if agent_def else None

        return SessionContext(
            agent_def=agent_def,
            system_prompt=system_prompt,
            extension_catalog=catalog,
            allowed_tools=allowed_tools,
        )

    async def _sync_skills(self) -> None:
        """扫描 .agents/skills/ 目录，同步到 SkillManager。"""
        skills_dir = self._agents_dir / "skills"
        if not skills_dir.exists():
            return
        for skill_dir in skills_dir.iterdir():
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
                # 检查是否已导入，根据 name 去重
                existing = await self._skill_manager.get_by_name(skill_dir.name)
                if existing is None:
                    await self._skill_manager.import_from_dir(str(skill_dir))

    async def _sync_agents(self) -> None:
        """扫描 .agents/agents/ 目录，同步到 AgentDefManager。"""
        agents_dir = self._agents_dir / "agents"
        if not agents_dir.exists():
            return
        for fpath in agents_dir.glob("*.agent.md"):
            existing = await self._agent_def_manager.get_by_name(fpath.stem)
            if existing is None:
                await self._agent_def_manager.import_from_file(str(fpath))

    async def _sync_prompts(self) -> None:
        """扫描 .agents/prompts/ 目录，同步到 PromptManager。"""
        prompts_dir = self._agents_dir / "prompts"
        if not prompts_dir.exists():
            return
        for fpath in prompts_dir.glob("*.prompt.md"):
            existing = await self._prompt_manager.get_by_name(fpath.stem)
            if existing is None:
                await self._prompt_manager.import_from_file(str(fpath))

    async def _sync_mcp_servers(self) -> None:
        """加载并连接 MCP Server。"""
        config_path = self._agents_dir / "mcp-servers.json"
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text())
        await self._mcp_manager.connect(config)

    def _sync_hooks(self) -> None:
        """加载文件 Hook 并注册到 EventBus。"""
        hooks_dir = self._agents_dir / "hooks"
        loader = FileHookLoader()
        for file_hook in loader.load_all(hooks_dir):
            adapter = HookRuntimeAdapter(file_hook, HookActionEngine())
            for event in file_hook.events:
                self._event_bus.register(adapter, event)
```

### 6.2 SessionContext（新增）

```python
@dataclass
class SessionContext:
    """会话上下文——一次 Agent 会话所需的所有定制化信息。"""
    agent_def: AgentDefinition | None
    system_prompt: str
    extension_catalog: str
    allowed_tools: list[str] | None
```

---

---

## 7. 架构合规性分析

本章对照项目现有架构原则（来自 [`架构.md`](架构.md)），逐条验证设计是否会产生破坏。

### 7.1 对照表

| 架构原则 | 原文 | 设计做了什么 | 结论 |
|---------|------|------------|------|
| **Harness 是顶层** | 治理（policy / guardrail / budget / sandbox / hook）由 Harness 控制 | 新增的 Hook 事件通过 EventBus 挂在 Harness 内部治理链上；File Instructions 注入放在 run_tool() 中作为治理链的一个阶段 | ✅ 一致 |
| **LangGraph 是唯一工作流引擎** | 不在其上叠加 Recipe/Stage/HarnessKernel 抽象 | Skills 的两种执行模式（扩展内容/工作流 TaskSkill）都走已有 LangGraph 路径 | ✅ 一致 |
| **无第二条平行主链** | 主链：TaskSpec → LangGraph → ExecutionHarness → Tool → Facade | 所有新增组件都挂在主链节点上，不新增第二条 | ✅ 一致 |
| **Harness 只依赖 ToolRegistry** | 不 import 任何具体 Capability | `FileInstructionManager` 是可选依赖（默认 None），不是具体 Capability | ✅ 一致 |
| **EventBus 广播事件** | 每次 step/tool 事件都广播 | 补上 `run_tool()` 中缺失的事件发射，让代码对齐架构描述 | ✅ 修复缺口 |
| **节点不直接调 trace.record()** | 通过 EventBus 由 hook 处理 | Hook 动作引擎走 RuntimeHook → HookRegistry → EventBus，不绕过 | ✅ 一致 |
| **治理链顺序固定** | guardrail → policy → sandbox → execution | File Instructions 注入放在 guardrail 之后、policy 之前；是数据注入而非权限决策，不破坏治理语义 | ⚠️ 微调 |
| **阻断走 ToolExecutionError** | 所有治理阻断统一抛 ToolExecutionError | Hook block 动作复用 ToolExecutionError，不新增异常类型 | ✅ 一致 |
| **ToolContext 向后兼容** | 新增字段不影响已有工具 | `file_instructions` 默认空列表，原工具不受影响 | ✅ 一致 |

### 7.2 治理链阶段数的变化

原有治理链（4 阶段）：

```text
guardrail → policy → sandbox → execution
```

设计后治理链（5 阶段）：

```text
before_tool_event → guardrail → file_instructions_inject → policy → sandbox → execution → after_tool_event
```

**注入点说明**：File Instructions 注入放在 guardrail（参数校验）之后、policy（权限决策）之前。原因是 File Instructions 是**数据**而非**决策**——它把匹配文件的规则注入到 ToolContext 中供 LLM/工具消费，不影响"是否允许执行"的判断。若放在 guardrail 之前，护栏规则可能依赖的文件元数据尚未就绪。

**影响范围**：当前代码中不存在硬编码"治理链只有 4 个阶段"的外部依赖。Orchestrator 不感知 Harness 治理链的内部阶段数。因此这个变化是安全的。

### 7.3 Hook 阻断的异常路径

设计明确：**所有治理阻断统一走 `ToolExecutionError`**。

```python
# 已有阻断模式：
raise ToolExecutionError(code='guardrail_blocked', ...)
raise ToolExecutionError(code='policy_tool_blocked', ...)
raise ToolExecutionError(code='sandbox_blocked', ...)

# 新增（一致）：
raise ToolExecutionError(code='hook_blocked', ...)
```

`execution.py` 的 `except ToolExecutionError` 块自然捕获，FallbackHandler 自然处理，不需要新增异常继承体系。

### 7.4 总结

> **设计不会破坏现有架构。** 7 项原则完全一致，1 项（治理链阶段数）有微调但安全，1 项（缺失的事件发射）反而是修复了已有缺口。唯一的架构注意点是 Hook 阻断异常复用 `ToolExecutionError`，已在 §4.2.3 中修正。

---

## 8. 实施路线图

### Phase 1：核心引擎 + 基础原语（1 周）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 创建 `InstructionsManager` | `app/services/instructions_manager.py` | 无 |
| 2 | 创建 `FileInstructionManager` | `app/services/file_instruction_manager.py` | 无 |
| 3 | 创建 `CustomizationEngine` | `app/services/customization_engine.py` | #1, #2 |
| 4 | `AgentDefManager.import_from_file()` | `app/services/agent_def_manager.py` | 无 |
| 5 | `PromptManager.import_from_file()` | `app/services/prompt_manager.py` | 无 |
| 6 | `ToolContext.file_instructions` 字段 + `ToolExecutor` 注入 | `app/agents/tools/base.py`, `app/harness/components/tool_executor.py` | #2 |
| 7 | `execution.py` 补事件发射 + File Instructions 阶段 | `app/harness/execution.py` | #2, #6 |
| 8 | 注入 `CustomizationEngine` 到 `AppContainer` | `app/container.py` | #3 |

### Phase 2：文件 Hook 执行引擎（2-3 天）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | `FileHookLoader`（JSON Schema 校验） | `app/services/hook_loader.py` | 无 |
| 2 | `HookActionEngine`（log/block/audit/notify/throttle 动作） | `app/services/hook_actions.py` | #1 |
| 3 | `HookRuntimeAdapter`（FileHook → RuntimeHook） | `app/services/hook_adapter.py` | #2 |
| 4 | 在 `CustomizationEngine._sync_hooks()` 中注册 + 容器装配 | `app/services/customization_engine.py`, `app/container.py` | #3 |

### Phase 3：Agent 身份路由 + 工具隔离（3-4 天）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | `AgentService` 集成 `CustomizationEngine.build_session_context()` | `app/services/agent_service.py` | Phase1#3 |
| 2 | `PolicyEngine` 支持 `allowed_tools` 白名单过滤 | `app/harness/policy.py` | 无 |
| 3 | `SessionManager` 支持 `agent_name` 切换 | `app/services/session_manager.py` | 无 |
| 4 | Agent 选择 API 端点 | `app/api/v1/endpoints/admin.py` | #3 |

### Phase 4：API 管理端点（2-3 天）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 创建 `/api/v1/admin/instructions` 端点 | `app/api/v1/endpoints/admin.py` | Phase1 |
| 2 | 创建 `/api/v1/admin/file-instructions` 端点 | 同上 | Phase1 |
| 3 | 创建 `/api/v1/admin/hooks` 端点 | 同上 | Phase2 |
| 4 | 批量导入端点（扫描目录 → 预览 → 确认导入） | 同上 | Phase1 |

### Phase 5：标准化文件 Schema + 校验器（1-2 天）

| # | 任务 | 文件 | 依赖 |
|---|------|------|------|
| 1 | 统一 Frontmatter Schema 定义 | `app/models/customization.py` | 无 |
| 2 | Frontmatter 解析 + 校验（Pydantic） | `app/services/frontmatter_parser.py` | #1 |
| 3 | 文件模板生成工具 CLI | `scripts/generate_primitive.py` | #2 |

---

## 附录：与现有系统的集成

### 容器初始化顺序

```python
class AppContainer:
    async def initialize_customization(self) -> None:
        # 1. 初始化各 Manager（已有）
        # 2. 创建 CustomizationEngine
        self.customization_engine = CustomizationEngine(
            agents_dir=Path(self.settings.resolved_data_dir) / ".agents",
            skill_manager=self.skill_manager,
            agent_def_manager=self.agent_def_manager,
            prompt_manager=self.prompt_manager,
            mcp_manager=self.mcp_manager,
            event_bus=self.event_bus,
            file_instruction_manager=FileInstructionManager(...),
            instructions_manager=InstructionsManager(...),
            settings=self.settings,
        )
        # 3. 启动时同步
        await self.customization_engine.initialize()

    def wire_agent_service(self) -> None:
        # 将 customization_engine 注入 AgentService
        self.agent_service._customization_engine = self.customization_engine
```

### 服务间关系总图

```text
CustomizationEngine
  ├── InstructionsManager        → 加载 AGENTS.md
  ├── FileInstructionManager     → 加载 .instructions.md + applyTo 匹配
  ├── SkillManager               → SKILL.md + rules/*
  ├── AgentDefManager            → .agent.md 文件
  ├── PromptManager              → .prompt.md 文件
  ├── McpManager                 → mcp-servers.json 连接
  └── EventBus + FileHookLoader  → hooks/*.json 注册
       │
       ▼
AgentService
  ├── 使用 SessionContext.system_prompt 构建 System Message
  ├── 使用 SessionContext.extension_catalog 注入扩展清单
  ├── 使用 SessionContext.allowed_tools 限制工具（→ PolicyEngine）
  └── 使用 SessionContext.agent_def 决定 Agent 身份
       │
       ▼
ExecutionHarness
  ├── EventBus 触发 Hook（代码 + 文件）
  ├── FileInstructionManager.match() 注入文件指令
  └── PolicyEngine.check_tool() 基于 allowed_tools
```
