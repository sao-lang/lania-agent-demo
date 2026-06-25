"""反馈模型模块。

负责定义用户反馈采集、评测候选筛选、反馈评测任务生成与对比分析相关的数据模型，作为
反馈 API、反馈服务和评测服务之间共享的数据契约。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.eval import EvalStrategyConfig, EvalTaskResponse, RagasCompareResponse
from app.models.query import CitationItem


# 反馈类型在反馈采集、候选筛选和评测样本导出三层之间共享。
# 单独抽成字面量别名，一方面便于多个模型共用，另一方面也能让反馈类型约束保持一致。
FeedbackType = Literal['upvote', 'downvote', 'correction']


class FeedbackCreateRequest(BaseModel):
    """创建反馈时提交的请求体。

    用于提交点赞、点踩或纠正类反馈，并可附带会话与引用信息。
    它是在线反馈入口的最小写入模型，也是后续生成评测候选样本的原始来源。
    """

    # 反馈主体信息。
    feedback_type: FeedbackType
    collection_name: str
    question: str
    answer: str
    session_id: str | None = None

    # 对纠正型反馈或补充说明的扩展信息。
    correction: str | None = None
    note: str | None = None
    citations: list[CitationItem] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class FeedbackItem(BaseModel):
    """反馈记录的完整表示。

    既用于持久化保存，也用于反馈列表查询返回。
    相比创建请求，这里补齐了持久化标识、评测候选生成状态和创建时间。
    """

    feedback_id: str
    feedback_type: FeedbackType
    collection_name: str
    question: str
    answer: str
    session_id: str | None = None
    correction: str | None = None
    note: str | None = None
    citations: list[CitationItem] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    eval_candidate_created: bool = False
    created_at: datetime


class FeedbackCreateResponse(BaseModel):
    """创建反馈后的返回结果。

    用于告诉调用方反馈已成功入库，以及是否顺带生成了评测候选。
    """

    feedback_id: str
    eval_candidate_created: bool
    created_at: datetime


class FeedbackListResponse(BaseModel):
    """反馈列表响应。

    统一封装列表数据与分页元信息。
    各类筛选接口都复用这一层包装，避免分页协议分散在不同接口里。
    """

    items: list[FeedbackItem]
    total: int
    limit: int
    offset: int


class EvalCandidateItem(BaseModel):
    """可用于评测的数据候选项。

    该模型把在线反馈转成离线评测更容易消费的标准样本结构。
    重点在于把用户问题、参考答案、原回答和引用放到同一个样本实体中。
    """

    candidate_id: str
    feedback_id: str
    collection_name: str
    bucket: str | None = None
    question: str
    reference: str
    answer: str
    feedback_type: FeedbackType
    citations: list[CitationItem] = Field(default_factory=list)
    note: str | None = None
    created_at: datetime


class EvalCandidateListResponse(BaseModel):
    """评测候选列表响应。"""

    items: list[EvalCandidateItem]
    total: int
    limit: int
    offset: int


class FeedbackEvalRunRequest(BaseModel):
    """从反馈数据生成评测任务时的筛选与策略参数。

    用于控制候选样本筛选范围，以及导出评测数据集时固化的查询策略开关。
    该请求同时承担“样本筛选”和“评测策略固化”两种职责。
    """

    collection_name: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    feedback_types: list[FeedbackType] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=500)
    top_k: int = Field(default=5, ge=1)
    use_query_rewrite: bool = True
    use_hybrid_retrieval: bool = False
    use_rerank: bool = True
    dataset_name: str | None = None


class FeedbackEvalDatasetResponse(BaseModel):
    """反馈导出评测数据集后的返回结果。

    该响应主要面向“先导出、后执行”的两阶段评测流程。
    当调用方只想拿到数据集文件做离线处理时，会优先消费这个模型。
    """

    dataset_path: str
    candidate_count: int
    collection_name: str
    candidate_ids: list[str] = Field(default_factory=list)
    generated_at: datetime


class FeedbackEvalRunResponse(BaseModel):
    """反馈评测任务创建结果。

    在数据集导出结果之外，再补充真正提交出去的评测任务信息。
    适用于“一次请求内完成导出并触发评测”的场景。
    """

    dataset_path: str
    candidate_count: int
    collection_name: str
    candidate_ids: list[str] = Field(default_factory=list)
    task: EvalTaskResponse


class FeedbackEvalCompareRequest(FeedbackEvalRunRequest):
    """反馈评测对比任务请求体。

    在普通反馈评测任务请求基础上追加多策略对比参数。
    """

    strategies: list[EvalStrategyConfig] = Field(default_factory=list)
    baseline_name: str | None = None


class FeedbackEvalCompareResponse(BaseModel):
    """反馈评测对比结果。

    用于承载基于反馈样本发起的多策略对比评测结果。
    外层保留数据集与候选信息，内层 `comparison` 则复用通用的评测对比结果模型。
    """

    dataset_path: str
    candidate_count: int
    collection_name: str
    candidate_ids: list[str] = Field(default_factory=list)
    comparison: RagasCompareResponse
