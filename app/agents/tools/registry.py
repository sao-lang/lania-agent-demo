"""Agent 工具注册表模块。

负责统一注册、描述和执行工具，并把调用过程里的 trace、记忆记录和错误封装串起来。
"""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from app.agents.tools.base import (
    AgentTool,
    ToolContext,
    ToolErrorDefinition,
    ToolExecutionError,
    ToolMessage,
    ToolOutputEnvelope,
    ToolRetryPolicy,
    ToolSchema,
)


class ToolRegistry:
    """带观测能力的工具注册表。"""

    def __init__(self) -> None:
        """初始化空注册表。"""

        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        """注册工具实例。"""

        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool:
        """按名称取工具。"""

        return self._tools[name]

    def describe(self, name: str) -> ToolSchema:
        """返回单个工具的 schema 描述。"""

        tool = self.get(name)
        return ToolSchema(
            name=tool.name,
            version=getattr(tool, 'version', 'v1'),
            input_schema=tool.input_model.model_json_schema(),
            output_schema=self._build_output_envelope_schema(tool.output_model.model_json_schema()),
            error_codes=list(getattr(tool, 'error_codes', self._default_error_codes(tool.name))),
            timeout_ms=int(getattr(tool, 'timeout_ms', 30000)),
            retry_policy=getattr(tool, 'retry_policy', ToolRetryPolicy()),
            risk_level=getattr(tool, 'risk_level', 'low'),
            sandbox_mode=getattr(tool, 'sandbox_mode', 'inline'),
            trace_fields=list(
                getattr(tool, 'trace_fields', ['tool_call_id', 'task_id', 'tool_name', 'duration_ms', 'status'])
            ),
        )

    def list_descriptions(self) -> list[ToolSchema]:
        """返回所有已注册工具的 schema 描述。"""

        return [self.describe(name) for name in sorted(self._tools)]

    def run(
        self,
        name: str,
        payload: BaseModel | dict[str, Any],
        context: ToolContext,
        *,
        sandbox_runner=None,
    ) -> BaseModel:
        """校验输入并执行工具。"""

        tool = self.get(name)
        tool_call_id = context.tool_call_id or f'tool-{uuid4().hex[:12]}'
        started = perf_counter()
        status = 'ok'
        result: BaseModel | None = None
        model_payload: BaseModel | None = None
        error: str | None = None
        error_type: str | None = None
        default_action: str | None = None
        retry_count = 0
        run_context = replace(context, tool_call_id=tool_call_id)
        try:
            model_payload = payload if isinstance(payload, tool.input_model) else tool.input_model.model_validate(payload)
            result = (
                sandbox_runner(tool, model_payload, run_context)
                if sandbox_runner is not None
                else tool.run(model_payload, run_context)
            )
            if not isinstance(result, tool.output_model):
                result = tool.output_model.model_validate(
                    result.model_dump(mode='json') if isinstance(result, BaseModel) else result
                )
            return result
        except ValidationError as exc:
            status = 'error'
            model_payload = payload if isinstance(payload, BaseModel) else tool.input_model.model_construct()
            wrapped = ToolExecutionError(
                code=f'{name}_validation_error',
                message='tool input validation failed',
                error_type='validation_error',
                default_action='abort',
                details={'errors': exc.errors()},
            )
            error = wrapped.message
            error_type = wrapped.error_type
            default_action = wrapped.default_action
            raise wrapped from exc
        except ToolExecutionError as exc:
            status = 'error'
            error = exc.message
            error_type = exc.error_type
            default_action = exc.default_action
            raise
        except Exception as exc:
            status = 'error'
            wrapped = self._wrap_runtime_error(name, exc)
            error = wrapped.message
            error_type = wrapped.error_type
            default_action = wrapped.default_action
            raise wrapped from exc
        finally:
            duration_ms = int((perf_counter() - started) * 1000)
            if context.task_id:
                # 无论成功还是失败，都把这次工具调用写进任务记忆，后面排障和复盘更方便。
                context.task_memory.record_tool_call(
                    task_id=context.task_id,
                    tool_call_id=tool_call_id,
                    tool_name=name,
                    step=context.step_name,
                    status=status,
                    error_type=error_type,
                    default_action=default_action,
                    retry_count=retry_count,
                    duration_ms=duration_ms,
                    input_preview=model_payload.model_dump(mode='json') if model_payload is not None else {},
                    output_summary=self._build_output_envelope(result=result, error=error).model_dump(mode='json'),
                    error=error,
                )
            context.trace.record(
                'agent_tool_call',
                {
                    'tool_call_id': tool_call_id,
                    'task_id': context.task_id,
                    'tool_name': name,
                    'duration_ms': duration_ms,
                    'status': status,
                    'error_type': error_type,
                    'default_action': default_action,
                    'retry_count': retry_count,
                    'input_preview': model_payload.model_dump(mode='json') if model_payload is not None else {},
                    'output_summary': self._build_output_envelope(result=result, error=error).model_dump(mode='json'),
                    'error': error,
                },
            )

    def _wrap_runtime_error(self, tool_name: str, exc: Exception) -> ToolExecutionError:
        """把底层异常映射成任务系统认识的统一错误类型。"""
        if isinstance(exc, TimeoutError):
            return ToolExecutionError(
                code=f'{tool_name}_timeout_error',
                message=str(exc) or 'tool timed out',
                error_type='timeout_error',
                default_action='retry',
            )
        if isinstance(exc, PermissionError):
            return ToolExecutionError(
                code=f'{tool_name}_permission_error',
                message=str(exc) or 'tool permission denied',
                error_type='permission_error',
                default_action='abort',
            )
        if isinstance(exc, (ConnectionError, OSError)):
            return ToolExecutionError(
                code=f'{tool_name}_dependency_error',
                message=str(exc) or 'tool dependency failed',
                error_type='dependency_error',
                default_action='fallback',
            )
        return ToolExecutionError(
            code=f'{tool_name}_fatal_error',
            message=str(exc) or 'tool execution failed',
            error_type='fatal_error',
            default_action='abort',
        )

    def _default_error_codes(self, tool_name: str) -> list[ToolErrorDefinition]:
        """给没有显式声明错误码的工具补一套默认定义。"""
        return [
            ToolErrorDefinition(
                code=f'{tool_name}_validation_error',
                error_type='validation_error',
                default_action='abort',
                description='输入 schema 校验失败。',
            ),
            ToolErrorDefinition(
                code=f'{tool_name}_dependency_error',
                error_type='dependency_error',
                default_action='fallback',
                description='外部依赖或底层组件不可用。',
            ),
            ToolErrorDefinition(
                code=f'{tool_name}_timeout_error',
                error_type='timeout_error',
                default_action='retry',
                description='工具执行超时。',
            ),
            ToolErrorDefinition(
                code=f'{tool_name}_fatal_error',
                error_type='fatal_error',
                default_action='abort',
                description='工具执行遇到不可恢复错误。',
            ),
        ]

    def _build_output_envelope(self, result: BaseModel | None, error: str | None) -> ToolOutputEnvelope:
        """把工具输出压成统一 envelope，顺手补 warning/error。"""
        warnings: list[ToolMessage] = []
        if result is not None:
            payload = result.model_dump(mode='json')
            if isinstance(payload, dict):
                if 'missing_aspects' in payload and payload.get('missing_aspects'):
                    warnings.append(
                        ToolMessage(
                            code='coverage_warning',
                            message='tool result contains missing aspects',
                            details={'missing_aspects': payload.get('missing_aspects')},
                        )
                    )
                if 'open_questions' in payload and payload.get('open_questions'):
                    warnings.append(
                        ToolMessage(
                            code='open_questions_present',
                            message='tool result contains open questions',
                            details={'open_questions': payload.get('open_questions')},
                        )
                    )
            return ToolOutputEnvelope(data=payload if isinstance(payload, dict) else {'value': payload}, warnings=warnings)
        errors = []
        if error:
            errors.append(ToolMessage(code='tool_execution_error', message=error))
        return ToolOutputEnvelope(data={}, warnings=warnings, errors=errors)

    def _build_output_envelope_schema(self, data_schema: dict[str, Any]) -> dict[str, Any]:
        """根据具体数据 schema 拼出统一输出 envelope 的 schema。"""
        message_schema = ToolMessage.model_json_schema()
        return {
            'title': 'ToolOutputEnvelope',
            'type': 'object',
            'properties': {
                'data': data_schema,
                'warnings': {
                    'type': 'array',
                    'items': message_schema,
                    'default': [],
                },
                'errors': {
                    'type': 'array',
                    'items': message_schema,
                    'default': [],
                },
            },
            'required': ['data', 'warnings', 'errors'],
        }
