# 记忆系统改造方案

> 本文档描述项目记忆系统（Memory System）的现状、问题、目标架构和实施计划。
>
> 对应代码：`app/agents/memory.py`、`app/models/runtime_contracts.py`、`app/services/session_manager.py`、`app/services/state.py`、`app/services/sqlite_store.py`、`app/harness/core/trace_hook.py`

---

## 1. 现状分析

### 1.1 当前架构

```
┌──────────────────────────────────────────────────┐
│              三种记忆相关的独立系统               │
├──────────────────────────────────────────────────┤
│                                                   │
│  SessionManager           TaskMemory              │
│  ┌──────────────┐       ┌──────────────────┐      │
│  │ _sessions:   │       │ memory_records:   │      │
│  │ dict[str,    │       │ list[MemoryRecord]│      │
│  │ Session]     │       │                  │      │
│  │              │       │ scopes:          │      │
│  │ history:     │       │  ✓ working       │      │
│  │ list[Message]│       │  ✓ run           │      │
│  │              │       │  ✗ session       │      │
│  │ 纯内存       │       │  ✗ semantic      │      │
│  │ 重启丢失     │       │  ✗ profile       │      │
│  └──────────────┘       └──────────────────┘      │
│                                                   │
│  InMemoryState + SQLiteStateStore                 │
│  ┌──────────────────────────────────────────┐     │
│  │ 有 sessions 表 / task_runs 含 memory      │     │
│  │ SessionManager 不用 sessions 表           │     │
│  │ UserProfile 不存在                        │     │
│  └──────────────────────────────────────────┘     │
└──────────────────────────────────────────────────┘
```

### 1.2 关键问题

| # | 问题 | 严重程度 |
|---|------|---------|
| 1 | `SessionManager` 纯内存运行，不读写 `InMemoryState` 也不走 `SQLiteStateStore`，**重启丢失全部会话** | 🔴 高 |
| 2 | `MemoryRecord` 定义了 5 种 scope（working / session / run / semantic / profile），实际**只用了 2 种**（working、run） | 🟡 中 |
| 3 | `trust_level`（unverified → provisional → verified → final）有定义，**没有任何代码沿此阶梯提升** | 🟡 中 |
| 4 | **用户画像（UserProfile）完全不存在**，无法记录用户偏好、语言、输出格式等 | 🔴 高 |
| 5 | 跨任务的**长期记忆（semantic）** 无实现，成功任务的结论不能被后续任务复用 | 🟡 中 |
| 6 | **working 记忆从不清理**，step 结束后堆积在列表中 | 🟢 低 |
| 7 | `conflict_refs`、`stale`、`degraded` 等字段定义了但从未使用 | 🟢 低 |
| 8 | 工具（Tool）**无法查询历史记忆**，`ToolContext` 没有 memory 引用 | 🟡 中 |

### 1.3 当前代码分布

| 文件 | 职责 | 改造动作 |
|------|------|---------|
| `app/models/runtime_contracts.py` | `MemoryRecord` 模型定义 | 不改（模型已完备） |
| `app/agents/memory.py` | `TaskMemory` 读写入口 | 增强 |
| `app/services/session_manager.py` | 会话管理（纯内存） | **重构** |
| `app/services/state.py` | `InMemoryState` 数据容器 | 微调 |
| `app/services/sqlite_store.py` | SQLite 持久化 | 增加 user_profiles 等 |
| `app/harness/core/trace_hook.py` | `MemoryHook` 事件→记忆 | 增强 |
| `app/agents/tools/base.py` | `ToolContext` | 增加 memory 字段 |
| `app/core/config.py` | 全局配置 | 增加记忆配置项 |
| `app/container.py` | DI 容器 | 装配新组件 |

---

## 2. 目标架构

### 2.1 统一记忆总线

