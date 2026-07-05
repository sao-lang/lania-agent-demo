"""Query 工作流的 Recipe 定义。

以声明式 Recipe + Stage 方式组织 query/chat 的标准执行流程。
Phase 3：各 Stage 填入真实业务逻辑，通过 ctx 访问运行时依赖。
"""

from __future__ import annotations

from typing import Any

from app.harness.core.recipe import BaseRecipe
from app.harness.core.stage import BaseStage


class GuardrailStage(BaseStage):
    """输入安全检查阶段。

    该阶段执行护栏检查，判断用户输入是否安全。
    若被拦截，写入 guardrail_blocked 标记，后续 orchestration 据此决定短路。
    """

    def __init__(self) -> None:
        super().__init__(
            name='guardrail',
            description='检查用户输入是否安全、是否命中 guardrail 规则',
            requires_policy_check=True,
            requires_guardrail=True,
        )

    def run(self, state: dict, ctx) -> dict:
        runtime = ctx.get('runtime')
        request = state.get('request')
        question = state.get('question') or ''

        if runtime and hasattr(runtime, 'check_guardrails'):
            mode = state.get('mode', 'query')
            guardrail_state = runtime.check_guardrails(question, request, mode)
            state['guardrail_state'] = guardrail_state
            state['guardrail_passed'] = not guardrail_state.get('blocked', False)
            state['question'] = question
        else:
            state['guardrail_passed'] = True

        return state


class RewriteStage(BaseStage):
    """查询改写阶段。

    对原始查询做改写/扩展，提升检索召回效果。
    改写后的查询存入 rewritten_query 供后续检索使用。
    """

    def __init__(self) -> None:
        super().__init__(
            name='rewrite',
            description='对原始查询做改写/扩展，提升检索效果',
            requires_policy_check=False,
        )

    def run(self, state: dict, ctx) -> dict:
        runtime = ctx.get('runtime')
        request = state.get('request')
        question = state.get('question') or ''

        if runtime and hasattr(runtime, 'resolve_rewrite_info'):
            use_rewrite = getattr(request, 'use_query_rewrite', True) if request else True
            mode = state.get('mode', 'query')
            retrieval_question, rewrite_info = runtime.resolve_rewrite_info(
                question, use_rewrite, mode,
            )
            state['retrieval_question'] = retrieval_question
            state['rewrite_info'] = rewrite_info
        else:
            state['retrieval_question'] = question

        return state


class RetrieveEvidenceStage(BaseStage):
    """证据检索阶段。

    执行混合检索及图检索，获取相关证据项。
    结果写入 evidence 和 citations 字段。
    """

    def __init__(self) -> None:
        super().__init__(
            name='retrieve_evidence',
            description='执行混合检索及图检索，获取相关证据',
            allowed_tools=['rag_retrieve_evidence', 'rag_retrieve_graph_evidence'],
            creates_checkpoint_after=True,
        )

    def run(self, state: dict, ctx) -> dict:
        execution = ctx.get('execution_harness')
        runtime = ctx.get('runtime')
        request = state.get('request')
        query = state.get('retrieval_question') or state.get('question') or ''

        if execution and hasattr(execution, 'run_tool'):
            try:
                result = execution.run_tool(
                    name='rag_retrieve_evidence',
                    payload={
                        'query': query,
                        'collection_name': getattr(request, 'collection_name', 'default') if request else 'default',
                        'doc_ids': [],
                        'top_k': getattr(request, 'top_k', 5) if request else 5,
                    },
                    workflow_state=state,
                    context_bundle=None,
                )
                from app.models.artifact import EvidencePack

                if isinstance(result, EvidencePack):
                    state['evidence_pack'] = result
                    citations = self._citations_from_evidence_pack(result)
                    state['citations'] = citations
            except Exception:
                pass

        if 'citations' not in state and runtime and hasattr(runtime, 'retrieve_citations'):
            citations = runtime.retrieve_citations(
                request,
                [query],
                query,
            )
            state['citations'] = citations

        return state

    @staticmethod
    def _citations_from_evidence_pack(evidence_pack: Any) -> list[dict[str, Any]]:
        """从 EvidencePack 提取 CitationItem 列表。"""
        citations: list[dict[str, Any]] = []
        for item in evidence_pack.evidence_items:
            citations.append({
                'chunk_id': item.chunk_id,
                'source': item.source,
                'page': item.page,
                'score': item.support_score,
                'text': item.text,
            })
        return citations


