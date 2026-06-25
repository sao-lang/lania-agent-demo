"""ExecutionHarness 工具执行运行时模块。

负责把超时、重试、沙箱执行和熔断控制统一包进单个执行器，供上层 workflow
以稳定接口调用任意 Agent 工具。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from time import perf_counter, sleep, time
from typing import Any

from pydantic import BaseModel

from app.agents.tools.base import ToolContext, ToolExecutionError
from app.agents.tools.registry import ToolRegistry
from app.core.config import Settings
from app.harness.models import ContextBundle, ExecutionAttempt, ExecutionPolicy
from app.harness.sandbox import ToolSandbox


@dataclass
class CircuitState:
    """记录单个工具的熔断状态。"""

    consecutive_failures: int = 0
    opened_until_ts: float = 0.0


@dataclass
class ExecutionRuntimeDependencies:
    """执行器运行所需的全部依赖集合。"""

    state: Any
    retrieval: Any
    trace: Any
    task_memory: Any
    settings: Settings
    llm: Any
    vector_store: Any
    knowledge: Any
    rag: Any
    repository: Any
    api_contract: Any
    artifact: Any
    database: Any
    model_router: Any


@dataclass
class ToolExecutionOutcome:
    """单次工具执行的统一结果封装。"""

    result: BaseModel
    retry_count: int
    attempts: list[ExecutionAttempt]


class ToolExecutor:
    """执行带超时、重试、沙箱与熔断控制的工具调用。"""

    def __init__(
        self,
        registry: ToolRegistry,
        settings: Settings,
        sandbox_engine: ToolSandbox,
        dependencies: ExecutionRuntimeDependencies,
        owner_id_getter,
        owner_step_getter,
        run_budget_getter,
        should_retry,
        timeout_budget_ms,
    ) -> None:
        """初始化工具执行器及其控制依赖。"""

        self.registry = registry
        self.settings = settings
        self.sandbox_engine = sandbox_engine
        self.dependencies = dependencies
        self.owner_id_getter = owner_id_getter
        self.owner_step_getter = owner_step_getter
        self.run_budget_getter = run_budget_getter
        self.should_retry = should_retry
        self.timeout_budget_ms = timeout_budget_ms
        self._circuits: dict[str, CircuitState] = {}

    def execute(
        self,
        name: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        tool_call_id: str,
        *,
        runtime_policy: ExecutionPolicy,
        sandbox_mode: str,
    ) -> ToolExecutionOutcome:
        """执行一次工具调用，并在成功后关闭失败计数。"""

        self.ensure_circuit_closed(name)
        result, retry_count, attempts = self._run_with_runtime_controls(
            name,
            payload,
            workflow_state,
            tool_call_id,
            runtime_policy=runtime_policy,
            sandbox_mode=sandbox_mode,
        )
        self.mark_circuit_success(name)
        return ToolExecutionOutcome(result=result, retry_count=retry_count, attempts=attempts)

    def _run_with_runtime_controls(
        self,
        name: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        tool_call_id: str,
        *,
        runtime_policy: ExecutionPolicy,
        sandbox_mode: str,
    ) -> tuple[BaseModel, int, list[ExecutionAttempt]]:
        """按运行时策略执行工具，并在失败时按需重试。"""

        schema = self.registry.describe(name)
        schema_total_attempts = max(1, int(schema.retry_policy.max_attempts) + 1)
        max_attempts = max(1, min(schema_total_attempts, runtime_policy.max_attempts))
        timeout_budget_ms = self.timeout_budget_ms(schema.timeout_ms, runtime_policy.timeout_budget_ms)
        last_error: ToolExecutionError | None = None
        attempts: list[ExecutionAttempt] = []
        for attempt_index in range(max_attempts):
            attempt_started = perf_counter()
            try:
                result = self._run_single_attempt(
                    name,
                    payload,
                    workflow_state,
                    tool_call_id,
                    timeout_ms=timeout_budget_ms,
                    sandbox_mode=sandbox_mode,
                )
                attempts.append(
                    ExecutionAttempt(
                        attempt_index=attempt_index,
                        status='ok',
                        latency_ms=int((perf_counter() - attempt_started) * 1000),
                    )
                )
                return result, attempt_index, attempts
            except ToolExecutionError as exc:
                exc.details = {**exc.details, 'retry_count': attempt_index}
                last_error = exc
                attempts.append(
                    ExecutionAttempt(
                        attempt_index=attempt_index,
                        status='error',
                        latency_ms=int((perf_counter() - attempt_started) * 1000),
                        error_code=exc.code,
                        error_type=exc.error_type,
                    )
                )
                if not self.should_retry(exc, attempt_index=attempt_index, max_attempts=max_attempts):
                    break
                if schema.retry_policy.backoff_ms > 0:
                    sleep(schema.retry_policy.backoff_ms / 1000)
        assert last_error is not None
        raise last_error

    def _run_single_attempt(
        self,
        name: str,
        payload: dict[str, Any],
        workflow_state: dict[str, Any],
        tool_call_id: str,
        *,
        timeout_ms: int,
        sandbox_mode: str,
    ) -> BaseModel:
        """执行单次尝试，按沙箱模式选择线程内或进程隔离执行。"""

        if sandbox_mode == 'process_isolated':
            return self.registry.run(
                name,
                payload,
                self._tool_context(workflow_state, tool_call_id),
                sandbox_runner=lambda tool, model_payload, run_context: self.sandbox_engine.execute_isolated(
                    tool_name=tool.name,
                    payload=model_payload.model_dump(mode='json'),
                    timeout_ms=timeout_ms,
                    output_model=tool.output_model,
                ),
            )
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.registry.run, name, payload, self._tool_context(workflow_state, tool_call_id))
            try:
                result = future.result(timeout=max(timeout_ms, 1) / 1000)
            except FuturesTimeoutError as exc:
                future.cancel()
                raise ToolExecutionError(
                    code=f'{name}_timeout_error',
                    message='tool execution exceeded harness timeout budget',
                    error_type='timeout_error',
                    default_action='retry',
                    details={'timeout_ms': timeout_ms},
                ) from exc
        if not isinstance(result, BaseModel):
            raise ToolExecutionError(
                code=f'{name}_invalid_output',
                message='tool returned unsupported result type',
                error_type='fatal_error',
                default_action='abort',
            )
        return result

    def _tool_context(self, workflow_state: dict[str, Any], tool_call_id: str) -> ToolContext:
        """把依赖与 workflow owner 信息组装成 ``ToolContext``。"""

        return ToolContext(
            state=self.dependencies.state,
            retrieval=self.dependencies.retrieval,
            trace=self.dependencies.trace,
            task_memory=self.dependencies.task_memory,
            settings=self.dependencies.settings,
            llm=self.dependencies.llm,
            vector_store=self.dependencies.vector_store,
            knowledge=self.dependencies.knowledge,
            rag=self.dependencies.rag,
            repository=self.dependencies.repository,
            api_contract=self.dependencies.api_contract,
            artifact=self.dependencies.artifact,
            database=self.dependencies.database,
            task_id=self.owner_id_getter(workflow_state),
            step_name=self.owner_step_getter(workflow_state),
            tool_call_id=tool_call_id,
            run_budget=self.run_budget_getter(workflow_state),
            model_router=self.dependencies.model_router,
        )

    def ensure_circuit_closed(self, name: str) -> None:
        """在工具熔断开启时阻止继续执行。"""

        state = self._circuits.get(name)
        if state is None:
            return
        now_ts = time()
        if state.opened_until_ts > now_ts:
            raise ToolExecutionError(
                code=f'{name}_circuit_open',
                message='tool circuit breaker is open',
                error_type='dependency_error',
                default_action='fallback',
                details={'opened_until_ts': state.opened_until_ts},
            )
        if state.opened_until_ts:
            state.opened_until_ts = 0.0
            state.consecutive_failures = 0

    def mark_circuit_success(self, name: str) -> None:
        """在工具执行成功后重置熔断状态。"""

        state = self._circuits.setdefault(name, CircuitState())
        state.consecutive_failures = 0
        state.opened_until_ts = 0.0

    def mark_circuit_failure(self, name: str, exc: ToolExecutionError, *, runtime_policy: ExecutionPolicy) -> None:
        """在可恢复失败累计到阈值后打开熔断器。"""

        state = self._circuits.setdefault(name, CircuitState())
        if exc.error_type not in {'timeout_error', 'dependency_error', 'retryable_error'}:
            return
        state.consecutive_failures += 1
        if state.consecutive_failures >= runtime_policy.circuit_breaker_threshold:
            state.opened_until_ts = time() + (runtime_policy.circuit_breaker_cooldown_ms / 1000)

    def is_circuit_open(self, name: str) -> bool:
        """判断指定工具当前是否处于熔断开启状态。"""

        state = self._circuits.get(name)
        return bool(state is not None and state.opened_until_ts > time())