```
                        ┌──────────────────────────────────┐
                        │       Unified Memory Bus         │
                        │                                  │
                        │  TaskMemory (增强版)              │
                        │  ┌────────────────────────────┐  │
                        │  │ append_memory_record()     │  │
                        │  │ query_memory_records()     │  │
                        │  │ promote_trust()            │  │
                        │  │ commit_to_semantic()       │  │
                        │  │ resolve_conflicts()        │  │
                        │  └────────────────────────────┘  │
                        └──────────┬───────────────────────┘
                                   │
            ┌──────────────────────┼──────────────────────┐
            │                      │                      │
            ▼                      ▼                      ▼
    ┌───────────────┐    ┌───────────────┐    ┌──────────────────┐
    │ SessionManager │    │  TaskMemory   │    │ UserProfile      │
    │   (重构)        │    │  (增强)        │    │   Service (新增)  │
    │                │    │               │    │                  │
    │ 写 session     │    │ 写 run /      │    │ 写 profile       │
    │  scope 记录    │    │ working 记录   │    │  scope 记录      │
    │                │    │               │    │                  │
    │ 走 state +     │    │ 走 state +    │    │ 走 SQLite        │
    │ SQLite 持久化   │    │ SQLite 持久化  │    │ 持久化           │
    └───────┬───────┘    └───────┬───────┘    └────────┬─────────┘
            │                    │                     │
            └────────────────────┼─────────────────────┘
                                 │
                                 ▼
                    ┌──────────────────────────┐
                    │     MemoryCommitGate      │
                    │     (新增)                 │
                    │                          │
                    │  trust_level 提升         │
                    │  run → semantic 晋升      │
                    │  冲突检测                  │
                    │  过期标记                  │
                    └──────────────────────────┘
```

### 2.2 记忆层次模型

```
信任等级阶梯：
  未验证(unverified) → 暂定(provisional) → 已验证(verified) → 最终(final)
                                      ↑              ↑
                                  3+ 来源验证     24h 无冲突

Scope 晋升路径：
  working ──step完成──▶ run ──任务成功──▶ semantic
     ↑                    │                    │
     │                    │ 冲突               │ 跨任务复用
     │                    ▼                    ▼
     │               conflict_refs       ContextBundle
     │               stale=True           注入
     │
  session ──会话结束──▶ （摘要下沉到 run）
  
  profile（独立维度，不参与晋升链）
```

### 2.3 五层记忆详解

| Scope | 生命周期 | 写入者 | 读取者 | 存储 |
|-------|---------|--------|--------|------|
| **working** | 单 step 内，完成后清除 | Tool calls、ReAct 观察 | 当前 step 的后续动作 | TaskRun 内存 |
| **session** | 单次对话，持久化 | `SessionManager.add_message()` | 同 session 的后续轮次 | SQLite sessions 表 + memory_records |
| **run** | 单次 TaskRun 的全过程 | Tool calls、反思、产物 | Checkpoint replay、审计 | TaskRun payload |
| **semantic** | 跨 run 复用，不自动删除 | `MemoryCommitGate.commit_to_semantic()` | ContextBundle 构建时检索 | 独立 semantic_memory 表 |
| **profile** | 用户级别，长期稳定 | `UserProfileService` | Agent 初始化时加载 | 独立 user_profiles 表 |

---

## 3. 详细设计

### 3.1 SessionManager 重构

#### 目标

将 `SessionManager` 从纯内存改为 `InMemoryState` + `SQLiteStateStore` 双写，并在每次消息交互时同步写入 `MemoryRecord(scope='session')`，同时增加历史压缩能力。

#### 代码设计

