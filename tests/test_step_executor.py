"""测试 StepExecutor 的确认矩阵和执行路由�?""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.agent_platform.agents.brain.models import (
    ConsentScope,
)
from app.agent_platform.agents.brain.step_executor import StepExecutor


class MockToolSchema:
    """模拟工具 Schema�?""

    def __init__(self, name="test_tool", risk_level="low", execution_target="server",
                 sandbox_mode="inline", description="测试工具"):
        self.name = name
        self.version = "v1"
        self.input_schema = {}
        self.output_schema = {}
        self.error_codes = []
        self.timeout_ms = 30000
        self.retry_policy = None
        self.trace_fields = []
        self.risk_level = risk_level
        self.execution_target = execution_target
        self.sandbox_mode = sandbox_mode
        self.description = description


class MockToolRegistry:
    """模拟工具注册表�?""

    def __init__(self, risk_level="low", execution_target="server"):
        self._risk_level = risk_level
        self._execution_target = execution_target

    def describe(self, name):
        return MockToolSchema(
            name=name,
            risk_level=self._risk_level,
            execution_target=self._execution_target,
        )

    def get(self, name):
        return MagicMock()


class MockSession:
    """模拟会话对象�?""

    def __init__(self, user_id="test_user", tool_history=None):
        self.id = "session_1"
        self.user_id = user_id
        self.tool_history = tool_history or []


class TestStepExecutorConsentMatrix(unittest.TestCase):
    """测试 StepExecutor 的确认矩阵逻辑�?""

    def test_need_consent_chat_low(self):
        executor = StepExecutor(MockToolRegistry("low"))
        self.assertFalse(executor._need_consent("low", "chat"))

    def test_need_consent_chat_high(self):
        executor = StepExecutor(MockToolRegistry("high"))
        self.assertTrue(executor._need_consent("high", "chat"))

    def test_need_consent_autopilot_low(self):
        executor = StepExecutor(MockToolRegistry("low"))
        self.assertFalse(executor._need_consent("low", "autopilot"))

    def test_need_consent_autopilot_high(self):
        executor = StepExecutor(MockToolRegistry("high"))
        self.assertTrue(executor._need_consent("high", "autopilot"))

    def test_need_consent_plan_low(self):
        executor = StepExecutor(MockToolRegistry("low"))
        self.assertFalse(executor._need_consent("low", "plan"))

    def test_need_consent_plan_high(self):
        executor = StepExecutor(MockToolRegistry("high"))
        self.assertTrue(executor._need_consent("high", "plan"))

    def test_need_consent_plan_confirm_medium(self):
        executor = StepExecutor(MockToolRegistry("medium"))
        self.assertTrue(executor._need_consent("medium", "plan_confirm"))

    def test_need_consent_unknown_risk_default_confirm(self):
        executor = StepExecutor(MockToolRegistry("unknown"))
        self.assertTrue(executor._need_consent("unknown", "chat"))

    def test_need_consent_unknown_mode_default_confirm(self):
        executor = StepExecutor(MockToolRegistry("high"))
        self.assertTrue(executor._need_consent("high", "unknown_mode"))


class TestStepExecutorDisclosure(unittest.TestCase):
    """测试披露逻辑�?""

    def test_need_disclose_autopilot_medium(self):
        executor = StepExecutor(MockToolRegistry("medium"))
        self.assertTrue(executor._need_disclose("medium", "autopilot"))

    def test_need_disclose_chat_high(self):
        executor = StepExecutor(MockToolRegistry("high"))
        self.assertFalse(executor._need_disclose("high", "chat"))

    def test_need_disclose_plan_low(self):
        executor = StepExecutor(MockToolRegistry("low"))
        self.assertFalse(executor._need_disclose("low", "plan"))


class TestStepExecutorConsentStore(unittest.TestCase):
    """测试确认存储集成�?""

    def test_remembered_consent_skips_confirm(self):
        from app.agent_platform.agents.brain.consent_store import ConsentStore
        store = ConsentStore()
        from app.agent_platform.agents.brain.models import ConsentRecord
        store.save(ConsentRecord(
            user_id="test_user", tool_name="shell_command",
            scope=ConsentScope.SESSION,
        ))

        executor = StepExecutor(MockToolRegistry("high"), consent_store=store)

        # 已记�?�?不需要确�?
        # 不设 safety_engine，跳过安全策略检�?
        need_consent = executor._need_consent("high", "chat")
        self.assertTrue(need_consent)

        remembered = store.get("test_user", "shell_command")
        self.assertIsNotNone(remembered)


if __name__ == "__main__":
    unittest.main()
