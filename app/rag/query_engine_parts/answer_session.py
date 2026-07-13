"""`query_engine.py` 的回答增强与会话持久化子模块。

集中维护多轮改写、HyDE、纠错式 RAG、上下文压缩和 session 摘要压缩，
避免主文件后半段被大量私有辅助方法淹没。
"""

from __future__ import annotations

import re
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

from llama_index.core.llms import ChatMessage, MessageRole

from app.models.query import CitationItem, QueryRequest
from app.rag.query_engine_parts._typing import QueryEngineTypingMixin
from app.types import MetadataFilters as MetadataFiltersMap, SSEEvent, SessionMessageRecord, SessionRecord


class QueryEngineAnswerSessionMixin(QueryEngineTypingMixin):
    """封装查询引擎中的回答增强、会话组装与摘要相关辅助逻辑。"""

    def _maybe_apply_multi_rewrite(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict | None]:
        """委托前处理服务执行多重改写。"""
        return self.preprocess_service.maybe_apply_multi_rewrite(
            payload,
            retrieval_question,
            answer_question,
            trace_context,
        )

    def _maybe_apply_hyde(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[str, dict | None]:
        """委托前处理服务执行 HyDE 改写。"""
        return self.preprocess_service.maybe_apply_hyde(
            payload,
            retrieval_question,
            answer_question,
            trace_context,
        )

    def _stream_answer(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
        payload: QueryRequest,
        trace_context: str,
    ) -> Generator[SSEEvent, None, tuple[str, str, dict[str, Any]]]:
        """按增量方式输出答案文本，并返回最终答案与生成模式。"""
        result = yield from self.answer_service.stream_answer(
            question,
            prompt,
            citations,
            collection_name,
            payload,
            trace_context,
        )
        return result

    def _maybe_apply_corrective_rag(
        self,
        payload: QueryRequest,
        question: str,
        answer: str,
        answer_mode: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """对答案执行一次证据校验，并在高风险时回退到保守答案。"""
        return self.answer_service.maybe_apply_corrective_rag(
            payload,
            question,
            answer,
            answer_mode,
            citations,
            collection_name,
        )

    def _heuristic_answer_support(self, answer: str, citations: list[CitationItem]) -> dict[str, Any]:
        """使用轻量规则估计答案是否被证据支持。"""
        return self.answer_service.heuristic_answer_support(answer, citations)

    def _llm_corrective_check(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        collection_name: str,
    ) -> dict[str, Any] | None:
        """使用 LLM 做一次支持度校验。"""
        return self.answer_service.llm_corrective_check(question, answer, contexts, collection_name)

    def _llm_corrective_rewrite(
        self,
        question: str,
        contexts: list[str],
        collection_name: str,
    ) -> str | None:
        """在自检失败后，让 LLM 基于证据保守重写答案。"""
        return self.answer_service.llm_corrective_rewrite(question, contexts, collection_name)

    def _empty_corrective_info(self) -> dict[str, Any]:
        """返回统一的 Corrective RAG 状态结构。"""
        return self.answer_service.empty_corrective_info()

    def _build_answer(self, question: str, citations: list[CitationItem]) -> str:
        """在无可用 LLM 时，根据引用片段拼装兜底答案。"""
        return self.answer_service.build_answer(question, citations)

    def _prepare_answer_context(
        self,
        question: str,
        citations: list[CitationItem],
        payload: QueryRequest,
    ) -> tuple[list[str], dict]:
        """根据配置决定是否压缩检索上下文，并返回压缩指标。"""
        return self.answer_service.prepare_answer_context(question, citations, payload)

    def _format_citation_context(self, citation: CitationItem) -> str:
        """把图谱路径和证据文本拼成更可解释的上下文。"""
        return self.answer_service.format_citation_context(citation)

    def _use_context_compression(self, payload: QueryRequest) -> bool:
        """返回本次请求是否启用上下文压缩。"""
        return self.answer_service.use_context_compression(payload)

    def _compress_citation_contexts(
        self,
        question: str,
        citations: list[CitationItem],
        max_sentences: int,
        max_chars: int,
    ) -> list[str]:
        """从候选引用中抽取最相关的句子，并控制总长度预算。"""
        return self.answer_service.compress_citation_contexts(question, citations, max_sentences, max_chars)

    def _build_chat_question(self, session_id: str) -> str:
        """把会话摘要和最近问题拼接为检索查询。"""
        session = self.state.sessions.get(session_id) or self._empty_session()
        summary = session.get('summary')
        recent_questions = [
            message['content']
            for message in session.get('messages', [])
            if message.get('role') == 'user'
        ][-3:]
        parts: list[str] = []
        if summary:
            parts.append(f'会话摘要：{summary}')
        parts.extend(recent_questions)
        # 聊天检索问题只拼最近几轮用户问题，避免把整段会话历史直接塞进检索层。
        return '\n'.join(item for item in parts if item).strip()

    def _build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        """为 chat workflow 构造包含历史与当前问题的检索查询。"""

        session = self.state.sessions.get(session_id) or self._empty_session()
        summary = session.get('summary')
        recent_questions = [
            message['content']
            for message in session.get('messages', [])
            if message.get('role') == 'user'
        ][-3:]
        parts: list[str] = []
        if summary:
            parts.append(f'会话摘要：{summary}')
        parts.extend(recent_questions)
        parts.append(current_question)
        return '\n'.join(item for item in parts if item).strip()

    def _to_llamaindex_history(
        self,
        messages: list[SessionMessageRecord],
        summary: str | None = None,
    ) -> list[ChatMessage]:
        """把内部消息结构转换为 LlamaIndex 历史消息格式。"""
        history: list[ChatMessage] = []
        if summary:
            history.append(
                ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f'以下是历史会话摘要，请在回答时保持上下文一致：{summary}',
                )
            )
        for message in messages:
            content = message.get('content', '')
            if not content:
                continue
            history.append(
                ChatMessage(
                    role=self._to_message_role(message.get('role', 'user')),
                    content=content,
                )
            )
        return history

    def _to_message_role(self, role: str) -> MessageRole:
        """把字符串角色映射为 LlamaIndex 枚举。"""
        normalized = role.lower()
        if normalized == 'assistant':
            return MessageRole.ASSISTANT
        if normalized == 'system':
            return MessageRole.SYSTEM
        return MessageRole.USER

    def _citations_from_source_nodes(
        self,
        source_nodes: list[Any],
        filters: MetadataFiltersMap | None,
    ) -> list[CitationItem]:
        """从 LlamaIndex source nodes 中提取标准引用对象。"""
        citations: list[CitationItem] = []
        seen: set[str] = set()
        for node_with_score in source_nodes:
            metadata = node_with_score.node.metadata or {}
            if not self.retrieval_service._matches_filters(metadata, filters):
                continue
            chunk_id = node_with_score.node.node_id
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            citations.append(
                CitationItem(
                    chunk_id=chunk_id,
                    source=self.retrieval_service._format_citation_source(metadata),
                    file_path=metadata.get('file_path'),
                    page=metadata.get('page'),
                    score=round(node_with_score.score or 0.0, 4),
                    text=node_with_score.node.get_content(),
                    source_archive=self.retrieval_service._metadata_text(metadata, 'source_archive'),
                    archive_member_path=self.retrieval_service._metadata_text(metadata, 'archive_member_path'),
                    archive_member_display_path=self.retrieval_service._metadata_text(metadata, 'archive_member_display_path'),
                )
            )
        return citations

    def _message(self, role: str, content: str) -> SessionMessageRecord:
        """构造内部会话消息记录。"""
        return {
            'role': role,
            'content': content,
            'created_at': datetime.now(timezone.utc),
        }

    def _get_or_create_session(self, session_id: str) -> SessionRecord:
        """获取已有会话；若不存在则创建并持久化。"""
        existing = self.state.sessions.get(session_id)
        if existing is not None:
            return existing
        session = self._empty_session()
        self.state.sessions[session_id] = session
        self._save_session(session_id)
        return session

    def _empty_session(self) -> SessionRecord:
        """创建新的空会话结构。"""
        return {
            'messages': [],
            'summary': None,
            'summary_updated_at': None,
            'compressed_message_count': 0,
            'updated_at': datetime.now(timezone.utc),
        }

    def _save_session(self, session_id: str) -> None:
        """将会话状态同步到持久化层。"""
        if self.persistence is None:
            return
        session = self.state.sessions.get(session_id)
        if session is None:
            self.persistence.delete_session(session_id)
            return
        self.persistence.upsert_session(session_id, session)

    def _auto_summarize_session(self, session_id: str) -> None:
        """当消息数量达到阈值时自动压缩会话。"""
        session = self.state.sessions.get(session_id)
        if session is None:
            return
        if len(session.get('messages', [])) < self.AUTO_SUMMARY_TRIGGER:
            return
        self._compress_session(session_id)

    def _compress_session(self, session_id: str) -> str:
        """保留最近消息，并把较早历史压缩成摘要。"""
        session = self.state.sessions[session_id]
        messages = session.get('messages', [])
        if not messages:
            summary = session.get('summary') or '暂无会话内容。'
            session['summary'] = summary
            session['summary_updated_at'] = datetime.now(timezone.utc)
            session['updated_at'] = datetime.now(timezone.utc)
            self._save_session(session_id)
            return summary

        # 消息较少时不做截断，只更新摘要字段。
        if len(messages) <= self.SUMMARY_KEEP_RECENT:
            retained = messages
            compressed = messages
            increment = 0
        else:
            retained = messages[-self.SUMMARY_KEEP_RECENT:]
            compressed = messages[:-self.SUMMARY_KEEP_RECENT]
            increment = len(compressed)
        summary = self._build_session_summary(session.get('summary'), compressed)
        session['summary'] = summary
        session['summary_updated_at'] = datetime.now(timezone.utc)
        session['compressed_message_count'] = session.get('compressed_message_count', 0) + increment
        session['messages'] = retained
        session['updated_at'] = datetime.now(timezone.utc)
        self._save_session(session_id)
        self.trace.record(
            'session_summarized',
            {
                'session_id': session_id,
                'compressed_count': increment,
                'retained_count': len(retained),
            },
        )
        return summary

    def _build_session_summary(self, existing_summary: str | None, messages: list[SessionMessageRecord]) -> str:
        """优先使用 LLM 生成会话摘要，失败时回退到规则压缩。"""
        units: list[str] = []
        if existing_summary:
            units.append(existing_summary)

        for message in messages:
            role = '用户' if message.get('role') == 'user' else '助手'
            content = re.sub(r'\s+', ' ', message.get('content', '')).strip()
            if not content:
                continue
            units.append(f'{role}：{content[:120]}')

        if not units:
            return existing_summary or '暂无可压缩的历史会话。'

        if self.llm is not None:
            prompt = (
                '请将以下会话整理为不超过 180 字的中文摘要，保留用户目标、约束和已确认结论。\n\n'
                + '\n'.join(units[-12:])
            )
            try:
                response = self.llm.complete(prompt)
                text = str(response).strip()
                if text:
                    return text
            except Exception as exc:
                self.trace.record('session_summary_fallback', {'reason': str(exc)})

        return '；'.join(units[-6:])[:180]

    def _split_sentences(self, text: str) -> list[str]:
        """按中英文句号和换行拆分文本。"""
        return self.answer_service.split_sentences(text)

    def _tokenize(self, text: str) -> list[str]:
        """把文本切成适合粗粒度匹配的 token。"""
        return self.answer_service.tokenize(text)

    def _chunk_text_for_stream(self, text: str, chunk_size: int = 24) -> list[str]:
        """把完整答案切成固定大小片段，供流式输出。"""
        return self.answer_service.chunk_text_for_stream(text, chunk_size)

    def _stream_citation_snapshot(self, citations: list[CitationItem], limit: int = 3) -> list[dict[str, Any]]:
        """生成用于流式预览的精简引用快照。"""
        return [
            {
                'chunk_id': citation.chunk_id,
                'source': citation.source,
                'source_archive': citation.source_archive,
                'archive_member_path': citation.archive_member_path,
                'archive_member_display_path': citation.archive_member_display_path,
                'score': citation.score,
                'text_preview': citation.text[:120],
            }
            for citation in citations[:limit]
        ]

    def _extract_stream_delta(self, item: Any, emitted: str) -> str:
        """从不同流式返回格式中提取新增文本片段。"""
        return self.answer_service.extract_stream_delta(item, emitted)
