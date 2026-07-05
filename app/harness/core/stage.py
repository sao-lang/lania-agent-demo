"""Harness Stage 契约模块。

约束单个 stage 的最小执行接口，并提供可被继承的基础类与丰富生命周期支持，
方便不同 runtime 以一致方式组织阶段执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class HarnessStage(Protocol):
    """单个阶段的最小运行契约。"""

    name: str

    def run(self, state: dict, ctx) -> dict:
        """执行当前阶段并返回更新后的状态切片。"""
        ...


@dataclass
class BaseStage:
    """阶段基类，占住稳定扩展位，并提供元数据信息。

    子类可以重写以下方法：
    - run(): 阶段执行逻辑
    - validate_input(state, ctx): 执行前校验输入
    - route_next(state_payload): 条件路由，返回下一阶段名称
    """

    name: str = ''
    description: str = ''
    timeout_ms: int = 30000
    allowed_tools: list[str] = field(default_factory=list)
    requires_policy_check: bool = True
    requires_guardrail: bool = True
    creates_checkpoint_after: bool = False
    risk_level: str = 'low'
    route_targets: list[str] = field(default_factory=list)

    def run(self, state: dict, ctx) -> dict:
        """由具体子类实现阶段执行逻辑。"""
        raise NotImplementedError

    def route_next(self, state_payload: dict) -> str:
        """条件路由：根据当前状态决定下一阶段。

        需要条件路由的 Stage 必须实现此方法并设置 route_targets。
        返回值必须是 route_targets 中的一个或 'finalize'。

        Args:
            state_payload: 当前阶段执行后的状态 payload

        Returns:
            下一个阶段的名称
        """
        raise NotImplementedError(f'{self.name} has route_targets but no route_next()')

    def validate_input(self, state: dict, ctx) -> list[str]:
        """执行前校验输入状态是否满足前置条件；返回空列表表示通过。"""
        return []

    def validate_output(self, new_state: dict) -> list[str]:
        """执行后校验输出状态是否合法；返回空列表表示通过。"""
        return []