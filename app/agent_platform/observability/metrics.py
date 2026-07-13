"""Agent 可观测性指标体系。

负责收集 Agent 执行过程中的关键指标，
包括循环次数、token 使用量、延迟、工具调用数和错误数。
支持按 agent 名称分组统计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass
class AgentMetricsEntry:
    """单个 Agent 的指标快照。"""
    total_loops: int = 0
    total_tokens: int = 0
    total_latency_ms: float = 0.0
    total_tool_calls: int = 0
    total_errors: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / max(self.total_loops, 1)


class AgentMetricsCollector:
    """Agent 指标收集器。

    用于在 AgentLoop 的关键节点记录指标，
    支持按 Agent 名称分组和全局汇总。
    """

    def __init__(self) -> None:
        self.global_metrics = AgentMetricsEntry()
        self.by_agent: dict[str, AgentMetricsEntry] = {}
        self._start_times: dict[str, float] = {}

    def record_loop_start(self, session_id: str) -> None:
        """记录一次循环开始。"""
        self._start_times[session_id] = time()

    def record_loop_end(
        self,
        session_id: str,
        agent_name: str | None = None,
        tokens: int = 0,
        tool_calls: int = 0,
        errors: int = 0,
    ) -> None:
        """记录一次循环结束。

        Args:
            session_id: 会话 ID。
            agent_name: Agent 名称。
            tokens: token 使用量。
            tool_calls: 工具调用次数。
            errors: 错误次数。
        """
        start = self._start_times.pop(session_id, None)
        latency_ms = (time() - start) * 1000 if start else 0

        self.global_metrics.total_loops += 1
        self.global_metrics.total_tokens += tokens
        self.global_metrics.total_latency_ms += latency_ms
        self.global_metrics.total_tool_calls += tool_calls
        self.global_metrics.total_errors += errors

        if agent_name:
            if agent_name not in self.by_agent:
                self.by_agent[agent_name] = AgentMetricsEntry()
            agent_m = self.by_agent[agent_name]
            agent_m.total_loops += 1
            agent_m.total_tokens += tokens
            agent_m.total_latency_ms += latency_ms
            agent_m.total_tool_calls += tool_calls
            agent_m.total_errors += errors

    def get_agent_metrics(self, agent_name: str) -> AgentMetricsEntry:
        """获取指定 Agent 的指标。"""
        return self.by_agent.get(agent_name, AgentMetricsEntry())

    def snapshot(self) -> dict:
        """输出当前所有指标的快照字典。"""
        return {
            "global": {
                "total_loops": self.global_metrics.total_loops,
                "total_tokens": self.global_metrics.total_tokens,
                "avg_latency_ms": round(self.global_metrics.avg_latency_ms, 2),
                "total_tool_calls": self.global_metrics.total_tool_calls,
                "total_errors": self.global_metrics.total_errors,
            },
            "by_agent": {
                name: {
                    "total_loops": m.total_loops,
                    "total_tokens": m.total_tokens,
                    "avg_latency_ms": round(m.avg_latency_ms, 2),
                    "total_tool_calls": m.total_tool_calls,
                    "total_errors": m.total_errors,
                }
                for name, m in sorted(self.by_agent.items())
            },
        }

    def reset(self) -> None:
        """重置所有指标。"""
        self.global_metrics = AgentMetricsEntry()
        self.by_agent.clear()
        self._start_times.clear()
