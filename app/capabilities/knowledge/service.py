"""默认 Knowledge 能力实现模块。

把现有 RAG 检索、证据归并、纠错式回答与模型路由能力收敛成统一的知识能力服务，
供 facade、workflow 与远程回退场景复用。
"""


from __future__ import annotations

import inspect
import json
import re
from typing import Any

from app.capabilities.knowledge.base import (
    DocumentContextItem,
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeSearchRequest,
)
from app.capabilities.knowledge.contracts import RetrievalQualityReport
from app.harness.model_router import ModelRouter
from app.models.artifact import EvidenceItem, EvidencePack
from app.rag.prompting import build_corrective_check_prompt, build_corrective_rewrite_prompt


class DefaultKnowledgeCapability:
    """把现有 RAG 栈适配成统一 Knowledge Capability。

    这个实现承担三类职责：
    - 文档上下文装载：把集合内文档、章节和摘要投影成统一上下文模型。
    - 证据检索与包装：把底层 retrieval 返回的引用结果整理成标准 `EvidencePack`。
    - grounded answer 生成：基于证据、模型路由和纠错流程产出更稳健的回答。
    """

    def __init__(self, state, retrieval, vector_store, llm=None, *, model_router: ModelRouter | None = None) -> None:
        """初始化知识能力所依赖的状态、检索器、向量库与模型路由器。"""
        self.state = state
        self.retrieval = retrieval
        self.vector_store = vector_store
        self.llm = llm
        self.model_router = model_router or ModelRouter()

    def load_document_context(self, request: DocumentContextRequest) -> DocumentContextResult:
        """汇总集合内文档的摘要、章节与基础元数据。

        该方法不做向量检索，只从状态存储和已有索引元数据中拼装文档级上下文。
        """
        documents: list[DocumentContextItem] = []
        for record in self.state.documents.values():
            if record.get('collection_name') != request.collection_name:
                continue
            if request.doc_ids and record.get('doc_id') not in request.doc_ids:
                continue
            # 章节信息优先从 chunk 元数据反查；缺失时退回到文档层级路径。
            documents.append(
                DocumentContextItem(
                    doc_id=str(record.get('doc_id')),
                    title=str(record.get('document_title') or record.get('file_name') or record.get('doc_id')),
                    summary=str(record.get('document_summary') or '').strip() or '暂无文档摘要。',
                    sections=self._load_sections(record),
                    metadata={
                        'file_name': record.get('file_name'),
                        'file_type': record.get('file_type'),
                        'document_hierarchy': record.get('document_hierarchy'),
                        'indexed_chunks': record.get('indexed_chunks'),
                    },
                )
            )
        return DocumentContextResult(documents=documents)

    def retrieve_evidence(
        self,
        request: KnowledgeSearchRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> EvidencePack:
        """调用底层检索服务并把引用结果转换为 EvidencePack。

        这里会把 query 请求上的检索增强开关透传给底层 retrieval，同时把返回结果裁剪成
        workflow/capability 层统一消费的证据包结构。
        """
        if self.retrieval is None:
            raise ConnectionError('knowledge retrieval backend is unavailable')
        citations = self.retrieval.retrieve(
            request.collection_name,
            request.query,
            request.top_k,
            **self._filter_supported_kwargs(
                self.retrieval.retrieve,
                {
                    'filters': None,
                    'use_hybrid_retrieval': request.use_hybrid_retrieval,
                    'use_rerank': request.use_rerank,
                    'use_graph_rag': request.use_graph_rag,
                    'graph_max_hops': request.graph_max_hops,
                    'graph_top_k': request.top_k,
                    'trace_context': trace_context or {},
                },
            ),
        )
        # 当请求限定了 doc_id 范围时，检索结果还需要做一次源文档级过滤。
        citations = self._filter_citations(request, citations)
        evidence_items = [
            EvidenceItem(
                citation_id=f'c{i}',
                source=item.source,
                chunk_id=item.chunk_id,
                text=item.text,
                support_score=max(0.0, min(1.0, float(item.score or 0.0))),
                page=item.page,
                tags=[
                    tag
                    for tag in [item.section_title, item.index_kind, item.context_scope, item.graph_relation]
                    if tag
                ][:4],
            )
            for i, item in enumerate(citations, start=1)
        ]
        # 覆盖率用于衡量现有证据是否触达了调用方关心的关注维度。
        coverage_score, missing_aspects = self._measure_coverage(
            evidence_items,
            request.focus_aspects,
            request.top_k,
        )
        task_id = str((trace_context or {}).get('task_id') or '')
        return EvidencePack(
            task_id=task_id,
            evidence_items=evidence_items,
            coverage_score=coverage_score,
            missing_aspects=missing_aspects,
        )

    def grounded_answer(
        self,
        request: GroundedAnswerRequest,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> GroundedAnswerResult:
        """先检索证据，再生成带质量报告的 grounded answer。

        这是 knowledge capability 最完整的入口，会先完成证据检索，再尝试生成答案，并在启用
        Corrective RAG 时追加支撑性检查与必要改写。
        """
        if self.retrieval is None:
            raise ConnectionError('knowledge retrieval backend is unavailable')
        evidence_pack = self.retrieve_evidence(
            KnowledgeSearchRequest(
                query=request.retrieval_query or request.question,
                collection_name=request.collection_name,
                doc_ids=list(request.doc_ids),
                top_k=request.top_k,
                focus_aspects=list(request.focus_aspects),
                use_graph_rag=request.strategy.use_graph_rag,
                use_hybrid_retrieval=request.strategy.use_hybrid_retrieval,
                use_rerank=request.strategy.use_rerank,
                graph_max_hops=request.strategy.graph_max_hops,
            ),
            trace_context=trace_context,
        )
        answer = self._synthesize_grounded_answer(
            request.question,
            evidence_pack,
            trace_context=trace_context,
        )
        quality_report = self._default_quality_report(enabled=request.strategy.use_corrective_rag)
        if request.strategy.use_corrective_rag and evidence_pack.evidence_items:
            answer, quality_report = self._apply_corrective_grounded_answer(
                request.question,
                answer,
                evidence_pack,
                request.collection_name,
                trace_context=trace_context,
            )
        return GroundedAnswerResult(
            answer=answer,
            evidence_pack=evidence_pack,
            citations=[
                {
                    'citation_id': item.citation_id,
                    'source': item.source,
                    'chunk_id': item.chunk_id,
                    'support_score': item.support_score,
                }
                for item in evidence_pack.evidence_items
            ],
            grounded=bool(evidence_pack.evidence_items),
            quality_report=quality_report,
        )

    def _load_sections(self, record: dict[str, Any]) -> list[str]:
        """尽量从向量索引元数据中提取文档章节信息。

        优先读取前若干 chunk 的 `section_title` / `hierarchy_path`；若索引不可用，则退回文档层级字符串。
        """
        chunk_ids = [str(item) for item in record.get('chunk_ids', []) if str(item).strip()]
        if not chunk_ids or self.vector_store is None:
            hierarchy = str(record.get('document_hierarchy') or '').strip()
            return [item.strip() for item in hierarchy.split('/') if item.strip()][-4:]
        try:
            collection = self.vector_store.get_or_create_collection(record['collection_name'])
            payload = collection.get(ids=chunk_ids[:24], include=['metadatas'])
        except Exception:
            hierarchy = str(record.get('document_hierarchy') or '').strip()
            return [item.strip() for item in hierarchy.split('/') if item.strip()][-4:]
        sections: list[str] = []
        seen: set[str] = set()
        for metadata in payload.get('metadatas') or []:
            if not isinstance(metadata, dict):
                continue
            section = str(metadata.get('section_title') or metadata.get('hierarchy_path') or '').strip()
            if not section or section in seen:
                continue
            sections.append(section)
            seen.add(section)
            if len(sections) >= 8:
                break
        return sections

    def _filter_citations(self, request: KnowledgeSearchRequest, citations):
        """按请求限制的文档范围过滤检索到的引用结果。

        过滤条件同时兼容 `source=file_name` 和 `file_path` 两类匹配方式，以适配不同 retrieval
        返回结构。
        """
        if not request.doc_ids:
            return citations
        allowed_sources: set[str] = set()
        allowed_paths: set[str] = set()
        for record in self.state.documents.values():
            if record.get('collection_name') != request.collection_name:
                continue
            if record.get('doc_id') not in request.doc_ids:
                continue
            file_name = str(record.get('file_name') or '').strip()
            file_path = str(record.get('file_path') or '').strip()
            if file_name:
                allowed_sources.add(file_name)
            if file_path:
                allowed_paths.add(file_path)
        if not allowed_sources and not allowed_paths:
            return citations
        return [
            item
            for item in citations
            if (str(item.source or '').strip() in allowed_sources)
            or (str(getattr(item, 'file_path', '') or '').strip() in allowed_paths)
        ]

    def _measure_coverage(
        self,
        evidence_items: list[EvidenceItem],
        focus_aspects: list[str],
        top_k: int,
    ) -> tuple[float, list[str]]:
        """估算证据对关注维度或 top_k 目标的覆盖程度。

        当调用方给出了 `focus_aspects` 时，优先按关注维度覆盖率计算；否则退化为“是否接近 top_k”
        的粗粒度覆盖估计。
        """
        if not evidence_items:
            return 0.0, list(focus_aspects)
        corpus = '\n'.join(item.text for item in evidence_items).lower()
        missing: list[str] = []
        covered = 0
        for aspect in focus_aspects:
            normalized = aspect.lower().strip()
            if not normalized:
                continue
            if normalized in corpus:
                covered += 1
            else:
                missing.append(aspect)
        if focus_aspects:
            return round(covered / max(1, len(focus_aspects)), 2), missing
        return round(min(1.0, len(evidence_items) / max(1, top_k)), 2), []

    def _synthesize_grounded_answer(
        self,
        question: str,
        evidence_pack: EvidencePack,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> str:
        """基于证据、模型路由与兜底策略生成初始回答。

        若模型路由允许使用 LLM，则优先生成约束性回答；失败时回退到直接拼接高分证据文本。
        """
        if not evidence_pack.evidence_items:
            return f'未找到可直接支撑问题“{question}”的证据。'
        top_evidence = evidence_pack.evidence_items[:3]
        decision = self._route_llm(
            purpose='knowledge_answer',
            evidence_pack=evidence_pack,
            trace_context=trace_context,
        )
        if decision is not None:
            # 只把最高优先级的证据片段送入提示词，控制成本并减少幻觉空间。
            prompt = (
                '基于以下证据回答问题，禁止补充证据之外的事实。\n'
                f'问题：{question}\n'
                '证据：\n'
                + '\n'.join(f'- {item.text}' for item in top_evidence)
            )
            try:
                response = self.llm.complete(prompt)
                text = str(getattr(response, 'text', response) or '').strip()
                self._record_model_completion(
                    decision,
                    prompt=prompt,
                    response=response,
                    response_text=text,
                    trace_context=trace_context,
                )
                if text:
                    return text
            except Exception:
                pass
        return '\n'.join(item.text for item in top_evidence)

    def _apply_corrective_grounded_answer(
        self,
        question: str,
        answer: str,
        evidence_pack: EvidencePack,
        collection_name: str,
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> tuple[str, RetrievalQualityReport]:
        """执行启发式或 LLM 自检，并在必要时改写回答。

        该流程先用本地启发式快速判断，再在可用时让 LLM 给出更细粒度的支撑性判断，最后决定
        是否需要保守重写答案。
        """
        report = self._default_quality_report(enabled=True)
        heuristic = self._heuristic_answer_support(answer, evidence_pack)
        report.supported = heuristic['supported']
        report.risk = heuristic['risk']
        report.confidence = heuristic['confidence']
        report.reason = heuristic['reason']
        report.rewrite_needed = not heuristic['supported']
        report.check_mode = 'heuristic'
        contexts = [item.text for item in evidence_pack.evidence_items[: max(1, min(len(evidence_pack.evidence_items), 4))]]

        llm_check = self._llm_corrective_check(question, answer, contexts, trace_context=trace_context)
        if llm_check is not None:
            report.supported = bool(llm_check.get('supported'))
            report.risk = str(llm_check.get('risk') or report.risk)
            report.confidence = float(llm_check.get('confidence') or report.confidence)
            report.reason = str(llm_check.get('reason') or report.reason)
            report.rewrite_needed = bool(llm_check.get('rewrite_needed', not report.supported))
            report.check_mode = 'llm'

        if report.supported:
            return answer, report

        # 默认先构造一个完全基于证据的保守兜底回答，随后再尝试 LLM 重写。
        corrected = self._fallback_grounded_answer(question, evidence_pack)
        corrected_mode = 'corrective_local_fallback'
        rewritten = self._llm_corrective_rewrite(question, contexts, trace_context=trace_context)
        if rewritten:
            corrected = rewritten
            corrected_mode = 'corrective_llm_rewrite'
        report.applied = True
        report.final_mode = corrected_mode
        return corrected, report

    def _default_quality_report(self, *, enabled: bool) -> RetrievalQualityReport:
        """构造 grounded answer 阶段的默认质量报告。"""
        if not enabled:
            return RetrievalQualityReport()
        return RetrievalQualityReport(
            enabled=True,
            supported=True,
            risk='low',
            confidence=1.0,
            reason='enabled',
            rewrite_needed=False,
            applied=False,
            check_mode='heuristic',
            final_mode=None,
        )

    def _fallback_grounded_answer(self, question: str, evidence_pack: EvidencePack) -> str:
        """在缺少可靠 LLM 输出时从证据中拼接保守答案。

        核心思路是从证据里选出与问题词项重叠更高的句子，尽量减少自由生成。
        """
        sentences: list[tuple[float, str]] = []
        question_tokens = set(self._tokenize(question))

        for item in evidence_pack.evidence_items:
            for sentence in self._split_sentences(item.text):
                sentence_tokens = set(self._tokenize(sentence))
                overlap = len(question_tokens & sentence_tokens)
                score = overlap + item.support_score
                if overlap:
                    sentences.append((score, sentence.strip()))

        if not sentences:
            top = evidence_pack.evidence_items[0]
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
        sources = '；'.join(dict.fromkeys(item.source for item in evidence_pack.evidence_items[:3]))
        return f'{" ".join(selected)}\n\n参考来源：{sources}'

    def _heuristic_answer_support(self, answer: str, evidence_pack: EvidencePack) -> dict[str, Any]:
        """用词项重叠启发式判断回答是否被证据充分支撑。

        这不是严格事实核验，只是一个轻量、本地、低成本的第一层支撑度估计。
        """
        evidence_text = '\n'.join(item.text for item in evidence_pack.evidence_items).lower()
        answer_sentences = [sentence.strip() for sentence in self._split_sentences(answer) if sentence.strip()]
        if not answer_sentences:
            return {'supported': False, 'risk': 'high', 'confidence': 0.0, 'reason': 'empty_answer'}
        unsupported = 0
        supported = 0
        for sentence in answer_sentences:
            normalized = sentence.lower()
            if normalized in evidence_text:
                supported += 1
                continue
            sentence_tokens = set(self._tokenize(sentence))
            if not sentence_tokens:
                continue
            overlaps = []
            for item in evidence_pack.evidence_items:
                citation_tokens = set(self._tokenize(item.text))
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

    def _llm_corrective_check(
        self,
        question: str,
        answer: str,
        contexts: list[str],
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """调用 LLM 对回答做支撑性自检并解析结构化结果。

        若返回内容不是合法 JSON 或字段不符合预期，则直接视为自检不可用并回退本地判断。
        """
        decision = self._route_llm(purpose='knowledge_check', trace_context=trace_context)
        if decision is None:
            return None
        prompt = build_corrective_check_prompt(question, answer, contexts)
        try:
            response = self.llm.complete(prompt)
            self._record_model_completion(
                decision,
                prompt=prompt,
                response=response,
                response_text=str(response).strip(),
                trace_context=trace_context,
            )
            payload = json.loads(str(response).strip())
        except Exception:
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

    def _llm_corrective_rewrite(
        self,
        question: str,
        contexts: list[str],
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> str | None:
        """调用 LLM 在证据约束下重写更保守的回答。

        这里不传入原答案，而是让模型仅基于问题和证据重新组织表述，降低把原错误答案继续“润色”
        的风险。
        """
        decision = self._route_llm(purpose='knowledge_rewrite', trace_context=trace_context)
        if decision is None:
            return None
        prompt = build_corrective_rewrite_prompt(question, contexts)
        try:
            response = self.llm.complete(prompt)
            answer = str(response).strip()
            self._record_model_completion(
                decision,
                prompt=prompt,
                response=response,
                response_text=answer,
                trace_context=trace_context,
            )
            return answer or None
        except Exception:
            return None

    def _route_llm(
        self,
        *,
        purpose: str,
        evidence_pack: EvidencePack | None = None,
        trace_context: dict[str, Any] | None = None,
    ):
        """根据用途和证据情况决定当前步骤是否使用 LLM。

        决策结果会同步写入 trace，便于后续分析某一步为什么选择了 LLM 或本地回退。
        """
        if self.llm is None or not hasattr(self.llm, 'complete'):
            return None
        decision = self.model_router.route(
            purpose=purpose,
            llm_available=True,
            feature_enabled=True,
            step_name='grounded_answer',
            evidence_count=len(evidence_pack.evidence_items) if evidence_pack is not None else 0,
            missing_aspects=len(evidence_pack.missing_aspects) if evidence_pack is not None else 0,
        )
        self.model_router.record_selection(
            self._trace_recorder(trace_context),
            decision,
            scope='knowledge_capability',
            purpose=purpose,
        )
        if trace_context and isinstance(trace_context.get('trace'), list):
            trace_context['trace'].append(
                {
                    'event': 'model_route_selected',
                    'scope': 'knowledge_capability',
                    **decision.model_dump(mode='json'),
                }
            )
        if decision.mode != 'llm':
            return None
        return decision

    def _record_model_completion(
        self,
        decision,
        *,
        prompt: str,
        response: Any,
        response_text: str,
        trace_context: dict[str, Any] | None = None,
    ) -> None:
        """把模型调用结果记录到统一的 trace 记录器。

        统一交给 `ModelRouter` 记录，可保证路由选择和实际消费事件的数据格式保持一致。
        """
        self.model_router.record_completion(
            self._trace_recorder(trace_context),
            decision,
            prompt_text=prompt,
            response=response,
            response_text=response_text,
            scope='knowledge_capability',
            purpose=decision.purpose,
        )

    def _trace_recorder(self, trace_context: dict[str, Any] | None):
        """返回 trace 上下文中的记录器，缺省时退化为空实现。

        这样 capability 在没有显式传入 recorder 的情况下也能安全调用记录逻辑，而不用在各处判空。
        """
        trace = trace_context.get('trace_recorder') if isinstance(trace_context, dict) else None
        if trace is not None and hasattr(trace, 'record'):
            return trace

        class _NullTrace:
            """空 trace 记录器，用于屏蔽未启用观测时的记录调用。"""

            def record(self, name: str, payload: dict[str, Any]) -> None:
                """丢弃 trace 事件，保持与真实记录器相同的调用签名。"""
                return None

        return _NullTrace()

    def _split_sentences(self, text: str) -> list[str]:
        """按中英文句号与换行切分文本为句子列表。"""
        return [item.strip() for item in re.split(r'(?<=[。！？.!?])(?:\s+)?|\n+', text) if item.strip()]

    def _tokenize(self, text: str) -> list[str]:
        """提取文本中的中英文词项，用于轻量重叠计算。"""
        return re.findall(r'[0-9A-Za-z_一-鿿]+', text.lower())

    def _filter_supported_kwargs(self, func, kwargs: dict[str, Any]) -> dict[str, Any]:
        """仅向目标函数传入其签名支持的关键字参数。

        主要用于兼容不同版本 retrieval 实现的签名差异，避免 capability 因透传了新参数而报错。
        """
        try:
            signature = inspect.signature(func)
        except (TypeError, ValueError):
            return kwargs
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
            return kwargs
        return {key: value for key, value in kwargs.items() if key in signature.parameters}
