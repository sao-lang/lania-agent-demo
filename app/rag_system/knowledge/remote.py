"""RAG 系统远程 Knowledge 能力实现模块。

通过 HTTP 调用外部知识能力服务，封装超时、熔断、鉴权与回退到本地能力。
与主应用的 `app/capabilities/knowledge/remote.py` 功能一致。
"""

from __future__ import annotations

from time import monotonic
from typing import Any

import httpx

from app.rag_system.knowledge.base import (
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeSearchRequest,
)


class RemoteKnowledgeProviderError(RuntimeError):
    """描述远程 knowledge provider 的结构化失败。"""
    def __init__(self, *, status_code: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
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
        fallback_capability=None,
        allow_local_fallback: bool = True,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_cooldown_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.api_prefix = api_prefix.rstrip('/')
        self.timeout_seconds = timeout_seconds
        self.auth_token = auth_token
        self.fallback_capability = fallback_capability
        self.allow_local_fallback = allow_local_fallback
        self._circuit_failures = 0
        self._circuit_open_until = 0.0
        self._circuit_threshold = circuit_breaker_threshold
        self._circuit_cooldown = circuit_breaker_cooldown_seconds

    def _headers(self) -> dict[str, str]:
        headers = {'Content-Type': 'application/json'}
        if self.auth_token:
            headers['Authorization'] = f'Bearer {self.auth_token}'
        return headers

    def _url(self, path: str) -> str:
        return f'{self.base_url}{self.api_prefix}{path}'

    def _check_circuit(self) -> None:
        now = monotonic()
        if self._circuit_open_until > now:
            raise RemoteKnowledgeProviderError(
                status_code=503, code='circuit_open',
                message=f'circuit breaker open, retry after {int(self._circuit_open_until - now)}s',
            )

    def _record_success(self) -> None:
        self._circuit_failures = 0
        self._circuit_open_until = 0.0

    def _record_failure(self) -> None:
        self._circuit_failures += 1
        if self._circuit_failures >= self._circuit_threshold:
            self._circuit_open_until = monotonic() + self._circuit_cooldown

    def _fallback_or_raise(self, method: str, exc: Exception):
        if self.allow_local_fallback and self.fallback_capability:
            return getattr(self.fallback_capability, method)()
        raise RemoteKnowledgeProviderError(
            status_code=500, code='remote_unavailable',
            message=f'remote knowledge unavailable: {exc}',
        )

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        self._check_circuit()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    self._url('/knowledge/document-context'),
                    json={'collection_name': request.collection_name, 'doc_ids': request.doc_ids},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                self._record_success()
                data = resp.json()
                return DocumentContextResult(**data)
        except Exception as exc:
            self._record_failure()
            return self._fallback_or_raise('load_document_context', exc)

    def retrieve_evidence(self, request: KnowledgeSearchRequest, *, trace_context=None):
        self._check_circuit()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    self._url('/knowledge/search'),
                    json={'query': request.query, 'collection_name': request.collection_name, 'top_k': request.top_k},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                self._record_success()
                from app.rag_system.knowledge.base import EvidencePack
                return EvidencePack(items=resp.json().get('items', []))
        except Exception as exc:
            self._record_failure()
            return self._fallback_or_raise('retrieve_evidence', exc)

    def grounded_answer(self, request: GroundedAnswerRequest, *, trace_context=None) -> GroundedAnswerResult:
        self._check_circuit()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(
                    self._url('/knowledge/grounded-answer'),
                    json={'question': request.question, 'collection_name': request.collection_name, 'top_k': request.top_k},
                    headers=self._headers(),
                )
                resp.raise_for_status()
                self._record_success()
                data = resp.json()
                return GroundedAnswerResult(**data)
        except Exception as exc:
            self._record_failure()
            return self._fallback_or_raise('grounded_answer', exc)
