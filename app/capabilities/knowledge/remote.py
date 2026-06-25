"""远程 Knowledge 能力实现模块。

通过 HTTP 调用外部知识能力服务，并封装超时、熔断、鉴权失败、回退到本地能力
等运行时控制逻辑。
"""


from __future__ import annotations

from time import monotonic
from typing import Any

import httpx

from app.capabilities.knowledge.base import (
    DocumentContextCall,
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerCall,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeCapability,
    KnowledgeSearchCall,
    KnowledgeSearchRequest,
)


class RemoteKnowledgeProviderError(RuntimeError):
    """描述远程 knowledge provider 的结构化失败。"""

    def __init__(self, *, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        """初始化远程知识能力调用失败时的结构化异常信息。"""
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


class RemoteKnowledgeCapability:
    """通过 HTTP 调用远程 Knowledge Capability 服务。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_prefix: str = '/api/v1',
        timeout_seconds: float = 15.0,
        auth_token: str | None = None,
        fallback_capability: KnowledgeCapability | None = None,
        allow_local_fallback: bool = True,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化远程 Knowledge 能力客户端、熔断参数与本地回退依赖。"""
        self.base_url = base_url.rstrip('/')
        self.api_prefix = api_prefix.rstrip('/')
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.auth_token = auth_token
        self.fallback_capability = fallback_capability
        self.allow_local_fallback = allow_local_fallback
        self.circuit_breaker_threshold = max(1, int(circuit_breaker_threshold))
        self.circuit_breaker_cooldown_seconds = max(1.0, float(circuit_breaker_cooldown_seconds))
        self._client = client
        self._consecutive_failures = 0
        self._opened_until = 0.0

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        """通过远程服务加载文档上下文，并在需要时回退到本地能力。"""
        payload = DocumentContextCall(request=request)
        response = self._post(
            '/knowledge/document-context',
            payload.model_dump(mode='json'),
            operation='load_document_context',
            trace_context=None,
            fallback=lambda: self.fallback_capability.load_document_context(request)
            if self.fallback_capability is not None
            else None,
        )
        return DocumentContextResult.model_validate(response)

    def retrieve_evidence(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ):
        """通过远程服务检索证据，并处理 trace 透传与本地回退。"""
        payload = KnowledgeSearchCall(request=request, trace_context=trace_context)
        response = self._post(
            '/knowledge/search',
            payload.model_dump(mode='json'),
            operation='retrieve_evidence',
            trace_context=trace_context,
            fallback=lambda: self.fallback_capability.retrieve_evidence(request, trace_context=trace_context)
            if self.fallback_capability is not None
            else None,
        )
        from app.models.artifact import EvidencePack

        return EvidencePack.model_validate(response)

    def grounded_answer(
        self,
        request: GroundedAnswerRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> GroundedAnswerResult:
        """通过远程服务生成 grounded answer，并在失败时按策略回退。"""
        payload = GroundedAnswerCall(request=request, trace_context=trace_context)
        response = self._post(
            '/knowledge/grounded-answer',
            payload.model_dump(mode='json'),
            operation='grounded_answer',
            trace_context=trace_context,
            fallback=lambda: self.fallback_capability.grounded_answer(request, trace_context=trace_context)
            if self.fallback_capability is not None
            else None,
        )
        return GroundedAnswerResult.model_validate(response)

    def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        operation: str,
        trace_context: dict[str, Any] | None,
        fallback,
    ) -> dict[str, Any]:
        """向远程 Knowledge 服务发起 POST 请求，并统一处理熔断、回退与错误封装。"""
        if self._circuit_is_open():
            self._record_trace(
                trace_context,
                'knowledge_remote_circuit_open',
                {
                    'operation': operation,
                    'base_url': self.base_url,
                    'provider': 'remote_http',
                    'fallback_enabled': self._can_fallback(),
                },
            )
            if self._can_fallback() and fallback is not None:
                return self._apply_fallback(
                    operation=operation,
                    trace_context=trace_context,
                    reason='circuit_open',
                    fallback=fallback,
                )
            raise RemoteKnowledgeProviderError(
                status_code=503,
                code='knowledge_remote_circuit_open',
                message='remote knowledge provider circuit is open',
                details={'provider': 'remote_http', 'base_url': self.base_url, 'operation': operation},
            )
        client = self._client or httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            headers=self._headers(),
        )
        owns_client = self._client is None
        try:
            response = client.post(f'{self.api_prefix}{path}', json=payload)
            response.raise_for_status()
            self._reset_circuit()
            body = dict(response.json())
            self._record_trace(
                trace_context,
                'knowledge_remote_request_completed',
                {
                    'operation': operation,
                    'status_code': response.status_code,
                    'provider': 'remote_http',
                    'base_url': self.base_url,
                    'fallback_applied': False,
                },
            )
            return body
        except httpx.TimeoutException as exc:
            self._register_failure()
            if self._can_fallback() and fallback is not None:
                return self._apply_fallback(
                    operation=operation,
                    trace_context=trace_context,
                    reason='timeout',
                    fallback=fallback,
                    details={'provider': 'remote_http', 'base_url': self.base_url},
                )
            raise RemoteKnowledgeProviderError(
                status_code=504,
                code='knowledge_remote_timeout',
                message='remote knowledge provider timed out',
                details={'provider': 'remote_http', 'base_url': self.base_url, 'operation': operation},
            ) from exc
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            category = self._status_category(status_code)
            self._record_trace(
                trace_context,
                'knowledge_remote_request_failed',
                {
                    'operation': operation,
                    'status_code': status_code,
                    'provider': 'remote_http',
                    'base_url': self.base_url,
                    'category': category,
                },
            )
            if category in {'rate_limit', 'upstream'}:
                self._register_failure()
                if self._can_fallback() and fallback is not None:
                    return self._apply_fallback(
                        operation=operation,
                        trace_context=trace_context,
                        reason=category,
                        fallback=fallback,
                        details={'status_code': status_code, 'provider': 'remote_http', 'base_url': self.base_url},
                    )
            elif category == 'auth':
                raise RemoteKnowledgeProviderError(
                    status_code=status_code,
                    code='knowledge_remote_auth_failed',
                    message='remote knowledge provider authentication failed',
                    details={'provider': 'remote_http', 'base_url': self.base_url, 'operation': operation},
                ) from exc
            else:
                self._register_failure()
            raise RemoteKnowledgeProviderError(
                status_code=502 if status_code >= 500 else status_code,
                code=f'knowledge_remote_{category}_error',
                message=f'remote knowledge provider request failed: {status_code}',
                details={'provider': 'remote_http', 'base_url': self.base_url, 'operation': operation, 'status_code': status_code},
            ) from exc
        except httpx.RequestError as exc:
            self._register_failure()
            if self._can_fallback() and fallback is not None:
                return self._apply_fallback(
                    operation=operation,
                    trace_context=trace_context,
                    reason='network',
                    fallback=fallback,
                    details={'provider': 'remote_http', 'base_url': self.base_url},
                )
            raise RemoteKnowledgeProviderError(
                status_code=502,
                code='knowledge_remote_network_error',
                message='remote knowledge provider is unavailable',
                details={'provider': 'remote_http', 'base_url': self.base_url, 'operation': operation},
            ) from exc
            return dict(response.json())
        finally:
            if owns_client:
                client.close()
    def _can_fallback(self) -> bool:
        """判断当前配置下是否允许切换到本地回退能力。"""
        return self.allow_local_fallback and self.fallback_capability is not None

    def _status_category(self, status_code: int) -> str:
        """把 HTTP 状态码归类为鉴权、限流、超时或上游错误。"""
        if status_code in {401, 403}:
            return 'auth'
        if status_code == 429:
            return 'rate_limit'
        if status_code == 408:
            return 'timeout'
        if status_code >= 500:
            return 'upstream'
        return 'client'

    def _register_failure(self) -> None:
        """记录连续失败次数，并在达到阈值后打开熔断器。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.circuit_breaker_threshold:
            self._opened_until = monotonic() + self.circuit_breaker_cooldown_seconds

    def _reset_circuit(self) -> None:
        """重置连续失败计数与熔断窗口。"""
        self._consecutive_failures = 0
        self._opened_until = 0.0

    def _circuit_is_open(self) -> bool:
        """判断熔断器是否仍处于打开状态。"""
        if self._opened_until <= 0.0:
            return False
        if monotonic() >= self._opened_until:
            self._reset_circuit()
            return False
        return True

    def _apply_fallback(
        self,
        *,
        operation: str,
        trace_context: dict[str, Any] | None,
        reason: str,
        fallback,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """执行本地回退调用，并补充回退相关 trace 信息。"""
        fallback_result = fallback()
        payload = fallback_result.model_dump(mode='json') if hasattr(fallback_result, 'model_dump') else dict(fallback_result)
        self._record_trace(
            trace_context,
            'knowledge_remote_fallback_applied',
            {
                'operation': operation,
                'reason': reason,
                'provider': 'remote_http',
                'base_url': self.base_url,
                'details': details or {},
            },
        )
        return dict(payload)

    def _record_trace(self, trace_context: dict[str, Any] | None, name: str, payload: dict[str, Any]) -> None:
        """向 trace 上下文追加远程调用阶段的诊断事件。"""
        if isinstance(trace_context, dict):
            recorder = trace_context.get('trace_recorder')
            if recorder is not None and hasattr(recorder, 'record'):
                recorder.record(name, payload)
            trace_list = trace_context.get('trace')
            if isinstance(trace_list, list):
                trace_list.append({'event': name, **payload})


    def _headers(self) -> dict[str, str]:
        """构造远程请求所需的鉴权与内容类型头部。"""
        headers = {'Content-Type': 'application/json'}
        if self.auth_token:
            headers['Authorization'] = f'Bearer {self.auth_token}'
