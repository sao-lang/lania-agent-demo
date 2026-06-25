"""Tool Sandbox 基础能力。

负责给工具调用提供统一的风险分级和隔离决策。当前版本对高风险工具提供最小可用的
独立进程隔离执行，并继续保留显式准入判断和结构化审计信息。
"""

from __future__ import annotations

from multiprocessing import get_context
from queue import Empty
from time import monotonic
from typing import Any, Callable, Literal, cast

import httpx
from pydantic import BaseModel, Field

from app.agents.tools.artifact_tools import (
    DraftReportInput,
    DraftReportOutput,
    FinalizeReportInput,
    ReviewReportInput,
    draft_report_content,
    finalize_report_content,
    review_report_content,
)
from app.agents.tools.base import ToolExecutionError
from app.core.config import Settings
from app.harness.models import ContextBundle
from app.models.artifact import ReportArtifactContent, ReviewResult

ToolRiskLevel = Literal['low', 'medium', 'high']
ToolSandboxMode = Literal['inline', 'thread_isolated', 'process_isolated']


class ToolSandboxDecision(BaseModel):
    """一次工具调用的沙盒决策。"""

    allowed: bool
    tool_name: str
    risk_level: ToolRiskLevel = 'low'
    sandbox_mode: ToolSandboxMode = 'inline'
    reason: str = 'sandbox_passed'
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SandboxExecutionRequest(BaseModel):
    """远程 sandbox worker 的统一执行请求。"""

    tool_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=30000, ge=1)


class SandboxExecutionResponse(BaseModel):
    """远程 sandbox worker 的统一执行响应。"""

    tool_name: str
    sandbox_mode: ToolSandboxMode = 'process_isolated'
    data: dict[str, Any] = Field(default_factory=dict)


class SandboxWorkerToolSchema(BaseModel):
    """描述 sandbox worker 中可执行的单个工具。"""

    tool_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    sandbox_mode: ToolSandboxMode = 'process_isolated'
    risk_level: ToolRiskLevel = 'high'


class SandboxWorkerToolCatalog(BaseModel):
    """返回 sandbox worker 支持的工具目录。"""

    tools: list[SandboxWorkerToolSchema] = Field(default_factory=list)


class SandboxWorkerToolDefinition(BaseModel):
    """sandbox worker 内部工具定义。"""

    tool_name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    sandbox_mode: ToolSandboxMode = 'process_isolated'
    risk_level: ToolRiskLevel = 'high'


class SandboxWorkerTool:
    """可在 sandbox worker 中执行的工具定义。"""

    def __init__(
        self,
        *,
        tool_name: str,
        description: str,
        input_model: type[BaseModel],
        output_model: type[BaseModel],
        execute: Callable[[BaseModel], BaseModel],
        sandbox_mode: ToolSandboxMode = 'process_isolated',
        risk_level: ToolRiskLevel = 'high',
    ) -> None:
        """初始化 sandbox worker 内部工具定义。"""

        self.tool_name = tool_name
        self.description = description
        self.input_model = input_model
        self.output_model = output_model
        self.execute = execute
        self.sandbox_mode = sandbox_mode
        self.risk_level = risk_level

    def describe(self) -> SandboxWorkerToolDefinition:
        """返回当前 worker 工具的静态定义。"""

        return SandboxWorkerToolDefinition(
            tool_name=self.tool_name,
            description=self.description,
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            sandbox_mode=cast(ToolSandboxMode, self.sandbox_mode),
            risk_level=cast(ToolRiskLevel, self.risk_level),
        )


