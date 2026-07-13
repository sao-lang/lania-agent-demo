"""任务记忆服务模块。

负责在任务工作流中统一读写任务状态、任务记忆、产物版本、反思记录和子 Agent 运行摘要，
并在可用时桥接 `SQLiteStateStore` 做持久化。该模块位于任务编排层下方，是 workflow、
worker 和 API 读取任务执行轨迹的共同入口。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, cast
from uuid import uuid4

from app.harness.models import ContextBundle
from app.models.artifact import Artifact, ReportArtifactContent, ReviewResult
from app.models.runtime_contracts import (
    GraphSubgraph,
    GroundedContext,
    MemoryRecord,
    PromptBuildRequest,
    PromptBuildResult,
    PromptSpec,
    dump_result_contract,
    load_result_contract,
)
from app.models.task import (
    ArtifactMemoryEntry,
    CheckpointRecord,
    PlanRevision,
    ReflectionEntry,
    SubAgentRunRecord,
    TaskDetail,
    TaskListResponse,
    TaskMemoryEntry,
    TaskRequest,
    TaskRun,
    TaskRunDetail,
    TaskRunEvent,
    TaskRunSummary,
    TaskSpec,
    TaskStatus,
    TaskSummaryItem,
    ToolCallRecord,
)
from app.capabilities.knowledge.contracts import RetrievalQualityReport
from app.runtime_contract_adapters import (
    artifact_memory_entry_to_memory_record,
    reflection_entry_to_memory_record,
    task_memory_entry_to_memory_record,
)
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import ArtifactRecord, TaskRecord, TaskRunRecord


class TaskMemory:
    """负责任务与产物的读写。"""

    def __init__(self, state: InMemoryState, persistence: SQLiteStateStore | None = None) -> None:
        """初始化任务记忆服务。

        Args:
            state: 进程内状态存储。
            persistence: 可选持久化实现；为空时仅使用内存态数据。
        """
        self.state = state
        self.persistence = persistence

    def create_task(self, request: TaskRequest) -> TaskDetail:
        """创建一条新的任务记录。

        Args:
            request: 用户提交的任务请求。

        Returns:
            初始化完成的任务详情对象。
        """

        now = datetime.now(timezone.utc)
        task = TaskDetail(
            task_id=f'task-{uuid4().hex[:12]}',
            request=request,
            queued_at=now,
            created_at=now,
            updated_at=now,
        )
        task.ensure_runtime_contracts()
        self.upsert_task(task)
        return task

    def get_task(self, task_id: str) -> TaskDetail | None:
        """读取任务。

        Args:
            task_id: 任务标识。

        Returns:
            命中时返回任务详情，否则返回 `None`。
        """

        if self.persistence is not None:
            payload = self.persistence.get_task(task_id)
            if payload is not None:
                self.state.tasks[task_id] = cast(TaskRecord, payload)
                return TaskDetail.model_validate(payload)
        payload = cast(Optional[dict[str, Any]], self.state.tasks.get(task_id))
        if payload is None:
            return None
        return TaskDetail.model_validate(payload)

    def upsert_task(self, task: TaskDetail) -> TaskDetail:
        """写入任务状态。

        Args:
            task: 待写入的任务详情对象。

        Returns:
            已更新 `updated_at` 并完成写入的任务对象。
        """

        task.ensure_runtime_contracts()
        task.updated_at = datetime.now(timezone.utc)
        payload = task.model_dump(mode='python')
        self.state.tasks[task.task_id] = cast(TaskRecord, payload)
        self._upsert_task_run(task)
        if self.persistence is not None:
            self.persistence.upsert_task(payload)
        return task

    def get_task_run(self, run_id: str) -> TaskRunDetail | None:
        """读取单个 task runtime 详情。"""
        if self.persistence is not None:
            payload = self.persistence.get_task_run(run_id)
            if payload is not None:
                self.state.task_runs[run_id] = cast(TaskRunRecord, payload)
                return self._to_task_run_detail(cast(TaskRunRecord, payload))
        payload = self.state.task_runs.get(run_id)
        if payload is None:
            return None
        return self._to_task_run_detail(payload)

    def list_task_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        collection_name: str | None = None,
        recoverable_only: bool = False,
    ) -> list[TaskRunSummary]:
        """列出 task runtime 历史。"""
        if self.persistence is not None:
            persisted = self.persistence.list_task_runs()
            if persisted:
                self.state.task_runs.update({item['run_id']: cast(TaskRunRecord, item) for item in persisted})
        records = list(self.state.task_runs.values())
        if status is not None:
            records = [item for item in records if item['status'] == status]
        if collection_name is not None:
            records = [item for item in records if item['collection_name'] == collection_name]
        if recoverable_only:
            records = [item for item in records if bool(item.get('recoverable'))]
        ordered = sorted(records, key=lambda item: (item['updated_at'], item['created_at']), reverse=True)
        return [self._to_task_run_summary(item) for item in ordered[offset: offset + limit]]

    def list_artifacts(self, task_id: str) -> list[Artifact]:
        """按版本顺序返回任务产物。"""

        if self.persistence is not None:
            persisted = self.persistence.list_artifacts_for_task(task_id)
            if persisted:
                for item in persisted:
                    self.state.artifacts[item['artifact_id']] = cast(ArtifactRecord, item)
                artifacts = [Artifact.model_validate(item) for item in persisted]
                return sorted(artifacts, key=lambda item: (item.version, item.created_at), reverse=True)
        artifacts = [
            Artifact.model_validate(item)
            for item in self.state.artifacts.values()
            if item.get('task_id') == task_id
        ]
        return sorted(artifacts, key=lambda item: (item.version, item.created_at), reverse=True)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        collection_name: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TaskListResponse:
        """按创建时间倒序返回任务列表。"""

        if self.persistence is not None:
            persisted = self.persistence.list_tasks()
            if persisted:
                self.state.tasks.update({item['task_id']: cast(TaskRecord, item) for item in persisted})
        tasks = [TaskDetail.model_validate(item) for item in self.state.tasks.values()]
        if status is not None:
            tasks = [item for item in tasks if item.status == status]
        if collection_name is not None:
            tasks = [item for item in tasks if item.request.collection_name == collection_name]
        ordered = sorted(tasks, key=lambda item: (item.updated_at, item.created_at), reverse=True)
        page = ordered[offset: offset + limit]
        items = [
            TaskSummaryItem(
                task_id=item.task_id,
                task_type=item.request.task_type,
                collection_name=item.request.collection_name,
                status=item.status,
                final_artifact_id=item.final_artifact_id,
                retry_count=item.retry_count,
                claimed_by=item.claimed_by,
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in page
        ]
        return TaskListResponse(items=items, total=len(ordered), limit=limit, offset=offset)

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """读取单个产物。"""

        if self.persistence is not None:
            payload = self.persistence.get_artifact(artifact_id)
            if payload is not None:
                self.state.artifacts[artifact_id] = cast(ArtifactRecord, payload)
                return Artifact.model_validate(payload)
        payload = cast(Optional[dict[str, Any]], self.state.artifacts.get(artifact_id))
        if payload is None:
            return None
        return Artifact.model_validate(payload)

    def claim_next_task(self, worker_id: str, lease_seconds: int) -> TaskDetail | None:
        """领取下一条待执行任务。

        Args:
            worker_id: 当前 worker 标识。
            lease_seconds: 任务租约时长。

        Returns:
            成功领取时返回任务详情，否则返回 `None`。
        """

        if self.persistence is not None:
            payload = self.persistence.claim_next_task(worker_id=worker_id, lease_seconds=lease_seconds)
            if payload is None:
                return None
            self.state.tasks[payload['task_id']] = cast(TaskRecord, payload)
            return TaskDetail.model_validate(payload)

        tasks = sorted(
            (TaskDetail.model_validate(item) for item in self.state.tasks.values()),
            key=lambda item: (item.queued_at or item.created_at, item.created_at),
        )
        now = datetime.now(timezone.utc)
        for task in tasks:
            if task.status != 'queued':
                continue
            task.status = 'running'
            task.claimed_by = worker_id
            task.started_at = task.started_at or now
            task.heartbeat_at = now
            task.lease_expires_at = now + timedelta(seconds=max(lease_seconds, 1))
            return self.upsert_task(task)
        return None

    def touch_task_heartbeat(self, task_id: str, worker_id: str, lease_seconds: int) -> TaskDetail | None:
        """续租任务，避免被其他 worker 抢占。

        Args:
            task_id: 任务标识。
            worker_id: 当前 worker 标识。
            lease_seconds: 新租约时长。

        Returns:
            续租成功时返回更新后的任务详情，否则返回 `None`。
        """

        if self.persistence is not None:
            payload = self.persistence.touch_task_heartbeat(
                task_id=task_id,
                worker_id=worker_id,
                lease_seconds=lease_seconds,
            )
            if payload is None:
                return None
            self.state.tasks[task_id] = cast(TaskRecord, payload)
            return TaskDetail.model_validate(payload)
        task = self.get_task(task_id)
        if task is None or task.claimed_by != worker_id:
            return None
        now = datetime.now(timezone.utc)
        task.heartbeat_at = now
        task.lease_expires_at = now + timedelta(seconds=max(lease_seconds, 1))
        return self.upsert_task(task)

    def store_artifact(
        self,
        task_id: str,
        artifact_type: str,
        status: str,
        content: ReportArtifactContent,
        review: ReviewResult | None = None,
    ) -> Artifact:
        """写入一版任务产物。"""

        existing_versions = [item.version for item in self.list_artifacts(task_id) if item.artifact_type == artifact_type]
        version = max(existing_versions, default=0) + 1
        now = datetime.now(timezone.utc)
        artifact = Artifact(
            artifact_id=f'artifact-{uuid4().hex[:12]}',
            task_id=task_id,
            artifact_type=artifact_type,
            version=version,
            status='final' if status == 'final' else 'draft',
            content=content,
            review=review,
            created_at=now,
            updated_at=now,
        )
        payload = artifact.model_dump(mode='python')
        self.state.artifacts[artifact.artifact_id] = cast(ArtifactRecord, payload)
        if self.persistence is not None:
            self.persistence.upsert_artifact(payload)
        return artifact

    def append_memory_record(
        self,
        record: MemoryRecord,
        *,
        task_id: str | None = None,
    ) -> TaskDetail | None:
        """直接追加一条统一的 MemoryRecord。

        这是新的活跃写入方法，绕过旧的 TaskMemoryEntry 中间层。
        如果 task_id 为空，则从 record.namespace 中提取。

        Args:
            record: 已构造好的 MemoryRecord 实例。
            task_id: 可选的任务 ID；为空时从 record.namespace 提取。

        Returns:
            更新后的 TaskDetail 对象，或返回 None（任务不存在时）。
        """
        tid = task_id or record.namespace.get('task_id')
        if tid is None:
            return None
        task = self.get_task(tid)
        if task is None:
            return None
        task.memory_records.append(record)
        task.memory_records = task.memory_records[-200:]
        return self.upsert_task(task)

    def query_memory_records(
        self,
        task_id: str,
        *,
        scope: str | None = None,
        kind: str | None = None,
        trust_level: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        """按条件查询任务的 MemoryRecord。

        Args:
            task_id: 任务标识。
            scope: 可选过滤范围（working / session / run / semantic / profile）。
            kind: 可选过滤种类。
            trust_level: 可选过滤信任级别。
            limit: 最大返回条数。

        Returns:
            符合条件的 MemoryRecord 列表，按创建时间倒序。
        """
        task = self.get_task(task_id)
        if task is None:
            return []
        records = task.memory_records
        if scope is not None:
            records = [r for r in records if r.scope == scope]
        if kind is not None:
            records = [r for r in records if r.kind == kind]
        if trust_level is not None:
            records = [r for r in records if r.trust_level == trust_level]
        return sorted(
            records,
            key=lambda r: (r.created_at, r.memory_id),
            reverse=True,
        )[:limit]

    def append_task_memory(
        self,
        task_id: str,
        step: str,
        kind: Literal['context', 'evidence', 'analysis', 'review', 'replan', 'state'],
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> TaskDetail | None:
        """追加一条任务执行记忆。"""

        task = self.get_task(task_id)
        if task is None:
            return None
        task.task_memory_entries.append(
            TaskMemoryEntry(
                entry_id=f'tm-{uuid4().hex[:12]}',
                step=step,
                kind=kind,
                summary=summary,
                payload=payload or {},
                created_at=datetime.now(timezone.utc),
            )
        )
        task.task_memory_entries = task.task_memory_entries[-100:]
        latest_entry = task.task_memory_entries[-1]
        task.memory_records.append(
            task_memory_entry_to_memory_record(
                latest_entry,
                task_id=task.task_id,
                task_run_id=task.task_run.run_id if task.task_run is not None else None,
                checkpoint_ref=task.task_run.checkpoints[-1].checkpoint_id if task.task_run is not None and task.task_run.checkpoints else None,
            )
        )
        task.memory_records = task.memory_records[-200:]
        return self.upsert_task(task)

    def append_artifact_memory(
        self,
        task_id: str,
        artifact: Artifact,
        summary: str,
        review_passed: bool | None = None,
    ) -> TaskDetail | None:
        """记录一版产物的记忆摘要。"""

        task = self.get_task(task_id)
        if task is None:
            return None
        task.artifact_memory_entries.append(
            ArtifactMemoryEntry(
                artifact_id=artifact.artifact_id,
                artifact_type=artifact.artifact_type,
                version=artifact.version,
                status=artifact.status,
                summary=summary,
                review_passed=review_passed,
                created_at=datetime.now(timezone.utc),
            )
        )
        task.artifact_memory_entries = task.artifact_memory_entries[-50:]
        latest_entry = task.artifact_memory_entries[-1]
        task.memory_records.append(
            artifact_memory_entry_to_memory_record(
                latest_entry,
                task_id=task.task_id,
                task_run_id=task.task_run.run_id if task.task_run is not None else None,
            )
        )
        task.memory_records = task.memory_records[-200:]
        return self.upsert_task(task)

    def append_reflection(
        self,
        task_id: str,
        step: str,
        trigger: Literal['evidence_gap', 'review'],
        decision: Literal['continue', 'replan', 'revise', 'finalize'],
        summary: str,
        *,
        missing_aspects: list[str] | None = None,
        missing_sections: list[str] | None = None,
        unsupported_claims: list[str] | None = None,
        review_notes: list[str] | None = None,
        plan_version: int = 1,
    ) -> TaskDetail | None:
        """记录一次结构化 reflection。

        Args:
            task_id: 任务标识。
            step: 当前触发 reflection 的步骤名。
            trigger: 触发原因，例如 `review`、`evidence_gap`。
            decision: 本次反思后的决策。
            summary: 面向后续排障的摘要说明。
            missing_aspects: 可选的证据缺口维度。
            missing_sections: 可选的缺失报告字段。
            unsupported_claims: 可选的未被证据支撑的结论。
            review_notes: 可选的审查备注。
            plan_version: 当前计划版本号。

        Returns:
            任务存在时返回更新后的任务详情，否则返回 `None`。
        """

        task = self.get_task(task_id)
        if task is None:
            return None
        task.reflection_entries.append(
            ReflectionEntry(
                entry_id=f'rf-{uuid4().hex[:12]}',
                step=step,
                trigger=trigger,
                decision=decision,
                summary=summary,
                missing_aspects=missing_aspects or [],
                missing_sections=missing_sections or [],
                unsupported_claims=unsupported_claims or [],
                review_notes=review_notes or [],
                plan_version=max(1, plan_version),
                created_at=datetime.now(timezone.utc),
            )
        )
        task.reflection_entries = task.reflection_entries[-50:]
        latest_entry = task.reflection_entries[-1]
        task.memory_records.append(
            reflection_entry_to_memory_record(
                latest_entry,
                task_id=task.task_id,
                task_run_id=task.task_run.run_id if task.task_run is not None else None,
            )
        )
        task.memory_records = task.memory_records[-200:]
        return self.upsert_task(task)

    def append_plan_revision(
        self,
        task_id: str,
        trigger: str,
        reason: str,
        added_steps: list[str] | None = None,
        plan=None,
    ) -> TaskDetail | None:
        """记录一次局部重规划并可选替换当前计划。

        Args:
            task_id: 任务标识。
            trigger: 触发重规划的原因类型。
            reason: 触发重规划的详细说明。
            added_steps: 本次新增的步骤标识列表。
            plan: 可选的新计划对象；提供时会替换当前计划。

        Returns:
            任务存在时返回更新后的任务详情，否则返回 `None`。
        """

        task = self.get_task(task_id)
        if task is None:
            return None
        task.plan_version += 1
        if plan is not None:
            task.plan = plan
        task.plan_revisions.append(
            PlanRevision(
                version=task.plan_version,
                trigger=trigger,
                reason=reason,
                added_steps=added_steps or [],
                created_at=datetime.now(timezone.utc),
            )
        )
        task.plan_revisions = task.plan_revisions[-20:]
        return self.upsert_task(task)

    def record_tool_call(
        self,
        task_id: str,
        tool_call_id: str,
        tool_name: str,
        status: str,
        duration_ms: int,
        input_preview: dict[str, Any],
        output_summary: dict[str, Any] | None = None,
        error: str | None = None,
        step: str | None = None,
        error_type: str | None = None,
        default_action: str | None = None,
        retry_count: int = 0,
    ) -> TaskDetail | None:
        """记录工具调用历史。

        Args:
            task_id: 任务标识。
            tool_call_id: 工具调用唯一标识。
            tool_name: 工具名称。
            status: 调用结果状态。
            duration_ms: 调用耗时。
            input_preview: 输入摘要。
            output_summary: 可选输出摘要。
            error: 可选错误信息。
            step: 可选步骤名称。
            error_type: 可选错误类型。
            default_action: 可选默认回退动作。
            retry_count: 当前调用重试次数。

        Returns:
            任务存在时返回更新后的任务详情，否则返回 `None`。
        """

        task = self.get_task(task_id)
        if task is None:
            return None
        task.tool_call_history.append(
            ToolCallRecord(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                step=step,
                status='error' if status == 'error' else 'ok',
                error_type=error_type,
                default_action=default_action,
                retry_count=max(0, retry_count),
                duration_ms=max(0, duration_ms),
                input_preview=input_preview,
                output_summary=output_summary,
                error=error,
                created_at=datetime.now(timezone.utc),
            )
        )
        task.tool_call_history = task.tool_call_history[-100:]
        return self.upsert_task(task)

    def record_sub_agent_run(
        self,
        task_id: str,
        agent_name: str,
        action: str,
        *,
        status: str = 'completed',
        allowed_tools: list[str] | None = None,
        selected_tools: list[str] | None = None,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
        handoff_id: str | None = None,
        source_step_id: str | None = None,
        context_keys: list[str] | None = None,
        step_limit: int | None = None,
        budget_limit: int | None = None,
        sandbox_profile: str | None = None,
    ) -> TaskDetail | None:
        """记录一次受控子代理执行摘要。

        Args:
            task_id: 任务标识。
            agent_name: 子 Agent 名称。
            action: 执行动作名称。
            status: 本次执行状态。
            allowed_tools: 允许使用的工具白名单。
            selected_tools: 本次实际使用的工具列表。
            input_summary: 输入摘要。
            output_summary: 输出摘要。

        Returns:
            任务存在时返回更新后的任务详情，否则返回 `None`。
        """

        task = self.get_task(task_id)
        if task is None:
            return None
        task.sub_agent_runs.append(
            SubAgentRunRecord(
                run_id=f'sag-{uuid4().hex[:12]}',
                agent_name=agent_name,
                action=action,
                status='failed' if status == 'failed' else ('fallback' if status == 'fallback' else 'completed'),
                handoff_id=handoff_id,
                source_step_id=source_step_id,
                context_keys=context_keys or [],
                step_limit=step_limit,
                budget_limit=budget_limit,
                sandbox_profile=sandbox_profile,
                allowed_tools=allowed_tools or [],
                selected_tools=selected_tools or [],
                input_summary=input_summary or {},
                output_summary=output_summary or {},
                created_at=datetime.now(timezone.utc),
            )
        )
        task.sub_agent_runs = task.sub_agent_runs[-50:]
        return self.upsert_task(task)

    def reset_for_retry(self, task_id: str) -> TaskDetail | None:
        """在保留历史产物的前提下重置任务状态。

        Args:
            task_id: 任务标识。

        Returns:
            重置成功时返回更新后的任务详情，否则返回 `None`。
        """

        task = self.get_task(task_id)
        if task is None:
            return None
        # 保留既有 artifact，便于人工回溯历史版本，但把运行态指标与中间记忆全部清空。
        task.status = 'queued'
        task.plan = None
        task.current_step = None
        task.completed_steps = []
        task.focus_aspects = []
        task.evidence_pack_id = None
        task.final_artifact_id = None
        task.metrics.step_count = 0
        task.metrics.tool_calls = 0
        task.metrics.latency_ms = 0
        task.metrics.sub_agent_runs = 0
        task.metrics.sub_agent_failures = 0
        task.failures = []
        task.final_artifact = None
        task.task_run = None
        task.run_events = []
        task.context_bundles = {}
        task.memory_records = []
        task.prompt_specs = []
        task.prompt_build_requests = []
        task.prompt_build_results = []
        task.grounded_context = None
        task.graph_subgraph = None
        task.retrieval_quality_report = None
        task.result_contract = None
        task.plan_version = 1
        task.plan_revisions = []
        task.task_memory_entries = []
        task.artifact_memory_entries = []
        task.reflection_entries = []
        task.tool_call_history = []
        task.sub_agent_runs = []
        task.evaluation_scorecard = None
        task.regression_result = None
        task.retry_count += 1
        now = datetime.now(timezone.utc)
        task.queued_at = now
        task.started_at = None
        task.completed_at = None
        task.heartbeat_at = None
        task.lease_expires_at = None
        task.claimed_by = None
        return self.upsert_task(task)

    def _upsert_task_run(self, task: TaskDetail) -> None:
        """把 task 当前运行态同步到独立的 task_runs 数据面。"""
        if task.task_run is None or task.task_spec is None:
            return
        record = self._build_task_run_record(task)
        self.state.task_runs[record['run_id']] = record
        if self.persistence is not None:
            self.persistence.upsert_task_run(record)

    def _build_task_run_record(self, task: TaskDetail) -> TaskRunRecord:
        """把 TaskDetail 收敛成可查询的 task run 记录。"""
        task_run = cast(TaskRun, task.task_run)
        checkpoints = [item.model_dump(mode='json') for item in task_run.checkpoints]
        return {
            'run_id': task_run.run_id,
            'task_id': task.task_id,
            'status': task_run.status,
            'task_type': task.request.task_type,
            'collection_name': task.request.collection_name,
            'request_payload': task.request.model_dump(mode='json'),
            'task_spec': task.task_spec.model_dump(mode='json'),
            'task_run': task_run.model_dump(mode='json'),
            'checkpoints': checkpoints,
            'run_events': [item.model_dump(mode='json') for item in task.run_events],
            'context_bundles': {key: value.model_dump(mode='json') for key, value in task.context_bundles.items()},
            'memory_records': [item.model_dump(mode='json') for item in task.memory_records],
            'prompt_specs': [item.model_dump(mode='json') for item in task.prompt_specs],
            'prompt_build_requests': [item.model_dump(mode='json') for item in task.prompt_build_requests],
            'prompt_build_results': [item.model_dump(mode='json') for item in task.prompt_build_results],
            'grounded_context': task.grounded_context.model_dump(mode='json') if task.grounded_context is not None else None,
            'graph_subgraph': task.graph_subgraph.model_dump(mode='json') if task.graph_subgraph is not None else None,
            'retrieval_quality_report': (
                task.retrieval_quality_report.model_dump(mode='json')
                if task.retrieval_quality_report is not None
                else None
            ),
            'result_contract': dump_result_contract(task.result_contract),
            'final_artifact_id': task.final_artifact_id,
            'replayed_from_checkpoint_id': cast(Optional[str], (dump_result_contract(task.result_contract) or {}).get('replayed_from_checkpoint_id')),
            'last_checkpoint_id': checkpoints[-1]['checkpoint_id'] if checkpoints else None,
            'latency_ms': int(task.metrics.latency_ms) if task.metrics.latency_ms else None,
            'recoverable': task_run.status in {'running', 'failed'} and bool(checkpoints),
            'created_at': task_run.started_at or task.created_at,
            'updated_at': datetime.now(timezone.utc),
            'completed_at': task_run.completed_at,
        }

    def _to_task_run_summary(self, record: TaskRunRecord) -> TaskRunSummary:
        """把持久化记录转换为 task runtime 摘要。"""
        request_payload = cast(dict[str, Any], record['request_payload'])
        return TaskRunSummary(
            run_id=str(record['run_id']),
            task_id=str(record['task_id']),
            status=str(record['status']),
            task_type=str(record['task_type']),
            collection_name=str(record['collection_name']),
            instructions=str(request_payload.get('instructions') or ''),
            created_at=cast(datetime, record['created_at']),
            completed_at=cast(Optional[datetime], record.get('completed_at')),
            checkpoint_count=len(cast(list[dict[str, Any]], record.get('checkpoints') or [])),
            event_count=len(cast(list[dict[str, Any]], record.get('run_events') or [])),
            replayed_from_checkpoint_id=cast(Optional[str], record.get('replayed_from_checkpoint_id')),
            last_checkpoint_id=cast(Optional[str], record.get('last_checkpoint_id')),
            final_artifact_id=cast(Optional[str], record.get('final_artifact_id')),
            latency_ms=cast(Optional[int], record.get('latency_ms')),
            recoverable=bool(record.get('recoverable')),
        )

    def _to_task_run_detail(self, record: TaskRunRecord) -> TaskRunDetail:
        """把持久化记录转换为 task runtime 详情。"""
        return TaskRunDetail(
            **self._to_task_run_summary(record).model_dump(mode='python'),
            request_payload=cast(dict[str, Any], record['request_payload']),
            task_spec=TaskSpec.model_validate(cast(dict[str, Any], record['task_spec'])),
            task_run=TaskRun.model_validate(cast(dict[str, Any], record['task_run'])),
            checkpoints=[CheckpointRecord.model_validate(item) for item in cast(list[dict[str, Any]], record.get('checkpoints') or [])],
            run_events=[TaskRunEvent.model_validate(item) for item in cast(list[dict[str, Any]], record.get('run_events') or [])],
            context_bundles={
                key: ContextBundle.model_validate(value)
                for key, value in cast(dict[str, dict[str, Any]], record.get('context_bundles') or {}).items()
            },
            memory_records=[MemoryRecord.model_validate(item) for item in cast(list[dict[str, Any]], record.get('memory_records') or [])],
            prompt_specs=[PromptSpec.model_validate(item) for item in cast(list[dict[str, Any]], record.get('prompt_specs') or [])],
            prompt_build_requests=[
                PromptBuildRequest.model_validate(item)
                for item in cast(list[dict[str, Any]], record.get('prompt_build_requests') or [])
            ],
            prompt_build_results=[
                PromptBuildResult.model_validate(item)
                for item in cast(list[dict[str, Any]], record.get('prompt_build_results') or [])
            ],
            grounded_context=(
                GroundedContext.model_validate(record['grounded_context'])
                if record.get('grounded_context') is not None
                else None
            ),
            graph_subgraph=(
                GraphSubgraph.model_validate(record['graph_subgraph'])
                if record.get('graph_subgraph') is not None
                else None
            ),
            retrieval_quality_report=(
                RetrievalQualityReport.model_validate(record['retrieval_quality_report'])
                if record.get('retrieval_quality_report') is not None
                else None
            ),
            result_contract=load_result_contract(cast(Optional[dict[str, Any]], record.get('result_contract'))),
        )