```python
# app/services/session_manager.py

class SessionManager:
    """会话管理器（持久化版）。

    接入 InMemoryState + SQLiteStateStore 做持久化，
    同步写 MemoryRecord 接入统一记忆总线。
    """

    def __init__(
        self,
        state: InMemoryState,
        persistence: SQLiteStateStore,
        task_memory: TaskMemory | None = None,
        max_history: int = 100,
    ) -> None:
        self._state = state
        self._persistence = persistence
        self._task_memory = task_memory
        self._max_history = max_history

    async def get_or_create(self, session_id: str | None = None) -> Session:
        """获取或创建会话（优先从 state → SQLite → 新建）。"""
        if session_id:
            # 1. 尝试内存
            raw = self._state.sessions.get(session_id)
            if raw:
                return Session(**raw)
            # 2. 尝试 SQLite
            raw = self._persistence.get_session(session_id)
            if raw:
                self._state.sessions[session_id] = raw
                return Session(**raw)
        # 3. 新建
        session = Session(id=session_id or str(uuid4()))
        await self.save(session)
        return session

    async def save(self, session: Session) -> None:
        """双写：InMemoryState + SQLiteStateStore。"""
        session.updated_at = datetime.now(timezone.utc)
        payload = session.model_dump(mode='json')
        self._state.sessions[session.id] = payload
        self._persistence.upsert_session(session.id, payload)

    async def add_message(self, session_id: str, message: Message) -> None:
        """追加消息 → 持久化 + 写 MemoryRecord。"""
        session = await self.get_or_create(session_id)
        session.history.append(message)

        # 历史上限控制（FIFO）
        if len(session.history) > self._max_history:
            # 保留最近 50 条，其余摘要（可选）
            session.history = session.history[-self._max_history:]

        await self.save(session)

        # 同步写记忆总线
        if self._task_memory:
            record = MemoryRecord(
                memory_id=f'ses-{uuid4().hex[:12]}',
                scope='session',
                kind='observation',
                trust_level='verified',
                source='user' if message.role == 'user' else 'system',
                summary=message.content[:200],
                payload={
                    'session_id': session.id,
                    'role': message.role,
                    'content_preview': message.content[:500],
                },
                created_at=message.timestamp or datetime.now(timezone.utc),
            )
            self._task_memory.append_memory_record(record)

    async def delete(self, session_id: str) -> None:
        self._state.sessions.pop(session_id, None)
        self._persistence.delete_session(session_id)

    async def clear_history(self, session_id: str) -> None:
        session = await self.get(session_id)
        if session:
            session.history = []
            await self.save(session)
```

#### 涉及修改

| 文件 | 改动 |
|------|------|
| `app/services/session_manager.py` | 构造函数增加 `state`、`persistence`、`task_memory` 参数；所有方法改为读写 state + persistence |
| `app/container.py` | `SessionManager(state, persistence, task_memory)` 传入依赖 |
| `app/services/sqlite_store.py` | 验证 `get_session()` 方法是否存在，补充 `upsert_session` `delete_session` 的完整实现 |

### 3.2 UserProfileService（新增）

#### 目标

提供用户画像的存储、查询和偏好推断能力，画像数据持久化到 SQLite，并同步写 `MemoryRecord(scope='profile')`。

#### 模型定义

```python
# app/services/user_profile_service.py

class UserProfile(BaseModel):
    """用户画像。"""
    user_id: str
    preferences: dict[str, Any] = Field(default_factory=lambda: {
        'language': 'zh',              # 偏好语言
        'output_format': 'markdown',   # 输出格式
        'risk_tolerance': 'medium',    # 风险容忍度 low/medium/high
        'tools_disabled': [],          # 禁用的工具列表
        'temperature': 0.7,            # LLM 温度偏好
        'max_tool_calls': 20,          # 单次最大工具调用数
    })
    behavioral_traits: dict[str, Any] = Field(default_factory=dict)
    interaction_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

#### Service 实现

```python
class UserProfileService:
    """用户画像服务。

    职责：
    1. 画像的 CRUD（SQLite 持久化）
    2. 从交互行为推断并更新偏好
    3. 同步写 MemoryRecord(scope='profile') 到记忆总线
    """

    def __init__(
        self,
        persistence: SQLiteStateStore,
        task_memory: TaskMemory | None = None,
    ) -> None:
        self._persistence = persistence
        self._task_memory = task_memory

    def get_profile(self, user_id: str) -> UserProfile | None:
        """获取用户画像。"""
        raw = self._persistence.get_user_profile(user_id)
        if raw:
            return UserProfile(**raw)
        return None

    def get_or_create(self, user_id: str) -> UserProfile:
        """获取或创建。"""
        profile = self.get_profile(user_id)
        if profile is None:
            profile = UserProfile(user_id=user_id)
            self.upsert_profile(profile)
        return profile

    def upsert_profile(self, profile: UserProfile) -> None:
        """保存画像（持久化 + 记忆总线）。"""
        profile.updated_at = datetime.now(timezone.utc)
        self._persistence.upsert_user_profile(
            profile.user_id,
            profile.model_dump(mode='json'),
        )
        if self._task_memory:
            self._task_memory.append_memory_record(MemoryRecord(
                memory_id=f'profile-{profile.user_id}',
                scope='profile',
                kind='preference',
                trust_level='verified',
                source='system',
                summary=f'User {profile.user_id} profile updated',
                payload=profile.preferences,
                created_at=datetime.now(timezone.utc),
            ))

    def infer_and_update(
        self,
        user_id: str,
        interaction: dict[str, Any],
    ) -> UserProfile:
        """从单次交互推断偏好变化并更新画像。

        Args:
            user_id: 用户 ID
            interaction: 交互数据，包含 message、mode、tool_calls 等

        Returns:
            更新后的 UserProfile
        """
        profile = self.get_or_create(user_id)
        profile.interaction_count += 1

        # 推断语言偏好
        message = interaction.get('message', '')
        if message and self._detect_chinese(message):
            profile.preferences['language'] = 'zh'
        elif message and self._detect_english(message):
            profile.preferences['language'] = 'en'

        # 推断风险容忍度
        tool_calls = interaction.get('tool_calls', [])
        if tool_calls:
            high_risk_count = sum(
                1 for t in tool_calls
                if t.get('risk_level') == 'high'
            )
            if high_risk_count > 3:
                profile.preferences['risk_tolerance'] = 'high'
            elif high_risk_count == 0:
                profile.preferences['risk_tolerance'] = 'low'

        # 更新行为特征
        traits = profile.behavioral_traits
        traits['avg_tool_calls_per_session'] = (
            traits.get('avg_tool_calls_per_session', 0) * 0.7 +
            len(tool_calls) * 0.3
        )
        traits['last_mode'] = interaction.get('mode', 'chat')
        profile.behavioral_traits = traits

        self.upsert_profile(profile)
        return profile

    @staticmethod
    def _detect_chinese(text: str) -> bool:
        import re
        return bool(re.search(r'[\u4e00-\u9fff]', text))

    @staticmethod
    def _detect_english(text: str) -> bool:
        return bool(text and text[0].isascii() and text[0].isalpha())