class SandboxWorkerRegistry:
    """管理 sandbox worker 可执行工具的注册表。"""

    def __init__(self) -> None:
        """初始化空的 worker 工具注册表。"""

        self._tools: dict[str, SandboxWorkerTool] = {}

    def register(self, tool: SandboxWorkerTool) -> None:
        """注册一个 sandbox worker 工具。"""

        self._tools[tool.tool_name] = tool

    def get(self, tool_name: str) -> SandboxWorkerTool:
        """读取指定 worker 工具，不存在时抛出统一错误。"""

        if tool_name not in self._tools:
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_not_supported',
                message='sandbox worker does not support this tool',
                error_type='fatal_error',
                default_action='abort',
            )
        return self._tools[tool_name]

    def has(self, tool_name: str) -> bool:
        """判断 worker 是否支持指定工具。"""

        return tool_name in self._tools

    def list(self) -> list[SandboxWorkerTool]:
        """按名称顺序返回全部已注册 worker 工具。"""

        return [self._tools[name] for name in sorted(self._tools)]

    def catalog(self) -> SandboxWorkerToolCatalog:
        """生成当前 worker 工具目录。"""

        return SandboxWorkerToolCatalog(
            tools=[
                SandboxWorkerToolSchema.model_validate(tool.describe().model_dump(mode='json'))
                for tool in self.list()
            ]
        )


def _execute_draft_report_worker(payload: BaseModel) -> BaseModel:
    """在 worker 进程内执行报告草稿生成。"""

    return draft_report_content(cast(DraftReportInput, payload))


def _execute_review_report_worker(payload: BaseModel) -> BaseModel:
    """在 worker 进程内执行报告审查。"""

    return review_report_content(cast(ReviewReportInput, payload))


def _execute_finalize_report_worker(payload: BaseModel) -> BaseModel:
    """在 worker 进程内执行报告定稿。"""

    return finalize_report_content(cast(FinalizeReportInput, payload))


def build_default_sandbox_worker_registry() -> SandboxWorkerRegistry:
    """构建默认的 sandbox worker 工具注册表。"""

    registry = SandboxWorkerRegistry()
    registry.register(
        SandboxWorkerTool(
            tool_name='draft_report',
            description='在独立 worker 中把结构化分析结果整理为报告草稿。',
            input_model=DraftReportInput,
            output_model=DraftReportOutput,
            execute=_execute_draft_report_worker,
            risk_level='medium',
        )
    )
    registry.register(
        SandboxWorkerTool(
            tool_name='review_report',
            description='在独立 worker 中执行确定性报告结构审查。',
            input_model=ReviewReportInput,
            output_model=ReviewResult,
            execute=_execute_review_report_worker,
            risk_level='medium',
        )
    )
    registry.register(
        SandboxWorkerTool(
            tool_name='finalize_report',
            description='在独立 worker 中补齐最终报告 markdown/json 输出。',
            input_model=FinalizeReportInput,
            output_model=ReportArtifactContent,
            execute=_execute_finalize_report_worker,
            risk_level='high',
        )
    )
    return registry


DEFAULT_SANDBOX_WORKER_REGISTRY = build_default_sandbox_worker_registry()


