"""Harness Core 契约导出模块。

聚合内核执行、阶段协议、运行时上下文与 recipe 基类等最底层抽象，供上层
运行时实现复用。该文件只负责统一导出，不承载实际执行逻辑。
"""

from .kernel import HarnessKernel, HarnessResult
from .recipe import BaseRecipe, HarnessRecipe
from .stage import BaseStage, HarnessStage

__all__ = [
    'BaseRecipe',
    'BaseStage',
    'HarnessKernel',
    'HarnessRecipe',
    'HarnessResult',
    'HarnessStage',
]
