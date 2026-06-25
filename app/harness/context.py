"""Context Harness v2 实现。

负责把任务、计划、证据、产物和近期记忆裁成当前步骤可直接消费的最小上下文切片。
在 phase3 中，ContextHarness 保留兼容 facade 角色，task/query 组装职责拆给 builders。
"""

from __future__ import annotations

from typing import Any

from app.agents.memory import TaskMemory
from app.agents.tools.registry import ToolRegistry
from app.harness.components.context_builders import ContextValueSerializer, QueryContextBuilder, TaskContextBuilder
from app.harness.components.context_models import ContextOptimizationResult
from app.core.config import Settings
from app.harness.models import ContextBundle
from app.models.task import StepSpec


class ContextHarness:
    """统一装配文档分析任务的步骤级上下文 (v2)。"""

    def __init__(self, memory: TaskMemory, registry: ToolRegistry, settings: Settings) -> None:
        """初始化上下文 facade 及 task/query 两类 builder。"""

        self.memory = memory
        self.registry = registry
        self.settings = settings
        self.serializer = ContextValueSerializer()
        self.task_context_builder = TaskContextBuilder(memory=memory, settings=settings, serializer=self.serializer)
        self.query_context_builder = QueryContextBuilder(serializer=self.serializer)

    def build_context(self, workflow_state: dict[str, Any], step_id: str | None = None) -> ContextBundle:
        """为当前步骤生成最小上下文切片。"""
        return self.task_context_builder.build_context(workflow_state, step_id)
    
    def build_optimized_context(self, workflow_state: dict[str, Any], step_id: str | None = None) -> ContextOptimizationResult:
        """为当前步骤生成优化后的上下文切片，包含完整的优化信息。"""
        return self.task_context_builder.build_optimized_context(workflow_state, step_id)

    def build_query_context(self, workflow_state: dict[str, Any], step_spec: StepSpec) -> ContextBundle:
        """为 query/chat runtime 生成步骤级 ContextBundle。"""
        return self.query_context_builder.build_query_context(workflow_state, step_spec)
