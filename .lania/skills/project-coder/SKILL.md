---
name: "project-coder"
description: "面向本仓库的日常编码 skill。Invoke when modifying this FastAPI RAG project for feature work, bug fixes, refactors, tests, or API/service/workflow changes."
---

# Project Coder

## Purpose

这个 skill 用于当前仓库的日常编码工作，目标是在有限上下文下快速定位到正确模块，按现有工程边界实现改动，并完成最小必要验证。

适用范围：

- 新增或修改 FastAPI 接口
- 调整 service、workflow、agent、capability、rag 链路
- 修复 bug
- 补充或修复 `unittest`
- 做小到中等规模重构

不适用范围：

- 纯代码审查
- 需要浏览器自动化的前端页面测试
- 与本仓库无关的通用编码任务

## Project Baseline

在开始编码前，默认假设本项目具备以下特征：

- Python 项目
- Web 框架：`FastAPI`
- RAG 相关组件：`LlamaIndex`、`ChromaDB`、`langgraph`
- 依赖装配入口：`app/container.py`
- 应用启动入口：`app/main.py`
- API 聚合入口：`app/api/router.py`
- 测试风格：`unittest`
- 类型检查：`pyright` + `mypy`

目录职责基线：

- `app/api/v1/endpoints/`：HTTP 路由层，保持薄，主要做请求/响应编排
- `app/services/`：业务服务层
- `app/models/`：请求、响应、领域模型
- `app/rag/`：检索、向量、ingestion、query engine
- `app/workflows/`：编排与 runtime
- `app/agents/`：任务代理、工具、子代理
- `app/capabilities/`：能力抽象与 provider 装配
- `app/harness/`：harness runtime 与策略能力
- `tests/`：以 `test_*.py` 命名的单元测试
- `docs/architecture/`：架构设计与迁移文档

## Default Loading Order

除非用户已经明确指出文件，否则按最小上下文顺序加载：

1. `README.md`
2. `docs/architecture/README.md`
3. 与任务直接相关的入口文件

按场景补充：

### 1. 普通接口或服务改动

优先读取：

- `app/main.py`
- `app/api/router.py`
- 对应 `app/api/v1/endpoints/*.py`
- 对应 `app/services/*.py`
- 对应 `app/models/*.py`

### 2. Query / Task / Agent / Harness 相关改动

先读取：

- `docs/architecture/harness-runtime-contracts.md`
- `docs/architecture/harness-composition-migration-checklist.md`

如果问题涉及能力归口或架构边界，再补：

- `docs/architecture/agent-capability-management-design.md`
- `docs/architecture/harness-capability-integration.md`

### 3. 需要理解重构方向

读取：

- `docs/architecture/harness-composition-refactor-plan.md`
- `docs/architecture/harness-runtime-contracts.md`

规则：

- 不要默认一次性加载所有架构文档
- 先读入口，再读直接依赖，再读设计文档
- 只在实现确实依赖架构判断时扩展上下文

## Coding Workflow

### Step 1. 先判断改动落点

基于需求先确定应该落在哪一层：

- HTTP 行为变化：`app/api/v1/endpoints/`
- 业务编排变化：`app/services/`
- 数据模型变化：`app/models/`
- 检索、索引、问答链路：`app/rag/`
- runtime / workflow / step 编排：`app/workflows/`
- agent、tool、sub-agent：`app/agents/`
- provider 抽象或能力工厂：`app/capabilities/`
- 全局装配与依赖注册：`app/container.py`
- 配置项：`app/core/config.py`

### Step 2. 复用现有模式

实现时遵守这些本地约束：

- 保持 endpoint 薄，把业务放到 service 或 workflow
- 新依赖优先在 `app/container.py` 装配，不在路由层直接 new
- 优先复用已有 `Pydantic` 模型和 service 接口
- 新增能力时，先看是否已有同类 `service / capability / tool / orchestrator` 模式
- 改动 query/task 主线时，避免跳过既有 runtime、memory、trace、policy 结构
- 优先做局部增量修改，不顺手重写邻近模块

### Step 3. 同步补测试

默认要求：

- 有行为变化就补测试
- 测试优先放在现有对应模块附近的 `tests/test_*.py`
- 延续 `unittest` 风格
- 新增接口时，优先参考已有 endpoint/service 测试写法
- 新增 orchestrator 或 runtime 行为时，覆盖成功路径和失败路径

### Step 4. 做最小必要验证

优先按影响范围执行：

```bash
.venv/bin/python -m unittest tests/test_xxx.py
npx pyright
.venv/bin/mypy
```

如果是 task / document analysis 主线，可优先参考 README 中已有验证集合，例如：

```bash
.venv/bin/python -m unittest \
  tests.test_task_service \
  tests.test_task_endpoints \
  tests.test_error_responses \
  tests.test_document_analysis_benchmark \
  tests.test_task_worker \
  tests.test_task_llm_tools
```

验证策略：

- 小改动先跑相关单测
- 跨模块改动再补类型检查
- 无法运行的命令要明确说明原因，不要假装通过

## Change Heuristics

### 新增接口

通常按这个顺序检查是否需要改动：

1. `app/models/`
2. `app/services/`
3. `app/api/v1/endpoints/`
4. `app/api/router.py`
5. `tests/`

### 新增任务工具或 agent 能力

通常按这个顺序检查：

1. `app/agents/tools/` 或 `app/agents/`
2. `app/capabilities/` 或 `app/services/`
3. `app/container.py`
4. `app/workflows/tasks/` 或 `app/workflows/`
5. `tests/`

### 修改 RAG 检索或 ingestion

通常按这个顺序检查：

1. `app/rag/` 相关实现
2. 上游 service / workflow 调用点
3. 配置项与兼容逻辑
4. 回归测试

## Output Requirements

完成后应给出：

1. 改了哪些文件
2. 改动为何放在这些层
3. 跑了哪些验证
4. 哪些验证未跑以及原因

## Trigger Examples

以下场景应触发本 skill：

- “给这个 RAG 项目加一个新的 API”
- “修一下 task workflow 的重试逻辑”
- “补这个 service 的单测”
- “改 document ingestion 的解析行为”
- “把 query runtime 的某段逻辑重构一下”
