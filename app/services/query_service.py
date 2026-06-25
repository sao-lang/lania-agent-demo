"""查询服务模块。

负责向 API 层暴露稳定的查询、会话和流式输出入口，并把实际执行委托给底层查询执行器。
该模块本身逻辑较轻，主要价值在于隔离接口依赖，方便替换编排实现或注入测试桩。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from app.models.query import (
    ChatRequest,
    QueryRequest,
    QueryResponse,
    QueryRunAnalytics,
    QueryRunDetail,
    QueryRunSummary,
)
from app.models.session import SessionDetail, SessionSummaryItem, SessionSummaryResponse
from app.types import SSEEvent


class QueryExecutor(Protocol):
    """约束 `QueryService` 依赖的最小查询接口。"""

    def query(self, payload: QueryRequest) -> QueryResponse:
        """执行单轮查询请求。"""

        ...

    def chat(self, payload: ChatRequest) -> QueryResponse:
        """执行多轮会话查询请求。"""

        ...

    def stream_query(self, payload: QueryRequest) -> Iterator[SSEEvent]:
        """按 SSE 事件流输出单轮查询结果。"""

        ...

    def stream_chat(self, payload: ChatRequest) -> Iterator[SSEEvent]:
        """按 SSE 事件流输出会话查询结果。"""

        ...

    def get_session(self, session_id: str) -> SessionDetail | None:
        """读取指定会话详情。"""

        ...

    def list_sessions(self) -> list[SessionSummaryItem]:
        """列出当前全部会话摘要。"""

        ...

    def summarize_session(self, session_id: str) -> SessionSummaryResponse | None:
        """生成或刷新指定会话摘要。"""

        ...

    def list_query_runs(self, *, limit: int = 20, offset: int = 0) -> list[QueryRunSummary]:
        """列出 query runtime 历史摘要。"""

        ...

    def get_query_run(self, run_id: str) -> QueryRunDetail | None:
        """读取单个 query runtime 详情。"""

        ...

    def replay_query_run(self, run_id: str, checkpoint_id: str | None = None) -> QueryRunDetail:
        """从给定 checkpoint 重放 query runtime。"""

        ...

    def resume_query_run(self, run_id: str) -> QueryRunDetail:
        """恢复一个处于可恢复状态的 query runtime。"""

        ...

    def recover_query_runs(self, *, limit: int = 20, auto_resume: bool = False) -> list[QueryRunDetail]:
        """扫描并可选恢复一批可恢复的 query runtime。"""

        ...

    def get_query_run_analytics(self, *, collection_name: str | None = None) -> QueryRunAnalytics:
        """读取 query runtime 聚合统计结果。"""

        ...


class QueryService:
    """对底层查询执行器做轻量封装，供 API 层直接调用。"""

    def __init__(self, query_engine: QueryExecutor) -> None:
        """保存底层查询执行器实例。

        Args:
            query_engine: 满足 `QueryExecutor` 协议的查询执行器实现。
        """
        self.query_engine = query_engine

    def query(self, payload: QueryRequest) -> QueryResponse:
        """执行单轮检索问答。

        Args:
            payload: 单轮问答请求模型。

        Returns:
            查询执行后的标准响应模型。
        """
        return self.query_engine.query(payload)

    def chat(self, payload: ChatRequest) -> QueryResponse:
        """执行带会话上下文的问答。

        Args:
            payload: 多轮会话请求模型。

        Returns:
            带会话信息的标准响应模型。
        """
        return self.query_engine.chat(payload)

    def stream_query(self, payload: QueryRequest) -> Iterator[SSEEvent]:
        """按流式事件输出单轮问答结果。

        Args:
            payload: 单轮问答请求模型。

        Returns:
            SSE 事件迭代器，供 API 层编码为流式响应。
        """
        return self.query_engine.stream_query(payload)

    def stream_chat(self, payload: ChatRequest) -> Iterator[SSEEvent]:
        """按流式事件输出多轮会话结果。

        Args:
            payload: 多轮会话请求模型。

        Returns:
            SSE 事件迭代器，供 API 层编码为流式响应。
        """
        return self.query_engine.stream_chat(payload)

    def get_session(self, session_id: str) -> SessionDetail | None:
        """查询指定会话的当前详情。

        Args:
            session_id: 会话唯一标识。

        Returns:
            会话存在时返回详情模型，否则返回 `None`。
        """
        return self.query_engine.get_session(session_id)

    def list_sessions(self) -> list[SessionSummaryItem]:
        """返回当前会话列表。

        Returns:
            按底层执行器定义返回的会话摘要列表。
        """
        return self.query_engine.list_sessions()

    def summarize_session(self, session_id: str) -> SessionSummaryResponse | None:
        """生成或刷新指定会话摘要。

        Args:
            session_id: 需要生成摘要的会话 ID。

        Returns:
            生成成功时返回会话摘要响应，否则返回 `None`。
        """
        return self.query_engine.summarize_session(session_id)

    def list_query_runs(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        mode: str | None = None,
        collection_name: str | None = None,
        recoverable_only: bool = False,
    ) -> list[QueryRunSummary]:
        """列出 query runtime 历史摘要。

        Args:
            limit: 返回数量上限。
            offset: 分页偏移量。
            status: 可选运行状态过滤条件。
            mode: 可选查询模式过滤条件。
            collection_name: 可选集合名称过滤条件。
            recoverable_only: 是否仅返回可恢复运行。

        Returns:
            query runtime 摘要列表。
        """
        return self.query_engine.list_query_runs(
            limit=limit,
            offset=offset,
            status=status,
            mode=mode,
            collection_name=collection_name,
            recoverable_only=recoverable_only,
        )

    def get_query_run(self, run_id: str) -> QueryRunDetail | None:
        """读取单个 query runtime 详情。

        Args:
            run_id: query runtime 唯一标识。

        Returns:
            运行存在时返回详情模型，否则返回 `None`。
        """
        return self.query_engine.get_query_run(run_id)

    def replay_query_run(self, run_id: str, checkpoint_id: str | None = None) -> QueryRunDetail:
        """从指定 query runtime checkpoint 发起重放。

        Args:
            run_id: query runtime 唯一标识。
            checkpoint_id: 可选 checkpoint 标识；为空时由底层决定起点。

        Returns:
            重放后的 query runtime 详情。
        """
        return self.query_engine.replay_query_run(run_id, checkpoint_id=checkpoint_id)

    def resume_query_run(self, run_id: str) -> QueryRunDetail:
        """恢复一个可恢复的 query runtime。

        Args:
            run_id: query runtime 唯一标识。

        Returns:
            恢复后的 query runtime 详情。
        """
        return self.query_engine.resume_query_run(run_id)

    def recover_query_runs(self, *, limit: int = 20, auto_resume: bool = False) -> list[QueryRunDetail]:
        """批量扫描或恢复可恢复的 query runtime。

        Args:
            limit: 最多处理的运行数量。
            auto_resume: 是否在扫描后自动执行恢复。

        Returns:
            扫描到或恢复后的 query runtime 详情列表。
        """
        return self.query_engine.recover_query_runs(limit=limit, auto_resume=auto_resume)

    def get_query_run_analytics(self, *, collection_name: str | None = None) -> QueryRunAnalytics:
        """读取 query runtime 聚合统计。

        Args:
            collection_name: 可选集合名称过滤条件。

        Returns:
            聚合后的 query runtime 统计信息。
        """
        return self.query_engine.get_query_run_analytics(collection_name=collection_name)
