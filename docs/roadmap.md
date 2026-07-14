# Lania Agent 系统架构全景图

> 本文档基于 `intent-recognition-module-design.md` (v5.0) 和 `agent-customization-primitives-design.md` 两份设计文档，以及当前代码实现，绘制完整的系统架构图。
> 先逐一解释每个模块的内部架构与链路，再通过总结构图串联所有模块。

---

# 第一部分：各模块内部架构与链路

---

## 1. 大脑层 (Brain Layer) — 感知 + 决策

> 设计文档对应：`intent-recognition-module-design.md` 第4-5章
> 代码位置：`app/agent_platform/agents/brain/`

### 1.1 模块定位

```
┌── harness/brain/（大脑层）──────────────────────────────────┐
│                                                              │
│  ┌─ 感知层（Perception）— 一次调用，设定上下文 ────────────┐ │
│  │                                                        │ │
│  │  IntentRecognizer  →  这是什么问题？                     │ │
│  │                       复杂度多高？                       │ │
│  │                       需要什么知识来源？                  │ │
│  │                       风险有多大？                       │ │
│  │                       建议什么模式？                     │ │
│  │                                                        │ │
│  │  输出: IntentDecision（一次性，会话开始时）               │ │
│  │                                                        │ │
│  └────────────────────────────────────────────────────────┘ │
│                            │                                  │
│                            ▼                                  │
│  ┌─ 决策层（Decision）— 多轮循环，在上下文中持续决策 ─────┐ │
│  │                                                        │ │
│  │  ModeRouter  →  根据 IntentDecision 决定最终执行模式      │ │
│  │                                                        │ │
│  │  AgentLoop   →  在确定模式下，逐轮决策：                  │ │
│  │                  调哪个工具？参数是什么？                  │ │
│  │                  结果够了没有？要不要继续？                │ │
│  │                                                        │ │
│  │  输出: 多轮工具调用 + 最终回答（持续整个会话）             │ │
│  │                                                        │ │
│  └────────────────────────────────────────────────────────┘ │
│                                                              │
│  全部 LLM 驱动，不是关键词驱动                                │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 IntentRecognizer — 统一意图识别

```
┌─────────────────────────────────────────────────────────────────────┐
│                        IntentRecognizer                              │
│  代码: app/agent_platform/agents/brain/intent_recognizer.py         │
│                                                                      │
│  输入: 用户消息 + 对话历史 + 可用 Capability 清单                     │
│  输出: IntentDecision {                                              │
│    complexity,           # 问题复杂度 (simple/moderate/complex)       │
│    suggested_sources,    # 建议的知识来源列表                         │
│    suggested_mode,       # 建议的执行模式                             │
│    risk_level,           # 整体风险等级                              │
│    needs_planning,       # 是否需要规划                              │
│    confidence,           # 置信度                                    │
│    reasoning,            # 决策理由                                  │
│    matched_capabilities, # 匹配的 Capability 列表                    │
│  }                                                                   │
│                                                                      │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Layer 1: QuickHeuristicClassifier（规则引擎，< 1ms）    │          │
│  │   ├─ 数学表达式检测 → calculator + simple + chat       │          │
│  │   ├─ 翻译请求检测 → internal_llm + simple + chat       │          │
│  │   ├─ 简单问候检测 → internal_llm + simple + chat       │          │
│  │   ├─ 搜索关键词检测 → web_search + simple + autopilot  │          │
│  │   ├─ 代码审查关键词 → code_repo + shell_cmd + plan     │          │
│  │   ├─ 类型报错关键词 → code_repo + shell_cmd + plan     │          │
│  │   ├─ 数据库操作关键词 → database + complex + plan_confirm│        │
│  │   └─ 兜底 → 进入 Layer 2                               │          │
│  └──────────────────────┬────────────────────────────────┘          │
│                         │ 未命中时 fallback                          │
│                         ▼                                            │
│  ┌───────────────────────────────────────────────────────┐          │
│  │ Layer 2: LLMIntentClassifier（LLM 分类，~200ms）       │          │
│  │   ├─ 结构化 Prompt：输出 JSON IntentDecision           │          │
│  │   └─ 最终兜底：默认 chat 模式                          │          │
│  └───────────────────────────────────────────────────────┘          │
│                                                                      │
│  知识来源分类 (KnowledgeSource):                                      │
│  ┌──────────────┬──────────────────────────────────────┐            │
│  │ internal_llm │ LLM 训练数据可覆盖（翻译、概念解释）    │            │
│  │ rag          │ 需要知识库检索（内部文档）              │            │
│  │ web_search   │ 需要互联网搜索（实时/外部信息）         │            │
│  │ web_fetch    │ 需要抓取特定网页                       │            │
│  │ calculator   │ 需要精确数学计算                       │            │
│  │ code_repo    │ 需要读取/分析代码仓库                  │            │
│  │ database     │ 需要查询/操作数据库                     │            │
│  │ sandbox_exec │ 需要沙箱执行代码                       │            │
│  │ shell_cmd    │ 需要 CLI 命令（客户端执行）             │            │
│  └──────────────┴──────────────────────────────────────┘            │
│                                                                      │
│  复杂度判定:                                                          │
│  ┌───────────┬──────────────────────────────────────┐               │
│  │ simple    │ 单步可解答，无需多工具编排             │               │
│  │ moderate  │ 需要 1-2 个工具辅助，单轮可完成        │               │
│  │ complex   │ 需要多步推理，多工具编排，有步骤依赖    │               │
│  └───────────┴──────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.3 ModeRouter — 模式路由

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ModeRouter                                   │
│  代码: app/agent_platform/agents/brain/mode_router.py               │
│                                                                      │
│  核心原则: Mode = 交互基调，不是安全门控                               │
│  安全门控由 StepExecutor 中的确认矩阵处理                              │
│                                                                      │
│  输入: IntentDecision + RouteContext                                 │
│  输出: RouteResult { mode, upgrade_reason }                          │
│                                                                      │
│  升级规则（按优先级）:                                                 │
│  ┌─────┬──────────────────────────────────────────────┬──────────┐  │
│  │ #1  │ risk_level == CRITICAL → plan_confirm        │ 强制升级 │  │
│  │ #2  │ suggested_sources >= 3 → plan               │ 强制升级 │  │
│  │ #3  │ needs_planning == true → plan               │ 强制升级 │  │
│  │ #4  │ user_prefers_confirmation → plan            │ 用户偏好 │  │
│  │ #5  │ risk_level HIGH/CRITICAL + chat → autopilot  │ 风险升级 │  │
│  └─────┴──────────────────────────────────────────────┴──────────┘  │
│                                                                      │
│  四种模式:                                                            │
│  ┌───────────────┬────────────────────────────────────────┐         │
│  │ chat          │ 全自动，无交互，无披露                    │         │
│  │ autopilot     │ 自动执行 + 披露，高风险步骤仍暂停         │         │
│  │ plan          │ 先展示计划，执行中高风险步骤逐个确认       │         │
│  │ plan_confirm  │ 先展示计划 + 二次确认                    │         │
│  └───────────────┴────────────────────────────────────────┘         │
│                                                                      │
│  内置确认矩阵查询 (consent_matrix):                                    │
│  用于 StepExecutor 的授权查询，也作为静态工具方法暴露                   │
│  ┌──────────────┬──────┬────────┬──────┬──────────┐                 │
│  │ mode         │ low  │ medium │ high │ critical │                 │
│  ├──────────────┼──────┼────────┼──────┼──────────┤                 │
│  │ chat         │ auto │ auto   │ 确认  │ 确认     │                 │
│  │ autopilot    │ auto │ 披露    │ 确认  │ 确认     │                 │
│  │ plan         │ auto │ 披露    │ 确认  │ 确认     │                 │
│  │ plan_confirm │ auto │ 确认    │ 确认  │ 确认     │                 │
│  └──────────────┴──────┴────────┴──────┴──────────┘                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.4 AgentLoop — LLM 工具调用循环

