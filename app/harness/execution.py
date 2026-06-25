"""Execution Harness 实现。

负责提供统一的工具执行入口，把 workflow 对工具的调用收口为一层可观测、可回退的运行时包装。
在 phase3 中，ExecutionHarness 保留兼容 facade 角色，内部执行职责拆给 components。
"""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel

from app.agents.memory import TaskMemory
from app.agents.tools.base import ToolExecutionError
from app.agents.tools.registry import ToolRegistry
from app.capabilities.api_contract import ApiContractCapability, build_api_contract_capability_from_provider
from app.capabilities.artifact import ArtifactCapability, build_artifact_capability_from_provider
from app.capabilities.database import DatabaseCapability, build_database_capability_from_provider
from app.capabilities.knowledge import KnowledgeCapability, build_knowledge_capability
from app.capabilities.repository import RepositoryCapability, build_repository_capability
from app.core.config import Settings
from app.harness.components.execution_hooks import ExecutionHooks
from app.harness.components.execution_policy import ExecutionPolicyResolver
from app.harness.components.fallback_handler import FallbackHandler
from app.harness.components.tool_executor import ExecutionRuntimeDependencies, ToolExecutor
from app.harness.guardrails import GuardrailEngine
from app.harness.model_router import ModelRouter
from app.harness.models import ContextBundle, ExecutionRuntimeSummary, ToolExecutionResult
from app.harness.policy import PolicyEngine
from app.harness.sandbox import ToolSandbox
from app.rag.facade import RagFacade
from app.rag.observability import TraceRecorder
from app.services.state import InMemoryState

FallbackFactory = Callable[[ToolExecutionError], BaseModel]


