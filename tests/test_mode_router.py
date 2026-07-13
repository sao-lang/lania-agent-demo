"""测试 ModeRouter 和确认矩阵�?""

import unittest

from app.agent_platform.agents.brain.mode_router import ModeRouter
from app.agent_platform.agents.brain.models import (
    Complexity,
    IntentDecision,
    KnowledgeSource,
    RiskLevel,
    RouteContext,
    SuggestedMode,
)


class TestModeRouter(unittest.TestCase):
    """测试模式路由�?""

    def setUp(self):
        self.router = ModeRouter()

    async def _route(self, decision, context=None):
        return await self.router.route(decision, context)

    # ── 基础路由 ──

    def test_chat_mode_default(self):
        decision = IntentDecision(
            complexity=Complexity.SIMPLE,
            suggested_sources=[KnowledgeSource.INTERNAL_LLM],
            suggested_mode=SuggestedMode.CHAT,
            risk_level=RiskLevel.LOW,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.CHAT)
            self.assertEqual(result.upgrade_reason, "")

        import asyncio
        asyncio.run(run())

    # ── 风险升级 ──

    def test_critical_risk_upgrades_to_plan_confirm(self):
        decision = IntentDecision(
            complexity=Complexity.SIMPLE,
            suggested_sources=[KnowledgeSource.INTERNAL_LLM],
            suggested_mode=SuggestedMode.CHAT,
            risk_level=RiskLevel.CRITICAL,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.PLAN_CONFIRM)
            self.assertIn("critical", result.upgrade_reason)

        import asyncio
        asyncio.run(run())

    def test_high_risk_upgrades_to_autopilot(self):
        decision = IntentDecision(
            complexity=Complexity.COMPLEX,
            suggested_sources=[KnowledgeSource.SHELL_CMD],
            suggested_mode=SuggestedMode.CHAT,
            risk_level=RiskLevel.HIGH,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.AUTOPILOT)
            self.assertIn("autopilot", result.upgrade_reason)

        import asyncio
        asyncio.run(run())

    def test_high_risk_with_plan_stays_plan(self):
        decision = IntentDecision(
            complexity=Complexity.COMPLEX,
            suggested_sources=[KnowledgeSource.CODE_REPO, KnowledgeSource.SHELL_CMD],
            suggested_mode=SuggestedMode.PLAN,
            risk_level=RiskLevel.HIGH,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.PLAN)

        import asyncio
        asyncio.run(run())

    # ── 知识来源升级 ──

    def test_three_or_more_sources_upgrades_to_plan(self):
        decision = IntentDecision(
            complexity=Complexity.COMPLEX,
            suggested_sources=[
                KnowledgeSource.RAG,
                KnowledgeSource.WEB_SEARCH,
                KnowledgeSource.CALCULATOR,
            ],
            suggested_mode=SuggestedMode.AUTOPILOT,
            risk_level=RiskLevel.MEDIUM,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.PLAN)
            self.assertIn("3 个知识来�?, result.upgrade_reason)

        import asyncio
        asyncio.run(run())

    def test_two_sources_not_upgraded(self):
        decision = IntentDecision(
            complexity=Complexity.MODERATE,
            suggested_sources=[
                KnowledgeSource.RAG,
                KnowledgeSource.WEB_SEARCH,
            ],
            suggested_mode=SuggestedMode.AUTOPILOT,
            risk_level=RiskLevel.MEDIUM,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.AUTOPILOT)

        import asyncio
        asyncio.run(run())

    # ── 用户偏好 ──

    def test_user_prefers_confirmation_upgrades_chat(self):
        decision = IntentDecision(
            complexity=Complexity.SIMPLE,
            suggested_sources=[KnowledgeSource.INTERNAL_LLM],
            suggested_mode=SuggestedMode.CHAT,
            risk_level=RiskLevel.LOW,
        )
        ctx = RouteContext(user_prefers_confirmation=True)

        async def run():
            result = await self._route(decision, ctx)
            self.assertEqual(result.mode, SuggestedMode.PLAN)

        import asyncio
        asyncio.run(run())

    # ── needs_planning 标志 ──

    def test_needs_planning_upgrades_chat(self):
        decision = IntentDecision(
            complexity=Complexity.COMPLEX,
            suggested_sources=[KnowledgeSource.CODE_REPO],
            suggested_mode=SuggestedMode.CHAT,
            needs_planning=True,
            risk_level=RiskLevel.MEDIUM,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.PLAN)
            self.assertIn("需要规�?, result.upgrade_reason)

        import asyncio
        asyncio.run(run())

    # ── critical 优先级最�?──

    def test_critical_overrides_needs_planning(self):
        decision = IntentDecision(
            complexity=Complexity.COMPLEX,
            suggested_sources=[
                KnowledgeSource.DATABASE,
                KnowledgeSource.SHELL_CMD,
            ],
            suggested_mode=SuggestedMode.PLAN,
            needs_planning=True,
            risk_level=RiskLevel.CRITICAL,
        )

        async def run():
            result = await self._route(decision)
            self.assertEqual(result.mode, SuggestedMode.PLAN_CONFIRM)
            self.assertIn("critical", result.upgrade_reason)

        import asyncio
        asyncio.run(run())


class TestConsentMatrix(unittest.TestCase):
    """测试确认矩阵�?""

    def setUp(self):
        self.router = ModeRouter()

    def test_chat_low_auto(self):
        self.assertFalse(self.router.consent_matrix(SuggestedMode.CHAT, "low"))

    def test_chat_medium_auto(self):
        self.assertFalse(self.router.consent_matrix(SuggestedMode.CHAT, "medium"))

    def test_chat_high_confirm(self):
        self.assertTrue(self.router.consent_matrix(SuggestedMode.CHAT, "high"))

    def test_chat_critical_confirm(self):
        self.assertTrue(self.router.consent_matrix(SuggestedMode.CHAT, "critical"))

    def test_autopilot_low_auto(self):
        self.assertFalse(self.router.consent_matrix(SuggestedMode.AUTOPILOT, "low"))

    def test_autopilot_high_confirm(self):
        self.assertTrue(self.router.consent_matrix(SuggestedMode.AUTOPILOT, "high"))

    def test_plan_low_auto(self):
        self.assertFalse(self.router.consent_matrix(SuggestedMode.PLAN, "low"))

    def test_plan_high_confirm(self):
        self.assertTrue(self.router.consent_matrix(SuggestedMode.PLAN, "high"))

    def test_plan_confirm_low_auto(self):
        self.assertFalse(self.router.consent_matrix(SuggestedMode.PLAN_CONFIRM, "low"))

    def test_plan_confirm_medium_confirm(self):
        self.assertTrue(self.router.consent_matrix(SuggestedMode.PLAN_CONFIRM, "medium"))


class TestDisclosureMatrix(unittest.TestCase):
    """测试披露矩阵�?""

    def setUp(self):
        self.router = ModeRouter()

    def test_chat_no_disclose(self):
        self.assertFalse(self.router.needs_disclosure(SuggestedMode.CHAT, "high"))

    def test_autopilot_high_disclose(self):
        self.assertTrue(self.router.needs_disclosure(SuggestedMode.AUTOPILOT, "high"))

    def test_plan_medium_disclose(self):
        self.assertTrue(self.router.needs_disclosure(SuggestedMode.PLAN, "medium"))

    def test_plan_low_no_disclose(self):
        self.assertFalse(self.router.needs_disclosure(SuggestedMode.PLAN, "low"))

    def test_autopilot_low_no_disclose(self):
        self.assertFalse(self.router.needs_disclosure(SuggestedMode.AUTOPILOT, "low"))


if __name__ == "__main__":
    unittest.main()
