"""Harness Stage 契约模块。

约束单个 stage 的最小执行接口，并提供一个可被继承的基础类，方便不同
runtime 以一致方式组织阶段执行。
"""

from __future__ import annotations

from typing import Protocol


class HarnessStage(Protocol):
    """单个阶段的最小运行契约。"""

    name: str

    def run(self, state: dict, ctx) -> dict:
        """执行当前阶段并返回更新后的状态切片。"""
        ...


class BaseStage:
    """阶段基类，占住稳定扩展位。"""

    name = ''

    def run(self, state: dict, ctx) -> dict:
        """由具体子类实现阶段执行逻辑。"""

        raise NotImplementedError
