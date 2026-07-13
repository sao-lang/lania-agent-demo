"""可观测性 FastAPI 中间件。

为每个请求注入 trace_id 和计时。
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

logger = logging.getLogger(__name__)


async def observability_middleware(request, call_next):
    """为每个请求注入 trace_id 并记录延迟。"""
    trace_id = uuid4().hex[:16]
    request.state.trace_id = trace_id
    request.state.start_time = time.time()

    response = await call_next(request)

    latency = (time.time() - request.state.start_time) * 1000
    response.headers["X-Trace-ID"] = trace_id

    logger.info(
        "request %s %s trace_id=%s latency=%.0fms status=%d",
        request.method, request.url.path, trace_id, latency, response.status_code,
    )
    return response