```

#### SQLite 新增

```python
# app/services/sqlite_store.py 追加
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT
)
```

并在 `load_into()`、`upsert_user_profile()`、`get_user_profile()`、`delete_user_profile()` 中实现对应读写。

### 3.3 MemoryCommitGate（新增）

#### 目标

提供记忆的信任等级提升、scope 晋升和冲突检测能力，是"记忆加工"的核心引擎。

```python
# app/services/memory_commit_gate.py

class MemoryCommitGate:
    """记忆提交门。

    职责：
    1. trust_level 阶梯提升（unverified → provisional → verified → final）
    2. scope 晋升（run → semantic）
    3. 冲突检测与标记
    4. 过期记录标记
    """

    def __init__(self, task_memory: TaskMemory):
        self._task_memory = task_memory

    # ── 信任提升 ──────────────────────────────

    TRUST_LEVELS = ['unverified', 'provisional', 'verified', 'final']

    def promote_trust(self, task_id: str, memory_id: str) -> MemoryRecord | None:
        """将单条记忆提升一个信任等级。"""
        records = self._task_memory.query_memory_records(task_id)
        for r in records:
            if r.memory_id == memory_id:
                idx = self.TRUST_LEVELS.index(r.trust_level)
                if idx < len(self.TRUST_LEVELS) - 1:
                    r.trust_level = self.TRUST_LEVELS[idx + 1]  # type: ignore
                    self._task_memory.append_memory_record(r)
                    return r
        return None

    def auto_promote(self, task_id: str) -> int:
        """自动检查并提升符合条件的记忆。

        规则：
        - 同一条 observation 被 3+ 不同来源引用 → verified
        - verified 记录存在 24h 以上无冲突 → final
        """
        records = self._task_memory.query_memory_records(task_id)
        promoted_count = 0

        for r in records:
            if r.trust_level == 'provisional':
                # 检查引用数
                ref_count = len([
                    x for x in records
                    if r.memory_id in x.conflict_refs or x.summary == r.summary
                ])
                if ref_count >= 3:
                    r.trust_level = 'verified'
                    self._task_memory.append_memory_record(r)
                    promoted_count += 1

            elif r.trust_level == 'verified':
                # 检查是否已稳定 24h
                age = datetime.now(timezone.utc) - r.created_at
                if age > timedelta(hours=24) and not r.conflict_refs:
                    r.trust_level = 'final'
                    self._task_memory.append_memory_record(r)
                    promoted_count += 1

        return promoted_count

    # ── Scope 晋升 ────────────────────────────

    def commit_to_semantic(self, task_id: str) -> list[MemoryRecord]:
        """任务完成后，将高信任度的 run 记录提升到 semantic 层。

        触发时机：TaskWorkflowOrchestrator 在任务完成时调用。
        条件：status=completed, trust_level=verified/final
        """
        task = self._task_memory.get_task(task_id)
        if not task or task.status != 'completed':
            return []

        promoted: list[MemoryRecord] = []
        seen_summaries: set[str] = set()

        for r in task.memory_records:
            if r.scope != 'run':
                continue
            if r.trust_level not in ('verified', 'final'):
                continue
            if r.summary in seen_summaries:
                continue  # 去重
            seen_summaries.add(r.summary)

            semantic = MemoryRecord(
                memory_id=f'sem-{uuid4().hex[:12]}',
                scope='semantic',
                kind=r.kind,
                trust_level=r.trust_level,
                source=r.source,
                summary=r.summary,
                payload={
                    **r.payload,
                    'origin_task_id': task_id,
                    'origin_run_id': r.related_task_run_id,
                },
                related_task_run_id=task_id,
                created_at=datetime.now(timezone.utc),
            )
            self._task_memory.append_memory_record(semantic)
            promoted.append(semantic)

        return promoted

    # ── 冲突检测 ──────────────────────────────

    def resolve_conflicts(self, task_id: str) -> int:
        """检测并标记冲突记忆。

        返回标记的冲突数。
        """
        records = self._task_memory.query_memory_records(task_id)
        conflict_count = 0

        for i, a in enumerate(records):
            for b in records[i + 1:]:
                if a.memory_id == b.memory_id:
                    continue
                # 同类型、同 scope、内容不同 → 冲突
                if a.kind == b.kind and a.scope == b.scope:
                    if a.summary != b.summary and a.summary and b.summary:
                        if b.memory_id not in a.conflict_refs:
                            a.conflict_refs.append(b.memory_id)
                            b.conflict_refs.append(a.memory_id)
                            conflict_count += 1

        return conflict_count

    # ── 过期标记 ──────────────────────────────

    def mark_stale(self, task_id: str, new_plan_id: str) -> int:
        """在计划修订后，标记旧计划相关的记忆为过期。"""
        records = self._task_memory.query_memory_records(task_id)
        stale_count = 0

        for r in records:
            if r.checkpoint_ref and r.checkpoint_ref != new_plan_id:
                if not r.stale:
                    r.stale = True
                    stale_count += 1

        return stale_count
