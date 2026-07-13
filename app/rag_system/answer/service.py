"""RAG 系统回答生成服务模块。

负责 Prompt 构造、答案生成、流式输出、上下文压缩以及 Corrective RAG。
与主应用的 `app/services/answer_service.py` 功能一致，但使用独立配置。
"""

from __future__ import annotations

import json
import re
from collections.abc import Generator
from typing import Any

from app.rag_system.answer.preprocess import QueryPreprocessService
from app.rag_system.answer.prompting import build_corrective_check_prompt, build_corrective_rewrite_prompt, build_qa_prompt
from app.rag_system.config.settings import RagSettings
from app.rag_system.models.query import CitationItem, QueryRequest
from app.rag_system.observability.trace import TraceRecorder


SSEEvent = dict[str, Any]


class AnswerService:
    """回答生成服务主类。"""

    def __init__(
        self,
        settings: RagSettings,
        trace: TraceRecorder,
        preprocess_service: QueryPreprocessService,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings
        self.trace = trace
        self.preprocess_service = preprocess_service
        self.llm = llm

    def build_qa_prompt(self, question: str, contexts: list[str], use_guardrails: bool = False) -> str:
        """构造回答 Prompt。"""
        return build_qa_prompt(question, contexts, use_guardrails=use_guardrails)

    # ── 答案生成 ──

    def generate_answer_with_mode(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str]:
        """生成答案并返回对应模式。"""
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

    def build_answer(self, question: str, citations: list[CitationItem]) -> str:
        """本地兜底答案构建（无 LLM 时）。"""
        if not citations:
            return '未找到足够依据来回答该问题。'
        parts = [f'基于找到的 {len(citations)} 条相关证据，请参考以下内容：']
        for idx, c in enumerate(citations, 1):
            src = c.source or c.file_path or ''
            prefix = f'{idx}. [{src}]' if src else f'{idx}.'
            parts.append(f'\n{prefix}\n{c.text[:300]}')
        return '\n\n'.join(parts)

    # ── 流式输出 ──

    def stream_answer(
        self,
        question: str,
        prompt: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> Generator[SSEEvent, None, tuple[str, str]]:
        """流式生成答案，yield SSE delta 事件。"""
        answer = ''
        if self.llm is None:
            answer = self.build_answer(question, citations)
            for chunk in self.chunk_text_for_stream(answer):
                yield {'event': 'delta', 'data': {'delta': chunk}}
            return answer, 'local_fallback'

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
                return answer, 'llm_stream'
        except Exception as exc:
            self.trace.record('llm_stream_fallback', {'reason': str(exc), 'collection_name': collection_name})

        try:
            completed = self.llm.complete(prompt)
            answer = str(completed).strip()
        except Exception:
            answer = self.build_answer(question, citations)
            answer_mode = 'local_fallback'
        else:
            answer_mode = 'llm_complete'

        if answer_mode != 'llm_stream':
            for chunk in self.chunk_text_for_stream(answer):
                yield {'event': 'delta', 'data': {'delta': chunk}}
        return answer, answer_mode

    def generate_answer_stream(self, prompt: str) -> Generator[str, None, None]:
        """简单的流式生成（逐 token）。"""
        if self.llm is None:
            yield '未配置 LLM，无法生成回答。'
            return
        try:
            stream = self.llm.stream_complete(prompt)
            for item in stream:
                yield self.extract_stream_delta(item, '')
        except Exception as exc:
            yield f'LLM 流式调用失败: {exc}'

    def chunk_text_for_stream(self, text: str, chunk_size: int = 20) -> Generator[str, None, None]:
        """将文本切分为适合流式输出的短块。"""
        if not text:
            return
        for i in range(0, len(text), chunk_size):
            yield text[i:i + chunk_size]

    def extract_stream_delta(self, item: Any, emitted: str) -> str:
        """从 LLM 流式响应中提取增量文本。"""
        if hasattr(item, 'delta'):
            raw = item.delta
        elif hasattr(item, 'text'):
            raw = item.text
        elif isinstance(item, str):
            raw = item
        else:
            raw = str(item)
        return raw[len(emitted):] if raw.startswith(emitted) else raw

    # ── Corrective RAG ──

    def generate_corrective_answer(
        self,
        question: str,
        contexts: list[str],
        max_retries: int = 1,
    ) -> tuple[str, dict[str, Any]]:
        """Corrective RAG：heuristic + LLM 双层自检。"""
        info = self.empty_corrective_info()
        info['enabled'] = True

        prompt = build_qa_prompt(question, contexts)
        answer, mode = self.generate_answer_with_mode(question, prompt, [], '')
        if mode == 'error' and not answer:
            return answer, info

        # Heuristic 自检
        heuristic = self.heuristic_answer_support(question, answer, contexts)
        info.update({
            'supported': heuristic['supported'],
            'risk': heuristic['risk'],
            'confidence': heuristic['confidence'],
            'reason': heuristic['reason'],
            'rewrite_needed': not heuristic['supported'],
            'check_mode': 'heuristic',
        })

        # LLM 自检
        if self.llm and len(contexts) > 0:
            llm_check = self.llm_corrective_check(question, answer, contexts[:4])
            if llm_check is not None:
                info.update(llm_check)
                info['check_mode'] = 'llm'

        if info.get('supported'):
            return answer, info

        # 重写
        if self.llm:
            rewrite_prompt = build_corrective_rewrite_prompt(question, contexts)
            answer, _ = self.generate_answer_with_mode(question, rewrite_prompt, [], '')
            mode = 'corrective_llm_rewrite'
        else:
            answer = self.build_answer(question, [CitationItem(chunk_id='', source='', text=c) for c in contexts])
            mode = 'corrective_local_fallback'

        info['applied'] = True
        info['final_mode'] = mode
        self.trace.record('corrective_rag_applied', {
            'question': question[:200], 'reason': info.get('reason', ''),
        })
        return answer, info

    def empty_corrective_info(self) -> dict[str, Any]:
        return {
            'enabled': False, 'supported': True, 'risk': 'low',
            'confidence': 1.0, 'reason': 'disabled', 'rewrite_needed': False,
            'applied': False, 'check_mode': 'disabled', 'final_mode': None,
        }

    def heuristic_answer_support(self, question: str, answer: str, contexts: list[str]) -> dict[str, Any]:
        """启发式评估答案是否被证据支持。"""
        if not contexts:
            return {'supported': False, 'risk': 'high', 'confidence': 0.0, 'reason': '无证据上下文'}
        # 检查答案是否包含"未找到"等词语
        unsupported_patterns = ['未找到', '无法回答', '没有足够', 'no information', 'cannot answer']
        for p in unsupported_patterns:
            if p in answer:
                return {'supported': False, 'risk': 'medium', 'confidence': 0.3, 'reason': f'答案包含"{p}"'}
        return {'supported': True, 'risk': 'low', 'confidence': 0.7, 'reason': '通过启发式检查'}

    def llm_corrective_check(self, question: str, answer: str, contexts: list[str]) -> dict[str, Any] | None:
        """LLM 自检：用 LLM 评估答案与证据的一致性。"""
        if not self.llm:
            return None
        check_prompt = build_corrective_check_prompt(question, answer, contexts)
        try:
            response = self.llm.complete(check_prompt)
            result = json.loads(str(response).strip())
            return {
                'supported': result.get('supported', True),
                'confidence': result.get('confidence', 0.5),
                'risk': result.get('risk', 'low'),
                'reason': result.get('reason', ''),
                'rewrite_needed': result.get('rewrite_needed', False),
            }
        except Exception:
            return None

    # ── 上下文压缩 ──

    def prepare_answer_context(
        self,
        question: str,
        citations: list[CitationItem],
    ) -> tuple[list[str], dict[str, Any]]:
        """准备回答上下文，可选压缩。"""
        original_contexts = [self.format_citation_context(c) for c in citations]
        original_chars = sum(len(t) for t in original_contexts)
        original_sentences = sum(len(self.split_sentences(t)) for t in original_contexts)

        max_chunks = self.settings.context_compression_max_chunks
        max_sentences = self.settings.context_compression_max_sentences
        max_chars = self.settings.context_compression_max_chars
        enabled = self.settings.enable_context_compression and bool(citations)

        if not enabled or len(citations) <= 1:
            return original_contexts, {
                'enabled': False, 'original_chunk_count': len(citations),
                'original_char_count': original_chars,
                'original_sentence_count': original_sentences,
                'compressed': False,
            }

        compressed = self.compress_citation_contexts(question, citations, max_sentences, max_chars)
        compressed_chars = sum(len(t) for t in compressed)
        self.trace.record('context_compressed', {
            'original_chunk_count': len(citations), 'compressed_chunk_count': len(compressed),
            'original_char_count': original_chars, 'compressed_char_count': compressed_chars,
            'strategy': 'sentence_extract',
        })
        return compressed, {'enabled': True, 'compressed': True, 'chunks': len(compressed)}

    def compress_citation_contexts(
        self,
        question: str,
        citations: list[CitationItem],
        max_sentences: int = 8,
        max_chars: int = 1600,
    ) -> list[str]:
        """从候选引用里抽取最相关的句子并控制长度预算。"""
        question_tokens = set(self.tokenize(question))
        ranked_sentences: list[tuple[float, str]] = []
        seen_sentences: set[str] = set()

        for c in citations:
            text = self.format_citation_context(c)
            sentences = self.split_sentences(text)
            for si, sentence in enumerate(sentences):
                normalized = re.sub(r'\s+', ' ', sentence).strip()
                if not normalized or normalized in seen_sentences:
                    continue
                seen_sentences.add(normalized)
                sent_tokens = set(self.tokenize(normalized))
                overlap = len(question_tokens & sent_tokens)
                coverage = overlap / max(len(question_tokens), 1)
                position_bonus = max(0.0, 0.12 - si * 0.02)
                score = coverage + position_bonus
                ranked_sentences.append((score, normalized))

        ranked_sentences.sort(key=lambda x: x[0], reverse=True)
        result: list[str] = []
        used_chars = 0
        for _, sentence in ranked_sentences:
            if len(result) >= max_sentences:
                break
            extra = len(sentence) + 2
            if used_chars + extra > max_chars:
                continue
            result.append(sentence)
            used_chars += extra

        if not result:
            for c in citations[:2]:
                result.append(c.text[:max_chars // 2])
        return result

    def format_citation_context(self, citation: CitationItem) -> str:
        """格式化引用上下文。"""
        if not citation.graph_path:
            return citation.text
        return f'图谱路径：{citation.graph_path}\n证据片段：{citation.text}'

    def split_sentences(self, text: str) -> list[str]:
        """按句子边界分词。"""
        sentences = re.split(r'(?<=[。！？.!?])\s*', text)
        return [s.strip() for s in sentences if s.strip()]

    def tokenize(self, text: str) -> list[str]:
        """分词。"""
        return re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower())

