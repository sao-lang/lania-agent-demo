# Lania Agent 平台扩展设计方案

> 版本: v1.0  
> 日期: 2026-07-04  
> 状态: 草案

---

## 目录

1. [现状与目标](#1-现状与目标)
2. [总体架构](#2-总体架构)
3. [Phase 1: Sandbox 命令执行](#3-phase-1-sandbox-命令执行)
4. [Phase 2: Coding Agent](#4-phase-2-coding-agent)
5. [Phase 3: Data Analysis Agent](#5-phase-3-data-analysis-agent)
6. [API 设计](#6-api-设计)
7. [扩展点总览](#7-扩展点总览)
8. [实施路线](#8-实施路线)

---

## 1. 现状与目标

### 1.1 当前能力

| 维度 | 现状 |
|------|------|
| **已实现的任务类型** | `document_analysis`（文档分析）、`document_summary`（文档摘要） |
| **Agent 类型** | EvidenceAgent、ReportingAgent、ReviewAgent、ContractAgent（均为文档分析领域） |
| **工具** | 5 个 RAG 工具 + 分析/报告/仓库/契约/数据库/产物 工具 |
| **沙盒** | `ToolSandbox` 框架完整，`command_tools.py` 已实现 Shell 命令执行 + 安全策略校验 |
| **治理** | Guardrail / Policy / Sandbox / Budget 体系完整 |
| **评测** | RAGAS / Benchmark / Baseline / 趋势分析 完整 |

### 1.2 核心问题

1. **业务场景偏少** — 文档分析之外，code_review / data_analysis / web_search 的 Capability 已实现，但缺少完整的 Workflow 级别任务类型
2. **沙盒能力已可用** — `command_tools.py` 已实现，含命令白名单/黑名单/危险模式拦截
3. **缺少"执行型"Agent** — 现有 Agent 都是"读"（检索、分析、报告），没有"写"（改代码、执行命令、生成图表）
4. **没有前端** — 只有 API，无法直观展示 Agent 编排过程

### 1.3 目标

| 阶段 | 目标 | 交付价值 |
|------|------|----------|
| **Phase 1** | 补全 Sandbox 命令执行能力 | Agent 能真正执行代码/脚本 |
| **Phase 2** | 新增 Coding Agent 任务类型 | Agent 能读代码、改代码、运行测试 |
| **Phase 3** | 新增 Data Analysis Agent 任务类型 | Agent 能查数据库、做分析、出图表 |

---

## 2. 总体架构

### 2.1 扩展后的架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Entry Layer (API)                           │
│  POST /api/v1/tasks/document-analysis                               │
│  POST /api/v1/tasks/coding-review          ← 新增                   │
│  POST /api/v1/tasks/data-analysis          ← 新增                   │
│  POST /api/v1/tasks/execution              ← 新增（通用命令执行）    │
└──────────────────────┬──────────────────────────────────────────────┘
                       ↓
┌──────────────────────┴──────────────────────────────────────────────┐
│                      Workflow Layer (LangGraph)                     │
│                                                                     │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │ DocumentAnalysis│  │  CodingReview   │  │  DataAnalysis   │     │
│  │    Workflow     │  │    Workflow     │  │    Workflow     │     │
│  │  (已有)          │  │  (新增)         │  │  (新增)         │     │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘     │
│           ↓                    ↓                    ↓              │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │              HarnessKernel (复用)                           │    │
│  │  ExecutionHarness | ContextHarness | GuardrailEngine        │    │
│  │  PolicyEngine | ToolSandbox | ModelRouter | Reflection     │    │
│  └────────────────────────┬───────────────────────────────────┘    │
└───────────────────────────┼────────────────────────────────────────┘
                            ↓
┌───────────────────────────┴────────────────────────────────────────┐
│                      Capability Layer                               │
│                                                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │Knowledge │ │Repository│ │ ApiCon-  │ │ Database │ │ Sandbox  │ │
│  │  (已有)  │ │  (已有)  │ │ tract    │ │  (已有)  │ │ Execute  │ │
│  │          │ │          │ │ (已有)   │ │          │ │ (新增)   │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 新增模块关系

```
app/
├── capabilities/
│   └── sandbox_execute/          ← 新增：沙盒执行能力
│       ├── __init__.py
│       ├── base.py               ← Protocol 定义
│       ├── service.py            ← 本地子进程实现
│       └── contracts.py          ← 输入/输出/安全策略模型
│
├── agents/
│   └── tools/
│       ├── command_tools.py      ← 改造：从存根改为实际实现
│       ├── coding_tools.py       ← 新增：代码分析/修改/搜索工具
│       └── analysis_tools.py     ← 扩展：增加数据分析相关工具
│
├── workflows/
│   └── tasks/
│       ├── coding_review_skill.py      ← 新增
│       ├── coding_review_graph.py      ← 新增
│       ├── coding_review_nodes.py      ← 新增
│       ├── data_analysis_skill.py      ← 新增
│       ├── data_analysis_graph.py      ← 新增
│       └── data_analysis_nodes.py      ← 新增
│
└── agents/
    └── subagents/
        ├── coding_agent.py       ← 新增
        └── analysis_agent.py     ← 新增
```

---

## 3. Phase 1: Sandbox 命令执行

### 3.1 目标

把 `command_tools.py` 从存根变成可用的沙盒命令执行工具，并配套完整的 Capability 层和安全策略。

### 3.2 设计要点

#### 3.2.1 SandboxExecute Capability（新增）

遵循现有 Capability 模式（参考 `KnowledgeCapability`），定义 `SandboxExecuteCapability` Protocol：

```python
# app/capabilities/sandbox_execute/base.py

class CommandSecurityPolicy(BaseModel):
    """命令安全策略。"""
    allowed_commands: list[str]       # 白名单命令列表，空 = 全部禁止
    blocked_commands: list[str]       # 黑名单命令列表
    allowed_paths: list[str]          # 允许的工作目录前缀
    max_output_bytes: int = 1_000_000 # 输出上限
    enable_network: bool = False      # 是否允许网络访问
    enable_filesystem_write: bool = False  # 是否允许写文件
    timeout_seconds_max: int = 300    # 最大超时


class CommandExecutionRequest(BaseModel):
    """命令执行请求。"""
    command: str
    args: list[str] = []
    working_directory: str | None = None
    timeout_seconds: int = 30
    env_overrides: dict[str, str] = {}  # 环境变量覆盖


class CommandExecutionResult(BaseModel):
    """命令执行结果。"""
    stdout: str = ''
    stderr: str = ''
    exit_code: int = 0
    truncated: bool = False
    duration_ms: int = 0


class SandboxExecuteCapability(Protocol):
    """沙盒执行能力协议。"""
    
    def execute(
        self,
        request: CommandExecutionRequest,
        policy: CommandSecurityPolicy,
    ) -> CommandExecutionResult:
        ...
```

#### 3.2.2 安全策略等级

参考已有 `harness-policy-profiles.yaml` 模式，定义三级策略：

| 等级 | 允许的命令 | 网络 | 写文件 | 适用场景 |
|------|-----------|------|--------|---------|
| **sandboxed** | `python`, `pip`, `git`（只读） | ❌ | ❌ | Coding Agent 代码检查 |
| **restricted** | `python`, `pip`, `node`, `npm`, `git`, `ls`, `cat`, `head`, `tail`, `grep`, `find` | ❌ | ✅（仅 /tmp） | 数据分析 Agent |
| **standard** | 同 restricted + `curl`, `wget`, `docker` | ✅ | ✅（限定路径） | 运维/部署 Agent |

#### 3.2.3 命令工具改造

`ShellCommandTool` 的实现：

```python
class ShellCommandTool(BaseCommandTool):
    """Shell 命令执行工具。"""
    
    name = 'shell_command'
    description = '在沙盒子进程中执行系统命令'
    risk_level = 'high'
    sandbox_mode = 'process_isolated'
    timeout_ms = 30000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=500)
    input_model = CommandInput
    output_model = CommandOutput

    def run(self, payload: CommandInput, context: ToolContext) -> CommandOutput:
        # 1. 通过 PolicyEngine 获取当前步骤的安全策略
        security_policy = self._resolve_policy(context)
        
        # 2. 通过 ToolSandbox 做沙盒决策
        sandbox_decision = context.sandbox.assess(
            tool_name=self.name,
            context_bundle=...
        )
        if not sandbox_decision.allowed:
            raise ToolExecutionError(...)
        
        # 3. 通过 SandboxExecuteCapability 执行
        capability: SandboxExecuteCapability = context.sandbox_execute
        result = capability.execute(
            CommandExecutionRequest(
                command=payload.command,
                args=payload.args,
                working_directory=payload.working_directory,
                timeout_seconds=payload.timeout_seconds,
            ),
            policy=security_policy,
        )
        
        return CommandOutput(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            truncated=result.truncated,
        )
```

#### 3.2.4 进程隔离执行

`LocalSandboxExecuteCapability` 使用 `subprocess` + 资源限制：

```python
class LocalSandboxExecuteCapability:
    """本地子进程沙盒执行。"""
    
    def execute(self, request, policy):
        # 1. 安全检查
        self._validate_command(request.command, policy)
        self._validate_path(request.working_directory, policy)
        
        # 2. 准备执行环境
        env = os.environ.copy()
        env.update(request.env_overrides)
        if not policy.enable_network:
            env['HTTP_PROXY'] = ''
            env['HTTPS_PROXY'] = ''
            env['NO_PROXY'] = '*'
        
        # 3. 子进程执行（带超时和输出截断）
        start = monotonic()
        proc = subprocess.run(
            [request.command, *request.args],
            cwd=request.working_directory,
            env=env,
            capture_output=True,
            text=True,
            timeout=request.timeout_seconds,
        )
        duration_ms = int((monotonic() - start) * 1000)
        
        # 4. 输出截断保护
        stdout = proc.stdout[:policy.max_output_bytes]
        stderr = proc.stderr[:policy.max_output_bytes]
        truncated = len(proc.stdout) > policy.max_output_bytes
        
        return CommandExecutionResult(
            stdout=stdout, stderr=stderr,
            exit_code=proc.returncode,
            truncated=truncated,
            duration_ms=duration_ms,
        )
```

#### 3.2.5 Docker 可选隔离

当配置 `SANDBOX_EXECUTOR_PROVIDER=docker` 时，使用 Docker 容器执行，提供更强的隔离。

---

## 4. Phase 2: Coding Agent

### 4.1 目标

新增 `coding_review` 任务类型，Agent 能够：
1. 读取仓库代码文件
2. 搜索相关代码片段
3. 执行代码分析（lint / type check / test）
4. 生成代码审查报告

### 4.2 任务流程

```
Coding Review Workflow

PlanStage
  └─ 根据 instructions 生成审查计划
     ├─ 确定审查范围（文件/模块）
     ├─ 确定审查维度（架构/风格/安全/性能）
     └─ 规划工具调用序列

CollectCodeContextStage
  └─ 调用 repository 工具获取代码
     ├─ list_repository_files → 确定文件列表
     ├─ read_repository_file  → 读取关键文件
     └─ search_repository     → 搜索相关模式

RunAnalysisStage
  └─ 执行代码分析
     ├─ shell_command("python -m pyflakes {file}")  → 静态分析
     ├─ shell_command("python -m mypy {file}")      → 类型检查（如有配置）
     └─ shell_command("python -m pytest {path}")    → 运行测试（可选）

AnalyzeStage
  └─ 综合代码上下文和分析结果
     ├─ extract_code_issues    → 提取代码问题
     ├─ classify_severity      → 严重程度分类
     └─ suggest_fixes          → 建议修复方案

DraftReviewStage
  └─ 生成审查报告
     ├─ 问题列表（位置/严重度/描述）
     ├─ 改进建议
     ├─ 良好实践
     └─ 总体评价

ReviewDraftStage → FinalizeStage
  └─ 审查与定稿
```

### 4.3 新增工具

#### 4.3.1 `extract_code_issues`

```python
class ExtractCodeIssuesInput(BaseModel):
    """代码问题提取输入。"""
    files: list[CodeFile]           # 文件路径+内容列表
    lint_results: list[LintResult]  # 静态分析结果
    test_results: list[TestResult]  # 测试结果（可选）
    focus_dimensions: list[str]     # 审查维度


class CodeIssue(BaseModel):
    """单个代码问题。"""
    issue_id: str
    file_path: str
    line_start: int
    line_end: int
    severity: Literal['critical', 'major', 'minor', 'info']
    category: Literal[
        'architecture', 'security', 'performance',
        'style', 'correctness', 'maintainability'
    ]
    title: str
    description: str
    suggestion: str | None = None
    source: Literal['linter', 'llm', 'test'] = 'llm'


class ExtractCodeIssuesOutput(BaseModel):
    """代码问题提取输出。"""
    issues: list[CodeIssue]
    summary: str
    overall_score: float  # 0-100
```

#### 4.3.2 `run_code_analysis`

```python
class RunCodeAnalysisInput(BaseModel):
    """代码分析执行输入。"""
    files: list[str]
    checks: list[Literal['pyflakes', 'mypy', 'pytest', 'bandit', 'radon']]
    working_directory: str


class RunCodeAnalysisOutput(BaseModel):
    """代码分析执行输出。"""
    results: dict[str, list[LintResult]]
    summary: str
```

### 4.4 CodingReview Skill 注册

遵循 `TaskSkill` 协议，在 `build_default_task_skill_registry()` 中注册：

```python
# app/workflows/tasks/builtin_skills.py (扩展)
BUILTIN_SKILL_SPECS: dict[str, dict[str, Any]] = {
    'document_analysis': { ... },
    'document_summary': { ... },
    'coding_review': {                       # ← 新增
        'task_type': 'coding_review',
        'skill_name': 'Coding Review Agent',
        'description': '对仓库代码进行自动化审查，发现潜在问题并给出改进建议',
        'supported_output_formats': ['markdown', 'json', 'markdown+json'],
        'default_max_steps': 10,
    },
}
```

### 4.5 CodingReview API 请求

```json
POST /api/v1/tasks/coding-review

{
  "task_type": "coding_review",
  "instructions": "审查 app/harness/ 目录下的代码，重点关注安全性和错误处理",
  "target_paths": ["app/harness/"],
  "review_dimensions": ["security", "correctness", "error_handling"],
  "run_linter": true,
  "run_type_checker": false,
  "run_tests": false,
  "output_format": "markdown+json",
  "constraints": {
    "max_steps": 10,
    "language": "zh-CN"
  }
}
```

---

## 5. Phase 3: Data Analysis Agent

### 5.1 目标

新增 `data_analysis` 任务类型，Agent 能够：
1. 查询数据库（SQLite）
2. 执行 Python 数据分析（pandas / numpy）
3. 生成可视化图表
4. 生成数据分析报告

### 5.2 任务流程

```
Data Analysis Workflow

PlanStage
  └─ 理解分析需求，生成分析计划

ExploreDataStage
  ├─ list_database_tables    → 了解数据源
  ├─ describe_database_table → 了解表结构
  └─ query_database          → 获取数据样例

RunAnalysisStage
  ├─ shell_command("python analysis.py")  → 执行分析脚本
  └─ 分析脚本由 LLM 动态生成

GenerateVisualizationStage
  ├─ shell_command("python chart.py")     → 生成图表
  └─ 图表保存为 artifact

InterpretStage
  └─ LLM 基于分析结果和图表生成解读

DraftReportStage → ReviewStage → FinalizeStage
  └─ 生成最终分析报告
```

### 5.3 新增 / 扩展工具

#### 5.3.1 `generate_analysis_code`

```python
class GenerateAnalysisCodeInput(BaseModel):
    """分析代码生成输入。"""
    question: str
    table_schemas: list[TableSchema]
    sample_data: list[dict[str, Any]]
    analysis_type: Literal['statistical', 'trend', 'comparison', 'custom']


class GenerateAnalysisCodeOutput(BaseModel):
    """分析代码生成输出。"""
    python_code: str
    required_packages: list[str]
    description: str
```

#### 5.3.2 `generate_chart`

```python
class GenerateChartInput(BaseModel):
    """图表生成输入。"""
    data: list[dict[str, Any]]
    chart_type: Literal['bar', 'line', 'pie', 'scatter', 'histogram', 'heatmap']
    x_field: str
    y_field: str | None = None
    group_field: str | None = None
    title: str = ''
    output_format: Literal['png', 'svg', 'html'] = 'png'


class GenerateChartOutput(BaseModel):
    """图表生成输出。"""
    artifact_id: str
    chart_type: str
    title: str
    format: str
    summary: str  # LLM 对图表的解读
```

### 5.4 DataAnalysis API 请求

```json
POST /api/v1/tasks/data-analysis

{
  "task_type": "data_analysis",
  "instructions": "分析 data/eval 目录下的 benchmark 数据，给出趋势分析和性能变化",
  "data_sources": [
    {
      "type": "database",
      "connection": "sqlite_local",
      "tables": ["benchmark_runs", "benchmark_results"]
    },
    {
      "type": "file",
      "path": "data/eval/latest.json",
      "format": "json"
    }
  ],
  "analysis_goals": ["trend_analysis", "anomaly_detection"],
  "generate_charts": true,
  "output_format": "markdown+json",
  "constraints": {
    "max_steps": 12,
    "language": "zh-CN"
  }
}
```

---

## 6. API 设计

### 6.1 新端点总览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/tasks/coding-review` | 创建代码审查任务 |
| POST | `/api/v1/tasks/data-analysis` | 创建数据分析任务 |
| POST | `/api/v1/tasks/exec` | 创建通用命令执行任务 |
| GET | `/api/v1/tasks/types` | 列出所有可用任务类型 |
| GET | `/api/v1/tasks/capabilities` | 列出平台所有可用能力 |

### 6.2 现有端点复用

| 端点 | 用途 |
|------|------|
| `GET /api/v1/tasks` | 列出所有任务（含新类型） |
| `GET /api/v1/tasks/{task_id}` | 任务详情（含子 Agent 轨迹） |
| `GET /api/v1/tasks/{task_id}/artifacts` | 产物（含图表 artifact） |
| `POST /api/v1/tasks/{task_id}/retry` | 重试 |
| `GET /api/v1/tasks/runs/{run_id}/replay` | 重放 |
| `GET /api/v1/tasks/tools` | 工具 schema（自动包含新工具） |

### 6.3 任务类型发现

```json
GET /api/v1/tasks/types

{
  "task_types": [
    {
      "type": "document_analysis",
      "name": "Document Analysis Agent",
      "description": "对集合中的文档进行深度分析并生成结构化报告",
      "supported_formats": ["markdown", "json", "markdown+json"],
      "max_steps": 16
    },
    {
      "type": "coding_review",
      "name": "Coding Review Agent",
      "description": "对仓库代码进行自动化审查，发现潜在问题并给出改进建议",
      "supported_formats": ["markdown", "json", "markdown+json"],
      "max_steps": 10
    },
    {
      "type": "data_analysis",
      "name": "Data Analysis Agent",
      "description": "对数据进行查询、分析和可视化，生成数据分析报告",
      "supported_formats": ["markdown", "json", "markdown+json"],
      "max_steps": 12
    }
  ]
}
```

---

## 7. 扩展点总览

### 7.1 新增一个任务类型需要修改的文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/workflows/tasks/{name}_skill.py` | **新增** | 定义 TaskSkill 实现 |
| `app/workflows/tasks/{name}_graph.py` | **新增** | 定义 LangGraph StateGraph |
| `app/workflows/tasks/{name}_nodes.py` | **新增** | 定义工作流节点函数 |
| `app/workflows/tasks/builtin_skills.py` | **修改** | 注册新 skill 元信息 |
| `app/agents/subagents/{name}_agent.py` | **新增** | 定义领域子 Agent（可选） |
| `app/agents/tools/{name}_tools.py` | **新增** | 定义领域工具（可选） |
| `app/container.py` | **修改** | 注册新 orchestator / tools |
| `app/api/v1/endpoints/tasks.py` | **修改** | 添加新端点（可选） |

### 7.2 修改量估算

| 阶段 | 新增文件 | 修改文件 | 估算代码量 |
|------|---------|---------|-----------|
| Phase 1: Sandbox | 4 | 3 | ~600 行 |
| Phase 2: Coding Agent | 5 | 5 | ~1500 行 |
| Phase 3: Data Analysis | 6 | 4 | ~1800 行 |

### 7.3 复用度分析

| 组件 | Phase 1 | Phase 2 | Phase 3 |
|------|---------|---------|---------|
| `HarnessKernel` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `ExecutionHarness` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `GuardrailEngine` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `PolicyEngine` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `ToolSandbox` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `ModelRouter` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `ToolRegistry` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `SubAgentRuntime` | — | ✅ 复用模式 | ✅ 复用模式 |
| `TaskWorker` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `SQLiteStateStore` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |
| `/health` / `/metrics` | ✅ 完全复用 | ✅ 完全复用 | ✅ 完全复用 |

---

## 8. 实施路线

### 8.1 优先级建议

```
Phase 1 (Sandbox 执行)
  ├── 最高优先级，因为 Phase 2/3 都依赖它
  └── 预计工时: 2-3 天

Phase 2 (Coding Agent)
  ├── 高优先级，展示 Agent 执行能力的杀手场景
  └── 预计工时: 4-5 天

Phase 3 (Data Analysis Agent)
  ├── 中优先级，进一步增强平台价值
  └── 预计工时: 4-5 天
```

### 8.2 Phase 1 子任务分解

```
Phase 1: Sandbox 命令执行
├── 1.1 定义 SandboxExecuteCapability Protocol
│   ├── app/capabilities/sandbox_execute/base.py
│   └── input/output/security policy 模型
│
├── 1.2 实现 LocalSandboxExecuteCapability
│   ├── app/capabilities/sandbox_execute/service.py
│   └── subprocess 执行 + 超时 + 输出截断 + 环境清理
│
├── 1.3 实现安全策略引擎
│   ├── app/capabilities/sandbox_execute/policy.py
│   ├── 命令白名单/黑名单
│   ├── 路径越界保护
│   └── 网络/文件写控制
│
├── 1.4 改造 command_tools.py
│   ├── ShellCommandTool.run() 实际实现
│   ├── RepositoryCommandTool.run() 实际实现
│   └── 接入 PolicyEngine + ToolSandbox
│
├── 1.5 容器装配
│   ├── app/container.py — 注册 SandboxExecuteCapability
│   ├── app/container.py — 注册新 tool 实例
│   └── config/harness-policy-profiles.yaml — 添加命令执行策略
│
└── 1.6 测试
    ├── tests/test_sandbox_execute.py
    └── tests/test_command_tools.py
```

### 8.3 实施原则

1. **渐进式** — 每个 Phase 独立可上线，不阻塞
2. **向后兼容** — 不修改已有 API 的请求/响应格式
3. **复用优先** — 尽可能复用现有 Harness / Tool / SubAgent 模式
4. **安全第一** — 命令执行默认禁止，通过 Policy 显式放开
5. **可观测** — 所有新工具调用自动 trace，暴露给 `/metrics` 和任务详情

---

## 附录

### A. 环境变量新增

```bash
# Sandbox 执行
SANDBOX_EXECUTOR_PROVIDER=local_process    # local_process | docker
SANDBOX_EXECUTOR_DEFAULT_POLICY=sandboxed  # sandboxed | restricted | standard
SANDBOX_EXECUTOR_ALLOW_LOCAL_FALLBACK=true
SANDBOX_EXECUTOR_TIMEOUT_SECONDS=30
SANDBOX_EXECUTOR_MAX_OUTPUT_BYTES=1000000

# Coding Agent
ENABLE_CODING_LINTER=true
ENABLE_CODING_TYPE_CHECKER=false
CODING_DEFAULT_REVIEW_DIMENSIONS=security,correctness,style

# Data Analysis Agent
ANALYSIS_ALLOWED_PACKAGES=pandas,numpy,matplotlib,seaborn,scipy
ANALYSIS_MAX_OUTPUT_ROWS=1000
ANALYSIS_ENABLE_CHART_GENERATION=true
```

### B. 安全边界矩阵

| 操作 | Sandboxed | Restricted | Standard |
|------|-----------|------------|----------|
| `python` 执行脚本 | ✅ | ✅ | ✅ |
| `pip install` | ❌ | ✅（限预批准包） | ✅ |
| `git clone` | ❌ | ❌ | ✅ |
| `curl` 外部 API | ❌ | ❌ | ✅ |
| 写文件到仓库目录 | ❌ | ❌ | ❌ |
| 写文件到 /tmp | ❌ | ✅ | ✅ |
| 读取任意文件 | ❌ | ✅（限仓库路径） | ✅ |
| 网络访问 | ❌ | ❌ | ✅（限白名单） |
| Docker 操作 | ❌ | ❌ | ❌ |
| 环境变量泄露 | 自动清理 | 自动清理 | 自动清理敏感项 |