```

### 3.4 Working Memory 清理

#### 目标

在 step 完成后自动清除 `scope='working'` 的记忆，防止工作记忆无限堆积。

```python
# app/harness/core/trace_hook.py — MemoryHook 增强

class MemoryHook(RuntimeHook):
    """监听运行时事件并写入 TaskMemory。"""

    def __init__(self, memory: TaskMemory, name: str = 'memory_hook') -> None:
        self._memory = memory
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def handle(self, event: EventPayload) -> None:
        ws = event.workflow_state or {}
        task = ws.get('task')
        if task is None:
            return
        task_id = getattr(task, 'task_id', None)
        if task_id is None:
            return

        step = event.payload.get('step_name') or getattr(task, 'current_step', None)

        # 写事件摘要
        summary = f'运行时事件: {event.event.value}'
        self._memory.append_task_memory(
            task_id, step or 'runtime', 'state', summary,
            payload={
                'hook_event': event.event.value,
                'payload': event.payload,
                'metadata': event.metadata,
            },
        )

        # ★ 新增：step/reAct 完成后清理 working scope
        if event.event in (
            HookEvent.AFTER_STAGE,
            HookEvent.STAGE_FAILED,
            HookEvent.AFTER_REACT_TURN,
        ):
            self._clear_working_memory(task_id)

    def _clear_working_memory(self, task_id: str) -> None:
        """清除指定 task 下所有 working scope 的记忆。"""
        task = self._memory.state.tasks.get(task_id)
        if task is None:
            return
        # 从内存中的 TaskDetail 对象清理
        task_obj = self._memory.get_task(task_id)
        if task_obj is None:
            return
        before = len(task_obj.memory_records)
        task_obj.memory_records = [
            r for r in task_obj.memory_records
            if r.scope != 'working'
        ]
        after = len(task_obj.memory_records)
        if before != after:
            self._memory.upsert_task(task_obj)