class ToolSandbox:
    """高风险工具的最小沙盒决策器。"""

    DEFAULT_HIGH_RISK_TOOLS = frozenset({'finalize_report'})
    DEFAULT_MEDIUM_RISK_TOOLS = frozenset({'draft_report', 'review_report'})
    HIGH_RISK_ALLOWED_STEPS = frozenset({'finalize'})

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        worker_registry: SandboxWorkerRegistry | None = None,
    ) -> None:
        """初始化沙箱执行器及远程执行熔断参数。"""

        self.settings = settings
        self._client = client
        self.worker_registry = worker_registry or DEFAULT_SANDBOX_WORKER_REGISTRY
        self.allow_local_fallback = bool(settings.sandbox_executor_allow_local_fallback) if settings is not None else True
        self.circuit_breaker_threshold = max(
            1,
            int(settings.remote_provider_circuit_breaker_threshold) if settings is not None else 3,
        )
        self.circuit_breaker_cooldown_seconds = max(
            1.0,
            float(settings.remote_provider_circuit_breaker_cooldown_seconds) if settings is not None else 30.0,
        )
        self._consecutive_remote_failures = 0
        self._remote_opened_until = 0.0

    def assess(
        self,
        *,
        tool_name: str,
        context_bundle: ContextBundle,
        declared_risk_level: ToolRiskLevel | None = None,
        declared_sandbox_mode: ToolSandboxMode | None = None,
    ) -> ToolSandboxDecision:
        """返回当前工具调用的沙盒决策。"""
        risk_level = declared_risk_level or self._risk_level_for_tool(tool_name)
        sandbox_mode = declared_sandbox_mode or ('process_isolated' if risk_level == 'high' else 'inline')
        if risk_level == 'high' and context_bundle.step_id not in self.HIGH_RISK_ALLOWED_STEPS:
            return ToolSandboxDecision(
                allowed=False,
                tool_name=tool_name,
                risk_level=risk_level,
                sandbox_mode=sandbox_mode,
                reason='high_risk_tool_blocked_outside_allowed_step',
                details={
                    'step_id': context_bundle.step_id,
                    'allowed_steps': ','.join(sorted(self.HIGH_RISK_ALLOWED_STEPS)),
                },
            )
        return ToolSandboxDecision(
            allowed=True,
            tool_name=tool_name,
            risk_level=risk_level,
            sandbox_mode=sandbox_mode,
            reason='sandbox_passed',
            details={'step_id': context_bundle.step_id},
        )

    def execute_isolated(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        timeout_ms: int,
        output_model: type[BaseModel],
    ) -> BaseModel:
        """按配置选择远程或本地隔离执行路径。"""

        if self._use_remote_executor():
            return self._execute_remote_isolated(
                tool_name=tool_name,
                payload=payload,
                timeout_ms=timeout_ms,
                output_model=output_model,
            )
        return self.execute_local_isolated(
            tool_name=tool_name,
            payload=payload,
            timeout_ms=timeout_ms,
            output_model=output_model,
        )

    def execute_local_isolated(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        timeout_ms: int,
        output_model: type[BaseModel],
    ) -> BaseModel:
        """在独立进程中执行受支持的高风险工具。"""
        worker_tool = self.worker_registry.get(tool_name)
        ctx = get_context('spawn')
        result_queue = ctx.Queue(maxsize=1)
        process = ctx.Process(target=_sandbox_worker_entry, args=(tool_name, payload, result_queue, self.worker_registry))
        process.start()
        process.join(max(timeout_ms, 1) / 1000)
        if process.is_alive():
            process.terminate()
            process.join(1)
            raise ToolExecutionError(
                code=f'{tool_name}_timeout_error',
                message='process-isolated tool execution exceeded sandbox timeout budget',
                error_type='timeout_error',
                default_action='retry',
                details={'timeout_ms': timeout_ms, 'sandbox_mode': 'process_isolated'},
            )
        try:
            result = result_queue.get_nowait()
        except Empty as exc:
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_error',
                message='sandbox worker exited without returning payload',
                error_type='dependency_error',
                default_action='fallback',
                details={'exitcode': process.exitcode},
            ) from exc
        if result.get('status') != 'ok':
            error = result.get('error') or {}
            raise ToolExecutionError(
                code=str(error.get('code') or f'{tool_name}_sandbox_error'),
                message=str(error.get('message') or 'sandbox worker failed'),
                error_type=cast(Any, str(error.get('error_type') or 'fatal_error')),
                default_action=cast(Any, str(error.get('default_action') or 'abort')),
                details=error.get('details') or {},
            )
        validated = worker_tool.output_model.model_validate(result.get('data') or {})
        return output_model.model_validate(validated.model_dump(mode='json'))

    def list_worker_tools(self) -> SandboxWorkerToolCatalog:
        """返回 sandbox worker 当前支持的工具目录。"""
        return self.worker_registry.catalog()

    def describe_worker_tool(self, tool_name: str) -> SandboxWorkerToolSchema:
        """返回单个 sandbox worker 工具的 schema。"""
        tool = self.worker_registry.get(tool_name)
        return SandboxWorkerToolSchema.model_validate(tool.describe().model_dump(mode='json'))

    def _execute_remote_isolated(
        self,
        *,
        tool_name: str,
        payload: dict[str, Any],
        timeout_ms: int,
        output_model: type[BaseModel],
    ) -> BaseModel:
        """通过远程 HTTP sandbox 执行隔离工具调用。"""

        if self.settings is None or not self.settings.sandbox_executor_base_url:
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_remote_config_missing',
                message='SANDBOX_EXECUTOR_BASE_URL is required when provider=remote_http',
                error_type='dependency_error',
                default_action='fallback',
            )
        if self._remote_circuit_is_open():
            if self._can_fallback_local():
                return self.execute_local_isolated(
                    tool_name=tool_name,
                    payload=payload,
                    timeout_ms=timeout_ms,
                    output_model=output_model,
                )
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_remote_circuit_open',
                message='remote sandbox executor circuit is open',
                error_type='dependency_error',
                default_action='abort',
                details={'provider': 'remote_http', 'sandbox_mode': 'process_isolated'},
            )
        request_payload = SandboxExecutionRequest(tool_name=tool_name, payload=payload, timeout_ms=timeout_ms)
        client = self._client or httpx.Client(
            base_url=self.settings.sandbox_executor_base_url.rstrip('/'),
            timeout=max(1.0, float(self.settings.sandbox_executor_timeout_seconds)),
            headers=self._headers(),
        )
        owns_client = self._client is None
        try:
            response = client.post(
                f"{self.settings.api_prefix.rstrip('/')}/sandbox/execute-tool",
                json=request_payload.model_dump(mode='json'),
            )
            response.raise_for_status()
            parsed = SandboxExecutionResponse.model_validate(response.json())
            self._reset_remote_circuit()
        except httpx.TimeoutException as exc:
            self._register_remote_failure()
            if self._can_fallback_local():
                return self.execute_local_isolated(
                    tool_name=tool_name,
                    payload=payload,
                    timeout_ms=timeout_ms,
                    output_model=output_model,
                )
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_remote_timeout',
                message='remote sandbox execution timed out',
                error_type='timeout_error',
                default_action='fallback',
                details={'sandbox_mode': 'process_isolated', 'provider': 'remote_http'},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            category = self._remote_status_category(status_code)
            if category in {'rate_limit', 'upstream'}:
                self._register_remote_failure()
                if self._can_fallback_local():
                    return self.execute_local_isolated(
                        tool_name=tool_name,
                        payload=payload,
                        timeout_ms=timeout_ms,
                        output_model=output_model,
                    )
            elif category == 'auth':
                raise ToolExecutionError(
                    code=f'{tool_name}_sandbox_remote_auth_error',
                    message='remote sandbox authentication failed',
                    error_type='permission_error',
                    default_action='abort',
                    details={
                        'sandbox_mode': 'process_isolated',
                        'provider': 'remote_http',
                        'status_code': status_code,
                    },
                ) from exc
            else:
                self._register_remote_failure()
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_remote_{category}_error',
                message=f'remote sandbox execution failed: {status_code}',
                error_type='dependency_error',
                default_action='fallback',
                details={
                    'sandbox_mode': 'process_isolated',
                    'provider': 'remote_http',
                    'status_code': status_code,
                },
            ) from exc
        except httpx.RequestError as exc:
            self._register_remote_failure()
            if self._can_fallback_local():
                return self.execute_local_isolated(
                    tool_name=tool_name,
                    payload=payload,
                    timeout_ms=timeout_ms,
                    output_model=output_model,
                )
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_remote_network_error',
                message='remote sandbox execution failed',
                error_type='dependency_error',
                default_action='fallback',
                details={'sandbox_mode': 'process_isolated', 'provider': 'remote_http'},
            ) from exc
        except httpx.HTTPError as exc:
            self._register_remote_failure()
            raise ToolExecutionError(
                code=f'{tool_name}_sandbox_remote_error',
                message=str(exc) or 'remote sandbox execution failed',
                error_type='dependency_error',
                default_action='fallback',
                details={'sandbox_mode': 'process_isolated', 'provider': 'remote_http'},
            ) from exc
        finally:
            if owns_client:
                client.close()
        return output_model.model_validate(parsed.data)

    def _use_remote_executor(self) -> bool:
        """判断当前是否启用远程 sandbox 执行器。"""

        return bool(self.settings is not None and self.settings.sandbox_executor_provider == 'remote_http')

    def _headers(self) -> dict[str, str]:
        """构造远程 sandbox 请求头。"""

        headers = {'Content-Type': 'application/json'}
        if self.settings is not None and self.settings.sandbox_executor_auth_token:
            headers['Authorization'] = f'Bearer {self.settings.sandbox_executor_auth_token}'
        return headers

    def _risk_level_for_tool(self, tool_name: str) -> ToolRiskLevel:
        """根据工具名返回默认风险等级。"""

        if tool_name in self.DEFAULT_HIGH_RISK_TOOLS:
            return 'high'
        if tool_name in self.DEFAULT_MEDIUM_RISK_TOOLS:
            return 'medium'
        return 'low'

    def _can_fallback_local(self) -> bool:
        """判断远程失败后是否允许回退本地隔离执行。"""

        return self.allow_local_fallback

    def _register_remote_failure(self) -> None:
        """记录一次远程执行失败，并在必要时打开熔断器。"""

        self._consecutive_remote_failures += 1
        if self._consecutive_remote_failures >= self.circuit_breaker_threshold:
            self._remote_opened_until = monotonic() + self.circuit_breaker_cooldown_seconds

    def _reset_remote_circuit(self) -> None:
        """重置远程执行熔断状态。"""

        self._consecutive_remote_failures = 0
        self._remote_opened_until = 0.0

    def _remote_circuit_is_open(self) -> bool:
        """判断远程 sandbox 熔断器当前是否开启。"""

        if self._remote_opened_until <= 0.0:
            return False
        if monotonic() >= self._remote_opened_until:
            self._reset_remote_circuit()
            return False
        return True

    def _remote_status_category(self, status_code: int) -> str:
        """把远程 HTTP 状态码映射为统一错误类别。"""

        if status_code in {401, 403}:
            return 'auth'
        if status_code == 429:
            return 'rate_limit'
        if status_code == 408:
            return 'timeout'
        if status_code >= 500:
            return 'upstream'
        return 'client'


