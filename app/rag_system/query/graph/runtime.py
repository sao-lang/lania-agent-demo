"""RAG 系统查询工作流运行时协议。

定义 ``RagGraphRuntime`` 协议，描述 LangGraph 查询工作流节点
对 RAG 引擎的最小依赖契约。rag_system 中的 ``RagQueryEngine`` 可通过适配
该协议被 LangGraph 工作流使用。
"""

from __future__ import annotations

from typing import Any, Protocol

from app.rag_system.models.query import CitationItem, QueryResponse


class RagGraphRuntime(Protocol):
    """RAG 图工作流运行时协议。

    ``RagQueryEngine`` 只需实现此协议中声明的方法即可被 ``QueryGraphNodes`` 使用。
    该协议只包含 RAG 核心能力（护栏、缓存、检索、改写、答案生成），
    不包含主应用 harness 的工具执行/ReAct/策略引擎等能力。

    方法按职责分为五组：
    - 护栏与脱敏
    - 语义缓存
    - 查询改写与检索增强
    - 证据准备与回答生成
    - 会话与会话持久化
    """

    state: Any
    settings: Any
    retrieval_service: Any
    llm: Any
    knowledge_capability: Any | None

    # ── 护栏与脱敏 ─────────────────────────────────────────

    def check_guardrails(self, question: str, payload: Any, trace_context: str) -> dict[str, Any]:
        """执行输入护栏检查，返回结构化护栏状态。"""
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
        """对文本做脱敏，返回(脱敏文本, 脱敏状态)。"""
        ...

    def sanitize_citations(
        self,
        citations: list[CitationItem],
        payload: Any,
        trace_context: str,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """对引用项做脱敏/裁剪，返回(公开引用列表, 脱敏状态)。"""
        ...

    def question_for_storage(self, question: str, guardrail_state: dict[str, Any]) -> str:
        """根据护栏结果生成适合落库保存的问题文本。"""
        ...

    def guardrail_block_message(self) -> str:
        """返回护栏拦截时的标准提示语。"""
        ...

    def build_blocked_query_response(
        self,
        payload: Any,
        guardrail_state: dict[str, Any],
    ) -> QueryResponse:
        """构造被拦截场景下的标准查询响应。"""
        ...

    def public_guardrail_state(
        self,
        guardrail_state: dict[str, Any],
        citation_redaction: dict[str, Any] | None = None,
        answer_redaction: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把内部护栏状态裁剪为可对外暴露的公开字段。"""
        ...

    # ── 语义缓存 ───────────────────────────────────────────

    def lookup_semantic_cache(
        self,
        payload: Any,
        question: str,
        cache_mode: str,
    ) -> tuple[QueryResponse | None, dict[str, Any]]:
        """查询语义缓存，返回(命中响应, 命中元数据)。"""
        ...

    def store_semantic_cache(
        self,
        payload: Any,
        *,
        question: str,
        cache_mode: str,
        response: QueryResponse,
        answer_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """将最终响应写入语义缓存。"""
        ...

    # ── 查询改写与检索增强 ─────────────────────────────────

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
        """返回改写后的检索问题及改写元数据。"""
        ...

    def maybe_apply_multi_rewrite(
        self,
        payload: Any,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """生成多组候选检索问题（多改写）。"""
        ...

    def maybe_apply_multi_query(
        self,
        payload: Any,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[list[str], dict[str, Any] | None]:
        """生成多路检索问题列表（多查询）。"""
        ...

    def maybe_apply_hyde(
        self,
        payload: Any,
        retrieval_question: str,
        answer_question: str,
        trace_context: str,
    ) -> tuple[str, dict[str, Any] | None]:
        """在启用 HyDE 时生成假设文档查询。"""
        ...

    def empty_corrective_info(self) -> dict[str, Any]:
        """返回空的 Corrective RAG 信息字典。"""
        ...

    # ── 证据准备与回答生成 ─────────────────────────────────

    def prepare_answer_context(
        self,
        question: str,
        citations: list[CitationItem],
        payload: Any,
    ) -> tuple[list[str], dict[str, Any]]:
        """把引用列表整理为回答阶段可消费的上下文片段。"""
        ...

    def use_context_compression(self, payload: Any) -> bool:
        """判断是否启用上下文压缩。"""
        ...

    def use_pii_redaction(self, payload: Any) -> bool:
        """判断是否启用 PII 脱敏。"""
        ...

    def build_chat_retrieval_question(self, session_id: str, current_question: str) -> str:
        """基于会话历史构造聊天模式下的检索问题。"""
        ...

    def retrieve_citations(
        self,
        payload: Any,
        retrieval_questions: list[str],
        answer_question: str,
    ) -> list[CitationItem]:
        """执行证据检索，返回结构化引用列表。"""
        ...

    def stream_citation_snapshot(
        self,
        citations: list[CitationItem],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """生成用于流式输出的引用快照摘要。"""
        ...

    def build_qa_prompt(
        self,
        question: str,
        contexts: list[str],
        *,
        use_guardrails: bool = True,
    ) -> str:
        """构建问答 prompt。"""
        ...

    def generate_answer(
        self,
        prompt: str,
        *,
        stream: bool = False,
    ) -> str | list[str]:
        """执行 LLM 问答生成，返回答案文本或流式数据块列表。"""
        ...

    def maybe_apply_corrective_rag(
        self,
        payload: Any,
        question: str,
        answer: str,
        answer_mode: str,
        citations: list[CitationItem],
        collection_name: str,
    ) -> tuple[str, str, dict[str, Any]]:
        """应用 Corrective RAG 自检与改写，返回(修正答案, 答案模式, 修正信息)。"""
        ...

    # ── 会话与会话持久化 ───────────────────────────────────

    def save_session(self, session_id: str) -> None:
        """持久化当前会话。"""
        ...

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        """加载指定会话。"""
        ...
