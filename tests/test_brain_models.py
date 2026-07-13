"""测试 harness/brain/models.py 的数据模型。"""

import unittest

from app.harness.brain.models import (
    CheckpointType,
    ClientExecutionResult,
    Complexity,
    ConsentRecord,
    ConsentScope,
    IntentDecision,
    KnowledgeSource,
    RiskLevel,
    RouteContext,
    RouteResult,
    SafetyContext,
    SafetyDecision,
    SuggestedMode,
    ToolCall,
)


class TestEnums(unittest.TestCase):
    """测试枚举类型的值和转换。"""

    def test_knowledge_source_values(self):
        self.assertEqual(KnowledgeSource.INTERNAL_LLM.value, "internal_llm")
        self.assertEqual(KnowledgeSource.RAG.value, "rag")
        self.assertEqual(KnowledgeSource.WEB_SEARCH.value, "web_search")
        self.assertEqual(KnowledgeSource.CALCULATOR.value, "calculator")
        self.assertEqual(KnowledgeSource.CODE_REPO.value, "code_repo")
        self.assertEqual(KnowledgeSource.DATABASE.value, "database")
        self.assertEqual(KnowledgeSource.SHELL_CMD.value, "shell_cmd")

    def test_complexity_values(self):
        self.assertEqual(Complexity.SIMPLE.value, "simple")
        self.assertEqual(Complexity.MODERATE.value, "moderate")
        self.assertEqual(Complexity.COMPLEX.value, "complex")

    def test_risk_level_values(self):
        self.assertEqual(RiskLevel.LOW.value, "low")
        self.assertEqual(RiskLevel.MEDIUM.value, "medium")
        self.assertEqual(RiskLevel.HIGH.value, "high")
        self.assertEqual(RiskLevel.CRITICAL.value, "critical")

    def test_suggested_mode_values(self):
        self.assertEqual(SuggestedMode.CHAT.value, "chat")
        self.assertEqual(SuggestedMode.AUTOPILOT.value, "autopilot")
        self.assertEqual(SuggestedMode.PLAN.value, "plan")
        self.assertEqual(SuggestedMode.PLAN_CONFIRM.value, "plan_confirm")

    def test_checkpoint_type_values(self):
        self.assertEqual(CheckpointType.PRE_TOOL_CALL.value, "pre_tool_call")
        self.assertEqual(CheckpointType.POST_TOOL_CALL.value, "post_tool_call")
        self.assertEqual(CheckpointType.PRE_TOOL_OUTPUT_TO_LLM.value, "pre_tool_output_to_llm")

    def test_consent_scope_values(self):
        self.assertEqual(ConsentScope.NONE.value, "none")
        self.assertEqual(ConsentScope.SESSION.value, "session")
        self.assertEqual(ConsentScope.PERSISTENT.value, "persistent")


class TestIntentDecision(unittest.TestCase):
    """测试 IntentDecision 模型。"""

    def test_default_values(self):
        d = IntentDecision(complexity=Complexity.SIMPLE)
        self.assertEqual(d.complexity, Complexity.SIMPLE)
        self.assertEqual(d.suggested_sources, [])
        self.assertEqual(d.suggested_mode, SuggestedMode.CHAT)
        self.assertFalse(d.needs_planning)
        self.assertEqual(d.risk_level, RiskLevel.LOW)
        self.assertEqual(d.confidence, 0.5)
        self.assertEqual(d.reasoning, "")
        self.assertEqual(d.matched_capabilities, [])

    def test_full_decision(self):
        d = IntentDecision(
            complexity=Complexity.COMPLEX,
            suggested_sources=[KnowledgeSource.RAG, KnowledgeSource.WEB_SEARCH],
            suggested_mode=SuggestedMode.PLAN,
            needs_planning=True,
            risk_level=RiskLevel.HIGH,
            confidence=0.9,
            reasoning="需要多来源对比",
            matched_capabilities=["rag", "web_search"],
        )
        self.assertEqual(d.complexity, Complexity.COMPLEX)
        self.assertEqual(len(d.suggested_sources), 2)
        self.assertEqual(d.suggested_mode, SuggestedMode.PLAN)
        self.assertTrue(d.needs_planning)
        self.assertEqual(d.risk_level, RiskLevel.HIGH)
        self.assertEqual(d.confidence, 0.9)

    def test_serialization(self):
        d = IntentDecision(
            complexity=Complexity.MODERATE,
            suggested_sources=[KnowledgeSource.CALCULATOR],
            suggested_mode=SuggestedMode.CHAT,
        )
        data = d.model_dump()
        self.assertEqual(data["complexity"], "moderate")
        self.assertEqual(data["suggested_sources"], ["calculator"])
        self.assertEqual(data["suggested_mode"], "chat")
        self.assertEqual(data["risk_level"], "low")