```
┌─────────────────────────────────────────────────────────────────────┐
│                           AgentLoop                                   │
│  代码: app/agent_platform/agents/brain/agent_loop.py                │
│                                                                      │
│  核心循环: LLM 决定 → StepExecutor 执行 → 结果回传 → LLM 继续       │
│  MAX_TURNS = 8                                                       │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    AgentLoop.run()                            │    │
│  │                                                               │    │
│  │  ┌─ 1. 生成计划（如需规划）─────────────────────────────────┐│    │
│  │  │  needs_planning=True 或 mode=plan/plan_confirm            ││    │
│  │  │  → LLM 生成 JSON 步骤列表                                 ││    │
│  │  │  → plan_confirm 模式：暂停等用户确认                      ││    │
│  │  └──────────────────────────────────────────────────────────┘│    │
│  │                         │                                     │    │
│  │                         ▼                                     │    │
│  │  ┌─ 2. 构建消息列表 ─────────────────────────────────────────┐│    │
│  │  │  system_prompt（来自 CustomizationEngine 或内置默认）      ││    │
│  │  │  + history[-6:]（最近 6 轮对话）                           ││    │
│  │  │  + user message                                           ││    │
│  │  └──────────────────────────────────────────────────────────┘│    │
│  │                         │                                     │    │
│  │               ┌─────────▼─────────┐                          │    │
│  │               │  for turn in 8:   │  ← 预算检查(budget)      │    │
│  │               │  LLM.chat(        │     token + 步数限制      │    │
│  │               │    messages,      │                          │    │
│  │               │    tools          │                          │    │
│  │               │  )                │                          │    │
│  │               └─────────┬─────────┘                          │    │
│  │                         │                                     │    │
│  │          ┌──────────────┴──────────────┐                     │    │
│  │          │ 有 tool_calls?              │                     │    │
│  │          └──────┬──────────────┬───────┘                     │    │
│  │           YES ↓               │ NO ↓                         │    │
│  │  ┌──────────────────┐  ┌──────────────┐                     │    │
│  │  │ 遍历 tool_calls:  │  │ 最终回答      │                     │    │
│  │  │ StepExecutor      │  │ → delta 事件  │                     │    │
│  │  │ .execute_step()   │  │ → 反思检查    │                     │    │
│  │  │                   │  │   (P3-9)      │                     │    │
│  │  │ 暂停场景:          │  │ → 保存历史    │                     │    │
│  │  │ ├ consent_required│  │ → completed   │                     │    │
│  │  │ ├ client_command  │  └──────────────┘                     │    │
│  │  │ └ safety_blocked  │                                       │    │
│  │  └──────────────────┘                                        │    │
│  │                                                               │    │
│  │  AgentLoop.resume() — 恢复暂停的执行                           │    │
│  │  ├─ consent_response → StepExecutor.resume_after_consent()    │    │
│  │  └─ client_result    → StepExecutor.resume_after_client_result()│   │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  执行预算 (AgentBudget):                                              │
│  ┌────────────────┬──────────────────────┐                          │
│  │ max_steps      │ 8 (最大轮次)          │                          │
│  │ max_tool_calls │ 16 (最大工具调用次数)  │                          │
│  │ max_tokens     │ 100K+10K (输入+输出)   │                          │
│  └────────────────┴──────────────────────┘                          │
│                                                                      │
│  反思机制 (P3-9):                                                     │
│  plan/plan_confirm 模式下，LLM 自我评估回答质量，                       │
│  不达标时提出改进计划并让 LLM 重新回答                                   │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.5 StepExecutor — 步骤执行器

```
┌─────────────────────────────────────────────────────────────────────┐
│                          StepExecutor                                 │
│  代码: app/agent_platform/agents/brain/step_executor.py             │
│                                                                      │
│  职责: 单次工具调用的全生命周期管理                                     │
│                                                                      │
│  execute_step(tool_call, mode, session)                               │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                                                               │    │
│  │  [1] 工具调用前安全策略检查 (SafetyEngine)                      │    │
│  │      ├─ 5 个 pre_tool_call 策略并行检查                        │    │
│  │      │  data_destruction, data_exfiltration,                  │    │
│  │      │  privilege_escalation, system_tampering,               │    │
│  │      │  remote_code_execution                                 │    │
│  │      └─ blocked? → 返回 blocked 事件，不执行                   │    │
│  │                         │                                     │    │
│  │                         ▼                                     │    │
│  │  [1b] PolicyEngine 检查（Agent 工具白名单）                     │    │
│  │        └─ blocked? → 返回 safety_blocked 事件                  │    │
│  │                         │                                     │    │
│  │                         ▼                                     │    │
│  │  [1c] GuardrailEngine 输入护栏                                 │    │
│  │        └─ blocked? → 返回 safety_blocked 事件                  │    │
│  │                         │                                     │    │
│  │                         ▼                                     │    │
│  │  [1d] EventBus 触发 brain.tool_call_started 事件               │    │
│  │                         │                                     │    │
│  │                         ▼                                     │    │
│  │  [2] 决定是否需要用户确认                                       │    │
│  │      ├─ 确认矩阵: step_risk × mode → need_consent              │    │
│  │      └─ ConsentStore 检查"记住此选择"                          │    │
│  │                         │                                     │    │
│  │              ┌──────────┴──────────┐                          │    │
│  │              │ need_consent?       │                          │    │
│  │              └──────┬──────────────┘                          │    │
│  │               YES ↓ │ NO ↓                                     │    │
│  │  ┌─────────────────┐ │ ┌──────────────────┐                   │    │
│  │  │ step_consent_    │ │ │ [4] 披露检查     │                   │    │
│  │  │ required 事件    │ │ │ autopilot/plan/  │                   │    │
│  │  │ → ⏸️ 暂停        │ │ │ plan_confirm 下  │                   │    │
│  │  └─────────────────┘ │ │ 中高风险披露      │                   │    │
│  │                       │ └────────┬─────────┘                   │    │
│  │                       │          ▼                              │    │
│  │                       │ ┌──────────────────────────┐           │    │
│  │                       │ │ [5] 根据 execution_target │           │    │
│  │                       │ │     路由执行              │           │    │
│  │                       │ │                          │           │    │
│  │                       │ │ server → 服务端沙箱执行   │           │    │
│  │                       │ │  ├─ ExecutionHarness     │           │    │
│  │                       │ │  │  (guardrail→policy→   │           │    │
│  │                       │ │  │   sandbox→execute)    │           │    │
│  │                       │ │  └─ 或 ToolRegistry 直接  │           │    │
│  │                       │ │     按风险分级:           │           │    │
│  │                       │ │     low→inline           │           │    │
│  │                       │ │     medium→thread_isolated│          │    │
│  │                       │ │     high→process_isolated │           │    │
│  │                       │ │                          │           │    │
│  │                       │ │ client → 下发客户端执行   │           │    │
│  │                       │ │  └─ client_command 事件  │           │    │
│  │                       │ │     → ⏸️ 暂停等客户端返回 │           │    │
│  │                       │ └──────────┬───────────────┘           │    │
│  │                       │            ▼                            │    │
│  │                       │  ┌──────────────────────────┐          │    │
│  │                       │  │ [6] 工具输出安全扫描      │          │    │
│  │                       │  │  GuardrailEngine 输出护栏 │          │    │
│  │                       │  │  SafetyEngine             │          │    │
│  │                       │  │  pre_tool_output_to_llm   │          │    │
│  │                       │  │  (ToolOutputContentPolicy) │          │    │
│  │                       │  └──────────┬───────────────┘          │    │
│  │                       │            ▼                            │    │
│  │                       │  ┌──────────────────────────┐          │    │
│  │                       │  │ [7] 工具调用后会话分析    │          │    │
│  │                       │  │  SafetyEngine             │          │    │
│  │                       │  │  post_tool_call           │          │    │
│  │                       │  │  (SessionContextPolicy)   │          │    │
│  │                       │  │  → context_risk_warning   │          │    │
│  │                       │  └──────────────────────────┘          │    │
│  │                       │                                         │    │
│  └───────────────────────┴─────────────────────────────────────────┘    │
│                                                                      │
│  resume_after_consent() — 用户确认后继续执行                            │
│  ├─ remember 选择 → ConsentStore 持久化                                │
│  ├─ deny → step_consent_denied 事件                                   │
│  └─ approve → 重新路由到 client/server 执行                            │
│                                                                      │
│  resume_after_client_result() — 客户端返回结果后处理                    │
│  └─ 构造 tool_result 事件 + 触发 post_tool_call 安全检查               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. 安全约束层 (Safety & Constraint Layer)

> 设计文档对应：`intent-recognition-module-design.md` 第7章
> 代码位置：`app/agent_platform/harness/safety/` + `app/agent_platform/harness/`

### 2.1 SafetyEngine — 可插拔安全策略引擎

