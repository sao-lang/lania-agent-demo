"""RAG 图运行时适配器模块。

将 ``RagQueryEngine`` 适配为 ``RagGraphRuntime`` 协议，
使 LangGraph 查询工作流能够直接使用 rag_system 的查询引擎。
"""

from __future__ import annotations

from typing import Any

from app.rag_system.knowledge.base import KnowledgeSearchRequest
from app.rag_system.knowledge.contracts import RetrievalQualityReport
from app.rag_system.models.query import (
    ChatRequest,
    CitationItem,
    QueryRequest,
    QueryResponse,
)
from app.rag_system.query.engine import RagQueryEngine
from app.rag_system.query.graph.runtime import RagGraphRuntime


class RagQueryEngineAdapter:
    """将 ``RagQueryEngine`` 适配为 ``RagGraphRuntime``。

    通过组合 engine 及其内部组件实现协议要求的全部方法，
    不修改 engine 本身的代码。
    """

    def __init__(self, engine: RagQueryEngine) -> None:
        self._engine = engine

    # ── 暴露 engine 内部状态 ───────────────────────────────

    @property
    def state(self) -> Any:
        return self._engine.state

    @property
    def settings(self) -> Any:
        return self._engine.settings

    @property
    def retrieval_service(self) -> Any:
        return self._engine.retrieval_service

    @property
    def llm(self) -> Any:
        return self._engine.llm

    @property
    def knowledge_capability(self) -> Any:
        return self._engine.knowledge_capability

    # ── 护栏与脱敏（直接委托 engine 的同名方法） ────────────

    def check_guardrails(self, question: str, payload: Any, trace_context: str) -> dict[str, Any]:
        return self._engine.check_guardrails(question, payload, trace_context)

    def empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        return self._engine.empty_redaction_state(enabled)

    def sanitize_text(
        self, text: str, payload: Any, *, target: str, trace_context: str,
    ) -> tuple[str, dict[str, Any]]:
        return self._engine.sanitize_text(text, payload, target=target, trace_context=trace_context)

    def sanitize_citations(
        self, citations: list[CitationItem], payload: Any, trace_context: str,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        return self._engine.sanitize_citations(citations, payload, trace_context)

    def question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        return self._engine.question_for_storage(question, guardrail_state)

    def guardrail_block_message(self) -> str:
        return self._engine.guardrail_block_message()

    def build_blocked_query_response(self, payload: Any, guardrail_state: dict[str, Any]) -> QueryResponse:
        return self._engine._build_blocked_query_response(payload, guardrail_state)

    def public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._engine.public_guardrail_state(guardrail_state, citation_redaction, answer_redaction)

    # ── 语义缓存（直接委托 engine 的同名方法） ─────────────

    def lookup_semantic_cache(
        self, payload: Any, question: str, cache_mode: str,
    ) -> tuple[QueryResponse | None, dict[str, Any]]:
        return self._engine.lookup_semantic_cache(payload, question, cache_mode)

    def store_semantic_cache(
        self, payload: Any, *, question: str, cache_mode: str,
        response: QueryResponse, answer_mode: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        self._engine.store_semantic_cache(
            payload, question=question, cache_mode=cache_mode,
            response=response, answer_mode=answer_mode,
        )

    # ── 查询改写与检索增强（直接委托 engine 的同名方法） ────

    def graph_trace_flags(self, payload: Any) -> dict[str, Any]:
        return self._engine.graph_trace_flags(payload)

    def prepare_retrieval_question(self, question: str, use_query_rewrite: bool, trace_context: str) -> str:
        return self._engine.prepare_retrieval_question(question, use_query_rewrite, trace_context)

    def resolve_rewrite_info(
        self, question: str, use_query_rewrite: bool, trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        return self._engine.resolve_rewrite_info(question, use_query_rewrite, trace_context)

    def maybe_apply_multi_rewrite(
        self, payload: Any, retrieval_question: str, answer_question: str, trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        return self._engine.maybe_apply_multi_rewrite(payload, retrieval_question, answer_question, trace_context)

    def maybe_apply_multi_query(
        self, payload: Any, retrieval_question: str, answer_question: str, trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        return self._engine.maybe_apply_multi_query(payload, retrieval_question, answer_question, trace_context)

    def maybe_apply_hyde(
        self, payload: Any, retrieval_question: str, answer_question: str, trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        return self._engine.maybe_apply_hyde(payload, retrieval_question, answer_question, trace_context)

    def empty_corrective_info(self) -> dict[str, Any]:
        return self._engine.empty_corrective_info()

    # ── 证据准备与回答生成（组合 engine 内部组件） ──────────

    def prepare_answer_context(
        self, question: str, citations: list[CitationItem], payload: Any,
    ) -> tuple[list[str], dict[str, Any]]:
        return self._engine.prepare_answer_context(question, citations, payload)

    def use_context_compression(self, payload: Any) -> bool:
        return self._engine.use_context_compression(payload)

    def use_pii_redaction(self, payload: Any) -> bool:
        return self._engine.use_pii_redaction(payload)

    def build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        return self._engine.build_chat_retrieval_question(session_id, current_question)

    def retrieve_citations(
        self, payload: Any, retrieval_questions: list[str], answer_question: str,
    ) -> list[CitationItem]:
        """通过 engine 的 retrieval_service 执行检索。"""
        collection_name = getattr(payload, 'collection_name', 'default')
        top_k = getattr(payload, 'top_k', 5)
        use_hybrid = getattr(payload, 'use_hybrid_retrieval', False)
        use_graph = getattr(payload, 'use_graph_rag', False)
        graph_hops = getattr(payload, 'graph_max_hops', 1)
        use_rerank = getattr(payload, 'use_rerank', True)

        citations: list[CitationItem] = []
        seen_chunks: set[str] = set()
        for query in retrieval_questions:
            batch = self._engine.retrieval_service.retrieve(
                collection_name, query, top_k,
                use_hybrid_retrieval=use_hybrid,
                use_rerank=use_rerank,
                use_graph_rag=use_graph,
                graph_max_hops=graph_hops,
            )
            for c in batch:
                if c.chunk_id not in seen_chunks:
                    citations.append(c)
                    seen_chunks.add(c.chunk_id)

        return citations[:top_k]

    def stream_citation_snapshot(self, citations: list[CitationItem], limit: int = 3) -> list[dict[str, Any]]:
        """生成用于流式输出的引用快照摘要。"""
        return [
            {
                'chunk_id': c.chunk_id,
                'source': c.source or c.file_path or '',
                'score': c.score,
            }
            for c in citations[:limit]
        ]

    def build_qa_prompt(self, question: str, contexts: list[str], *, use_guardrails: bool = True) -> str:
        """通过 engine 的 answer_service 构建问答 prompt。"""
        return self._engine.answer_service.build_qa_prompt(question, contexts, use_guardrails=use_guardrails)

    def generate_answer(self, prompt: str, *, stream: bool = False) -> str | list[str]:
        """执行 LLM 问答生成。

        Args:
            prompt: 问答 prompt。
            stream: 是否流式输出。

        Returns:
            - 非流式：完整答案文本
            - 流式：delta 文本块列表
        """
        from collections.abc import Generator

        if self._engine.llm is None:
            return 'LLM 不可用，无法生成回答。'

        if stream:
            deltas: list[str] = []
            raw_stream: Generator = self._engine.llm.stream_complete(prompt)
            emitted = ''
            for item in raw_stream:
                text = str(item.delta if hasattr(item, 'delta') else item)
                if not text:
                    continue
                delta = text[len(emitted):] if text.startswith(emitted) else text
                if delta:
                    deltas.append(delta)
                    emitted = text
            return deltas if deltas else ['']
        else:
            response = self._engine.llm.complete(prompt)
            return str(response).strip()

    def maybe_apply_corrective_rag(
        self,
        payload: Any,
        question: str,
        answer: str,
        answer_mode: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """应用 Corrective RAG 自检与改写。

        通过 engine 的 answer_service 执行 corrective check 和 rewrite。
        """
        use_corrective = getattr(payload, 'use_corrective_rag', False)

        if not use_corrective or not citations:
            return answer, answer_mode, {
                'enabled': bool(use_corrective),
                'applied': False,
                'decision': 'accept',
                'reason': 'disabled_or_no_citations',
            }

        contexts = [c.text for c in citations if c.text]
        if not contexts:
            return answer, answer_mode, {
                'enabled': True, 'applied': False,
                'decision': 'accept', 'reason': 'no_context_text',
            }

        try:
            corrected_answer, corrective_info = (
                self._engine.answer_service.generate_corrective_answer(
                    question=question,
                    contexts=contexts,
                    max_retries=1,
                )
            )
            # 将 answer_service 的 corrective_info 映射为节点期望的格式
            decision = 'accept'
            if corrective_info.get('applied'):
                decision = 'rewrite'
            elif not corrective_info.get('supported'):
                decision = 'retry'

            mapped_info = {
                'enabled': corrective_info.get('enabled', True),
                'applied': corrective_info.get('applied', False),
                'decision': decision,
                'reason': corrective_info.get('reason', ''),
                'supported': corrective_info.get('supported', True),
                'check_mode': corrective_info.get('check_mode', 'disabled'),
            }
            if corrective_info.get('applied'):
                return corrected_answer, 'corrective_rag_applied', mapped_info
            return answer, answer_mode, mapped_info
        except Exception as exc:
            return answer, answer_mode, {
                'enabled': True, 'applied': False,
                'decision': 'accept',
                'reason': f'corrective_check_error: {exc}',
            }

    # ── 会话与会话持久化 ───────────────────────────────────

    def save_session(self, session_id: str) -> None:
        """持久化当前会话。"""
        self._engine._save_session(session_id)

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        """加载指定会话。"""
        if self._engine.persistence is None or not session_id:
            return None
        return self._engine.persistence.get_session(session_id)