class TestSafetyModels(unittest.TestCase):
    """测试安全相关数据模型。"""

    def test_safety_decision_pass(self):
        d = SafetyDecision(allowed=True, level="pass")
        self.assertTrue(d.allowed)
        self.assertEqual(d.level, "pass")

    def test_safety_decision_block(self):
        d = SafetyDecision(
            allowed=False, level="block",
            reason="高风险操作",
            details={"command": "rm -rf /", "category": "data_destruction"},
        )
        self.assertFalse(d.allowed)
        self.assertEqual(d.level, "block")
        self.assertEqual(d.details["category"], "data_destruction")

    def test_safety_context_defaults(self):
        ctx = SafetyContext()
        self.assertEqual(ctx.tool_name, "")
        self.assertEqual(ctx.tool_args, {})
        self.assertEqual(ctx.execution_target, "server")
        self.assertEqual(ctx.session_history, [])
        self.assertEqual(ctx.raw, {})

    def test_safety_context_full(self):
        ctx = SafetyContext(
            tool_name="shell_command",
            tool_args={"command": "rm -rf /tmp"},
            execution_target="client",
            session_history=["read_file", "shell_command"],
            user_id="user_1",
        )
        self.assertEqual(ctx.tool_name, "shell_command")
        self.assertEqual(ctx.execution_target, "client")
        self.assertEqual(len(ctx.session_history), 2)


class TestExecutionModels(unittest.TestCase):
    """测试执行相关模型。"""

    def test_tool_call(self):
        tc = ToolCall(name="web_search", args={"query": "test"})
        self.assertEqual(tc.name, "web_search")
        self.assertEqual(tc.args["query"], "test")

    def test_client_execution_result(self):
        r = ClientExecutionResult(stdout="ok", stderr="", exit_code=0)
        self.assertEqual(r.exit_code, 0)
        self.assertEqual(r.stdout, "ok")
        self.assertFalse(r.truncated)

    def test_client_execution_result_error(self):
        r = ClientExecutionResult(stdout="", stderr="not found", exit_code=127)
        self.assertEqual(r.exit_code, 127)
        self.assertNotEqual(r.exit_code, 0)

    def test_consent_record_valid(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.PERSISTENT,
        )
        self.assertTrue(record.is_valid())

    def test_consent_record_session_valid(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.SESSION,
        )
        self.assertTrue(record.is_valid())

    def test_consent_record_none_invalid(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.NONE,
        )
        self.assertFalse(record.is_valid())


class TestRouteModels(unittest.TestCase):
    """测试路由相关模型。"""

    def test_route_context_defaults(self):
        ctx = RouteContext()
        self.assertFalse(ctx.user_prefers_confirmation)
        self.assertEqual(ctx.tool_count, 0)

    def test_route_result(self):
        r = RouteResult(mode=SuggestedMode.PLAN, upgrade_reason="高风险")
        self.assertEqual(r.mode, SuggestedMode.PLAN)
        self.assertEqual(r.upgrade_reason, "高风险")


if __name__ == "__main__":
    unittest.main()
