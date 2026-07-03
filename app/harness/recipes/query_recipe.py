"""Query 工作流的 Recipe 定义。

以声明式 Recipe + Stage 方式组织 query/chat 的标准执行流程，
取代当前 orchestrator 中分散的编排逻辑。
"""

from __future__ import annotations

from app.harness.core.recipe import BaseRecipe
from app.harness.core.stage import BaseStage


class GuardrailStage(BaseStage):
    """输入安全检查阶段。"""

    name = 'guardrail'
    description = '检查用户输入是否安全、是否命中 guardrail 规则'
    requires_policy_check = True
    requires_guardrail = True

    def run(self, state: dict, ctx) -> dict:
        # 实际 guardrail 由 ExecutionHarness 处理，stage 只需标记状态
        state['guardrail_passed'] = True
        return state


class RewriteStage(BaseStage):
    """查询改写阶段。"""

    name = 'rewrite'
    description = '对原始查询做改写/扩展，提升检索效果'
    requires_policy_check = False

    def run(self, state: dict, ctx) -> dict:
        state['query_rewritten'] = True
        return state


class RetrieveEvidenceStage(BaseStage):
    """证据检索阶段。"""

    name = 'retrieve_evidence'
    description = '执行混合检索及图检索，获取相关证据'
    allowed_tools = ['rag_retrieve_evidence', 'rag_retrieve_graph_evidence']

    def run(self, state: dict, ctx) -> dict:
        # 检索逻辑由外部 executor 处理
        return state


class GroundedAnswerStage(BaseStage):
    """基于证据生成 grounded answer 阶段。"""

    name = 'grounded_answer'
    description = '结合检索到的证据，生成有据可依的回答'
    allowed_tools = ['rag_grounded_answer']

    def run(self, state: dict, ctx) -> dict:
        return state


class ReflectionStage(BaseStage):
    """反思评估阶段。"""

    name = 'reflection'
    description = '对生成结果做质量评估，决定是否需要补充检索'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        return state


class FinalizeStage(BaseStage):
    """结果收尾阶段。"""

    name = 'finalize'
    description = '组装最终响应和证据包'
    creates_checkpoint_after = True

    def run(self, state: dict, ctx) -> dict:
        return state


class QueryRecipe(BaseRecipe):
    """标准 query 工作流 recipe。"""

    name = 'query'
    description = '标准问答工作流：安全检查 → 检索 → 回答 → 反思 → 收尾'
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
    """带会话上下文的 chat 工作流 recipe。"""

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