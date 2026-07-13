"""RAG 系统查询前处理模块。

负责护栏、脱敏、查询改写等前置逻辑。
与主应用的 `app/services/query_preprocess_service.py` 功能一致，但使用独立配置。
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from app.rag_system.config.settings import RagSettings
from app.rag_system.guardrails.input import inspect_prompt_injection
from app.rag_system.guardrails.output import redact_text
from app.rag_system.models.query import CitationItem, QueryRequest
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.retrieval.service import RagRetrievalService


class QueryPreprocessService:
    """处理 Guardrails、脱敏、查询改写和扩展。"""

    def __init__(
        self,
        settings: RagSettings,
        retrieval_service: RagRetrievalService,
        trace: TraceRecorder,
        llm: Any | None = None,
    ) -> None:
        """初始化查询前处理服务。

        Args:
            settings: RAG 系统配置。
            retrieval_service: 检索服务。
            trace: 链路追踪记录器。
            llm: 可选的大模型实例。
        """
        self.settings = settings
        self.retrieval_service = retrieval_service
        self.trace = trace
        self.llm = llm

    def check_guardrails(self, text: str) -> dict[str, Any]:
        """执行 Prompt Injection 检测。"""
        if not self.settings.enable_prompt_guardrails:
            return {'blocked': False, 'risk': 'none'}
        return inspect_prompt_injection(text)

    def redact_pii(self, text: str) -> tuple[str, dict[str, Any]]:
        """执行 PII 脱敏。"""
        if not self.settings.enable_pii_redaction:
            return text, {'none': True}
        return redact_text(text)

    def rewrite_query(self, question: str, collection_name: str) -> str:
        """查询改写。"""
        cleaned = question.strip()
        for filler in self.retrieval_service.QUERY_FILLER_TERMS:
            cleaned = cleaned.replace(filler, '')
        cleaned = cleaned.strip()
        return cleaned or question

    def expand_multi_query(self, question: str, count: int = 3) -> list[str]:
        """多查询扩展。"""
        if not self.llm:
            return [question]
        try:
            prompt = (
                f"请将以下问题改写为 {count} 个不同角度的检索查询，"
                f"每行一个，只输出查询内容：\n{question}"
            )
            response = self.llm.complete(prompt)
            lines = str(response).strip().split('\n')
            queries = [q.strip().strip('-').strip() for q in lines if q.strip()]
            return queries[:count] or [question]
        except Exception:
            return [question]

    def generate_hyde(self, question: str) -> str:
        """HyDE：生成假设文档。"""
        if not self.llm:
            return question
        try:
            prompt = (
                f"请为以下问题撰写一段假设性的回答文本，"
                f"假设你手头有足够的资料来回答这个问题：\n{question}"
            )
            response = self.llm.complete(prompt)
            return str(response).strip()
        except Exception:
            return question

    def sanitize_citations(
        self,
        citations: list[CitationItem],
        redact: bool = True,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """对引用文本做脱敏处理。"""
        if not redact or not self.settings.enable_pii_redaction:
            return citations, {'none': True}
        redaction: dict[str, Any] = {'applied': False}
        for c in citations:
            c.text, _ = redact_text(c.text)
        redaction['applied'] = True
        return citations, redaction

    def guardrail_block_message(self) -> str:
        """返回 guardrails 拦截时的统一提示。"""
        return '请求被安全策略拦截，拒绝执行。'

    def public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """将内部 guardrail 状态裁剪成可安全暴露的结构。"""
        return {
            'blocked': guardrail_state.get('blocked', False),
            'risk': guardrail_state.get('risk', 'none'),
        }

    def empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        """返回默认脱敏状态。"""
        return {'enabled': enabled, 'applied': False}

    def resolve_rewrite_info(self, question: str, use_query_rewrite: bool) -> tuple[str, dict[str, Any] | None]:
        """生成改写后的检索问题与展示信息。"""
        if not use_query_rewrite:
            return question, None
        rewritten = self.rewrite_query(question, '')
        info = {'original': question, 'rewritten': rewritten} if rewritten != question else None
        return rewritten, info