```

### 3.5 ToolContext 增加 memory 引用

#### 目标

工具（Tool）在执行时可以查询历史记忆，实现"记忆感知"的行为。

```python
# app/agents/tools/base.py — ToolContext

@dataclass
class ToolContext:
    # ... 现有字段不变 ...

    # ★ 新增
    memory: TaskMemory | None = None
```

#### 使用示例

```python
class GetCurrentWeatherTool:
    name = 'get_current_weather'

    def run(self, payload, context):
        # 查询 semantic 记忆，看是否有缓存
        if context.memory:
            semantic_records = context.memory.query_memory_records(
                scope='semantic', kind='observation',
                limit=10,
            )
            for r in semantic_records:
                # 如果之前查过同一地点，直接返回缓存
                if r.payload.get('location') == payload.location:
                    return GetCurrentWeatherOutput(**r.payload['weather'])

        # 否则真正调用 API
        result = self._fetch_weather(payload.location)
        return result
```

#### 注入链

```
container.py
  → TaskWorkflowOrchestrator.__init__(memory=self.task_memory)
    → ExecutionHarness.__init__(memory=self.memory)
      → ToolExecutor._tool_context()
        → ToolContext(memory=self.dependencies.task_memory)
          → 工具 run() 内使用 context.memory.query_memory_records(...)
```

### 3.6 Semantic Memory 查询集成到 ContextBundle

#### 目标

在构建 `ContextBundle` 时自动检索 `scope='semantic'` 的记录，注入到 step 的上下文中。

```python
# app/harness/components/context_builders.py

class TaskContextBuilder:
    def build(self, ...) -> ContextBundle:
        # ... 现有逻辑 ...

        # ★ 新增：检索 semantic 记忆
        semantic_memory = []
        if self._task_memory:
            semantic_memory = self._task_memory.query_memory_records(
                scope='semantic',
                trust_level='verified',
                limit=20,
            )

        bundle.memory_slice['semantic'] = [
            {'summary': r.summary, 'kind': r.kind}
            for r in semantic_memory
        ]
        if semantic_memory:
            bundle.source_summary['semantic_memory'] = (
                f'semantic_memory/{len(semantic_memory)}_records'
            )

        return bundle
```

---

## 4. SQLite 存储变更

### 4.1 新增表

```sql
-- 用户画像表
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT
);

