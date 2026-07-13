"""安全策略引擎。

设计原则：
  - 策略可插拔：通过配置加载，不是硬编码
  - 策略可配置：保护路径、风险阈值全部可配置
  - 策略可扩展：部署者可以写自己的策略插件
  - 平台无关：只做结构级检查，不预判操作系统
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.harness.brain.models import (
    CheckpointType,
    SafetyContext,
    SafetyDecision,
)


class SafetyPolicy(ABC):
    """安全策略插件基类。"""
    name: str = ""
    description: str = ""

    @abstractmethod
    async def check(self, context: SafetyContext) -> SafetyDecision:
        """检查并返回安全决策。"""
        ...


class SafetyEngine:
    """安全策略引擎。

    职责：
    1. 加载可插拔的安全策略
    2. 按检查点执行策略链
    3. 汇总结果（最严格的决策生效）
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._policies: dict[CheckpointType, list[SafetyPolicy]] = {}
        self._load_policies(config or {})

    def register_policy(self, checkpoint: CheckpointType, policy: SafetyPolicy) -> None:
        """注册一个策略到指定检查点。

        Args:
            checkpoint: 检查点类型。
            policy: 策略实例。
        """
        if checkpoint not in self._policies:
            self._policies[checkpoint] = []
        self._policies[checkpoint].append(policy)

    def register_policies(self, policies: dict[CheckpointType, list[SafetyPolicy]]) -> None:
        """批量注册策略。

        Args:
            policies: 检查点到策略列表的映射。
        """
        for checkpoint, policy_list in policies.items():
            for policy in policy_list:
                self.register_policy(checkpoint, policy)

    async def check(
        self,
        checkpoint: CheckpointType,
        context: SafetyContext,
    ) -> SafetyDecision:
        """在指定检查点执行所有策略。

        Args:
            checkpoint: 检查点类型。
            context: 安全上下文。

        Returns:
            最严格的决策结果（block > warn > pass）。
        """
        policies = self._policies.get(checkpoint, [])
        worst = SafetyDecision(allowed=True, level="pass")

        for policy in policies:
            decision = await policy.check(context)
            if not decision.allowed:
                return decision  # 任何 block 直接返回
            if decision.level == "warn" and worst.level == "pass":
                worst = decision

        return worst

    def _load_policies(self, config: dict[str, Any]) -> None:
        """从配置加载内置策略。"""
        disabled = set(config.get("disabled", []))

        # 注册所有内置策略到对应检查点
        from app.harness.safety.policies.data_destruction import DataDestructionPolicy
        from app.harness.safety.policies.data_exfiltration import DataExfiltrationPolicy
        from app.harness.safety.policies.privilege_escalation import PrivilegeEscalationPolicy
        from app.harness.safety.policies.system_tampering import SystemTamperingPolicy
        from app.harness.safety.policies.remote_code_execution import RemoteCodeExecutionPolicy
        from app.harness.safety.policies.session_risk import SessionContextPolicy
        from app.harness.safety.policies.tool_output_content import ToolOutputContentPolicy

        checkpoints: dict[CheckpointType, list[SafetyPolicy]] = {
            CheckpointType.PRE_TOOL_CALL: [
                DataDestructionPolicy(config.get("policy_config", {})),
                DataExfiltrationPolicy(config.get("policy_config", {})),
                PrivilegeEscalationPolicy(config.get("policy_config", {})),
                SystemTamperingPolicy(config.get("policy_config", {})),
                RemoteCodeExecutionPolicy(config.get("policy_config", {})),
            ],
            CheckpointType.PRE_TOOL_OUTPUT_TO_LLM: [
                ToolOutputContentPolicy(config.get("policy_config", {})),
            ],
            CheckpointType.POST_TOOL_CALL: [
                SessionContextPolicy(config.get("policy_config", {})),
            ],
        }

        for checkpoint, policies in checkpoints.items():
            for policy in policies:
                if policy.name not in disabled:
                    self.register_policy(checkpoint, policy)