def _sandbox_worker_entry(tool_name: str, payload: dict[str, Any], result_queue, worker_registry: SandboxWorkerRegistry | None = None) -> None:
    """sandbox 子进程入口，负责执行工具并回写结构化结果。"""

    try:
        data = _execute_supported_tool(tool_name, payload, worker_registry=worker_registry)
        result_queue.put({'status': 'ok', 'data': data})
    except ToolExecutionError as exc:
        result_queue.put(
            {
                'status': 'error',
                'error': {
                    'code': exc.code,
                    'message': exc.message,
                    'error_type': exc.error_type,
                    'default_action': exc.default_action,
                    'details': exc.details,
                },
            }
        )
    except Exception as exc:
        result_queue.put(
            {
                'status': 'error',
                'error': {
                    'code': f'{tool_name}_sandbox_error',
                    'message': str(exc) or 'sandbox worker failed',
                    'error_type': 'fatal_error',
                    'default_action': 'abort',
                    'details': {},
                },
            }
        )


def _execute_supported_tool(
    tool_name: str,
    payload: dict[str, Any],
    *,
    worker_registry: SandboxWorkerRegistry | None = None,
) -> dict[str, Any]:
    """执行 worker 注册表中受支持的单个工具。"""

    worker_tool = (worker_registry or DEFAULT_SANDBOX_WORKER_REGISTRY).get(tool_name)
    model_payload = worker_tool.input_model.model_validate(payload)
    result = worker_tool.execute(model_payload)
    return result.model_dump(mode='json')
