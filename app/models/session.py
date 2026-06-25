"""会话模型模块。

负责定义聊天会话中的消息、会话详情和摘要响应模型，用于查询引擎、会话 API 和前端展示之间
共享统一的数据结构。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SessionMessage(BaseModel):
    """单条会话消息。

    表示会话历史中的一条用户或助手消息。
    这里只保留角色、正文和创建时间，刻意不混入检索或工具调用细节，避免会话层过度耦合
    query/runtime 的内部实现。
    """

    role: str
    content: str
    created_at: datetime


class SessionDetail(BaseModel):
    """会话详情及其消息列表。

    用于会话详情页和会话恢复场景。
    它在摘要元数据之外还携带完整消息序列，因此是读取单会话时最完整的展示模型。
    """

    # 会话总体信息与摘要状态。
    session_id: str
    message_count: int
    summary: str | None = None
    summary_updated_at: datetime | None = None
    compressed_message_count: int = 0
    updated_at: datetime | None = None

    # 具体的会话消息列表，按存储顺序返回。
    messages: list[SessionMessage]


class SessionSummaryItem(BaseModel):
    """会话列表中的摘要项。

    用于列表页快速展示会话状态，而无需返回完整消息内容。
    相比 `SessionDetail`，这里强调轻量列表展示与最近状态概览。
    """

    session_id: str
    message_count: int
    summary: str | None = None
    summary_updated_at: datetime | None = None
    compressed_message_count: int = 0
    updated_at: datetime | None = None


class SessionSummaryResponse(BaseModel):
    """会话摘要生成结果。

    用于返回一次摘要压缩操作后的最新结果，并说明压缩后保留了多少消息摘要量。
    该模型主要服务于“触发摘要压缩”后的即时反馈，而不是常规会话查询。
    """

    session_id: str
    summary: str
    compressed_message_count: int
    updated_at: datetime