-- 语义/长期记忆独立存储（不嵌入 task payload）
CREATE TABLE IF NOT EXISTS semantic_memory (
    memory_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'semantic',
    created_at TEXT
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_semantic_memory_scope ON semantic_memory(scope);
CREATE INDEX IF NOT EXISTS idx_semantic_memory_created ON semantic_memory(created_at);
```

### 4.2 SQLiteStateStore 新增方法

```python
# app/services/sqlite_store.py

def get_user_profile(self, user_id: str) -> dict | None: ...
def upsert_user_profile(self, user_id: str, payload: dict) -> None: ...
def delete_user_profile(self, user_id: str) -> None: ...

def get_semantic_memory(self, memory_id: str) -> dict | None: ...
def query_semantic_memory(self, limit: int = 50, scope: str = 'semantic') -> list[dict]: ...
def upsert_semantic_memory(self, memory_id: str, payload: dict) -> None: ...
```

---

## 5. 配置变更

```python
# app/core/config.py — Settings 新增

# 记忆系统配置
memory_max_records_per_task: int = Field(default=200, alias='MEMORY_MAX_RECORDS_PER_TASK')
memory_enable_semantic: bool = Field(default=True, alias='MEMORY_ENABLE_SEMANTIC')
memory_enable_profile: bool = Field(default=True, alias='MEMORY_ENABLE_PROFILE')
memory_auto_promote_interval_minutes: int = Field(default=60, alias='MEMORY_AUTO_PROMOTE_INTERVAL_MINUTES')
session_max_history: int = Field(default=100, alias='SESSION_MAX_HISTORY')
```

---

## 6. 实施计划

### Phase 1 — 基础改造（预计 1-2 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 1.1 SessionManager 重构 | `session_manager.py` | 接入 state + persistence + task_memory |
| 1.2 SQLite 补充 | `sqlite_store.py` | 确保 `get_session` 等方法完备 |
| 1.3 容器装配 | `container.py` | 传递依赖到 SessionManager |
| 1.4 验证 | — | 会话在重启后仍可恢复 |

### Phase 2 — 用户画像（预计 1 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 2.1 UserProfile 模型 | `user_profile_service.py`（新） | 模型 + CRUD |
| 2.2 user_profiles 表 | `sqlite_store.py` | 建表 + 读写方法 |
| 2.3 偏好推断 | `user_profile_service.py` | `infer_and_update()` |
| 2.4 画像 API | `agent.py` endpoints | 暴露查询/更新接口 |
| 2.5 容器装配 | `container.py` | 注册 UserProfileService |

### Phase 3 — 记忆加工（预计 1-2 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 3.1 MemoryCommitGate | `memory_commit_gate.py`（新） | 信任提升 + scope 晋升 + 冲突检测 |
| 3.2 Working 清理 | `trace_hook.py` | `_clear_working_memory()` |
| 3.3 semantic 表 | `sqlite_store.py` | 建表 + 读写方法 |
| 3.4 任务完成时自动晋升 | `task_orchestrator.py` | 调用 `commit_to_semantic()` |

### Phase 4 — 记忆感知（预计 1 天）

| 任务 | 文件 | 说明 |
|------|------|------|
| 4.1 ToolContext.memory | `base.py` | 增加 memory 字段 |
| 4.2 注入链 | `tool_executor.py`、`execution.py`、`task_orchestrator.py` | 传递 TaskMemory |
| 4.3 ContextBundle 集成 | `context_builders.py` | 检索 semantic 记录注入 |
| 4.4 工具示例改造 | 某个 tool | 展示记忆感知用法 |

---

## 7. 设计原则

### 原则 1：MemoryRecord 是唯一格式

所有记忆数据——无论是会话消息、工具调用结果、用户画像、跨任务知识——统一使用 `MemoryRecord` 模型，用 `scope` 区分层次，用 `trust_level` 表示可信度。

### 原则 2：记忆总线只有一个入口

所有写操作通过 `TaskMemory.append_memory_record()` 进入，不绕过统一入口直接写 SQLite 或 state。

### 原则 3：晋升需要门控

从 `run → semantic` 不是自动的，必须通过 `MemoryCommitGate` ——确保只有高信任度、经过验证的知识才能进入长期记忆。

### 原则 4：记忆可查询

工具和工作流可以通过 `TaskMemory.query_memory_records()` 按 scope/kind/trust_level 过滤查询，实现"记忆感知"行为。

### 原则 5：分层存储

- **高频/临时**（working/session）：优先从 InMemoryState 读写
- **低频/稳定**（semantic/profile）：优先从独立 SQLite 表读写
- **任务绑定**（run）：嵌入 TaskRun payload

---

## 8. 与原架构文档的对应关系

| 架构文档概念 | 本次实现 |
|-------------|---------|
| `MemoryRecord.scope='profile'` | `UserProfileService` + `user_profiles` 表 |
| `MemoryRecord.scope='semantic'` | `MemoryCommitGate.commit_to_semantic()` + `semantic_memory` 表 |
| `MemoryRecord.scope='session'` | `SessionManager.add_message()` 同步写 |
| `MemoryRecord.trust_level` 晋升 | `MemoryCommitGate.promote_trust()` / `auto_promote()` |
| 记忆提交门 | `MemoryCommitGate` |
| Working 记忆清理 | `MemoryHook._clear_working_memory()` |
| 冲突检测 | `MemoryCommitGate.resolve_conflicts()` |
| 过期标记 | `MemoryCommitGate.mark_stale()` |
| 记忆感知工具 | `ToolContext.memory` |
| 长期记忆注入 Context | `TaskContextBuilder` 检索 semantic 记录 |