```
┌─────────────────────────────────────────────────────────────────────┐
│                           SafetyEngine                                │
│  代码: app/agent_platform/harness/safety/engine.py                   │
│                                                                      │
│  设计原则:                                                            │
│  ├─ 策略可插拔：通过配置加载，不是硬编码                                │
│  ├─ 策略可配置：保护路径、风险阈值全部可配置                            │
│  ├─ 策略可扩展：部署者可以写自己的策略插件                              │
│  └─ 平台无关：只做结构级检查，不预判操作系统                            │
│                                                                      │
│  三个检查点 + 对应策略:                                                │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Checkpoint 1: PRE_TOOL_CALL（工具调用前）                    │    │
│  │  ┌───────────────────────────┬──────────────────────────┐   │    │
│  │  │ data_destruction          │ 检测不可逆的删除/覆盖     │   │    │
│  │  │   - 递归删除标志 (-r/-rf) │  操作（rm, DROP, format） │   │    │
│  │  │   - 强制覆盖 (-f)        │  默认: block              │   │    │
│  │  │   - 批量操作 (*, --all)   │                          │   │    │
│  │  ├───────────────────────────┼──────────────────────────┤   │    │
│  │  │ data_exfiltration         │ 检测敏感文件 + 网络发送   │   │    │
│  │  │   - 敏感扩展名 (.env/.pem)│  工具的组合               │   │    │
│  │  │   - 外泄工具 (curl/scp)  │  默认: block              │   │    │
│  │  │   - 管道到网络模式        │                          │   │    │
│  │  ├───────────────────────────┼──────────────────────────┤   │    │
│  │  │ privilege_escalation      │ 检测提权操作              │   │    │
│  │  │   - 提权工具 (sudo/su)   │  sudo + 后续命令 → block  │   │    │
│  │  │   - 宽泛权限 (chmod 777) │  默认: warn               │   │    │
│  │  ├───────────────────────────┼──────────────────────────┤   │    │
│  │  │ system_tampering          │ 检测系统级配置篡改         │   │    │
│  │  │   - 受保护路径写入        │  /etc/, C:\Windows\ 写入  │   │    │
│  │  │   - 系统工具 + 变更操作   │  → block                  │   │    │
│  │  ├───────────────────────────┼──────────────────────────┤   │    │
│  │  │ remote_code_execution     │ 检测下载+执行模式         │   │    │
│  │  │   - curl/wget | bash     │  管道到解释器 → block     │   │    │
│  │  │   - 下载到文件 + 执行    │  eval + 外部输入 → block  │   │    │
│  │  └───────────────────────────┴──────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Checkpoint 2: PRE_TOOL_OUTPUT_TO_LLM（工具输出传给LLM前）   │    │
│  │  ┌───────────────────────────┬──────────────────────────┐   │    │
│  │  │ tool_output_content       │ 检测 Prompt Injection     │   │    │
│  │  │   - "ignore previous"    │  模式匹配                  │   │    │
│  │  │   - "act as / pretend"   │  默认: block              │   │    │
│  │  │   - "system prompt"      │  输出被过滤替代            │   │    │
│  │  └───────────────────────────┴──────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  Checkpoint 3: POST_TOOL_CALL（工具调用后）                   │    │
│  │  ┌───────────────────────────┬──────────────────────────┐   │    │
│  │  │ session_context           │ 滑动窗口内多步骤组合风险  │   │    │
│  │  │   - 风险评分累加          │  评分 >= 6 → warn         │   │    │
│  │  │   - window_size=5        │  评分 >= 10 → block       │   │    │
│  │  │   - shell_command 权重=5  │                          │   │    │
│  │  └───────────────────────────┴──────────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  策略链执行逻辑:                                                      │
│  for policy in policies:                                             │
│    decision = await policy.check(context)                            │
│    if not decision.allowed: return decision  # 任何 block 直接返回    │
│    if decision.level == "warn": worst = decision                     │
│  return worst  # 取最严格的结果 (block > warn > pass)                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Harness 约束层 — Guardrail / Policy / Sandbox / Execution

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Harness 约束层（执行治理链）                        │
│  代码: app/agent_platform/harness/                                    │
│                                                                      │
│  治理链顺序（7 阶段模型）:                                             │
│                                                                      │
│  阶段 0: BEFORE_TOOL 事件                                             │
│  ─────────────────────────                                           │
│    EventBus.emit(BEFORE_TOOL) → TraceHook / MemoryHook / FileHook    │
│          │                                                            │
│          ▼                                                            │
│  阶段 1: GuardrailEngine 护栏检查                                     │
│  ─────────────────────────────                                        │
│    代码: app/agent_platform/harness/guardrails.py                     │
│    ├─ validate_tool_call(): 工具注册校验 + 白名单 + 载荷大小           │
│    ├─ validate_input(): Prompt Injection / 不安全意图 / 敏感内容       │
│    └─ validate_output(): 敏感内容脱敏                                  │
│          │                                                            │
│          ▼                                                            │
│  阶段 2: File Instructions 注入                                       │
│  ─────────────────────────────                                        │
│    FileInstructionManager.match(tool_name, payload)                   │
│    ├─ 检查操作文件路径，匹配 applyTo glob 模式                         │
│    └─ 注入到 ToolContext.file_instructions                             │
│          │                                                            │
│          ▼                                                            │
│  阶段 3: PolicyEngine 权限检查                                         │
│  ───────────────────────────                                          │
│    代码: app/agent_platform/harness/policy.py                         │
│    ├─ Agent.allowed_tools 白名单过滤                                  │
│    └─ PolicyProfile 策略检查                                          │
│          │                                                            │
│          ▼                                                            │
│  阶段 4: ToolSandbox 沙盒决策                                          │
│  ────────────────────────────                                         │
│    代码: app/agent_platform/harness/sandbox.py                        │
│    ├─ 工具声明风险等级 + 上下文风险加权                                │
│    └─ 决定 inline / thread_isolated / process_isolated                │
│          │                                                            │
│          ▼                                                            │
│  阶段 5: ToolExecutor 执行（含重试/熔断/超时）                          │
│  ─────────────────────────────────────────                            │
│    代码: app/agent_platform/harness/components/tool_executor.py       │
│    ├─ CircuitBreaker 检查 → 熔断开启则抛出                             │
│    ├─ 重试循环（最多 max_attempts 次 + backoff）                       │
│    ├─ ThreadPoolExecutor 提交 / 超时控制                               │
│    └─ 结果/异常返回                                                    │
│          │                                                            │
│          ▼                                                            │
│  阶段 6: AFTER_TOOL / TOOL_FAILED 事件                                 │
│  ─────────────────────────────────────                                 │
│    EventBus.emit(AFTER_TOOL) → TraceHook / MemoryHook                  │
│    或 EventBus.emit(TOOL_FAILED) → FallbackHandler                     │
│          │                                                            │
│          ▼                                                            │
│  阶段 7: 后处理（无论成功/失败）                                        │
│  ─────────────────────────────                                          │
│    ExecutionHooks.record_execution() → trace                           │
│    ExecutionHooks.record_runtime_summary() → memory                    │
│                                                                      │
│  阻断统一走 ToolExecutionError:                                        │
│  ├─ guardrail_blocked → ToolExecutionError(code='guardrail_blocked')  │
│  ├─ policy_tool_blocked → ToolExecutionError(code='policy_tool_blocked')│
│  ├─ sandbox_blocked → ToolExecutionError(code='sandbox_blocked')      │
│  └─ hook_blocked → ToolExecutionError(code='hook_blocked')            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 工具层 (Tool Layer)

> 代码位置：`app/agent_platform/agents/tools/`

### 3.1 ToolRegistry — 工具注册表

```
┌─────────────────────────────────────────────────────────────────────┐
│                           ToolRegistry                                │
│  代码: app/agent_platform/agents/tools/registry.py                   │
│                                                                      │
│  核心职责: 统一注册、描述和执行工具，封装 trace/记忆/错误               │
│                                                                      │
│  注册:                                                                │
│    register(tool: AgentTool) → self._tools[tool.name] = tool         │
│                                                                      │
│  描述:                                                                │
│    describe(name) → ToolSchema {                                      │
│      name, version, input_schema, output_schema,                     │
│      error_codes, timeout_ms, retry_policy,                          │
│      risk_level,          # low / medium / high / critical           │
│      execution_target,    # server / client                          │
│      sandbox_mode,        # inline / thread_isolated / process_isolated│
│      trace_fields                                                    │
│    }                                                                  │
│                                                                      │
│    list_descriptions() → list[ToolSchema]  # 所有已注册工具           │
│                                                                      │
│  执行 (run):                                                          │
│    ┌─ 输入校验 (input_model.model_validate)                          │
│    ├─ 沙箱执行器 (sandbox_runner) 或直接执行 (tool.run)              │
│    ├─ 输出校验 (output_model.model_validate)                         │
│    ├─ 错误分类:                                                       │
│    │   ValidationError → validation_error → abort                   │
│    │   TimeoutError → timeout_error → retry                          │
│    │   PermissionError → permission_error → abort                    │
│    │   ConnectionError/OSError → dependency_error → fallback         │
│    │   其他 → fatal_error → abort                                    │
│    └─ finally: 记录 trace + task_memory                              │
│                                                                      │
│  工具分层（execution_target）:                                         │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │  服务端执行 (server) — 低/中风险，纯数据操作                 │     │
│  │  ┌──────────────────┬──────────┬────────┬────────────────┐│     │
│  │  │ rag_retrieve      │ server   │ low    │ inline         ││     │
│  │  │ calculator        │ server   │ low    │ inline         ││     │
│  │  │ list_repo_files   │ server   │ low    │ inline         ││     │
│  │  │ web_search        │ server   │ medium │ thread_isolated││     │
│  │  │ search_repository │ server   │ medium │ thread_isolated││     │
│  │  │ read_repo_file    │ server   │ medium │ thread_isolated││     │
│  │  │ query_database    │ server   │ high   │ process_isolated│    │
│  │  └──────────────────┴──────────┴────────┴────────────────┘│     │
│  │                                                             │     │
│  │  客户端执行 (client) — 高风险，需要用户本地环境              │     │
│  │  ┌──────────────────┬──────────┬────────┬────────────────┐│     │
│  │  │ shell_command    │ client   │ high   │ 用户终端确认    ││     │
│  │  └──────────────────┴──────────┴────────┴────────────────┘│     │
│  │  覆盖所有 CLI: tsc, git, npm, pytest, curl, sed, rm...     │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                      │
│  AgentTool 协议 (base.py):                                            │
│  ┌──────────────────────────────────────────────────────┐           │
│  │ class AgentTool(Protocol):                            │           │
│  │   name: str                                          │           │
│  │   input_model: type[BaseModel]                       │           │
│  │   output_model: type[BaseModel]                      │           │
│  │   version: str                                       │           │
│  │   timeout_ms: int                                    │           │
│  │   retry_policy: ToolRetryPolicy                      │           │
│  │   trace_fields: list[str]                            │           │
│  │   def run(payload, context: ToolContext) → BaseModel  │           │
│  └──────────────────────────────────────────────────────┘           │
│                                                                      │
│  ToolContext (运行时依赖注入):                                         │
│  ├─ state: InMemoryState                                             │
│  ├─ trace: TraceRecorder                                             │
│  ├─ task_memory: TaskMemory                                          │
│  ├─ settings: Settings                                               │
│  ├─ llm, vector_store, services, deps                                │
│  ├─ model_router: ModelRouter                                        │
│  └─ file_instructions: list[FileInstruction]  # 文件级指令注入        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Capability 层 (能力管理层)

