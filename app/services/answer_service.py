"""回答生成服务模块。

负责承接 Prompt 构造、答案生成、流式输出、上下文压缩以及 Corrective RAG 自检与
保守重写逻辑。该服务位于查询主链路中游，向下依赖 LLM 和预处理服务，向上为查询引擎
提供统一的回答生成能力。
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from typing import Any

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.observability import TraceRecorder
from app.rag.prompting import build_corrective_check_prompt, build_corrective_rewrite_prompt, build_qa_prompt
from app.services.query_preprocess_service import QueryPreprocessService
from app.services.system_settings import RuntimeConfigReader
from app.types import SSEEvent


class AnswerService:
    """回答生成服务主类。

    这一层主要负责把检索结果变成真正给用户的答案，同时兜住流式输出、上下文压缩和
    Corrective RAG 纠偏。
    """

    def __init__(
        self,
        settings: Settings,
        trace: TraceRecorder,
        preprocess_service: QueryPreprocessService,
        llm: Any | None = None,
        runtime_config: RuntimeConfigReader | None = None,
    ) -> None:
        """初始化回答生成服务。

        Args:
            settings: 全局配置对象，决定上下文压缩等行为。
            trace: 链路追踪记录器，用于记录回退和纠偏事件。
            preprocess_service: 查询预处理服务，负责脱敏等公共能力。
            llm: 可选的大模型实例；为空时退化为本地兜底回答。
            runtime_config: 运行时配置，优先于 settings。
        """
        self.settings = settings
        self.trace = trace
        self.preprocess_service = preprocess_service
        self.llm = llm
        self._runtime_config = runtime_config

    def build_qa_prompt(self, question: str, contexts: list[str], use_guardrails: bool = False) -> str:
        """构造回答 Prompt。

        Args:
            question: 用户问题。
            contexts: 检索得到的上下文列表。
            use_guardrails: 是否在 Prompt 中附加安全要求。

        Returns:
            可直接交给 LLM 的问答提示词。
        """

        return build_qa_prompt(question, contexts, use_guardrails=use_guardrails)

    def generate_answer_with_mode(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str]:
        """生成答案并返回对应模式。

        Args:
            question: 用户问题。
            prompt: 已构造好的问答 Prompt。
            citations: 当前检索命中的引用列表。
            collection_name: 当前知识库名称，用于追踪记录。

        Returns:
            第一项是答案文本，第二项是答案生成模式。
        """

        if self.llm is None:
            return self.build_answer(question, citations), 'local_fallback'

        try:
            response = self.llm.complete(prompt)
            answer = str(response).strip()
            if answer:
                return answer, 'llm_complete'
        except Exception as exc:
            self.trace.record('llm_complete_fallback', {'reason': str(exc), 'collection_name': collection_name})

        return self.build_answer(question, citations), 'local_fallback'

    def stream_answer(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
        payload: QueryRequest,
        trace_context: str,
    ) -> Generator[SSEEvent, None, tuple[str, str, dict[str, Any]]]:
        """按增量方式输出答案文本，并返回最终答案与生成模式。

        Args:
            question: 用户问题。
            prompt: 已构造好的问答 Prompt。
            citations: 当前检索命中的引用列表。
            collection_name: 当前知识库名称，用于追踪记录。
            payload: 当前请求对象，用于读取脱敏等配置。
            trace_context: 当前链路上下文名称。

        Yields:
            SSE 增量事件。

        Returns:
            生成器结束时返回最终答案、答案模式和脱敏信息。
        """

        answer = ''
        if self.llm is None:
            answer = self.build_answer(question, citations)
            answer, answer_redaction = self.preprocess_service.sanitize_text(
                answer,
                payload,
                target='answer',
                trace_context=trace_context,
            )
            for chunk in self.chunk_text_for_stream(answer):
                yield {'event': 'delta', 'data': {'delta': chunk}}
            return answer, 'local_fallback', answer_redaction

        # 只有不需要回答脱敏时，才能直接透传 LLM 流输出；否则得先拿到完整答案再统一处理。
        if hasattr(self.llm, 'stream_complete') and not self.preprocess_service.use_pii_redaction(payload):
            try:
                stream = self.llm.stream_complete(prompt)
                emitted = ''
                for item in stream:
                    delta = self.extract_stream_delta(item, emitted)
                    if not delta:
                        continue
                    emitted += delta
                    yield {'event': 'delta', 'data': {'delta': delta}}
                answer = emitted.strip()
                if answer:
                    return answer, 'llm_stream', self.preprocess_service.empty_redaction_state(False)
            except Exception as exc:
                self.trace.record(
                    'llm_stream_fallback',
                    {'reason': str(exc), 'collection_name': collection_name},
                )

        try:
            completed = self.llm.complete(prompt)
            answer = str(completed).strip()
        except Exception as exc:
            self.trace.record(
                'llamaindex_query_fallback',
                {'reason': str(exc), 'collection_name': collection_name},
            )
            answer = self.build_answer(question, citations)
            answer_mode = 'local_fallback'
        else:
            answer_mode = 'llm_complete'

        answer, answer_redaction = self.preprocess_service.sanitize_text(
            answer,
            payload,
            target='answer',
            trace_context=trace_context,
        )
        for chunk in self.chunk_text_for_stream(answer):
            yield {'event': 'delta', 'data': {'delta': chunk}}
        return answer, answer_mode, answer_redaction

    def maybe_apply_corrective_rag(
        self,
        payload: QueryRequest,
        question: str,
        answer: str,
        answer_mode: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """对答案做一次证据校验，并在风险高时回退到更保守的答案。

        Args:
            payload: 当前请求对象。
            question: 用户问题。
            answer: 当前候选答案。
            answer_mode: 当前答案生成模式。
            citations: 当前检索命中的引用列表。
            collection_name: 当前知识库名称，用于追踪记录。

        Returns:
            第一项是最终答案，第二项是最终答案模式，第三项是 Corrective RAG 状态信息。
        """

        info = self.empty_corrective_info()
        info['enabled'] = payload.use_corrective_rag and bool(citations)
        if not payload.use_corrective_rag or not citations:
            return answer, answer_mode, info

        # 自检只拿前几条代表性证据，主要是控制 token 成本，也避免判断被长上下文带偏。
        contexts = [item.text for item in citations[: max(1, min(len(citations), 4))]]
        heuristic = self.heuristic_answer_support(answer, citations)
        info.update(
            {
                'supported': heuristic['supported'],
                'risk': heuristic['risk'],
                'confidence': heuristic['confidence'],
                'reason': heuristic['reason'],
                'rewrite_needed': not heuristic['supported'],
                'check_mode': 'heuristic',
            }
        )

        if self.llm is not None:
            llm_check = self.llm_corrective_check(question, answer, contexts, collection_name)
            if llm_check is not None:
                info.update(llm_check)
                info['check_mode'] = 'llm'

        if info.get('supported'):
            self.trace.record(
                'corrective_rag_checked',
                {'collection_name': collection_name, 'result': 'accepted', **info},
            )
            return answer, answer_mode, info

        corrected = self.build_answer(question, citations)
        corrected_mode = 'corrective_local_fallback'
        if self.llm is not None:
            rewritten = self.llm_corrective_rewrite(question, contexts, collection_name)
            if rewritten:
                corrected = rewritten
                corrected_mode = 'corrective_llm_rewrite'

        info['applied'] = True
        info['final_mode'] = corrected_mode
        self.trace.record(
            'corrective_rag_checked',
            {'collection_name': collection_name, 'result': 'corrected', **info},
        )
        return corrected, corrected_mode, info

    def empty_corrective_info(self) -> dict[str, Any]:
        """返回统一的 Corrective RAG 状态结构。

        Returns:
            便于主链路直接透传和记录的默认状态字典。
        """

        return {
            'enabled': False,
            'supported': True,
            'risk': 'low',
            'confidence': 1.0,
            'reason': 'disabled',
            'rewrite_needed': False,
            'applied': False,
            'check_mode': 'disabled',
            'final_mode': None,
        }

    def prepare_answer_context(
        self,
        question: str,
        citations: list[CitationItem],
        payload: QueryRequest,
    ) -> tuple[list[str], dict[str, Any]]:
        """根据配置决定要不要压缩检索上下文，并返回压缩指标。

        Args:
            question: 用户问题。
            citations: 检索命中的引用列表。
            payload: 当前请求对象。

        Returns:
            第一项是用于回答的上下文列表，第二项是上下文压缩指标。
        """

        original_contexts = [self.format_citation_context(item) for item in citations]
        original_chars = sum(len(text) for text in original_contexts)
        original_sentences = sum(len(self.split_sentences(text)) or 1 for text in original_contexts)
        enabled = self.use_context_compression(payload)
        metrics = {
            'enabled': enabled and bool(citations),
            'original_chunk_count': len(citations),
            'compressed_chunk_count': len(citations),
            'original_sentence_count': original_sentences,
            'compressed_sentence_count': original_sentences,
            'original_char_count': original_chars,
            'compressed_char_count': original_chars,
            'strategy': 'disabled',
        }
        if not citations:
            return original_contexts, metrics

        if not enabled:
            return original_contexts, metrics

        max_chunks = max(1, self.settings.context_compression_max_chunks)
        max_sentences = max(1, self.settings.context_compression_max_sentences)
        max_chars = max(80, self.settings.context_compression_max_chars)
        compressed_contexts = self.compress_citation_contexts(
            question=question,
            citations=citations[:max_chunks],
            max_sentences=max_sentences,
            max_chars=max_chars,
        )
        compressed_chars = sum(len(text) for text in compressed_contexts)
        compressed_sentences = sum(len(self.split_sentences(text)) or 1 for text in compressed_contexts)
        metrics.update(
            {
                'compressed_chunk_count': len(compressed_contexts),
                'compressed_sentence_count': compressed_sentences,
                'compressed_char_count': compressed_chars,
                'strategy': 'sentence_extract',
            }
        )
        self.trace.record('context_compressed', metrics)
        return compressed_contexts, metrics

    def use_context_compression(self, payload: QueryRequest) -> bool:
        """返回本次请求要不要启用上下文压缩。

        Args:
            payload: 当前请求对象。

        Returns:
            当前请求是否启用上下文压缩。
        """

        if payload.use_context_compression is None:
            if self._runtime_config is not None:
                return self._runtime_config.enable_context_compression
            return self.settings.enable_context_compression
        return payload.use_context_compression

    def format_citation_context(self, citation: CitationItem) -> str:
        """把图谱路径和证据文本拼成更容易给模型理解的上下文。

        Args:
            citation: 单条引用记录。

        Returns:
            适合放入 Prompt 的上下文字符串。
        """

        if not citation.graph_path:
            return citation.text
        path_line = f'图谱路径：{citation.graph_path}'
        if citation.text.strip() and citation.text.strip() != citation.graph_path.strip():
            return f'{path_line}\n证据片段：{citation.text}'
        return path_line

    def compress_citation_contexts(
        self,
        question: str,
        citations: list[CitationItem],
        max_sentences: int,
        max_chars: int,
    ) -> list[str]:
        """从候选引用里抽取最相关的句子，并控制总长度预算。

        Args:
            question: 用户问题。
            citations: 候选引用列表。
            max_sentences: 最多保留的句子数量。
            max_chars: 总字符预算上限。

        Returns:
            压缩后的上下文列表。
        """

        question_tokens = set(self.tokenize(question))
        ranked_sentences: list[tuple[float, str, str]] = []
        seen_sentences: set[str] = set()

        for citation_index, citation in enumerate(citations):
            context_text = self.format_citation_context(citation)
            sentences = self.split_sentences(context_text)
            if not sentences:
                sentences = [context_text.strip()]
            for sentence_index, sentence in enumerate(sentences):
                normalized_sentence = re.sub(r'\s+', ' ', sentence).strip()
                if not normalized_sentence or normalized_sentence in seen_sentences:
                    continue
                seen_sentences.add(normalized_sentence)
                sentence_tokens = set(self.tokenize(normalized_sentence))
                overlap = len(question_tokens & sentence_tokens)
                coverage = overlap / max(len(question_tokens), 1)
                position_bonus = max(0.0, 0.12 - sentence_index * 0.02)
                citation_bonus = (citation.score or 0.0) * 0.1
                fallback_bonus = 0.05 if citation_index == 0 and sentence_index == 0 else 0.0
                score = coverage + position_bonus + citation_bonus + fallback_bonus
                ranked_sentences.append((score, citation.source, normalized_sentence))

        ranked_sentences.sort(key=lambda item: item[0], reverse=True)
        selected_by_source: dict[str, list[str]] = {}
        used_chars = 0

        for _, source, sentence in ranked_sentences:
            if len(selected_by_source) >= len(citations) and sum(len(items) for items in selected_by_source.values()) >= max_sentences:
                break
            candidate_line = f'[{source}] {sentence}'
            extra_chars = len(candidate_line) + 2
            if selected_by_source and used_chars + extra_chars > max_chars:
                continue
            selected_by_source.setdefault(source, []).append(sentence)
            used_chars += extra_chars
            if sum(len(items) for items in selected_by_source.values()) >= max_sentences:
                break

        if not selected_by_source:
            # 句子级排序一个都没留下时，至少保底塞回几段裁剪后的原文，避免回答完全失去上下文。
            fallback_contexts: list[str] = []
            used_chars = 0
            for citation in citations:
                text = self.format_citation_context(citation).strip()
                if not text:
                    continue
                remaining = max_chars - used_chars
                if remaining <= 0:
                    break
                clipped = text[:remaining].strip()
                if not clipped:
                    continue
                fallback_contexts.append(f'[{citation.source}] {clipped}')
                used_chars += len(clipped) + len(citation.source) + 4
            return fallback_contexts or [
                f'[{citations[0].source}] {self.format_citation_context(citations[0])[:max_chars].strip()}'
            ]

        contexts: list[str] = []
        for source, sentences in selected_by_source.items():
            contexts.append(f'[{source}] {" ".join(sentences)}')
        return contexts

    def build_answer(self, question: str, citations: list[CitationItem]) -> str:
        """在没有可用 LLM 时，根据引用片段拼一个兜底答案。

        Args:
            question: 用户问题。
            citations: 候选引用列表。

        Returns:
            基于证据片段拼装的简易回答。
        """

        sentences: list[tuple[float, str]] = []
        question_tokens = set(self.tokenize(question))

        for citation in citations:
            for sentence in self.split_sentences(citation.text):
                sentence_tokens = set(self.tokenize(sentence))
                overlap = len(question_tokens & sentence_tokens)
                score = overlap + (citation.score or 0.0)
                if overlap:
                    sentences.append((score, sentence.strip()))

        if not sentences:
            top = citations[0]
            return f'根据 {top.source} 的相关片段，{top.text[:220].strip()}'

        selected: list[str] = []
        seen: set[str] = set()
        for _, sentence in sorted(sentences, key=lambda item: item[0], reverse=True):
            if sentence in seen:
                continue
            selected.append(sentence)
            seen.add(sentence)
            if len(selected) >= 3:
                break

        body = ' '.join(selected)
        sources = '；'.join(dict.fromkeys(citation.source for citation in citations[:3]))
        return f'{body}\n\n参考来源：{sources}'

    def heuristic_answer_support(self, answer: str, citations: list[CitationItem]) -> dict[str, Any]:
        """用轻量规则粗估答案有没有被证据支持。

        Args:
            answer: 待校验答案。
            citations: 当前检索命中的引用列表。

        Returns:
            包含支持度、风险和置信度的启发式结果字典。
        """

        evidence_text = '\n'.join(item.text for item in citations).lower()
        answer_sentences = [sentence.strip() for sentence in self.split_sentences(answer) if sentence.strip()]
        if not answer_sentences:
            return {'supported': False, 'risk': 'high', 'confidence': 0.0, 'reason': 'empty_answer'}

        unsupported = 0
        supported = 0
        for sentence in answer_sentences:
            normalized = sentence.lower()
            if normalized in evidence_text:
                supported += 1
                continue
            sentence_tokens = set(self.tokenize(sentence))
            if not sentence_tokens:
                continue
            overlaps = []
            for citation in citations:
                citation_tokens = set(self.tokenize(citation.text))
                overlaps.append(len(sentence_tokens & citation_tokens) / max(len(sentence_tokens), 1))
            best_overlap = max(overlaps) if overlaps else 0.0
            if best_overlap >= 0.45:
                supported += 1
            else:
                unsupported += 1

        confidence = supported / max(len(answer_sentences), 1)
        risk = 'low' if unsupported == 0 else 'medium' if unsupported == 1 else 'high'
        return {
            'supported': unsupported == 0,
            'risk': risk,
            'confidence': round(confidence, 4),
            'reason': 'heuristic_overlap',
        }

    def llm_corrective_check(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        collection_name: str,
    ) -> dict[str, Any] | None:
        """用 LLM 再做一次支持度校验。

        Args:
            question: 用户问题。
            answer: 当前候选答案。
            contexts: 支撑答案的证据上下文。
            collection_name: 当前知识库名称，用于追踪记录。

        Returns:
            校验成功时返回结构化结果，否则返回 `None`。
        """

        if self.llm is None:
            return None
        prompt = build_corrective_check_prompt(question, answer, contexts)
        try:
            response = self.llm.complete(prompt)
            raw = str(response).strip()
            payload = json.loads(raw)
        except Exception as exc:
            self.trace.record('corrective_rag_llm_check_failed', {'collection_name': collection_name, 'reason': str(exc)})
            return None
        if not isinstance(payload, dict):
            return None
        supported = bool(payload.get('supported'))
        return {
            'supported': supported,
            'risk': str(payload.get('risk') or ('low' if supported else 'high')),
            'confidence': float(payload.get('confidence') or 0.0),
            'reason': str(payload.get('reason') or 'llm_check'),
            'rewrite_needed': bool(payload.get('rewrite_needed', not supported)),
        }

    def llm_corrective_rewrite(
        self,
        question: str,
        contexts: list[str],
        collection_name: str,
    ) -> str | None:
        """在自检失败后，让 LLM 基于证据把答案重写得更保守一点。

        Args:
            question: 用户问题。
            contexts: 支撑答案的证据上下文。
            collection_name: 当前知识库名称，用于追踪记录。

        Returns:
            重写成功时返回新答案，否则返回 `None`。
        """

        if self.llm is None:
            return None
        prompt = build_corrective_rewrite_prompt(question, contexts)
        try:
            response = self.llm.complete(prompt)
            answer = str(response).strip()
            return answer or None
        except Exception as exc:
            self.trace.record(
                'corrective_rag_llm_rewrite_failed',
                {'collection_name': collection_name, 'reason': str(exc)},
            )
            return None

    def split_sentences(self, text: str) -> list[str]:
        """按中英文句号和换行拆分文本。"""

        return [item.strip() for item in re.split(r'(?<=[。！？.!?])(?:\s+)?|\n+', text) if item.strip()]

    def tokenize(self, text: str) -> list[str]:
        """把文本切成适合做粗粒度匹配的 token。"""

        return re.findall(r'[0-9A-Za-z_一-鿿]+', text.lower())

    def chunk_text_for_stream(self, text: str, chunk_size: int = 24) -> list[str]:
        """把完整答案切成固定大小片段，供流式输出。

        Args:
            text: 完整答案文本。
            chunk_size: 单个流片段的最大字符数。

        Returns:
            按固定大小切分后的文本片段列表。
        """

        stripped = text.strip()
        if not stripped:
            return []
        return [stripped[index : index + chunk_size] for index in range(0, len(stripped), chunk_size)]

    def extract_stream_delta(self, item: Any, emitted: str) -> str:
        """从不同流式返回格式里提取新增文本片段。

        Args:
            item: 上游流式接口返回的单个数据项。
            emitted: 已经向客户端输出过的累积文本。

        Returns:
            当前数据项对应的新增文本片段；提取失败时返回空字符串。
        """

        if item is None:
            return ''

        delta = getattr(item, 'delta', None)
        if isinstance(delta, str) and delta:
            return delta

        text = getattr(item, 'text', None)
        if isinstance(text, str) and text:
            if text.startswith(emitted):
                return text[len(emitted) :]
            return text

        if isinstance(item, str):
            if item.startswith(emitted):
                return item[len(emitted) :]
            return item

        return ''
