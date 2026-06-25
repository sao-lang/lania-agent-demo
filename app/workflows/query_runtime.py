"""查询工作流运行时适配模块。

负责把 `RagQueryEngine` 对外暴露的分散能力整理成 query workflow 可稳定依赖的运行时接口。
该模块的核心目标是隔离节点层与经典查询引擎的直接耦合，避免图节点在访问护栏、缓存、检索、
回答生成与会话持久化能力时，继续依赖 engine 内部私有实现细节。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.models.query import CitationItem, QueryRequest, QueryResponse
    from app.rag.query_engine import RagQueryEngine
    from app.types import SessionMessageRecord, SessionRecord


class QueryWorkflowRuntime(Protocol):
    """定义 query workflow 可依赖的最小运行时契约。

    这个协议不是为了完整复刻 `RagQueryEngine`，而是为了声明 workflow 节点真正会调用的那一
    小组能力。接口按职责大致分为：

    - 护栏与脱敏：输入校验、文本脱敏、引用脱敏。
    - 缓存与改写：语义缓存命中、多路改写、多路检索与 HyDE。
    - 检索与回答：证据检索、上下文准备、答案生成与 Corrective RAG。
    - 会话与流式输出：消息构造、会话读写、分块流式输出。
    """

    state: Any
    settings: Any
    retrieval_service: Any
    llm: Any
    knowledge_capability: Any | None

    # 输入护栏与脱敏能力：负责在真正检索前做拦截、改写与公开态输出。
    def check_guardrails(self, question: str, payload: Any, trace_context: str) -> dict[str, Any]:
        """执行输入护栏检查，并返回结构化护栏状态。"""
        ...
    def empty_redaction_state(self, enabled: bool) -> dict[str, Any]:
        """构造一份空的脱敏状态字典。"""
        ...
    def sanitize_text(
        self,
        text: str,
        payload: Any,
        *,
        target: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any]]:
        """对问题、上下文或答案文本做脱敏，并返回更新后的脱敏状态。"""
        ...
    def sanitize_citations(
        self,
        citations: list["CitationItem"],
        payload: Any,
        trace_context: str,
    ) -> tuple[list["CitationItem"], dict[str, Any]]:
        """对引用项做脱敏或裁剪，并返回公开可展示的引用列表。"""
        ...
    def question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        """根据护栏结果生成适合落库保存的问题文本。"""
        ...
    def guardrail_block_message(self) -> str:
        """返回护栏拦截时面向用户的标准提示语。"""
        ...
    def build_blocked_query_response(
        self,
        payload: Any,
        guardrail_state: dict[str, Any],
    ) -> "QueryResponse":
        """基于护栏状态构造被拦截场景下的标准查询响应。"""
        ...
    def public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把内部护栏状态裁剪为可对外暴露的公开字段。"""
        ...

    # 语义缓存：在命中时可直接短路回答链路，未命中则在收尾阶段回写。
    def lookup_semantic_cache(
        self,
        payload: Any,
        question: str,
        cache_mode: str,
    ) -> tuple["QueryResponse" | None, dict[str, Any]]:
        """查询语义缓存，并返回命中响应及命中元数据。"""
        ...
    def store_semantic_cache(
        self,
        payload: Any,
        *,
        question: str,
        cache_mode: str,
        response: "QueryResponse",
        answer_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """把最终响应写入语义缓存，供后续相似问题复用。"""
        ...

    # 查询改写与检索增强：负责把原始问题展开成更适合召回的查询集合。
    def graph_trace_flags(self, payload: Any) -> dict[str, Any]:
        """提取图检索相关的追踪标志位。"""
        ...
    def prepare_retrieval_question(self, question: str, use_query_rewrite: bool, trace_context: str) -> str:
        """生成用于检索的主问题文本。"""
        ...
    def resolve_rewrite_info(
        self,
        question: str,
        use_query_rewrite: bool,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """返回改写后的检索问题及对应改写元数据。"""
        ...
    def maybe_apply_multi_rewrite(
        self,
        payload: Any,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """在启用多改写时生成多组候选检索问题。"""
        ...
    def maybe_apply_multi_query(
        self,
        payload: Any,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """在启用多查询时生成多路检索问题列表。"""
        ...
    def maybe_apply_hyde(
        self,
        payload: Any,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """在启用 HyDE 时生成假设文档查询及其元数据。"""
        ...
    def empty_corrective_info(self) -> dict[str, Any]:
        """返回空的 Corrective RAG 信息字典。"""
        ...

    # 证据准备与回答生成：负责上下文拼装、引用快照、最终回答与 Corrective RAG。
    def prepare_answer_context(
        self,
        question: str,
        citations: list["CitationItem"],
        payload: Any,
    ) -> tuple[list[str], dict[str, Any]]:
        """把引用列表整理为回答阶段可直接消费的上下文片段。"""
        ...
    def use_context_compression(self, payload: Any) -> bool:
        """判断当前请求是否启用上下文压缩。"""
        ...
    def use_pii_redaction(self, payload: Any) -> bool:
        """判断当前请求是否启用 PII 脱敏。"""
        ...
    def build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        """基于会话历史构造聊天模式下的检索问题。"""
        ...
    def retrieve_citations(
        self,
        payload: Any,
        retrieval_questions: list[str],
        answer_question: str,
    ) -> list["CitationItem"]:
        """执行证据检索，并返回结构化引用列表。"""
        ...
    def stream_citation_snapshot(
        self,
        citations: list["CitationItem"],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """抽取适合流式事件发送的引用快照。"""
        ...
    def generate_answer_with_mode(
        self,
        *,
        question: str,
        prompt: str,
        citations: list["CitationItem"],
        collection_name: str,
    ) -> tuple[str, str]:
        """生成最终答案，并返回答案模式。"""
        ...
    def maybe_apply_corrective_rag(
        self,
        *,
        payload: Any,
        question: str,
        answer: str,
        answer_mode: str,
        citations: list["CitationItem"],
        collection_name: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """按需执行 Corrective RAG，并返回修正后的答案、模式和决策信息。"""
        ...

    # 会话持久化与流式输出：负责聊天模式下的消息落库、摘要和 chunk 输出。
    def message(self, role: str, content: str) -> "SessionMessageRecord":
        """构造一条会话消息记录。"""
        ...
    def get_or_create_session(self, session_id: str) -> "SessionRecord":
        """读取或初始化一个会话记录。"""
        ...
    def save_session(self, session_id: str) -> None:
        """持久化指定会话。"""
        ...
    def auto_summarize_session(self, session_id: str) -> None:
        """在需要时自动触发会话摘要压缩。"""
        ...
    def chunk_text_for_stream(self, text: str, chunk_size: int = 24) -> list[str]:
        """把长答案切成适合 SSE 输出的小文本块。"""
        ...
    def build_qa_prompt(self, question: str, contexts: list[str], *, use_guardrails: bool) -> str:
        """基于问题和上下文构造回答提示词。"""
        ...
    def self_rag_retry_enabled(self) -> bool:
        """返回当前运行时是否允许 Self-RAG 重试。"""
        ...
    def self_rag_min_grounding_confidence(self) -> float:
        """返回触发 Self-RAG 决策时使用的最小 grounding 置信度阈值。"""
        ...


class QueryEngineWorkflowRuntimeAdapter:
    """把 `RagQueryEngine` 适配为 query workflow 稳定运行时。

    当经典查询引擎仍然是 workflow 的真实能力提供者时，这个适配器负责补齐 workflow 需要的
    访问入口，并把部分公开方法与历史私有方法做兼容映射，减少迁移期间的接口抖动。
    换句话说，它是 workflow 层和历史 `RagQueryEngine` 之间的一层兼容壳。
    """

    def __init__(self, engine: "RagQueryEngine") -> None:
        """缓存 workflow 常用依赖，降低节点访问成本。

        Args:
            engine: 经典查询引擎实例，也是底层真实能力提供者。
        """
        self._engine = engine
        self.state = getattr(engine, 'state', None)
        self.settings = getattr(engine, 'settings', None)
        self.retrieval_service = getattr(engine, 'retrieval_service', None)
        self.llm = getattr(engine, 'llm', None)
        self.knowledge_capability = getattr(engine, 'knowledge_capability', None)

    def __getattr__(self, name: str) -> Any:
        """把未显式适配的方法透传给底层 engine。

        这里优先尝试公开属性名；若不存在，再回退到历史 `_name` 私有方法，兼容旧版
        `RagQueryEngine` 仍未完全公开的方法实现。
        这样 workflow 迁移可以渐进进行，而不用一次性公开 engine 的全部 helper。
        """
        try:
            return getattr(self._engine, name)
        except AttributeError:
            legacy_name = f'_{name}'
            return getattr(self._engine, legacy_name)

    def build_qa_prompt(self, question: str, contexts: list[str], *, use_guardrails: bool) -> str:
        """委托答案服务构造问答提示词。"""
        return self._engine.answer_service.build_qa_prompt(
            question,
            contexts,
            use_guardrails=use_guardrails,
        )

    def graph_trace_flags(self, payload: Any) -> dict[str, Any]:
        """读取 workflow graph 追踪开关。

        部分 engine 版本公开了 `graph_trace_flags`，部分仍保留为私有 helper，这里统一兼容。
        """
        helper = getattr(self._engine, 'graph_trace_flags', None) or getattr(self._engine, '_graph_trace_flags', None)
        if helper is None:
            return {}
        return helper(payload)

    def self_rag_retry_enabled(self) -> bool:
        """返回当前配置下是否启用 Self-RAG 重试。"""
        return bool(getattr(self.settings, 'enable_self_rag_retry', False))

    def self_rag_min_grounding_confidence(self) -> float:
        """返回触发 Self-RAG 保守补救所需的最小 grounding 置信度阈值。"""
        return float(getattr(self.settings, 'self_rag_min_grounding_confidence', 0.65))


def ensure_query_workflow_runtime(
    runtime_or_engine: QueryWorkflowRuntime | "RagQueryEngine",
) -> QueryWorkflowRuntime:
    """确保 query workflow 始终拿到稳定 runtime。

    Args:
        runtime_or_engine: 已满足协议的 runtime，或尚未适配的经典查询引擎。

    Returns:
        满足 `QueryWorkflowRuntime` 契约的运行时对象。
    """
    # 已经是适配器时直接返回，避免重复包裹。
    if isinstance(runtime_or_engine, QueryEngineWorkflowRuntimeAdapter):
        return runtime_or_engine
    # 若对象已经具备 workflow 依赖的关键能力，则视为满足协议。
    if all(
        hasattr(runtime_or_engine, attr)
        for attr in (
            'build_qa_prompt',
            'self_rag_retry_enabled',
            'self_rag_min_grounding_confidence',
            'check_guardrails',
            'graph_trace_flags',
        )
    ):
        return runtime_or_engine
    # 其余情况统一退回经典 engine 适配器。
    return QueryEngineWorkflowRuntimeAdapter(runtime_or_engine)