class ExecutionHarness:
    """统一封装工具执行入口。"""

    def __init__(
        self,
        registry: ToolRegistry,
        memory: TaskMemory,
        trace: TraceRecorder,
        settings: Settings,
        state: InMemoryState,
        retrieval,
        vector_store,
        llm,
        knowledge: KnowledgeCapability | None = None,
        rag: RagFacade | None = None,
        repository: RepositoryCapability | None = None,
        api_contract: ApiContractCapability | None = None,
        artifact: ArtifactCapability | None = None,
        database: DatabaseCapability | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        sandbox_engine: ToolSandbox | None = None,
        model_router: ModelRouter | None = None,
    ) -> None:
        """初始化执行 facade 及其 policy/guardrail/sandbox 组件。"""

        self.registry = registry
        self.memory = memory
        self.trace = trace
        self.settings = settings
        self.state = state
        self.retrieval = retrieval
        self.vector_store = vector_store
        self.llm = llm
        self.knowledge = knowledge or build_knowledge_capability(
            settings=settings,
            state=state,
            retrieval=retrieval,
            vector_store=vector_store,
            llm=llm,
            model_router=model_router,
        )
        self.rag = rag or RagFacade(self.knowledge)
        self.repository = repository or build_repository_capability()
        self.api_contract = api_contract or build_api_contract_capability_from_provider(settings=settings)
        self.artifact = artifact or build_artifact_capability_from_provider(
            settings=settings,
            state=state,
            persistence=memory.persistence,
        )
        self.database = database or build_database_capability_from_provider(settings=settings)
        self.guardrail_engine = guardrail_engine or GuardrailEngine(registry)
        self.policy_engine = policy_engine or PolicyEngine()
        self.sandbox_engine = sandbox_engine or ToolSandbox()
        self.model_router = model_router or ModelRouter()
        self.hooks = ExecutionHooks(memory=self.memory, trace=self.trace)
        self.policy_resolver = ExecutionPolicyResolver(registry=self.registry, settings=self.settings)
        self.fallback_handler = FallbackHandler(memory=self.memory, trace=self.trace)
        self.tool_executor = ToolExecutor(
            registry=self.registry,
            settings=self.settings,
            sandbox_engine=self.sandbox_engine,
            dependencies=ExecutionRuntimeDependencies(
                state=self.state,
                retrieval=self.retrieval,
                trace=self.trace,
                task_memory=self.memory,
                settings=self.settings,
                llm=self.llm,
                vector_store=self.vector_store,
                knowledge=self.knowledge,
                rag=self.rag,
                repository=self.repository,
                api_contract=self.api_contract,
                artifact=self.artifact,
                database=self.database,
                model_router=self.model_router,
            ),
            owner_id_getter=self.hooks.workflow_owner_id,
            owner_step_getter=self.hooks.workflow_owner_step,
            run_budget_getter=self.hooks.workflow_run_budget,
            should_retry=self.policy_resolver.should_retry,
            timeout_budget_ms=self.policy_resolver.timeout_budget_ms,
        )

    def run_tool(
        self,
        name: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        context_bundle: ContextBundle,
        *,
        failure_action: str | None = None,
        fallback_factory: FallbackFactory | None = None,
    ) -> Any:
        """执行单次工具调用，并在允许时应用统一降级逻辑。"""
        tool_call_id = f'tool-{uuid4().hex[:12]}'
        started = perf_counter()
        runtime_policy = self.policy_resolver.resolve(name, context_bundle, failure_action=failure_action)
        result: BaseModel | None = None
        status = 'ok'
        selected_action = runtime_policy.failure_action
        failure_category: str | None = None
        warnings: list[str] = []
        errors: list[str] = []
        retry_count = 0
        attempts = []
        sandbox_mode = 'inline'
        try:
            tool_decision = self.guardrail_engine.validate_tool_call(name, payload, context_bundle.tool_options)
            self.guardrail_engine.raise_tool_error(tool_decision)
            if self.hooks.has_task_request(workflow_state):
                policy_decision = self.policy_engine.check_tool(workflow_state['task'].request, name, payload)
                if not policy_decision.allowed:
                    raise ToolExecutionError(
                        code='policy_tool_blocked',
                        message=policy_decision.reason,
                        error_type='permission_error',
                        default_action='abort',
                        details=policy_decision.details,
                    )
            schema = self.registry.describe(name)
            sandbox_decision = self.sandbox_engine.assess(
                tool_name=name,
                context_bundle=context_bundle,
                declared_risk_level=schema.risk_level,
                declared_sandbox_mode=schema.sandbox_mode,
            )
            if not sandbox_decision.allowed:
                raise ToolExecutionError(
                    code='sandbox_tool_blocked',
                    message=sandbox_decision.reason,
                    error_type='permission_error',
                    default_action='abort',
                    details=sandbox_decision.details,
                )
            sandbox_mode = sandbox_decision.sandbox_mode
            warnings.append(f'sandbox:{sandbox_decision.sandbox_mode}:{sandbox_decision.risk_level}')
            outcome = self.tool_executor.execute(
                name,
                payload,
                workflow_state,
                tool_call_id,
                runtime_policy=runtime_policy,
                sandbox_mode=sandbox_decision.sandbox_mode,
            )
            result = outcome.result
            retry_count = outcome.retry_count
            attempts = outcome.attempts
            return result
        except ToolExecutionError as exc:
            retry_count = max(retry_count, self.policy_resolver.derive_retry_count(exc))
            failure_category = self.policy_resolver.failure_category(exc)
            self.tool_executor.mark_circuit_failure(name, exc, runtime_policy=runtime_policy)
            effective_action = failure_action if failure_action in {'fallback', 'degrade', 'skip_with_gap'} else exc.default_action
            selected_action = effective_action
            if fallback_factory is None or effective_action not in {'fallback', 'degrade', 'skip_with_gap'}:
                status = 'error'
                errors.append(exc.message)
                raise
            status = 'fallback'
            warnings.append(f'{effective_action}:{exc.code}')
            result = self.fallback_handler.apply(
                exc,
                tool_name=name,
                tool_call_id=tool_call_id,
                workflow_state=workflow_state,
                context_bundle=context_bundle,
                effective_action=effective_action,
                fallback_factory=fallback_factory,
                workflow_owner_id=self.hooks.workflow_owner_id(workflow_state),
            )
            return result
        finally:
            duration_ms = int((perf_counter() - started) * 1000)
            execution = ToolExecutionResult(
                tool_name=name,
                status=status,
                failure_category=failure_category,
                selected_action=selected_action,
                latency_ms=duration_ms,
                retries=retry_count,
                timeout_budget_ms=runtime_policy.timeout_budget_ms,
                sandbox_mode=sandbox_mode,
                cost=None,
                warnings=warnings + self.hooks.derive_warnings(result),
                errors=errors,
                data=result.model_dump(mode='json') if isinstance(result, BaseModel) else None,
                trace_id=tool_call_id,
            )
            runtime_summary = ExecutionRuntimeSummary(
                tool_name=name,
                step_id=context_bundle.step_id,
                status=status,
                selected_action=selected_action,
                failure_category=failure_category,
                retry_count=retry_count,
                timeout_budget_ms=runtime_policy.timeout_budget_ms,
                sandbox_mode=sandbox_mode,
                circuit_breaker_open=self.tool_executor.is_circuit_open(name),
                used_fallback=status == 'fallback',
                attempts=attempts,
                trace_id=tool_call_id,
            )
            self.hooks.record_runtime_summary(workflow_state, runtime_summary)
            self.hooks.record_execution(workflow_state, context_bundle.step_id, execution)
