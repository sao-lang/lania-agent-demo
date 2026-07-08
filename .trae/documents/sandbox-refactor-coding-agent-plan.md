# Sandbox 重构 + Coding Agent 实现计划

## 背景

用户要求两个任务：
1. **重构 Sandbox**：将扁平化的 `command_tools.py` 重构为符合原始设计的三层架构，抽取独立的 `SandboxExecuteCapability`
2. **实现 Coding Agent**：新增 `coding` Capability，支持实际执行 lint/静态分析工具（区别于纯 LLM 审查的 `code_review`）

---

## 一、Task 1: 重构 Sandbox（抽取 SandboxExecuteCapability）

### 1.1 新建文件

#### `app/capabilities/sandbox_execute/__init__.py`
导出 Protocol、模型、策略工厂函数和实现类。

#### `app/capabilities/sandbox_execute/base.py`
定义：
- `CommandSecurityPolicy` 模型（含 `allowed_commands`, `blocked_commands`, `blocked_patterns`, `enable_network`, `enable_filesystem_write`, `writable_paths` 等字段）
- `CommandExecutionRequest` / `CommandExecutionResult` 模型
- `SandboxExecuteCapability` Protocol：`execute(request, policy) -> CommandExecutionResult`
- 三级策略工厂：`build_sandboxed_policy()`, `build_restricted_policy()`, `build_standard_policy()`

#### `app/capabilities/sandbox_execute/service.py`
实现 `LocalSandboxExecuteCapability`：
- 从 `command_tools.py` 迁移 `validate_command` 和 `execute_command` 逻辑
- 安全校验：命令白名单/黑名单/正则模式拦截
- 子进程执行：`subprocess.run` + 超时 + 输出截断
- 网络隔离：通过环境变量 `HTTP_PROXY=''` 等阻断

### 1.2 修改文件

#### `app/agents/tools/command_tools.py`
- 删除迁移的常量：`DEFAULT_ALLOWED_COMMANDS`, `DEFAULT_BLOCKED_COMMANDS`, `DEFAULT_BLOCKED_PATTERNS`
- 删除迁移的函数：`validate_command`, `execute_command`
- `ShellCommandTool.run()` 和 `RepositoryCommandTool.run()` 改为通过 `context.services.get('sandbox_execute')` 调用
- 保留 `CommandInput`/`CommandOutput` 模型（工具层抽象）和 `RepositoryCommandTool` 的仓库路径解析逻辑

#### `app/core/config.py`
新增配置项：
- `sandbox_executor_default_policy`（默认 `"sandboxed"`）

#### `app/container.py`
- 创建 `LocalSandboxExecuteCapability` 实例
- 注入到 `external_services` dict 中：`'sandbox_execute': self.sandbox_execute_capability`

---

## 二、Task 2: 实现 Coding Agent

### 2.1 新建文件

#### `app/agents/tools/coding_tools.py`
两个新工具：
- `ExtractCodeIssuesTool`：LLM 分析代码文件，结合 lint 结果，提取结构化问题列表
- `RunCodeAnalysisTool`：通过 `sandbox_execute` 执行 pyflakes/mypy/pytest 等工具，解析输出

#### `app/capabilities/coding/__init__.py`
导出 `CodingCapability`。

#### `app/capabilities/coding/service.py`
`CodingCapability` 实现 `CapabilityProvider` 协议，6 阶段工作流：
1. **Plan** → 确定审查范围和维度
2. **CollectCodeContext** → 读取目标代码文件（≤15 个）
3. **RunAnalysis** → 执行 lint/静态分析工具
4. **Analyze** → LLM 多维度分析
5. **DraftReview** → 生成结构化审查报告
6. **Finalize** → 完成

关键设计：如 `sandbox_execute` 不可用，lint 阶段降级但不阻塞。

### 2.2 修改文件

#### `app/capabilities/registry.py`
- `build_default_capabilities()` 中添加 `coding` 的 `CapabilityDefinition`
- `match_by_keywords()` 中添加 coding 关键词匹配

#### `app/services/agent_service.py`
- 注册 `CodingCapability(llm=llm)` 为 provider

#### `app/services/intent_matcher.py`
- 添加 coding 关键词规则

#### `app/container.py`
- 注册 `ExtractCodeIssuesTool()` 和 `RunCodeAnalysisTool()` 到 `task_tool_registry`

---

## 三、实现顺序

1. 创建 `sandbox_execute/base.py`（Protocol + 模型）
2. 创建 `sandbox_execute/service.py`（实现）
3. 创建 `sandbox_execute/__init__.py`
4. 修改 `config.py`（加配置项）
5. 修改 `command_tools.py`（重构）
6. 修改 `container.py`（注册 sandbox_execute）
7. 创建 `coding_tools.py`
8. 创建 `coding/service.py`
9. 创建 `coding/__init__.py`
10. 修改 `registry.py`（注册 coding capability）
11. 修改 `agent_service.py`（注册 provider）
12. 修改 `intent_matcher.py`（关键词）
13. 修改 `container.py`（注册 coding tools）

---

## 四、验证

1. 启动服务：`python -m uvicorn app.main:app`
2. 测试 Sandbox：沙盒执行 `echo hello` 成功，`curl` 在 sandboxed 策略下被拒绝
3. 测试 Coding Agent：`POST /agent/chat {"message": "帮我审查 app/harness/ 目录的代码", "mode": "chat"}` 返回结构化审查报告
4. 运行现有测试确保无回归