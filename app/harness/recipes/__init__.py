"""Harness Recipe 注册表工厂模块。

提供便捷工厂函数，将内置 Recipe（query/chat/task）一次性注册到 RecipeRegistry。
"""

from __future__ import annotations

from app.harness.core.recipe import BaseRecipe, RecipeRegistry
from app.harness.recipes.query_recipe import ChatRecipe, QueryRecipe
from app.harness.recipes.task_recipe import (
    DocumentAnalysisRecipe,
    DocumentSummaryRecipe,
)


def build_default_recipe_registry() -> RecipeRegistry:
    """构建默认 recipe 注册表，注册全部内置 recipe。

    Returns:
        已注册了 query/chat/task 类型 recipe 的 RecipeRegistry 实例。
    """
    registry = RecipeRegistry()
    registry.register_many([
        QueryRecipe(),
        ChatRecipe(),
        DocumentAnalysisRecipe(),
        DocumentSummaryRecipe(),
    ])
    return registry


__all__ = [
    'build_default_recipe_registry',
    'QueryRecipe',
    'ChatRecipe',
    'DocumentAnalysisRecipe',
    'DocumentSummaryRecipe',
    'RecipeRegistry',
    'BaseRecipe',
]