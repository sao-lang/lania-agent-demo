"""任务服务模块。

负责封装文档分析任务的创建、查询、重试，以及工具 schema 和受控子 Agent schema 的对外
读取能力。该服务位于 API 层与任务运行时之间，负责把外部请求校验为可执行任务并交给
`TaskDispatcher` 调度。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.agents.memory import TaskMemory
from app.agents.runtime import AgentRuntime
from app.agents.subagents import SubAgentRegistry, SubAgentSchema
from app.agents.tools.base import ToolSchema
from app.agents.tools.registry import ToolRegistry
from app.core.errors import bad_request_error, not_found_error
from app.harness.guardrails import GuardrailEngine
from app.harness.policy import PolicyEngine
from app.models.artifact import Artifact
from app.models.policy import (
    PolicyProfileCreateRequest,
    PolicyProfileItem,
    PolicyProfileListResponse,
    PolicyProfileUpdateRequest,
)
from app.models.task import TaskDetail, TaskListResponse, TaskRequest, TaskRunDetail, TaskRunSummary, TaskStatus
from app.services.sqlite_store import SQLiteStateStore
from app.services.task_dispatcher import TaskDispatcher
from app.services.state import InMemoryState


class TaskService:
    """封装文档分析任务的创建、查询和重试。"""

    def __init__(
        self,
        runtime: AgentRuntime,
        memory: TaskMemory,
        state: InMemoryState,
        dispatcher: TaskDispatcher,
        registry: ToolRegistry | None = None,
        subagent_registry: SubAgentRegistry | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        persistence: SQLiteStateStore | None = None,
    ) -> None:
        """初始化任务服务。

        Args:
            runtime: 任务运行时。
            memory: 任务记忆服务。
            state: 内存态业务数据。
            dispatcher: 任务调度器。
            registry: 可选工具注册表，用于暴露工具 schema。
            subagent_registry: 可选子 Agent 注册表，用于暴露子 Agent schema。
        """
        self.runtime = runtime
        self.memory = memory
        self.state = state
        self.dispatcher = dispatcher
        self.registry = registry
        self.subagent_registry = subagent_registry
        self.guardrail_engine = guardrail_engine or GuardrailEngine(registry or ToolRegistry())
        self.persistence = persistence
        self.policy_engine = policy_engine or PolicyEngine(self.runtime.orchestrator.settings, persistence=self.persistence)

    def create_task(self, payload: TaskRequest) -> TaskDetail:
        """创建任务并提交到后台执行。

        Args:
            payload: 通用任务请求体。

        Returns:
            入队后的任务详情对象。
        """

        self._ensure_task_type_supported(payload.task_type)
        payload = self._validate_request(payload)
        task = self.memory.create_task(payload)
        self.dispatcher.submit(task)
        return self.get_task(task.task_id)

    def create_document_analysis(self, payload: TaskRequest) -> TaskDetail:
        """创建文档分析任务并提交到后台执行。"""
        if payload.task_type != 'document_analysis':
            raise bad_request_error(
                'task_type_mismatch',
                'document-analysis endpoint only accepts document_analysis task_type',
                details={'task_type': payload.task_type},
            )
        return self.create_task(payload)

    def list_tasks(
        self,
        status: TaskStatus | None = None,
        collection_name: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> TaskListResponse:
        """返回任务列表。

        Args:
            status: 可选任务状态过滤条件。
            collection_name: 可选集合名称过滤条件。
            limit: 返回数量上限。
            offset: 分页偏移量。

        Returns:
            任务列表响应对象。
        """

        if collection_name is not None and collection_name not in self.state.collections:
            raise not_found_error('collection', collection_name)
        return self.memory.list_tasks(status=status, collection_name=collection_name, limit=limit, offset=offset)

    def get_task(self, task_id: str) -> TaskDetail:
        """返回任务详情。

        Args:
            task_id: 任务 ID。

        Returns:
            任务详情对象。
        """

        task = self.memory.get_task(task_id)
        if task is None:
            raise not_found_error('task', task_id)
        # 最终产物通常按需加载，避免任务列表查询时把全部 artifact 一起反序列化。
        if task.final_artifact_id and task.final_artifact is None:
            task.final_artifact = self.memory.get_artifact(task.final_artifact_id)
        return task

    def list_artifacts(self, task_id: str) -> list[Artifact]:
        """返回任务所有产物版本。

        Args:
            task_id: 任务 ID。

        Returns:
            与任务关联的全部产物版本列表。
        """

        self.get_task(task_id)
        return self.memory.list_artifacts(task_id)

    def retry_task(self, task_id: str) -> TaskDetail:
        """重试一个已有任务，并重新提交后台执行。

        Args:
            task_id: 任务 ID。

        Returns:
            重置并重新入队后的任务详情对象。
        """

        task = self.memory.reset_for_retry(task_id)
        if task is None:
            raise not_found_error('task', task_id)
        task.request = self._validate_request(task.request)
        self.memory.upsert_task(task)
        self.dispatcher.submit(task)
        return self.get_task(task_id)

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
        return self.runtime.orchestrator.list_task_runs(
            limit=limit,
            offset=offset,
            status=status,
            collection_name=collection_name,
            recoverable_only=recoverable_only,
        )

    def get_task_run(self, run_id: str) -> TaskRunDetail:
        """读取单个 task runtime。"""
        run = self.runtime.orchestrator.get_task_run(run_id)
        if run is None:
            raise not_found_error('task_run', run_id)
        return run

    def replay_task_run(self, run_id: str, checkpoint_id: str | None = None) -> TaskRunDetail:
        """从 task runtime checkpoint 发起 replay。"""
        self.get_task_run(run_id)
        try:
            return self.runtime.orchestrator.replay_task_run(run_id, checkpoint_id=checkpoint_id)
        except LookupError as exc:
            raise not_found_error('task_run', run_id) from exc
        except ValueError as exc:
            raise bad_request_error('invalid_checkpoint', str(exc)) from exc

    def resume_task_run(self, run_id: str) -> TaskRunDetail:
        """恢复一个可恢复的 task runtime。"""
        self.get_task_run(run_id)
        try:
            return self.runtime.orchestrator.resume_task_run(run_id)
        except LookupError as exc:
            raise not_found_error('task_run', run_id) from exc
        except RuntimeError as exc:
            raise bad_request_error('task_run_not_recoverable', str(exc)) from exc

    def list_tool_schemas(self) -> list[ToolSchema]:
        """返回任务工作流允许使用的工具 schema。

        Returns:
            工具 schema 列表；未配置注册表时返回空列表。
        """

        if self.registry is None:
            return []
        return self.registry.list_descriptions()

    def get_tool_schema(self, tool_name: str) -> ToolSchema:
        """返回单个工具 schema。

        Args:
            tool_name: 工具名称。

        Returns:
            指定工具的 schema 定义。
        """

        if self.registry is None:
            raise not_found_error('tool', tool_name)
        try:
            return self.registry.describe(tool_name)
        except KeyError as exc:
            raise not_found_error('tool', tool_name) from exc

    def list_subagent_schemas(self) -> list[SubAgentSchema]:
        """返回任务工作流可用的受控子代理 schema。

        Returns:
            子 Agent schema 列表；未配置注册表时返回空列表。
        """

        if self.subagent_registry is None:
            return []
        return self.subagent_registry.list_descriptions()

    def get_subagent_schema(self, agent_name: str) -> SubAgentSchema:
        """返回单个受控子代理 schema。

        Args:
            agent_name: 子 Agent 名称。

        Returns:
            指定子 Agent 的 schema 定义。
        """

        if self.subagent_registry is None:
            raise not_found_error('sub_agent', agent_name)
        try:
            return self.subagent_registry.describe(agent_name)
        except KeyError as exc:
            raise not_found_error('sub_agent', agent_name) from exc

    def list_policy_profiles(
        self,
        *,
        organization_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> PolicyProfileListResponse:
        """列出数据库化策略画像。"""
        items = [self._policy_profile_from_payload(item) for item in self._list_policy_profile_payloads()]
        if organization_id is not None and organization_id.strip():
            items = [item for item in items if item.organization_id == organization_id.strip()]
        if tenant_id is not None and tenant_id.strip():
            items = [item for item in items if item.tenant_id == tenant_id.strip()]
        total = len(items)
        paged = items[offset : offset + limit]
        return PolicyProfileListResponse(
            total=total,
            limit=limit,
            offset=offset,
            organization_id=organization_id.strip() if organization_id is not None and organization_id.strip() else None,
            tenant_id=tenant_id.strip() if tenant_id is not None and tenant_id.strip() else None,
            items=paged,
        )

    def create_policy_profile(self, payload: PolicyProfileCreateRequest) -> PolicyProfileItem:
        """创建一条数据库化策略画像。"""
        if self.persistence is None:
            raise bad_request_error('policy_store_unavailable', 'policy profile persistence is not configured')
        now = datetime.now(timezone.utc)
        item = PolicyProfileItem(
            profile_id=f'policy-{uuid4().hex[:12]}',
            name=payload.name.strip(),
            version=(payload.version or 'v1').strip() or 'v1',
            is_default=payload.is_default,
            organization_id=(payload.organization_id or '').strip() or None,
            tenant_id=(payload.tenant_id or '').strip() or None,
            allowed_roles=self._normalize_text_list(payload.allowed_roles, lower=True),
            match_keywords=self._normalize_text_list(payload.match_keywords),
            require_evidence=payload.require_evidence,
            min_coverage=payload.min_coverage,
            confidence_threshold=payload.confidence_threshold,
            require_review_passed=payload.require_review_passed,
            allowed_output_formats=self._normalize_text_list(payload.allowed_output_formats),
            blocked_tools=self._normalize_text_list(payload.blocked_tools),
            denied_permissions=self._normalize_permission_list(payload.denied_permissions),
            evaluation_baseline_order=self._normalize_text_list(payload.evaluation_baseline_order),
            evaluation_report_path=(payload.evaluation_report_path or '').strip() or None,
            description=(payload.description or '').strip() or None,
            created_at=now,
            updated_at=now,
        )
        records = self._list_policy_profile_payloads()
        records.append(item.model_dump(mode='python'))
        self._save_policy_profile_payloads(records, default_profile_id=item.profile_id if item.is_default else None)
        self.policy_engine.reload_if_needed()
        return item

    def update_policy_profile(self, profile_id: str, payload: PolicyProfileUpdateRequest) -> PolicyProfileItem:
        """更新一条数据库化策略画像。"""
        if self.persistence is None:
            raise bad_request_error('policy_store_unavailable', 'policy profile persistence is not configured')
        records = self._list_policy_profile_payloads()
        updated_records: list[dict[str, object]] = []
        target: PolicyProfileItem | None = None
        default_profile_id: str | None = None
        for record in records:
            item = self._policy_profile_from_payload(record)
            if item.profile_id != profile_id:
                updated_records.append(item.model_dump(mode='python'))
                if item.is_default:
                    default_profile_id = item.profile_id
                continue
            item = item.model_copy(
                update={
                    'name': payload.name.strip() if payload.name is not None and payload.name.strip() else item.name,
                    'version': payload.version.strip() if payload.version is not None and payload.version.strip() else item.version,
                    'is_default': payload.is_default if payload.is_default is not None else item.is_default,
                    'organization_id': (
                        payload.organization_id.strip()
                        if payload.organization_id is not None and payload.organization_id.strip()
                        else None if payload.organization_id is not None else item.organization_id
                    ),
                    'tenant_id': (
                        payload.tenant_id.strip()
                        if payload.tenant_id is not None and payload.tenant_id.strip()
                        else None if payload.tenant_id is not None else item.tenant_id
                    ),
                    'allowed_roles': (
                        self._normalize_text_list(payload.allowed_roles, lower=True)
                        if payload.allowed_roles is not None
                        else item.allowed_roles
                    ),
                    'match_keywords': (
                        self._normalize_text_list(payload.match_keywords)
                        if payload.match_keywords is not None
                        else item.match_keywords
                    ),
                    'require_evidence': payload.require_evidence if payload.require_evidence is not None else item.require_evidence,
                    'min_coverage': payload.min_coverage if payload.min_coverage is not None else item.min_coverage,
                    'confidence_threshold': (
                        payload.confidence_threshold
                        if payload.confidence_threshold is not None
                        else item.confidence_threshold
                    ),
                    'require_review_passed': (
                        payload.require_review_passed
                        if payload.require_review_passed is not None
                        else item.require_review_passed
                    ),
                    'allowed_output_formats': (
                        self._normalize_text_list(payload.allowed_output_formats)
                        if payload.allowed_output_formats is not None
                        else item.allowed_output_formats
                    ),
                    'blocked_tools': (
                        self._normalize_text_list(payload.blocked_tools)
                        if payload.blocked_tools is not None
                        else item.blocked_tools
                    ),
                    'denied_permissions': (
                        self._normalize_permission_list(payload.denied_permissions)
                        if payload.denied_permissions is not None
                        else item.denied_permissions
                    ),
                    'evaluation_baseline_order': (
                        self._normalize_text_list(payload.evaluation_baseline_order)
                        if payload.evaluation_baseline_order is not None
                        else item.evaluation_baseline_order
                    ),
                    'evaluation_report_path': (
                        payload.evaluation_report_path.strip()
                        if payload.evaluation_report_path is not None and payload.evaluation_report_path.strip()
                        else None if payload.evaluation_report_path is not None else item.evaluation_report_path
                    ),
                    'description': (
                        payload.description.strip()
                        if payload.description is not None and payload.description.strip()
                        else None if payload.description is not None else item.description
                    ),
                    'updated_at': datetime.now(timezone.utc),
                }
            )
            if item.is_default:
                default_profile_id = item.profile_id
            target = item
            updated_records.append(item.model_dump(mode='python'))
        if target is None:
            raise not_found_error('policy_profile', profile_id)
        self._save_policy_profile_payloads(updated_records, default_profile_id=default_profile_id)
        self.policy_engine.reload_if_needed()
        return target

    def delete_policy_profile(self, profile_id: str) -> None:
        """删除一条数据库化策略画像。"""
        if self.persistence is None:
            raise bad_request_error('policy_store_unavailable', 'policy profile persistence is not configured')
        records = self._list_policy_profile_payloads()
        remaining = [record for record in records if self._policy_profile_from_payload(record).profile_id != profile_id]
        if len(remaining) == len(records):
            raise not_found_error('policy_profile', profile_id)
        self._save_policy_profile_payloads(remaining)
        self.policy_engine.reload_if_needed()

    def _validate_request(self, payload: TaskRequest) -> TaskRequest:
        """在执行前校验 collection/doc_ids 是否可用。

        Args:
            payload: 待执行任务请求。

        Returns:
            补齐并规范化 `doc_ids` 后的新任务请求对象。
        """

        if payload.collection_name not in self.state.collections:
            raise not_found_error('collection', payload.collection_name)

        allowed_permissions = self._resolve_allowed_permissions(payload)
        normalized_doc_ids: list[str] = []
        if payload.doc_ids:
            for doc_id in payload.doc_ids:
                record = self.state.documents.get(doc_id)
                if record is None:
                    raise not_found_error('document', doc_id)
                if record['collection_name'] != payload.collection_name:
                    raise bad_request_error(
                        code='document_collection_mismatch',
                        message='document does not belong to collection',
                        details={
                            'doc_id': doc_id,
                            'collection_name': payload.collection_name,
                            'actual_collection_name': record['collection_name'],
                        },
                    )
                self._ensure_document_permission_allowed(doc_id, record, allowed_permissions)
                normalized_doc_ids.append(doc_id)
        else:
            # 未显式传文档列表时，默认分析该集合下全部文档，降低任务入口使用门槛。
            normalized_doc_ids = [
                str(doc_id)
                for doc_id, record in self.state.documents.items()
                if record.get('collection_name') == payload.collection_name
                and self._document_permission_allowed(record, allowed_permissions)
            ]
            if not normalized_doc_ids:
                raise bad_request_error(
                    code='task_documents_required',
                    message='collection has no documents visible to current task scope',
                    details={
                        'collection_name': payload.collection_name,
                        'allowed_permissions': allowed_permissions,
                    },
                )

        normalized = payload.model_copy(update={'doc_ids': normalized_doc_ids, 'allowed_permissions': allowed_permissions})
        decision = self.guardrail_engine.validate_input(normalized, self.state)
        self.guardrail_engine.raise_input_error(decision)
        policy_decision = self.policy_engine.check_task(normalized)
        if not policy_decision.allowed:
            raise bad_request_error('policy_task_rejected', policy_decision.reason, policy_decision.details)
        return normalized

    def _ensure_task_type_supported(self, task_type: str) -> None:
        """校验当前 orchestrator 是否支持指定任务类型。

        Args:
            task_type: 待校验的任务类型标识。
        """
        registry = getattr(self.runtime.orchestrator, 'skill_registry', None)
        if registry is None:
            return
        if registry.has(task_type):
            return
        raise bad_request_error(
            'unsupported_task_type',
            f'unsupported task_type: {task_type}',
            details={'task_type': task_type},
        )

    def _list_policy_profile_payloads(self) -> list[dict[str, object]]:
        """读取持久化层中的全部策略画像原始载荷。"""
        if self.persistence is None:
            return []
        return [item for item in self.persistence.list_policy_profiles() if isinstance(item, dict)]

    def _save_policy_profile_payloads(
        self,
        records: list[dict[str, object]],
        *,
        default_profile_id: str | None = None,
    ) -> None:
        """保存策略画像列表，并同步默认画像标记。

        Args:
            records: 待保存的策略画像原始载荷列表。
            default_profile_id: 可选默认画像 ID；为空时根据 `is_default` 推断。
        """
        assert self.persistence is not None
        resolved_default_profile_id = default_profile_id
        if resolved_default_profile_id is None:
            resolved_default_profile_id = next(
                (
                    str(item.get('profile_id') or '').strip()
                    for item in records
                    if bool(item.get('is_default'))
                ),
                None,
            )
        existing_ids = {
            str(item.get('profile_id') or '').strip()
            for item in self.persistence.list_policy_profiles()
            if str(item.get('profile_id') or '').strip()
        }
        new_ids = set()
        for record in records:
            item = self._policy_profile_from_payload(record)
            normalized = item.model_dump(mode='python')
            normalized['is_default'] = item.profile_id == resolved_default_profile_id
            self.persistence.upsert_policy_profile(normalized)
            new_ids.add(item.profile_id)
        for profile_id in existing_ids - new_ids:
            self.persistence.delete_policy_profile(profile_id)

    def _policy_profile_from_payload(self, payload: dict[str, object]) -> PolicyProfileItem:
        """把原始字典载荷转换为策略画像模型。"""
        return PolicyProfileItem.model_validate(payload)

    def _resolve_allowed_permissions(self, payload: TaskRequest) -> list[str]:
        """解析任务请求最终生效的文档权限范围。"""
        if payload.allowed_permissions:
            return self._normalize_permission_list(payload.allowed_permissions)
        if payload.permission_scope:
            return self._permissions_up_to_scope(payload.permission_scope)
        return []

    def _permissions_up_to_scope(self, scope: str) -> list[str]:
        """把权限层级扩展成“包含当前级别及以下”的权限列表。"""
        normalized_scope = self._normalize_permission(scope)
        if not normalized_scope:
            return []
        hierarchy = ['public', 'internal', 'private', 'restricted', 'confidential']
        if normalized_scope not in hierarchy:
            return [normalized_scope]
        return hierarchy[: hierarchy.index(normalized_scope) + 1]

    def _normalize_permission_list(self, values: list[str] | tuple[str, ...]) -> list[str]:
        """对权限列表做归一化、去重并保留输入顺序。"""
        ordered: list[str] = []
        seen: set[str] = set()
        for item in values:
            normalized = self._normalize_permission(item)
            if not normalized or normalized in seen:
                continue
            ordered.append(normalized)
            seen.add(normalized)
        return ordered

    def _normalize_permission(self, value: str | None) -> str | None:
        """把权限别名统一映射到系统内部标准值。"""
        if value is None:
            return None
        text = value.strip().lower()
        if not text:
            return None
        alias_map = {
            'public': 'public',
            'open': 'public',
            '公开': 'public',
            'internal': 'internal',
            'intranet': 'internal',
            '内部': 'internal',
            'private': 'private',
            '私有': 'private',
            'restricted': 'restricted',
            'sensitive': 'restricted',
            '受限': 'restricted',
            '敏感': 'restricted',
            'confidential': 'confidential',
            'secret': 'confidential',
            '机密': 'confidential',
            '保密': 'confidential',
        }
        return alias_map.get(text, text)

    def _document_permission_allowed(self, record: dict[str, object], allowed_permissions: list[str]) -> bool:
        """判断单个文档是否落在当前任务允许访问的权限范围内。"""
        if not allowed_permissions:
            return True
        record_permission = self._normalize_permission(str(record.get('permission') or 'public'))
        return record_permission in set(allowed_permissions)

    def _ensure_document_permission_allowed(
        self,
        doc_id: str,
        record: dict[str, object],
        allowed_permissions: list[str],
    ) -> None:
        """在文档超出权限范围时抛出标准业务异常。"""
        if self._document_permission_allowed(record, allowed_permissions):
            return
        raise bad_request_error(
            code='document_permission_denied',
            message='document is outside current task permission scope',
            details={
                'doc_id': doc_id,
                'document_permission': self._normalize_permission(str(record.get('permission') or 'public')),
                'allowed_permissions': allowed_permissions,
            },
        )

    def _normalize_text_list(self, values: list[str], *, lower: bool = False) -> list[str]:
        """对文本列表做裁剪、可选小写化与去重。"""
        ordered: list[str] = []
        seen: set[str] = set()
        for item in values:
            normalized = str(item).strip().lower() if lower else str(item).strip()
            if not normalized or normalized in seen:
                continue
            ordered.append(normalized)
            seen.add(normalized)
        return ordered