> 代码位置：`app/agent_platform/capabilities/`

### 4.1 CapabilityRegistry — 能力注册表

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CapabilityRegistry                              │
│  代码: app/agent_platform/capabilities/registry.py                   │
│                                                                      │
│  Capability = 高级用户意图抽象，编排多个 Tool 调用                      │
│  新增 Capability 只需 register()，不需要改其他代码                      │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │              已注册的 Capability 列表                          │   │
│  │                                                               │   │
│  │  ┌───────────────┬──────────────────────────────────────┐    │   │
│  │  │ chat          │ 通用对话，直接 LLM 回答（默认）         │    │   │
│  │  │               │ requires: []                          │    │   │
│  │  ├───────────────┼──────────────────────────────────────┤    │   │
│  │  │ code_review   │ 代码审查，自动化发现潜在问题            │    │   │
│  │  │               │ requires: [repository]                │    │   │
│  │  │               │ tools: list_repo_files, read_repo,    │    │   │
│  │  │               │        search_repo                    │    │   │
│  │  ├───────────────┼──────────────────────────────────────┤    │   │
│  │  │ data_analysis │ 数据分析，查询、分析和可视化            │    │   │
│  │  │               │ requires: [database]                  │    │   │
│  │  │               │ tools: query_database, shell_command  │    │   │
│  │  ├───────────────┼──────────────────────────────────────┤    │   │
│  │  │ web_search    │ 联网搜索，获取实时信息                  │    │   │
│  │  │               │ requires: []                          │    │   │
│  │  ├───────────────┼──────────────────────────────────────┤    │   │
│  │  │ coding        │ 代码助手，lint 检查 + LLM 分析         │    │   │
│  │  │               │ requires: [repository]                │    │   │
│  │  │               │ tools: read_repo, shell_command,      │    │   │
│  │  │               │        extract_code_issues,           │    │   │
│  │  │               │        run_code_analysis              │    │   │
│  │  ├───────────────┼──────────────────────────────────────┤    │   │
│  │  │ document_     │ 文档分析，提取关键发现和风险点          │    │   │
│  │  │ analysis      │ requires: [knowledge, repository]     │    │   │
│  │  ├───────────────┼──────────────────────────────────────┤    │   │
│  │  │ document_     │ 文档摘要，提取核心内容                  │    │   │
│  │  │ summary       │ requires: [knowledge]                 │    │   │
│  │  └───────────────┴──────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  CapabilityProvider 协议:                                              │
│  class CapabilityProvider(Protocol):                                  │
│    name: str                                                          │
│    async def execute(message, context) → Any                          │
│                                                                      │
│  CapabilityDefinition 定义:                                            │
│  ├─ name, display_name, description                                  │
│  ├─ workflow_type: str | None  (关联的 LangGraph 工作流)              │
│  ├─ requires: list[str]  (需要的基础设施: knowledge, repository...)   │
│  ├─ tools: list[str]  (关联的工具列表)                                │
│  ├─ is_default: bool  (是否为默认 Capability)                         │
│  └─ enabled: bool  (是否启用)                                        │
│                                                                      │
│  意图匹配 (关键词匹配，旧路径备用):                                     │
│  match_by_keywords(message) → [(capability_name, confidence)]         │
│  match(message) → 最匹配的 capability_name, 兜底 'chat'               │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 5. 定制化原语层 (Customization Layer)

> 设计文档对应：`agent-customization-primitives-design.md` 第2-6章
> 代码位置：`app/agent_platform/services/customization_engine.py` + 各 Manager

### 5.1 CustomizationEngine — 统一原语引擎

