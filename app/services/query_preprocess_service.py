"""查询前处理服务模块。

负责在真正进入检索与回答阶段之前，统一处理 Prompt Guardrails、敏感信息脱敏、
查询改写、多查询扩展和 HyDE 生成等能力，避免这些前置逻辑分散在查询主链路中。
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from app.core.config import Settings
from app.models.query import CitationItem, QueryRequest
from app.rag.guardrails import inspect_prompt_injection, redact_text
from app.rag.observability import TraceRecorder
from app.rag.retrieval import RagRetrievalService


class QueryPreprocessService:
    """处理 Guardrails、脱敏、查询改写和扩展。"""

    def __init__(
        self,
        settings: Settings,
        retrieval_service: RagRetrievalService,
        trace: TraceRecorder,
        llm: Any | None = None,
    ) -> None:
        """初始化查询前处理服务。

        Args:
            settings: 全局配置对象，决定护栏和脱敏默认开关。
            retrieval_service: 检索服务，用于执行规则改写和多路改写生成。
            trace: 链路追踪记录器，用于记录前处理阶段事件。
            llm: 可选的大模型实例，用于多查询和 HyDE 扩展。
        """
        self.settings = settings
        self.retrieval_service = retrieval_service
        self.trace = trace
        self.llm = llm

    def use_prompt_guardrails(self, payload: QueryRequest) -> bool:
        """返回本次请求是否启用 Prompt Guardrails。"""

        if payload.use_prompt_guardrails is None:
            return self.settings.enable_prompt_guardrails
        return payload.use_prompt_guardrails

    def use_pii_redaction(self, payload: QueryRequest) -> bool:
        """返回本次请求是否启用脱敏。"""

        if payload.use_pii_redaction is None:
            return self.settings.enable_pii_redaction
        return payload.use_pii_redaction

    def empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        """返回统一的脱敏结果结构。

        Args:
            enabled: 当前链路是否启用脱敏能力。

        Returns:
            便于主链路直接透传和记录的默认脱敏状态字典。
        """

        return {
            'enabled': enabled,
            'applied': False,
            'replacement_count': 0,
            'matched_types': [],
            'counts': {},
        }

    def sanitize_text(
        self,
        text: str,
        payload: QueryRequest,
        target: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any]]:
        """按请求配置对文本执行脱敏。

        Args:
            text: 待处理文本。
            payload: 当前请求对象。
            target: 当前脱敏目标名称，例如 `question`、`answer`。
            trace_context: 当前链路上下文名称。

        Returns:
            第一项为脱敏后的文本，第二项为脱敏结果摘要。
        """

        enabled = self.use_pii_redaction(payload)
        if not enabled or not text:
            return text, self.empty_redaction_state(enabled)
        redacted, summary = redact_text(text)
        info = {'enabled': enabled, **summary}
        if info['applied']:
            self.trace.record(
                'guardrails_redacted',
                {
                    'context': trace_context,
                    'target': target,
                    'replacement_count': info['replacement_count'],
                    'matched_types': info['matched_types'],
                },
            )
        return redacted, info

    def sanitize_citations(
        self,
        citations: list[CitationItem],
        payload: QueryRequest,
        trace_context: str,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """对引用片段内容执行脱敏，并汇总命中情况。

        Args:
            citations: 待处理引用列表。
            payload: 当前请求对象。
            trace_context: 当前链路上下文名称。

        Returns:
            第一项为脱敏后的引用列表，第二项为汇总脱敏结果。
        """

        enabled = self.use_pii_redaction(payload)
        if not enabled or not citations:
            return citations, self.empty_redaction_state(enabled)

        sanitized: list[CitationItem] = []
        replacement_count = 0
        matched_types: set[str] = set()
        counts: dict[str, int] = {}
        applied = False

        for citation in citations:
            redacted_text, info = self.sanitize_text(
                citation.text,
                payload,
                target='citation_text',
                trace_context=trace_context,
            )
            replacement_count += int(info['replacement_count'])
            matched_types.update(info['matched_types'])
            for key, value in info['counts'].items():
                counts[key] = counts.get(key, 0) + int(value)
            applied = applied or bool(info['applied'])
            sanitized.append(citation.model_copy(update={'text': redacted_text}))

        summary = {
            'enabled': enabled,
            'applied': applied,
            'replacement_count': replacement_count,
            'matched_types': sorted(matched_types),
            'counts': counts,
        }
        if applied:
            self.trace.record(
                'guardrails_redacted',
                {
                    'context': trace_context,
                    'target': 'citations',
                    'replacement_count': replacement_count,
                    'matched_types': sorted(matched_types),
                },
            )
        return sanitized, summary

    def check_guardrails(self, question: str, payload: QueryRequest, trace_context: str) -> dict[str, Any]:
        """检查输入护栏并在需要时完成问题脱敏。

        Args:
            question: 用户问题。
            payload: 当前请求对象。
            trace_context: 当前链路上下文名称。

        Returns:
            包含阻断状态、风险等级和脱敏结果的护栏状态字典。
        """

        prompt_guardrails_enabled = self.use_prompt_guardrails(payload)
        pii_redaction_enabled = self.use_pii_redaction(payload)
        inspection = (
            inspect_prompt_injection(question)
            if prompt_guardrails_enabled
            else {'blocked': False, 'risk': 'low', 'matched_rules': [], 'reason': 'disabled'}
        )
        sanitized_question = question
        question_redaction = self.empty_redaction_state(pii_redaction_enabled)
        if pii_redaction_enabled:
            # 问题在进入 rewrite / retrieval / cache 之前先统一脱敏，避免敏感信息进入后续链路。
            sanitized_question, question_redaction = self.sanitize_text(
                question,
                payload,
                target='question',
                trace_context=trace_context,
            )
        state = {
            'enabled': prompt_guardrails_enabled or pii_redaction_enabled,
            'prompt_guardrails_enabled': prompt_guardrails_enabled,
            'pii_redaction_enabled': pii_redaction_enabled,
            'blocked': inspection['blocked'],
            'risk': inspection['risk'],
            'reason': inspection['reason'],
            'matched_rules': inspection['matched_rules'],
            'sanitized_question': sanitized_question,
            'question_redaction': question_redaction,
        }
        self.trace.record(
            'guardrails_checked',
            {
                'context': trace_context,
                **self.public_guardrail_state(state),
            },
        )
        return state

    def public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """生成可安全暴露给 trace / SSE 的护栏状态。

        Args:
            guardrail_state: 内部完整护栏状态。
            citation_redaction: 可选的引用脱敏结果。
            answer_redaction: 可选的答案脱敏结果。

        Returns:
            仅包含安全可暴露字段的护栏状态字典。
        """

        return {
            'enabled': guardrail_state['enabled'],
            'blocked': guardrail_state['blocked'],
            'risk': guardrail_state['risk'],
            'reason': guardrail_state['reason'],
            'matched_rules': guardrail_state['matched_rules'],
            'prompt_guardrails_enabled': guardrail_state['prompt_guardrails_enabled'],
            'pii_redaction_enabled': guardrail_state['pii_redaction_enabled'],
            'question_redacted': guardrail_state['question_redaction']['applied'],
            'citation_redacted': bool(citation_redaction and citation_redaction.get('applied')),
            'answer_redacted': bool(answer_redaction and answer_redaction.get('applied')),
        }

    def question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        """返回写入会话历史时使用的问题文本。

        开启问题脱敏时，会话历史中只保留脱敏后的文本，避免把原始敏感信息写入存储。
        """

        if guardrail_state['pii_redaction_enabled']:
            return guardrail_state['sanitized_question']
        return question

    def guardrail_block_message(self) -> str:
        """返回统一的护栏拦截提示。"""

        return '检测到疑似提示注入、越权控制或敏感信息导出请求，已触发安全护栏。请改为直接描述你的业务问题。'

    def prepare_retrieval_question(
        self,
        question: str,
        use_query_rewrite: bool,
        trace_context: str,
    ) -> str:
        """根据配置决定是否对检索问题做改写。

        Args:
            question: 原始检索问题。
            use_query_rewrite: 是否启用查询改写。
            trace_context: 当前链路上下文名称。

        Returns:
            最终用于检索的问题文本。
        """

        rewritten_question, _ = self.resolve_rewrite_info(question, use_query_rewrite, trace_context)
        return rewritten_question

    def resolve_rewrite_info(
        self,
        question: str,
        use_query_rewrite: bool,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """返回改写后的问题及其详细元信息。

        Args:
            question: 原始检索问题。
            use_query_rewrite: 是否启用查询改写。
            trace_context: 当前链路上下文名称。

        Returns:
            第一项为改写后的问题，第二项为改写元信息；未启用时返回 `None`。
        """

        if not use_query_rewrite:
            return question, None

        # 兼容旧版检索服务接口，优先使用包含详细命中规则的新接口。
        if hasattr(self.retrieval_service, 'rewrite_query_info'):
            rewrite_info = self.retrieval_service.rewrite_query_info(question)
        else:
            rewritten_query = self.retrieval_service.rewrite_query(question)
            rewrite_info = {
                'original_query': question,
                'normalized_query': question.strip(),
                'rewritten_query': rewritten_query,
                'applied_rules': ['legacy_rewrite'],
                'expanded_terms': [],
                'changed': rewritten_query != question,
            }
        self.trace.record(
            'query_rewritten',
            {
                'context': trace_context,
                'question': rewrite_info['original_query'],
                'normalized_query': rewrite_info['normalized_query'],
                'rewritten_query': rewrite_info['rewritten_query'],
                'applied_rules': rewrite_info['applied_rules'],
                'expanded_terms': rewrite_info['expanded_terms'],
                'changed': rewrite_info['changed'],
            },
        )
        return rewrite_info['rewritten_query'], rewrite_info

    def maybe_apply_multi_query(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """按需生成多路检索查询。

        Args:
            payload: 当前请求对象。
            retrieval_question: 基础检索问题。
            answer_question: 用户原始问题。
            trace_context: 当前链路上下文名称。

        Returns:
            第一项为查询列表，第二项为生成结果元信息。
        """

        if not payload.use_multi_query:
            return [retrieval_question], None

        desired_count = max(2, min(int(payload.multi_query_count or 3), 6))

        # 多查询、HyDE、多重改写会彼此影响召回分布，这里明确做互斥裁剪。
        if payload.use_hyde:
            info = {
                'enabled': False,
                'reason': 'hyde_enabled',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_query_skipped', {'context': trace_context, **info})
            return [retrieval_question], info

        if payload.use_multi_rewrite:
            info = {
                'enabled': False,
                'reason': 'multi_rewrite_enabled',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_query_skipped', {'context': trace_context, **info})
            return [retrieval_question], info

        if self.llm is None:
            info = {
                'enabled': False,
                'reason': 'llm_unavailable',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_query_skipped', {'context': trace_context, **info})
            return [retrieval_question], info

        prompt = (
            '请基于用户问题生成多个不同角度的检索查询，用于召回更全面的证据。\n'
            '要求：\n'
            '1) 只输出 JSON 数组（array），数组元素为字符串。\n'
            '2) 每条查询 8~60 字，尽量包含关键词（接口路径、参数名、术语、英文缩写）。\n'
            f'3) 输出 {desired_count} 条，去重，避免只做同义改写。\n'
            f'用户问题：{answer_question}\n'
            f'基础检索查询：{retrieval_question}\n'
            '输出：'
        )
        try:
            completed = self.llm.complete(prompt)
            raw = str(completed).strip()
        except Exception as exc:
            info = {
                'enabled': False,
                'reason': f'llm_failed:{str(exc)}',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_query_failed', {'context': trace_context, **info})
            return [retrieval_question], info

        candidates: list[str] = []
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, list):
                candidates = [str(item).strip() for item in loaded if str(item).strip()]
        except Exception:
            candidates = []

        if not candidates:
            lines = [line.strip('- ').strip() for line in raw.splitlines() if line.strip()]
            candidates = [line for line in lines if 4 <= len(line) <= 120]

        queries: list[str] = []
        seen: set[str] = set()

        base = retrieval_question.strip()
        if base:
            normalized = base.lower()
            if normalized not in seen:
                queries.append(base)
                seen.add(normalized)

        for item in candidates:
            cleaned = re.sub(r'\s+', ' ', item).strip()
            if not cleaned:
                continue
            if len(cleaned) > 120:
                cleaned = cleaned[:120].strip()
            normalized = cleaned.lower()
            if normalized in seen:
                continue
            queries.append(cleaned)
            seen.add(normalized)
            if len(queries) >= desired_count:
                break

        if len(queries) < 2:
            info = {
                'enabled': False,
                'reason': 'insufficient_queries',
                'query_count': len(queries) or 1,
                'queries': [(queries[0] if queries else retrieval_question)[:200]],
            }
            self.trace.record('multi_query_failed', {'context': trace_context, **info})
            return [retrieval_question], info

        info = {
            'enabled': True,
            'query_count': len(queries),
            'queries': [item[:200] for item in queries],
        }
        self.trace.record('multi_query_generated', {'context': trace_context, **info})
        return queries, info

    def maybe_apply_multi_rewrite(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """按需生成多路改写查询。

        Args:
            payload: 当前请求对象。
            retrieval_question: 基础检索问题。
            answer_question: 用户原始问题。
            trace_context: 当前链路上下文名称。

        Returns:
            第一项为改写查询列表，第二项为改写结果元信息。
        """

        _ = answer_question
        if not payload.use_multi_rewrite:
            return [retrieval_question], None

        desired_count = max(2, min(int(payload.multi_rewrite_count or 3), 6))

        if payload.use_hyde:
            info = {
                'enabled': False,
                'reason': 'hyde_enabled',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_rewrite_skipped', {'context': trace_context, **info})
            return [retrieval_question], info

        if payload.use_multi_query:
            info = {
                'enabled': False,
                'reason': 'multi_query_enabled',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_rewrite_skipped', {'context': trace_context, **info})
            return [retrieval_question], info

        # 多重改写优先走 retrieval service 的规则能力，保持结果更可控、可解释。
        if hasattr(self.retrieval_service, 'rewrite_multi_query_info'):
            try:
                info = self.retrieval_service.rewrite_multi_query_info(retrieval_question, max_queries=desired_count)
                raw_queries = cast(list[Any], info.get('queries') or [])
                queries = [str(item).strip() for item in raw_queries if str(item).strip()]
            except Exception as exc:
                info = {
                    'enabled': False,
                    'reason': f'failed:{str(exc)}',
                    'query_count': 1,
                    'queries': [retrieval_question[:200]],
                }
                self.trace.record('multi_rewrite_failed', {'context': trace_context, **info})
                return [retrieval_question], info
        else:
            info = {
                'enabled': False,
                'reason': 'unsupported_retrieval_service',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_rewrite_skipped', {'context': trace_context, **info})
            return [retrieval_question], info

        if len(queries) < 2:
            fallback = {
                'enabled': False,
                'reason': 'insufficient_queries',
                'query_count': 1,
                'queries': [retrieval_question[:200]],
            }
            self.trace.record('multi_rewrite_failed', {'context': trace_context, **fallback})
            return [retrieval_question], fallback

        normalized_info = {
            'enabled': True,
            'query_count': len(queries),
            'queries': [item[:200] for item in queries[:6]],
        } | {key: value for key, value in info.items() if key in {'strategies', 'base'}}
        self.trace.record('multi_rewrite_generated', {'context': trace_context, **normalized_info})
        return queries[:desired_count], normalized_info

    def maybe_apply_hyde(
        self,
        payload: QueryRequest,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """按需生成 HyDE 检索片段。

        Args:
            payload: 当前请求对象。
            retrieval_question: 基础检索问题。
            answer_question: 用户原始问题。
            trace_context: 当前链路上下文名称。

        Returns:
            第一项为最终检索文本，第二项为 HyDE 生成信息。
        """

        if not payload.use_hyde:
            return retrieval_question, None

        if self.llm is None:
            info = {
                'enabled': False,
                'reason': 'llm_unavailable',
            }
            self.trace.record('hyde_skipped', {'context': trace_context, **info})
            return retrieval_question, info

        prompt = (
            '请根据下面的问题，写一段“可能出现在知识库/文档中的说明性片段”，用于检索召回相关内容。\n'
            '要求：\n'
            '1) 只输出片段正文，不要加标题、编号、引用标记。\n'
            '2) 不要提到“我/你/AI/模型/助手”。\n'
            '3) 尽量包含关键术语、接口名、参数名、路径、错误码等可检索线索。\n'
            '4) 长度控制在 120~260 字。\n'
            f'问题：{answer_question}\n'
            f'检索上下文：{retrieval_question}\n'
            '片段正文：'
        )
        try:
            completed = self.llm.complete(prompt)
            hyde_doc = str(completed).strip()
        except Exception as exc:
            info = {
                'enabled': False,
                'reason': f'llm_failed:{str(exc)}',
            }
            self.trace.record('hyde_failed', {'context': trace_context, **info})
            return retrieval_question, info

        if not hyde_doc:
            info = {
                'enabled': False,
                'reason': 'empty_output',
            }
            self.trace.record('hyde_failed', {'context': trace_context, **info})
            return retrieval_question, info

        final_query = f'{answer_question}\n\n{hyde_doc}'.strip()
        info = {
            'enabled': True,
            'doc_preview': hyde_doc[:240],
            'final_query_preview': final_query[:240],
        }
        self.trace.record('hyde_applied', {'context': trace_context, **info})
        return final_query, info
