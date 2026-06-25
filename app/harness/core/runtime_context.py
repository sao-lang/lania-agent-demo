"""Harness RuntimeContext 契约模块。

定义运行时在各阶段之间共享的依赖容器，统一承载 trace、策略、工具执行器和
扩展对象，避免 stage 之间直接耦合具体实现。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ConfigDict


class RuntimeContext(BaseModel):
    """稳定运行时依赖容器。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    request_id: str
    task_id: str | None = None
    run_id: str | None = None
    trace: Any | None = None
    tool_registry: Any | None = None
    tool_executor: Any | None = None
    subagent_runtime: Any | None = None
    policy: Any | None = None
    guardrail: Any | None = None
    extensions: dict[str, Any] = Field(default_factory=dict)
