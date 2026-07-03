"""Harness Recipe 契约模块。

定义阶段配方对象的最小接口与可继承基础实现，支持以声明式方式把多个 stage
组织成一条可重复执行的流水线。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


class HarnessRecipe(Protocol):
    """描述阶段组合的最小协议。"""

    name: str

    def stages(self) -> list[Any]:
        """返回当前配方包含的阶段列表。"""
        ...


@dataclass
class RecipeMeta:
    """Recipe 的静态元数据，供工具链和前端消费。"""

    name: str
    description: str = ''
    task_type: str = ''
    version: str = 'v1'
    tags: list[str] = field(default_factory=list)


class BaseRecipe:
    """声明式 recipe 基类，支持元数据与阶段组合扩展。"""

    name = ''
    description: str = ''
    task_type: str = ''
    version: str = 'v1'
    tags: list[str] = field(default_factory=list)

    def __init__(self, stages: Iterable[Any] | None = None) -> None:
        """初始化基础配方，并保存阶段顺序。"""
        self._stages = list(stages or [])

    def stages(self) -> list[Any]:
        """返回当前 recipe 的阶段快照。"""
        return list(self._stages)

    @property
    def meta(self) -> RecipeMeta:
        """返回 recipe 的静态元数据。"""
        return RecipeMeta(
            name=self.name,
            description=self.description,
            task_type=self.task_type,
            version=self.version,
            tags=list(self.tags),
        )

    def stage_names(self) -> list[str]:
        """返回所有 stage 的名称列表。"""
        return [
            getattr(s, 'name', str(s))
            for s in self._stages
        ]


class RecipeRegistry:
    """按名称或 task_type 注册和管理 recipe。"""

    def __init__(self) -> None:
        self._recipes_by_name: dict[str, BaseRecipe] = {}
        self._recipes_by_task_type: dict[str, BaseRecipe] = {}

    def register(self, recipe: BaseRecipe) -> None:
        """注册一个 recipe。

        同时按 recipe.name 和 (如果 task_type 非空) task_type 建立索引。
        """
        self._recipes_by_name[recipe.name] = recipe
        if recipe.task_type:
            self._recipes_by_task_type[recipe.task_type] = recipe

    def register_many(self, recipes: list[BaseRecipe]) -> None:
        """批量注册多个 recipe。"""
        for recipe in recipes:
            self.register(recipe)

    def get(self, name: str) -> BaseRecipe:
        """按名称查找 recipe。"""
        return self._recipes_by_name[name]

    def get_by_task_type(self, task_type: str) -> BaseRecipe | None:
        """按任务类型查找 recipe。"""
        return self._recipes_by_task_type.get(task_type)

    def has(self, name: str) -> bool:
        """判断某名称是否已注册。"""
        return name in self._recipes_by_name

    def list(self) -> list[BaseRecipe]:
        """返回按名称排序的所有 recipe。"""
        return [
            self._recipes_by_name[name]
            for name in sorted(self._recipes_by_name)
        ]

    def list_meta(self) -> list[RecipeMeta]:
        """返回所有 recipe 的元数据。"""
        return [r.meta for r in self.list()]