class GroundedAnswerStage(BaseStage):
    """基于证据生成 grounded answer 阶段。

    结合检索到的证据，生成有据可依的回答。
    若没有证据，给出兜底回答。
    """

    def __init__(self) -> None:
        super().__init__(
            name='grounded_answer',
            description='结合检索到的证据，生成有据可依的回答',
            allowed_tools=['rag_grounded_answer'],
        )

    def run(self, state: dict, ctx) -> dict:
        runtime = ctx.get('runtime')
        request = state.get('request')
        citations = state.get('citations') or []

        if not citations:
            state['answer'] = '未找到足够依据来回答该问题，请尝试补充文档、放宽筛选条件或换一种问法。'
            state['answer_mode'] = 'no_context'
            return state

        if runtime and hasattr(runtime, 'generate_answer_with_mode'):
            question = state.get('question') or ''
            collection_name = getattr(request, 'collection_name', 'default') if request else 'default'
            contexts = [c.get('text', '') for c in citations]
            prompt = runtime.build_qa_prompt(
                question, contexts,
                use_guardrails=state.get('guardrail_state', {}).get('prompt_guardrails_enabled', False),
            )
            answer, answer_mode = runtime.generate_answer_with_mode(
                question=question,
                prompt=prompt,
                citations=citations,
                collection_name=collection_name,
            )
            state['answer'] = answer
            state['answer_mode'] = answer_mode
            state['prompt'] = prompt

        return state


class ReflectionStage(BaseStage):
    """反思评估阶段。

    对生成结果做质量评估，决定是否需要补充检索。
    若 needs_retry 为 True，路由回 retrieve_evidence；
    否则进入 finalize。
    """

    def __init__(self) -> None:
        super().__init__(
            name='reflection',
            description='对生成结果做质量评估，决定是否需要补充检索',
            creates_checkpoint_after=True,
            route_targets=['retrieve_evidence', 'finalize'],
        )

    def run(self, state: dict, ctx) -> dict:
        reflection = ctx.get('reflection_harness')
        request = state.get('request')

        if reflection and hasattr(reflection, 'build_query_reflection_decision'):
            retry_count = state.get('retry_count', 0)
            max_retry_count = state.get('max_retry_count', 0)

            decision = reflection.build_query_reflection_decision(
                request=request,
                corrective_info=state.get('corrective_info', {}),
                retry_count=retry_count,
                max_retry_count=max_retry_count,
                min_grounding_confidence=0.65,
            )
            state['needs_retry'] = getattr(decision, 'needs_retry', False)
            state['reflection_decision'] = decision.model_dump() if hasattr(decision, 'model_dump') else decision
            state['retry_count'] = retry_count + 1
        else:
            state['needs_retry'] = False

        return state

    def route_next(self, state_payload: dict) -> str:
        if state_payload.get('needs_retry'):
            return 'retrieve_evidence'
        return 'finalize'


class FinalizeStage(BaseStage):
    """结果收尾阶段。

    组装最终响应和证据包，写入 result 字段。
    """

    def __init__(self) -> None:
        super().__init__(
            name='finalize',
            description='组装最终响应和证据包',
            creates_checkpoint_after=True,
        )

    def run(self, state: dict, ctx) -> dict:
        from app.models.query import QueryResponse

        citations = state.get('citations', [])
        state['result'] = QueryResponse(
            answer=state.get('answer', ''),
            citations=citations,
            retrieved_count=len(citations),
            latency_ms=0,
            evidence=state.get('evidence_pack'),
        ).model_dump(mode='json')
        return state


class QueryRecipe(BaseRecipe):
    """标准 query 工作流 recipe。

    guardrail → rewrite → retrieve_evidence → grounded_answer → reflection → finalize
    其中 reflection 可能路由回 retrieve_evidence 做重试。
    """

    name = 'query'
    description = '标准问答工作流：安全检查 → 改写 → 检索 → 回答 → 反思 → 收尾'
    task_type = 'query'
    version = 'v1'

    def __init__(self) -> None:
        super().__init__(stages=[
            GuardrailStage(),
            RewriteStage(),
            RetrieveEvidenceStage(),
            GroundedAnswerStage(),
            ReflectionStage(),
            FinalizeStage(),
        ])


class ChatRecipe(BaseRecipe):
    """带会话上下文的 chat 工作流 recipe。

    guardrail → retrieve_evidence → grounded_answer → finalize
    """

    name = 'chat'
    description = '多轮会话工作流：安全检查 → 检索 → 回答 → 收尾'
    task_type = 'chat'
    version = 'v1'

    def __init__(self) -> None:
        super().__init__(stages=[
            GuardrailStage(),
            RetrieveEvidenceStage(),
            GroundedAnswerStage(),
            FinalizeStage(),
        ])