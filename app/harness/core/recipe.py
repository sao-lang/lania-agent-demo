"""Harness Recipe 契约模块。

定义阶段配方对象的最小接口与基础实现，便于上层把多个 stage 以声明式方式
组织成一条可重复执行的流水线。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol


class HarnessRecipe(Protocol):
    """描述阶段组合的最小协议。"""

    name: str

    def stages(self) -> list[Any]:
        """返回当前配方包含的阶段列表。"""
        ...


class BaseRecipe:
    """声明式 recipe 基类。"""

    name = ''

    def __init__(self, stages: Iterable[Any] | None = None) -> None:
        """初始化基础配方，并保存阶段顺序。"""

        self._stages = list(stages or [])

    def stages(self) -> list[Any]:
        """返回当前 recipe 的阶段快照。"""

        return list(self._stages)