```
┌─────────────────────────────────────────────────────────────────────┐
│                       CustomizationEngine                             │
│  代码: app/agent_platform/services/customization_engine.py           │
│                                                                      │
│  职责: 统一加载 .agents/ 目录下所有原语，构建会话级上下文               │
│                                                                      │
│  架构分层:                                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                     API 管理层                                 │   │
│  │     FastAPI 端点 (admin/*): CRUD + 文件导入 + 预览              │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │                     原语管理层                                  │   │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────┐ ┌─────────────┐  │   │
│  │  │InstManager │ │FileInstMgr │ │PromptMgr │ │SkillManager │  │   │
│  │  └────────────┘ └────────────┘ └──────────┘ └─────────────┘  │   │
│  │  ┌────────────┐ ┌────────────┐ ┌─────────────────────────┐  │   │
│  │  │AgentDefMgr │ │HookManager │ │      McpManager         │  │   │
│  │  └────────────┘ └────────────┘ └─────────────────────────┘  │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │                     CustomizationEngine                        │   │
│  │     .agents/ 目录扫描 → 文件解析 → 缓存管理 → 运行时组装        │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │                     运行时注入层                                 │   │
│  │     AgentService / SessionManager / ExecutionHarness           │   │
│  │     ↓ 注入到 System Prompt / LLM Messages / ToolContext         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  生命周期:                                                            │
│                                                                      │
│  控制面（启动时一次性）:                                               │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ CustomizationEngine.initialize()                               │   │
│  │  ├─ _sync_skills()     → 扫描 .agents/skills/ → SkillManager  │   │
│  │  ├─ _sync_agents()     → 扫描 .agents/agents/ → AgentDefManager│   │
│  │  ├─ _sync_prompts()    → 扫描 .agents/prompts/ → PromptManager │   │
│  │  ├─ _sync_mcp_servers()→ 连接 MCP → McpManager → ToolRegistry │   │
│  │  ├─ _sync_hooks()      → 加载 .agents/hooks/ → EventBus       │   │
│  │  └─ FileInstructionManager.load_all() → 文件指令缓存           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│         │                                                            │
│         ▼ 每个会话                                                    │
│  数据面（每次请求）:                                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ CustomizationEngine.build_session_context(agent_name)          │   │
│  │  → SessionContext {                                            │   │
│  │      agent_def,       # Agent 定义（含 instructions）           │   │
│  │      system_prompt,   # 组装后的系统提示词                      │   │
│  │      extension_catalog, # 扩展清单（轻量 ~50 tokens/扩展）      │   │
│  │      allowed_tools,   # 工具白名单                             │   │
│  │    }                                                           │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 七大原语详解

```
┌─────────────────────────────────────────────────────────────────────┐
│                        七大定制化原语                                  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ 1. Instructions（系统指令）                                    │   │
│  │    文件: .agents/AGENTS.md                                    │   │
│  │    触发: 始终加载到 Agent 会话的系统提示词中                     │   │
│  │    本质: 项目通用的行为准则，LLM 不可绕过                        │   │
│  │    代码: InstructionsManager.build_system_prompt()             │   │
│  │    优先级: 请求级 > Agent 级 > 项目级 > 系统内置                │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ 2. File Instructions（文件级指令）                              │   │
│  │    文件: .agents/instructions/*.instructions.md                │   │
│  │    触发: Agent 操作匹配 applyTo 模式的文件时自动注入             │   │
│  │    本质: 特定文件的针对性约束                                    │   │
│  │    代码: FileInstructionManager.match(file_path)               │   │
│  │    注入点: ToolContext.file_instructions                        │   │
│  │    复用 Skill Rule 的 applyTo 机制                              │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ 3. Prompts（快捷提示模板）                                      │   │
│  │    文件: .agents/prompts/*.prompt.md                           │   │
│  │    触发: 用户输入 /name 触发，或意图匹配自动推荐                 │   │
│  │    本质: 参数化的单次任务模板（变量插值）                         │   │
│  │    代码: PromptManager (CRUD + import_from_file + render)      │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ 4. Skills（技能）                                              │   │
│  │    文件: .agents/skills/<name>/SKILL.md + rules/*.md           │   │
│  │    触发: load_extension("skill_name", "skill") 工具调用         │   │
│  │    本质: 多步骤工作流 + 附带规则资源                             │   │
│  │    代码: SkillManager + ExtensionCatalog（懒加载 + 路由表）     │   │
│  │    两种执行模式:                                                │   │
│  │      A) 扩展内容: LLM 按需加载 → 遵循规则执行                   │   │
│  │      B) 工作流 TaskSkill: TaskWorkflowOrchestrator → LangGraph  │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ 5. Custom Agents（自定义 Agent）                                │   │
│  │    文件: .agents/agents/*.agent.md                             │   │
│  │    触发: Agent 选择器切换 / 子 Agent 调用                        │   │
│  │    本质: 独立的 AI 身份 + 权限边界                               │   │
│  │    代码: AgentDefManager (import_from_file + CRUD)             │   │
│  │    运行时:                                                      │   │
│  │      instructions → System Prompt                               │   │
│  │      allowed_tools → PolicyEngine 白名单                       │   │
│  │      skills → ExtensionCatalog 限定清单                        │   │
│  │      model/temperature → LLM 配置覆盖                          │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ 6. Hooks（生命周期钩子）                                        │   │
│  │    文件: .agents/hooks/*.json                                  │   │
│  │    触发: Agent 生命周期事件自动触发                              │   │
│  │    本质: 确定性的拦截/扩展脚本                                    │   │
│  │    代码: FileHookLoader → HookRuntimeAdapter → HookActionEngine│   │
│  │    Action 类型: log, block, audit, notify, throttle,            │   │
│  │                 mutate_payload, custom_script                   │   │
│  │    条件匹配: tool_names, payload_match, stage_names,            │   │
│  │              sandbox_modes, risk_levels                         │   │
│  │    执行优先级: system_guard(0) > file_hook(1) > code_hook(2)    │   │
│  │                > trace_hook(3) > memory_hook(3)                 │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ 7. MCP Server（外部工具协议）                                    │   │
│  │    文件: .agents/mcp-servers.json                              │   │
│  │    触发: 通过 tools: [server/*] 注入工具列表                     │   │
│  │    本质: 外部动态注册的工具箱                                     │   │
│  │    代码: McpManager (URL/STDIO 连接 + 工具发现 + 调用)          │   │
│  │    适配: McpAgentToolAdapter → AgentTool 协议                   │   │
│  │    执行路径: ToolRegistry → McpAgentToolAdapter.run()           │   │
│  │              → McpManager.call_tool() → 远程 MCP Server         │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  .agents/ 目录结构:                                                   │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ .agents/                                                       │   │
│  │ ├── AGENTS.md                          ← 项目级系统指令         │   │
│  │ ├── instructions/                      ← 文件指令               │   │
│  │ │   ├── python.instructions.md                                 │   │
│  │ │   └── sql.instructions.md                                    │   │
│  │ ├── prompts/                           ← 快捷提示模板           │   │
│  │ │   ├── code-review.prompt.md                                  │   │
│  │ │   └── bug-analysis.prompt.md                                 │   │
│  │ ├── skills/                            ← 技能                   │   │
│  │ │   ├── ai-coding-rules/                                       │   │
│  │ │   └── debug-tools/                                           │   │
│  │ ├── agents/                            ← 自定义 Agent           │   │
│  │ │   ├── code-reviewer.agent.md                                 │   │
│  │ │   └── data-analyst.agent.md                                  │   │
│  │ ├── hooks/                             ← 生命周期钩子           │   │
│  │ │   ├── pre-tool-execution.json                                │   │
│  │ │   └── post-tool-execution.json                               │   │
│  │ └── mcp-servers.json                   ← MCP Server 注册        │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.3 ExtensionCatalog — 扩展清单（懒加载）

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ExtensionCatalog                               │
│  代码: app/agent_platform/services/extension_catalog.py             │
│                                                                      │
│  Token 节省策略:                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  轻量清单 (~50 tokens/扩展) 始终在系统提示词中                   │   │
│  │    ↓ LLM 按需调用 load_extension(name, type)                   │   │
│  │  完整内容加载到上下文                                            │   │
│  │    ↓ Skill 加载后，根据路由表按需调用 load_rule(name, rule)      │   │
│  │  完整规则内容加载                                                │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  build_catalog(skill_names) → 格式化的扩展清单字符串:                  │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ ## 可用扩展                                                     │   │
│  │ ### Skills                                                     │   │
│  │ - `ai-coding-rules`: AI 编码规则                               │   │
│  │ - `debug-tools`: 通用调试工具                                   │   │
│  │ ### MCP 工具                                                   │   │
│  │ - `github`: url 连接                                           │   │
│  │ ### 子 Agent                                                   │   │
│  │ - `code-reviewer`: 代码审查员                                   │   │
│  │ 使用 `load_extension(name, type)` 加载扩展的完整内容。           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  按需加载:                                                            │
│  _load_skill(name) → 返回 SKILL.md 完整 instructions + 规则路由表     │
│  _load_rule(skill_name, rule_name) → 返回完整规则内容                 │
│  _load_mcp(name) → 返回 MCP Server 工具列表                          │
│  _load_agent(name) → 返回 Agent 完整定义                              │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 6. 记忆系统 (Memory System)

> 代码位置：`app/agent_platform/agents/memory.py` + `app/agent_platform/services/memory_commit_gate.py`

### 6.1 五层记忆模型

```
┌─────────────────────────────────────────────────────────────────────┐
│                         五层记忆模型                                  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ Layer 1: Working Memory（工作记忆）                            │   │
│  │   作用域: 单次 LLM 调用                                         │   │
│  │   内容: 当前轮的工具调用结果、中间推理                           │   │
│  │   生命周期: 调用结束即清空                                       │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ Layer 2: Session Memory（会话记忆）                             │   │
│  │   作用域: 单次会话 (session_id)                                 │   │
│  │   内容: 对话历史 (user/assistant messages)、tool_history        │   │
│  │   生命周期: 会话结束或超时                                       │   │
│  │   代码: SessionManager.get_or_create() / save()                 │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ Layer 3: Task Memory（任务记忆）                                │   │
│  │   作用域: 单次任务 (task_id)                                    │   │
│  │   内容: 工具调用记录 (TaskMemory.record_tool_call)               │   │
│  │   生命周期: 任务完成                                             │   │
│  │   代码: TaskMemory (app/agent_platform/agents/memory.py)        │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ Layer 4: Semantic Memory（语义记忆）                            │   │
│  │   作用域: 跨会话                                                │   │
│  │   内容: 向量化的知识片段、FAQ                                    │   │
│  │   生命周期: 持久化                                               │   │
│  ├──────────────────────────────────────────────────────────────┤   │
│  │ Layer 5: Profile Memory（用户画像）                             │   │
│  │   作用域: 跨会话、跨用户                                        │   │
│  │   内容: 用户偏好、技能水平、常用模式                             │   │
│  │   生命周期: 持久化                                               │   │
│  │   代码: UserProfileService → BrainContextManager 注入            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  MemoryCommitGate — 记忆提交门控:                                     │
│  代码: app/agent_platform/services/memory_commit_gate.py             │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  ├─ 信任等级提升: 临时记忆 → 可信记忆                          │   │
│  │  ├─ 作用域提升: session → user → global                        │   │
│  │  └─ 冲突检测: 新记忆与旧记忆冲突时合并/覆盖                     │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  BrainContextManager — 大脑上下文管理器:                               │
│  代码: app/agent_platform/agents/brain/context_manager.py            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  构建 LLM 调用所需的完整上下文:                                 │   │
│  │  ├─ 1. 从 CustomizationEngine 组装 system_prompt               │   │
│  │  ├─ 2. 注入记忆上下文 (MemoryCommitGate)                       │   │
│  │  ├─ 3. 注入用户画像 (UserProfileService)                       │   │
│  │  ├─ 4. 压缩历史（三层: 超长截断 → 滑窗 → 递归摘要）             │   │
│  │  ├─ 5. Token 计数与预算检查                                    │   │
│  │  └─ 可扩展 context_hooks: 注入格式由调用方定制                  │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. 模型路由层 (LLM Routing)

> 代码位置：`app/agent_platform/harness/model_router.py`

### 7.1 ModelRouter — 模型路由与成本调度

```
┌─────────────────────────────────────────────────────────────────────┐
│                           ModelRouter                                 │
│  代码: app/agent_platform/harness/model_router.py                   │
│                                                                      │
│  职责: 根据用途、预算和上下文复杂度决定 LLM 调用参数                    │
│                                                                      │
│  路由决策:                                                            │
│  route(purpose, llm_available, feature_enabled,                      │
│        run_budget, step_name, evidence_count, missing_aspects)       │
│  → ModelRouteDecision { mode, profile, estimated_cost, reason }      │
│                                                                      │
│  用途分类 (ModelRoutePurpose):                                        │
│  ┌──────────────────┬──────────────────────────────────────────┐    │
│  │ task_analysis    │ 任务分析 → 根据证据量选 quality/economy    │    │
│  │ task_review      │ 任务审查 → 强制 quality                    │    │
│  │ knowledge_answer │ 知识回答 → grounded_answer 步骤强制 quality │    │
│  │ knowledge_check  │ 知识校验 → 强制 balanced                   │    │
│  │ knowledge_rewrite│ 知识改写 → 强制 quality                    │    │
│  │ json_repair      │ JSON 修复 → 强制 economy                   │    │
│  └──────────────────┴──────────────────────────────────────────┘    │
│                                                                      │
│  成本档位 (ModelRouteProfile):                                        │
│  ┌──────────┬──────────────────────────────────────────────────┐    │
│  │ economy  │ 低成本，适合简单任务 (json_repair, knowledge_check) │    │
│  │ balanced │ 均衡，默认档位                                      │    │
│  │ quality  │ 高质量，适合复杂分析 (task_review, knowledge_rewrite)│    │
│  │ disabled │ 禁用 LLM                                            │    │
│  └──────────┴──────────────────────────────────────────────────┘    │
│                                                                      │
│  消费追踪:                                                            │
│  ├─ record_selection(): 记录路由选择结果到 trace                      │
│  ├─ record_completion(): 记录实际消费 (prompt_tokens, cost)           │
│  └─ capture_usage(): 从 provider response 提取 usage 信息             │
│      ├─ 优先使用 provider 报告的 usage (prompt_tokens, completion)    │
│      └─ 回退到本地估算 (len(text) / 4)                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 8. 入口服务层 (AgentService)

> 代码位置：`app/agent_platform/services/agent_service.py`

### 8.1 AgentService — 入口门面

```
┌─────────────────────────────────────────────────────────────────────┐
│                           AgentService                                │
│  代码: app/agent_platform/services/agent_service.py                 │
│                                                                      │
│  职责: 入口门面，委托给 harness.brain 组件                            │
│                                                                      │
│  process(request) → AsyncIterator[AgentEvent]                        │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                                                               │   │
│  │  ┌─ 1. 获取/创建会话 (SessionManager)                         │   │
│  │  │    ├─ 解析 Agent 身份 (请求级 > 会话级 > 默认)              │   │
│  │  │    └─ 持久化 Agent 选择                                    │   │
│  │  │                                                            │   │
│  │  ├─ 1.5 构建系统提示词 (CustomizationEngine)                   │   │
│  │  │    → yield system_prompt 事件                               │   │
│  │  │                                                            │   │
│  │  ├─ 2. 处理 MCP 配置 (McpManager)                             │   │
│  │  │    → yield tool_call 事件                                   │   │
│  │  │                                                            │   │
│  │  ├─ 3. 路径选择:                                               │   │
│  │  │    _use_brain_path()?                                       │   │
│  │  │    ├─ YES → _process_via_brain()  (新路径)                  │   │
│  │  │    └─ NO  → _process_legacy()   (旧路径，向后兼容)          │   │
│  │  │                                                            │   │
│  │  └─ 新路径 (_process_via_brain):                               │   │
│  │       ├─ 3a. IntentRecognizer.recognize() → intent 事件        │   │
│  │       ├─ 3b. ModeRouter.route() → mode_switched 事件(如有)     │   │
│  │       ├─ 3c. 构建工具列表 (ToolRegistry.list_descriptions)     │   │
│  │       ├─ 3d. 保存用户消息到 session.history                    │   │
│  │       ├─ 3e. AgentLoop.run() → 事件流                          │   │
│  │       ├─ 3f. 持久化 session (含 user + assistant 消息)         │   │
│  │       └─ 3g. completed 事件 (含 duration_ms)                   │   │
│  │                                                               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  注入依赖:                                                            │
│  ├─ CapabilityRegistry (注册 Chat/CodeReview/DataAnalysis/WebSearch/ │
│  │                       Coding 等 Provider)                         │
│  ├─ IntentMatcher (旧路径，向后兼容)                                  │
│  ├─ SessionManager (会话管理)                                        │
│  ├─ McpManager (MCP 连接)                                            │
│  ├─ PlanGenerator / PlanExecutor (计划生成/执行)                     │
│  ├─ TaskOrchestrator / QueryOrchestrator (RAG 工作流)               │
│  ├─ SkillManager / AgentDefManager / ExtensionCatalog                │
│  ├─ CustomizationEngine (定制化原语)                                  │
│  └─ Brain 组件: IntentRecognizer / ModeRouter / AgentLoop /          │
│                 StepExecutor                                          │
│                                                                      │
│  SSE 事件类型:                                                        │
│  ┌──────────────────────┬──────────────────────────────────────┐    │
│  │ intent               │ 意图识别结果                          │    │
│  │ mode_switched        │ 模式被升级                            │    │
│  │ system_prompt        │ 系统提示词已构建                      │    │
│  │ plan                 │ 执行计划                              │    │
│  │ tool_call            │ LLM 决定调用工具                      │    │
│  │ step_consent_required│ 步骤需要用户确认                      │    │
│  │ step_consent_granted │ 用户已确认                            │    │
│  │ step_consent_denied  │ 用户已拒绝                            │    │
│  │ step_disclosed       │ 步骤已披露                            │    │
│  │ client_command       │ 下发到客户端执行                      │    │
│  │ tool_result          │ 工具执行结果                          │    │
│  │ safety_blocked       │ 安全策略拒绝                          │    │
│  │ context_risk_warning │ 会话上下文风险警告                    │    │
│  │ reflection           │ 反思反馈 (P3-9)                       │    │
│  │ delta                │ LLM 文本增量输出                      │    │
│  │ completed            │ 请求完成                              │    │
│  │ error                │ 错误                                  │    │
│  └──────────────────────┴──────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 9. 事件总线与 Hook 系统 (EventBus & Hooks)

> 代码位置：`app/agent_platform/services/hook_loader.py` + `hook_actions.py` + `hook_adapter.py`

### 9.1 Hook 生命周期引擎

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Hook 生命周期引擎                                   │
│                                                                      │
│  事件源 (Event Sources):                                              │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │ CustomizationEngine → 会话生命周期事件 (RUN_STARTED)           │   │
│  │ ExecutionHarness    → Tool 生命周期事件 (BEFORE_TOOL, AFTER)   │   │
│  │ ToolExecutor        → 执行生命周期事件                         │   │
│  │ AgentLoop/StepExecutor → Brain 工具调用事件                    │   │
│  │ WorkflowOrchestrator → Stage 生命周期事件                      │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         │                                            │
│                         ▼                                            │
│  EventBus (发布-订阅总线):                                            │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  支持 16 个 HookEvent 枚举                                     │   │
│  │  支持通配符注册 ('all')                                        │   │
│  │  支持多 handler 顺序执行                                       │   │
│  └──────────────────────┬───────────────────────────────────────┘   │
│                         │                                            │
│                         ▼                                            │
│  Handler 链（按优先级分层执行）:                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  优先级 0: System Guard（系统内置防护）                         │   │
│  │  优先级 1: File Hook（JSON 文件配置的 Hook）                    │   │
│  │  优先级 2: Code Hook（代码注册的普通 Hook）                     │   │
│  │  优先级 3: Trace Hook（Trace 记录）                             │   │
│  │  优先级 3: Memory Hook（记忆记录）                              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  完整事件发射图谱:                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  请求初始化阶段:                                               │   │
│  │    RUN_STARTED ← CustomizationEngine                          │   │
│  │    CONTEXT_BUILT ← ContextHarness                              │   │
│  │                                                               │   │
│  │  工具执行阶段:                                                  │   │
│  │    BEFORE_TOOL → Guardrail → Policy → Sandbox → Execute       │   │
│  │    → AFTER_TOOL / TOOL_FAILED                                 │   │
│  │                                                               │   │
│  │  ReAct 步骤阶段:                                               │   │
│  │    BEFORE_REACT_TURN / AFTER_REACT_TURN                       │   │
│  │    REACT_EXCEEDED_MAX_TURNS                                   │   │
│  │                                                               │   │
│  │  Stage 阶段:                                                   │   │
│  │    BEFORE_STAGE / AFTER_STAGE / STAGE_FAILED                  │   │
│  │                                                               │   │
│  │  Checkpoint 阶段:                                              │   │
│  │    BEFORE_CHECKPOINT / AFTER_CHECKPOINT                       │   │
│  │                                                               │   │
│  │  请求结束阶段:                                                  │   │
│  │    RUN_COMPLETED / RUN_FAILED                                 │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Hook 条件匹配:                                                       │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │  tool_names: ["shell_command", "command"]  白名单匹配          │   │
│  │  tool_names_exclude: ["readonly"]           黑名单排除          │   │
│  │  payload_match: {"command": "rm -rf *"}     负载精确匹配        │   │
│  │  stage_names: ["analyze", "draft"]          Stage 匹配         │   │
│  │  sandbox_modes: ["inline"]                  沙盒模式匹配        │   │
│  │  risk_levels: ["high"]                      风险等级匹配        │   │
│  │  rate_limit: {max_calls, window_seconds}    速率限制            │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Hook Action 类型:                                                    │
│  ┌──────────────┬──────────────────────────────────────────────┐    │
│  │ log          │ 记录日志 (不阻断)                              │    │
│  │ audit        │ 写入审计记录到 DB (不阻断)                     │    │
│  │ notify       │ 发送通知 (不阻断, fire-and-forget)            │    │
│  │ block        │ 终止工具执行 (阻断, 抛 ToolExecutionError)     │    │
│  │ throttle     │ 限流 (可阻断)                                  │    │
│  │ mutate_payload│ 修改工具入参 (不阻断)                         │    │
│  │ custom_script│ 执行自定义 Python 脚本 (取决于脚本)            │    │
│  └──────────────┴──────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 10. 可观测性 (Observability)

```
┌─────────────────────────────────────────────────────────────────────┐
│                          可观测性层                                    │
│                                                                      │
│  TraceRecorder (app/agent_platform/observability/trace_recorder.py): │
│  ├─ 记录每次工具调用: tool_call_id, tool_name, duration_ms, status   │
│  ├─ 记录模型路由: model_route_selected, model_route_consumed         │
│  └─ 记录错误: error_type, default_action, retry_count                │
│                                                                      │
│  TaskMemory (app/agent_platform/agents/memory.py):                   │
│  ├─ record_tool_call(): 记录工具调用详情到任务记忆                    │
│  └─ 用于排障和复盘                                                   │
│                                                                      │
│  ModelUsageSnapshot:                                                  │
│  ├─ prompt_tokens, completion_tokens, total_tokens                   │
│  ├─ actual_cost_units (provider 报告 或 本地估算)                     │
│  └─ cost_source: provider_cost / provider_usage / local_estimate      │
│                                                                      │
│  ConsentStore (app/agent_platform/agents/brain/consent_store.py):    │
│  ├─ 内存缓存 + SQLite 持久化 (P1-7)                                  │
│  ├─ session 级: 会话结束自动清理                                      │
│  └─ persistent 级: 跨会话持久化                                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

# 第二部分：系统总体架构图

## 总图：全链路串联

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              Lania Agent 系统总体架构                                       │
│                                                                                          │
│                                  ┌─────────────┐                                         │
│                                  │  用户输入     │                                         │
│                                  └──────┬──────┘                                         │
│                                         │                                                │
│                                         ▼                                                │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                          入口门面层 (AgentService)                                  │   │
│  │  ┌─────────────────────────────────────────────────────────────────────────────┐ │   │
│  │  │  process(request)                                                             │ │   │
│  │  │  ├─ SessionManager.get_or_create()  → 会话管理                                │ │   │
│  │  │  ├─ CustomizationEngine.build_session_context() → 系统提示词 + 扩展清单        │ │   │
│  │  │  ├─ McpManager.connect() → MCP 工具注册                                       │ │   │
│  │  │  └─ 路径选择 → _process_via_brain() 或 _process_legacy()                       │ │   │
│  │  └─────────────────────────────────────────────────────────────────────────────┘ │   │
│  └────────────────────────────────────┬─────────────────────────────────────────────┘   │
│                                       │                                                  │
│                                       ▼                                                  │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                       大脑层 (Brain — 感知 + 决策)                                  │   │
│  │                                                                                    │   │
│  │  ┌──────────────────────────────────────────────────────────────────────────────┐ │   │
│  │  │                     感知层: IntentRecognizer                                    │ │   │
│  │  │  ┌────────────────────────────┐    ┌──────────────────────────────┐           │ │   │
│  │  │  │ Layer 1: QuickHeuristic    │───▶│ Layer 2: LLMIntentClassifier │           │ │   │
│  │  │  │ 规则引擎 (< 1ms)           │    │ LLM 兜底分类 (~200ms)         │           │ │   │
│  │  │  └────────────────────────────┘    └──────────────┬───────────────┘           │ │   │
│  │  │                                                   │                            │ │   │
│  │  │ 输出: IntentDecision { complexity, suggested_sources,                          │ │   │
│  │  │        suggested_mode, risk_level, needs_planning, confidence }                │ │   │
│  │  └───────────────────────────────────────┬──────────────────────────────────────┘ │   │
│  │                                          │                                         │   │
│  │                                          ▼                                         │   │
│  │  ┌──────────────────────────────────────────────────────────────────────────────┐ │   │
│  │  │                     决策层: ModeRouter + AgentLoop + StepExecutor              │ │   │
│  │  │                                                                                │ │   │
│  │  │  ModeRouter.route(IntentDecision) → 最终模式 (chat/autopilot/plan/plan_confirm) │ │   │
│  │  │        │                                                                       │ │   │
│  │  │        ▼                                                                       │ │   │
│  │  │  AgentLoop.run() — LLM 工具调用循环 (MAX_TURNS=8)                               │ │   │
│  │  │  ┌──────────────────────────────────────────────────────────────────────┐      │ │   │
│  │  │  │  for turn in 8:                                                       │      │ │   │
│  │  │  │    LLM.chat(messages, tools) → tool_calls?                            │      │ │   │
│  │  │  │    ├─ YES → StepExecutor.execute_step(tool_call, mode, session)       │      │ │   │
│  │  │  │    │        ├─ SafetyEngine.check(pre_tool_call)                       │      │ │   │
│  │  │  │    │        ├─ PolicyEngine.check                                     │      │ │   │
│  │  │  │    │        ├─ GuardrailEngine.validate                               │      │ │   │
│  │  │  │    │        ├─ 确认矩阵 (mode × step_risk) → consent?                 │      │ │   │
│  │  │  │    │        ├─ execution_target 路由:                                  │      │ │   │
│  │  │  │    │        │   server → ExecutionHarness / ToolRegistry              │      │ │   │
│  │  │  │    │        │   client → client_command 事件 → ⏸️ 暂停               │      │ │   │
│  │  │  │    │        ├─ SafetyEngine.check(pre_tool_output_to_llm)             │      │ │   │
│  │  │  │    │        └─ SafetyEngine.check(post_tool_call)                      │      │ │   │
│  │  │  │    └─ NO  → 最终回答 → 反思 (P3-9) → completed                        │      │ │   │
│  │  │  └──────────────────────────────────────────────────────────────────────┘      │ │   │
│  │  └────────────────────────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                       │                                                  │
│             ┌─────────────────────────┼─────────────────────────┐                        │
│             │                         │                         │                        │
│             ▼                         ▼                         ▼                        │
│  ┌──────────────────┐  ┌──────────────────────────┐  ┌──────────────────────┐           │
│  │   安全约束层       │  │      工具层               │  │   定制化原语层         │           │
│  │                  │  │                          │  │                      │           │
│  │ SafetyEngine     │  │ ToolRegistry              │  │ CustomizationEngine  │           │
│  │ ├─ 7 内置策略    │  │ ├─ register/describe/run  │  │ ├─ Instructions     │           │
│  │ ├─ 3 检查点     │  │ ├─ risk_level             │  │ ├─ FileInstructions │           │
│  │ │  pre_tool_call │  │ ├─ execution_target       │  │ ├─ Prompts          │           │
│  │ │  pre_tool_     │  │ ├─ sandbox_mode           │  │ ├─ Skills           │           │
│  │ │  output_to_llm │  │ └─ AgentTool 协议         │  │ ├─ Agents           │           │
│  │ │  post_tool_call│  │                          │  │ ├─ Hooks            │           │
│  │ └─────────────── │  │ 工具分层:                 │  │ └─ MCP Server       │           │
│  │                  │  │ ├─ server (低/中风险)     │  │                      │           │
│  │ GuardrailEngine  │  │ └─ client (高风险)        │  │ ExtensionCatalog     │           │
│  │ ├─ validate_input│  │                          │  │ ├─ 轻量清单          │           │
│  │ ├─ validate_tool │  │ 执行分级:                 │  │ └─ 懒加载            │           │
│  │ └─ validate_output│ │ ├─ inline (low)           │  │                      │           │
│  │                  │  │ ├─ thread_isolated (med)  │  │ .agents/ 目录        │           │
│  │ PolicyEngine     │  │ └─ process_isolated (high)│  │ ├─ AGENTS.md         │           │
│  │ ├─ allowed_tools │  │                          │  │ ├─ instructions/     │           │
│  │ └─ policy_profile│  │ CapabilityRegistry        │  │ ├─ prompts/          │           │
│  │                  │  │ ├─ chat                   │  │ ├─ skills/           │           │
│  │ ToolSandbox      │  │ ├─ code_review            │  │ ├─ agents/           │           │
│  │ └─ assess()      │  │ ├─ data_analysis          │  │ ├─ hooks/            │           │
│  │                  │  │ ├─ web_search             │  │ └─ mcp-servers.json  │           │
│  │ ToolExecutor     │  │ └─ coding                │  │                      │           │
│  │ ├─ 超时/重试     │  │                          │  │ EventBus + Hooks     │           │
│  │ └─ 熔断          │  │                          │  │ ├─ 16 个 HookEvent   │           │
│  └──────────────────┘  └──────────────────────────┘  │ ├─ 5 级优先级        │           │
│                                                       │ └─ 7 种 Action 类型  │           │
│                                                       └──────────────────────┘           │
│                                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           基础设施层 (Infrastructure)                               │   │
│  │                                                                                    │   │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐              │   │
│  │  │ 记忆系统      │ │ 模型路由      │ │ 可观测性      │ │ 会话管理      │              │   │
│  │  │              │ │              │ │              │ │              │              │   │
│  │  │ 五层记忆:    │ │ ModelRouter  │ │ TraceRecorder│ │ SessionMgr   │              │   │
│  │  │ ├ Working    │ │ ├ route()    │ │ ├ tool_call  │ │ ├ get/create │              │   │
│  │  │ ├ Session    │ │ ├ select_    │ │ ├ model_route│ │ ├ save       │              │   │
│  │  │ ├ Task       │ │ │  profile   │ │ └ error      │ │ └ history    │              │   │
│  │  │ ├ Semantic   │ │ ├ record_    │ │              │ │              │              │   │
│  │  │ └ Profile    │ │ │  selection │ │ TaskMemory    │ │ ConsentStore │              │   │
│  │  │              │ │ └ capture_   │ │ ├ record     │ │ ├ save       │              │   │
│  │  │ MemoryCommit │ │    usage     │ │ └ replay     │ │ ├ get        │              │   │
│  │  │ Gate         │ │              │ │              │ │ └ clear      │              │   │
│  │  │ ├ 信任提升   │ │ 成本调度:     │ │ ModelUsage   │ │              │              │   │
│  │  │ ├ 作用域提升 │ │ economy/     │ │ Snapshot     │ │ BrainContext │              │   │
│  │  │ └ 冲突检测   │ │ balanced/    │ │ (token+成本) │ │ Manager      │              │   │
│  │  │              │ │ quality       │ │              │ │ (上下文组装) │              │   │
│  │  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘              │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────────────┐   │
│  │                             RAG 独立应用 + 工作流                                    │   │
│  │                                                                                    │   │
│  │  ┌─────────────────────────────────┐  ┌──────────────────────────────────────┐    │   │
│  │  │ RAG 独立应用                     │  │ RAG 工作流 (LangGraph)                 │    │   │
│  │  │ 不经过 harness 大脑层             │  │ 经过约束层 + 执行层，不经过大脑层        │    │   │
│  │  │                                 │  │                                      │    │   │
│  │  │ 直接 LLM 调用 + 知识库检索       │  │ QueryOrchestrator / TaskOrchestrator  │    │   │
│  │  └─────────────────────────────────┘  │ → GuardrailEngine → PolicyEngine       │    │   │
│  │                                       │ → ToolSandbox → ToolExecutor           │    │   │
│  │                                       └──────────────────────────────────────┘    │   │
│  └──────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 总链路：一次完整请求的数据流

```
用户输入: "解决当前项目中的类型报错"
  │
  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  AgentService.process()                                                        │
│                                                                               │
│  1. SessionManager.get_or_create() → 会话上下文                                │
│  2. CustomizationEngine.build_session_context() →                             │
│     ├─ InstructionsManager → AGENTS.md 项目指令                                │
│     ├─ AgentDefManager → 当前 Agent 的 instructions + allowed_tools            │
│     └─ ExtensionCatalog.build_catalog() → Skills/MCPs/Agents 轻量清单          │
│                                                                               │
│  3. IntentRecognizer.recognize("解决当前项目中的类型报错")                       │
│     ├─ Layer 1 (QuickHeuristic): "类型报错"关键词命中                           │
│     │   → code_repo + shell_cmd + complex + plan + high                        │
│     └─ 输出: IntentDecision { complexity=complex,                              │
│              sources=[code_repo, shell_cmd], mode=plan, risk=high }            │
│                                                                               │
│  4. ModeRouter.route(decision)                                                 │
│     ├─ risk=high → 无需升级 (已是 plan)                                        │
│     └─ 最终 mode = plan                                                        │
│                                                                               │
│  5. AgentLoop.run()                                                            │
│     ├─ needs_planning=True → 生成计划:                                         │
│     │   [1/3] npx tsc --noEmit (shell_command, risk: high)                     │
│     │   [2/3] 读取报错文件 (read_repo_file, risk: medium)                      │
│     │   [3/3] 修复代码 + 验证 (shell_command, risk: high)                      │
│     │                                                                          │
│     ├─ LLM 决定调用 shell_command("npx tsc --noEmit")                          │
│     │   │                                                                      │
│     │   └─ StepExecutor.execute_step()                                         │
│     │       ├─ SafetyEngine.check(pre_tool_call) → all pass                    │
│     │       ├─ PolicyEngine.check → pass                                       │
│     │       ├─ GuardrailEngine.validate_tool_call → pass                       │
│     │       ├─ 确认矩阵: risk=high, mode=plan → need_consent = True            │
│     │       ├─ ConsentStore: 未记住 → 需要确认                                 │
│     │       └─ yield step_consent_required → ⏸️ 暂停                           │
│     │                                                                          │
│     │       用户确认 → AgentLoop.resume()                                       │
│     │       ├─ StepExecutor.resume_after_consent()                             │
│     │       ├─ execution_target=client → client_command 事件 → ⏸️ 暂停        │
│     │       └─ 客户端执行 tsc → 返回 exit_code: 2                              │
│     │                                                                          │
│     │       AgentLoop.resume(client_result)                                     │
│     │       ├─ tool_result 事件 (stdout + stderr)                               │
│     │       ├─ SafetyEngine.check(pre_tool_output_to_llm) → pass               │
│     │       └─ SafetyEngine.check(post_tool_call) → score=5 < 6 → pass         │
│     │                                                                          │
│     ├─ LLM 继续: 调用 read_repository_file × 8 (自动执行，披露)                  │
│     │   └─ SafetyEngine.check(post_tool_call) → score=6 >= 6 → ⚠️ warn         │
│     │                                                                          │
│     ├─ LLM 继续: 调用 shell_command("sed -i ...") (用户确认，记住会话)           │
│     │   └─ SafetyEngine.check(post_tool_call) → score=11 >= 10 → ⚠️ block!    │
│     │                                                                          │
│     ├─ LLM 继续: 调用 shell_command("npx tsc --noEmit") (已记住，自动执行)       │
│     │                                                                          │
│     └─ LLM: "所有类型错误已修复，验证通过。"                                      │
│         ├─ 反思 (P3-9): 自我评估 → ACCEPT                                       │
│         └─ yield delta + completed                                              │
│                                                                               │
│  6. SessionManager.save() → 持久化对话历史                                      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 模块依赖关系图

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          模块依赖关系 (自上而下)                                 │
│                                                                               │
│  AgentService (入口门面)                                                        │
│  ├──▶ CustomizationEngine (定制化原语引擎)                                      │
│  │     ├──▶ InstructionsManager (AGENTS.md)                                    │
│  │     ├──▶ FileInstructionManager (.instructions.md)                          │
│  │     ├──▶ SkillManager (SKILL.md + rules)                                    │
│  │     ├──▶ AgentDefManager (.agent.md)                                        │
│  │     ├──▶ PromptManager (.prompt.md)                                         │
│  │     ├──▶ McpManager (mcp-servers.json)                                      │
│  │     └──▶ EventBus (hooks/*.json)                                            │
│  │                                                                             │
│  ├──▶ IntentRecognizer (意图识别)                                               │
│  │     ├──▶ QuickHeuristicClassifier (Layer 1: 规则引擎)                        │
│  │     └──▶ LLMIntentClassifier (Layer 2: LLM 分类)                            │
│  │                                                                             │
│  ├──▶ ModeRouter (模式路由)                                                     │
│  │                                                                             │
│  ├──▶ AgentLoop (LLM 工具调用循环)                                              │
│  │     ├──▶ StepExecutor (步骤执行器)                                           │
│  │     │     ├──▶ SafetyEngine (安全策略引擎)                                   │
│  │     │     │     ├──▶ DataDestructionPolicy                                  │
│  │     │     │     ├──▶ DataExfiltrationPolicy                                 │
│  │     │     │     ├──▶ PrivilegeEscalationPolicy                              │
│  │     │     │     ├──▶ SystemTamperingPolicy                                  │
│  │     │     │     ├──▶ RemoteCodeExecutionPolicy                              │
│  │     │     │     ├──▶ SessionContextPolicy                                   │
│  │     │     │     └──▶ ToolOutputContentPolicy                                │
│  │     │     ├──▶ PolicyEngine (权限检查)                                       │
│  │     │     ├──▶ GuardrailEngine (护栏检查)                                    │
│  │     │     ├──▶ ConsentStore (确认记录)                                       │
│  │     │     ├──▶ EventBus (事件总线)                                           │
│  │     │     ├──▶ ExecutionHarness (服务端执行)                                 │
│  │     │     │     ├──▶ ToolSandbox (沙盒决策)                                  │
│  │     │     │     └──▶ ToolExecutor (执行器)                                   │
│  │     │     └──▶ ToolRegistry (工具注册表)                                     │
│  │     │           └──▶ AgentTool 实现 (各具体工具)                              │
│  │     ├──▶ BrainContextManager (上下文管理器)                                  │
│  │     │     ├──▶ MemoryCommitGate (记忆门控)                                   │
│  │     │     └──▶ UserProfileService (用户画像)                                 │
│  │     └──▶ ModelRouter (LLM 路由 + 成本调度)                                   │
│  │                                                                             │
│  ├──▶ CapabilityRegistry (能力注册表)                                           │
│  │     ├──▶ ChatCapability                                                     │
│  │     ├──▶ CodeReviewCapability                                               │
│  │     ├──▶ DataAnalysisCapability                                             │
│  │     ├──▶ WebSearchCapability                                                │
│  │     └──▶ CodingCapability                                                   │
│  │                                                                             │
│  ├──▶ SessionManager (会话管理)                                                 │
│  │                                                                             │
│  ├──▶ ExtensionCatalog (扩展清单)                                               │
│  │     ├──▶ SkillManager                                                       │
│  │     ├──▶ AgentDefManager                                                    │
│  │     └──▶ McpManager                                                         │
│  │                                                                             │
│  └──▶ RAG 工作流 (独立路径)                                                     │
│        ├──▶ QueryOrchestrator (LangGraph)                                       │
│        └──▶ TaskOrchestrator (LangGraph)                                        │
│                                                                               │
│  基础设施层:                                                                    │
│  ├── 记忆系统: Working → Session → Task → Semantic → Profile                   │
│  ├── 可观测性: TraceRecorder, TaskMemory, ModelUsageSnapshot                    │
│  └── 共享状态: InMemoryState, Settings                                          │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 代码文件映射表

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         代码文件 → 功能点映射                                   │
│                                                                               │
│  大脑层 (Brain):                                                               │
│  ├── app/agent_platform/agents/brain/models.py         数据模型定义            │
│  ├── app/agent_platform/agents/brain/intent_recognizer.py  意图识别            │
│  ├── app/agent_platform/agents/brain/mode_router.py     模式路由              │
│  ├── app/agent_platform/agents/brain/agent_loop.py      LLM 工具调用循环       │
│  ├── app/agent_platform/agents/brain/step_executor.py   步骤执行器             │
│  ├── app/agent_platform/agents/brain/consent_store.py   用户确认记录存储        │
│  └── app/agent_platform/agents/brain/context_manager.py 上下文管理器           │
│                                                                               │
│  安全约束层:                                                                    │
│  ├── app/agent_platform/harness/safety/engine.py        安全策略引擎            │
│  ├── app/agent_platform/harness/safety/policies/        七大内置策略            │
│  │   ├── data_destruction.py                            数据破坏策略            │
│  │   ├── data_exfiltration.py                           数据外泄策略            │
│  │   ├── privilege_escalation.py                        权限提升策略            │
│  │