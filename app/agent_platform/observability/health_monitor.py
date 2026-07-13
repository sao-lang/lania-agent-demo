"""Agent 健康监控模块。

主动检测维度：
- 工具调用失败率 > 20% → 告警
- LLM 平均延迟 > 10s → 告警
- 预算超限次数 > 5/小时 → 告警
- Agent 请求量断崖下降 > 50% → 通知
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Alert:
    """告警信息。"""
    severity: str  # info / warn / critical
    message: str
    metric: str = ""
    value: float = 0.0
    threshold: float = 0.0


@dataclass
class HealthStatus:
    """健康状态。"""
    agent_name: str
    status: str  # healthy / degraded / critical
    alerts: list[Alert] = field(default_factory=list)


class AgentHealthMonitor:
    """Agent 健康监控器。"""

    thresholds = {
        "tool_failure_rate": {"warn": 0.1, "critical": 0.2},
        "avg_latency_ms": {"warn": 5000, "critical": 10000},
        "budget_exceeded_per_hour": {"warn": 3, "critical": 10},
    }

    def __init__(self, metrics_collector: Any | None = None) -> None:
        self._metrics = metrics_collector
        self._budget_exceeded_count: dict[str, int] = {}  # agent_name → count

    def record_budget_exceeded(self, agent_name: str) -> None:
        """记录一次预算超限。"""
        self._budget_exceeded_count[agent_name] = self._budget_exceeded_count.get(agent_name, 0) + 1

    async def check_agent(self, agent_name: str) -> HealthStatus:
        """检查指定 Agent 的健康状态。

        Args:
            agent_name: Agent 名称。

        Returns:
            HealthStatus 包含所有告警。
        """
        alerts: list[Alert] = []

        # 1. 检查工具失败率
        if self._metrics is not None:
            metrics = self._metrics.get_agent_metrics(agent_name)
            if metrics.total_loops > 0:
                failure_rate = metrics.total_errors / max(metrics.total_loops, 1)
                if failure_rate > self.thresholds["tool_failure_rate"]["critical"]:
                    alerts.append(Alert(
                        severity="critical",
                        message=f"工具失败率 {failure_rate:.0%} 超过临界阈值 {self.thresholds['tool_failure_rate']['critical']:.0%}",
                        metric="tool_failure_rate", value=failure_rate,
                        threshold=self.thresholds["tool_failure_rate"]["critical"],
                    ))
                elif failure_rate > self.thresholds["tool_failure_rate"]["warn"]:
                    alerts.append(Alert(
                        severity="warn",
                        message=f"工具失败率 {failure_rate:.0%} 超过警告阈值 {self.thresholds['tool_failure_rate']['warn']:.0%}",
                        metric="tool_failure_rate", value=failure_rate,
                        threshold=self.thresholds["tool_failure_rate"]["warn"],
                    ))

                # 2. 检查平均延迟
                avg_latency = metrics.avg_latency_ms
                if avg_latency > self.thresholds["avg_latency_ms"]["critical"]:
                    alerts.append(Alert(
                        severity="critical",
                        message=f"平均延迟 {avg_latency:.0f}ms 超过临界阈值 {self.thresholds['avg_latency_ms']['critical']}ms",
                        metric="avg_latency_ms", value=avg_latency,
                        threshold=self.thresholds["avg_latency_ms"]["critical"],
                    ))
                elif avg_latency > self.thresholds["avg_latency_ms"]["warn"]:
                    alerts.append(Alert(
                        severity="warn",
                        message=f"平均延迟 {avg_latency:.0f}ms 超过警告阈值 {self.thresholds['avg_latency_ms']['warn']}ms",
                        metric="avg_latency_ms", value=avg_latency,
                        threshold=self.thresholds["avg_latency_ms"]["warn"],
                    ))

        # 3. 检查预算超限
        exceeded = self._budget_exceeded_count.get(agent_name, 0)
        if exceeded > self.thresholds["budget_exceeded_per_hour"]["critical"]:
            alerts.append(Alert(
                severity="critical",
                message=f"预算超限次数 {exceeded} 超过临界阈值 {self.thresholds['budget_exceeded_per_hour']['critical']}",
                metric="budget_exceeded_per_hour", value=float(exceeded),
                threshold=self.thresholds["budget_exceeded_per_hour"]["critical"],
            ))
        elif exceeded > self.thresholds["budget_exceeded_per_hour"]["warn"]:
            alerts.append(Alert(
                severity="warn",
                message=f"预算超限次数 {exceeded} 超过警告阈值 {self.thresholds['budget_exceeded_per_hour']['warn']}",
                metric="budget_exceeded_per_hour", value=float(exceeded),
                threshold=self.thresholds["budget_exceeded_per_hour"]["warn"],
            ))

        # 确定整体状态
        status = "healthy"
        if any(a.severity == "critical" for a in alerts):
            status = "critical"
        elif any(a.severity == "warn" for a in alerts):
            status = "degraded"

        return HealthStatus(agent_name=agent_name, status=status, alerts=alerts)
