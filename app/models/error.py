"""错误模型模块。

负责定义对外统一错误响应的数据结构，用于 API 层把内部异常稳定映射为标准错误协议。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ErrorInfo(BaseModel):
    """错误主体信息。

    包含错误代码、面向调用方的消息和可选细节字段。
    设计上把“稳定机器码”和“人类可读描述”拆开，便于前端既能做分支处理，也能直接展示。
    """

    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """对外暴露的标准错误响应。

    统一补充请求路径与时间戳，便于排障与日志对齐，也让不同接口的错误返回结构保持一致。
    外层包一层 `error`，可以避免未来扩展公共元字段时打破现有客户端解析逻辑。
    """

    error: ErrorInfo
    path: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
