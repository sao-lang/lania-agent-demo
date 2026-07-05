"""Harness Kernel 实现模块。

将 Recipe/Stage 声明转换为 LangGraph StateGraph，支持条件路由、循环和 checkpoint。
治理（trace/audit/checkpoint）通过 EventBus 与业务执行解耦。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from langgraph.graph import END, StateGraph

from app.harness.core.hooks import EventBus, HookRegistry

StateType = TypeVar('StateType')


@dataclass
class HarnessResult(Generic[StateType]):
    """内核执行结果。"""

    state: StateType
    completed: bool = True
    error: str | None = None
    failed_stage: str | None = None


class HarnessKernel(Generic[StateType]):
    """将 Recipe 转换为 LangGraph 执行图的内核。

    负责：
    - 从 Recipe.stages() 动态构建 StateGraph
    - 处理条件路由（route_next）
    - 在关键节点发射 EventBus 事件
    - 支持 checkpoint 和循环

    用法::

        kernel = HarnessKernel(event_bus=bus)
        graph = kernel.build_graph(recipe)
        result = kernel.run(graph, initial_state, ctx)
    """

    def __init__(
        self, event_bus: EventBus | None = None, bus: EventBus | None = None
    ) -> None:
        """初始化内核及事件总线。

        Args:
            event_bus: 运行时事件总线；为 None 时自动创建空总线。
            bus: event_bus 的别名，方便已有调用兼容。
        """
        self.event_bus = event_bus or bus or EventBus()

    @property
    def hooks(self) -> HookRegistry:
        """获取事件总线底层的 hook registry。"""
        return self.event_bus.registry

    def build_graph(self, recipe: Any) -> StateGraph:
        """从 Recipe 构建 LangGraph StateGraph。

        Args:
            recipe: 实现 stages() 方法的 recipe 实例，返回 BaseStage 列表。

        Returns:
            编译前的 StateGraph，可进一步配置后 compile() 执行。
        """
        stages = recipe.stages()
        if not stages:
            raise ValueError('Recipe must have at least one stage')

        graph = StateGraph(dict)

        stage_map = {stage.name: stage for stage in stages}

        for stage in stages:
            node_func = self._build_stage_node(stage, stage_map)
            graph.add_node(stage.name, node_func)

            if stage.route_targets:
                route_func = self._build_route_condition(stage, stage_map)
                graph.add_conditional_edges(stage.name, route_func)
            elif stage is not stages[-1]:
                next_stage = stages[stages.index(stage) + 1]
                graph.add_edge(stage.name, next_stage.name)
            else:
                graph.add_edge(stage.name, END)

        graph.set_entry_point(stages[0].name)

        return graph

    def _build_stage_node(
        self, stage: Any, stage_map: dict[str, Any]
    ) -> Callable[[dict], dict]:
        """构建单个 Stage 的 LangGraph 节点函数。

        Args:
            stage: 单个 Stage 实例
            stage_map: 所有 Stage 的名称映射

        Returns:
            可作为 LangGraph 节点的函数
        """

        def stage_node(state: dict) -> dict:
            ctx = state.get('__harness_ctx__', {})
            ws = state.get('__harness_workflow_state__', {})
            stage_name = stage.name

            self.event_bus.before_stage(ws, stage_name=stage_name)

            try:
                payload = stage.run(dict(state), ctx)
                result = dict(state)
                result.update(payload)
                result['__harness_completed_stages__'] = result.get('__harness_completed_stages__', []) + [stage_name]
            except Exception as exc:
                error_msg = str(exc)
                result = dict(state)
                result['__harness_stage_errors__'] = result.get('__harness_stage_errors__', {}) | {stage_name: error_msg}
                self.event_bus.stage_failed(
                    ws,
                    stage_name=stage_name,
                    error=error_msg,
                )
                raise

            self.event_bus.after_stage(ws, stage_name=stage_name)

            return result

        return stage_node

    def _build_route_condition(
        self, stage: Any, stage_map: dict[str, Any]
    ) -> Callable[[dict], str]:
        """构建条件路由函数。

        Args:
            stage: 带有 route_targets 的 Stage 实例
            stage_map: 所有 Stage 的名称映射

        Returns:
            返回下一阶段名称的路由函数
        """
        route_targets_set = set(stage.route_targets)

        def route_condition(state: dict) -> str:
            result = stage.route_next(dict(state))

            if result == 'finalize':
                return END

            if result not in route_targets_set:
                raise ValueError(
                    f'{stage.name} route_next() returned "{result}", '
                    f'but route_targets only contains {list(route_targets_set)}'
                )

            return result

        return route_condition

    def run(
        self,
        graph: StateGraph,
        initial_state: dict,
        ctx: Any,
        *,
        workflow_state: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> HarnessResult:
        """执行构建好的 LangGraph。

        Args:
            graph: 通过 build_graph() 构建的 StateGraph
            initial_state: 初始状态字典
            ctx: 运行时上下文
            workflow_state: 可选完整工作流状态，随事件发射供 hook 消费
            config: LangGraph 配置，可包含 checkpoint_id 等

        Returns:
            执行结果，包含最终状态和可能的错误信息
        """
        recipe_name = initial_state.get('_recipe_name', 'unknown')
        ws = workflow_state

        self.event_bus.run_started(ws, recipe_name=recipe_name)

        try:
            app = graph.compile()

            state = dict(initial_state)
            state.setdefault('__harness_ctx__', ctx)
            state.setdefault('__harness_workflow_state__', ws or {})
            state.setdefault('__harness_completed_stages__', [])
            state.setdefault('__harness_stage_errors__', {})

            final_state = app.invoke(state, config=config or {})

            self.event_bus.run_completed(
                ws,
                completed_stages=final_state.get('__harness_completed_stages__', []),
            )

            return HarnessResult(
                state=final_state,
                completed=True,
            )

        except Exception as exc:
            error_msg = str(exc)
            self.event_bus.run_failed(
                ws,
                error=error_msg,
                completed_stages=initial_state.get('_completed_stages', []),
            )
            return HarnessResult(
                state=initial_state,
                completed=False,
                error=error_msg,
            )