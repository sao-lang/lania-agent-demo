"""测试 AgentLoop 的基本逻辑�?""

from __future__ import annotations

import unittest

from app.agent_platform.agents.brain.agent_loop import AgentLoop, PauseState
from app.agent_platform.agents.brain.models import (
    Complexity,
    IntentDecision,
    KnowledgeSource,
    RiskLevel,
    SuggestedMode,
    ToolCall,
)


class MockLLM:
    """模拟 LLM 响应�?""

    def __init__(self, has_tool_calls=False, content="test response", tool_calls=None):
        self._has_tool_calls = has_tool_calls
        self._content = content
        self._tool_calls = tool_calls or []

        class Choice:
            class Message:
                def __init__(self, content, tool_calls=None):
                    self.content = content
                    self.tool_calls = tool_calls

            def __init__(self, content, tool_calls=None):
                self.message = self.Message(content, tool_calls)
                self.finish_reason = "tool_calls" if tool_calls else "stop"

        if has_tool_calls and tool_calls:
            self.choices = [Choice(content, tool_calls)]
        else:
            self.choices = [Choice(content)]

    async def chat(self, messages, tools=None):
        return self


class TestAgentLoopBasics(unittest.TestCase):
    """测试 AgentLoop 基础功能�?""

    def test_build_system_prompt(self):
        decision = IntentDecision(
            complexity=Complexity.MODERATE,
            suggested_sources=[KnowledgeSource.RAG, KnowledgeSource.WEB_SEARCH],
            suggested_mode=SuggestedMode.AUTOPILOT,
            risk_level=RiskLevel.MEDIUM,
        )
        prompt = AgentLoop._build_system_prompt(decision, "autopilot")
        self.assertIn("autopilot", prompt)
        self.assertIn("rag", prompt)
        self.assertIn("web_search", prompt)

    def test_has_tool_calls_true(self):
        from types import SimpleNamespace
        mock_tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="web_search",
                arguments='{"query": "test"}',
            ),
        )
        response = MockLLM(has_tool_calls=True, tool_calls=[mock_tc])
        self.assertTrue(AgentLoop._has_tool_calls(response))

    def test_has_tool_calls_false(self):
        response = MockLLM(has_tool_calls=False)
        self.assertFalse(AgentLoop._has_tool_calls(response))

    def test_extract_content(self):
        response = MockLLM(content="Hello world")
        content = AgentLoop._extract_content(response)
        self.assertEqual(content, "Hello world")

    def test_extract_tool_calls(self):
        from types import SimpleNamespace
        mock_tc = SimpleNamespace(
            id="call_1",
            function=SimpleNamespace(
                name="web_search",
                arguments={"query": "test"},
            ),
        )
        response = MockLLM(has_tool_calls=True, tool_calls=[mock_tc])
        calls = AgentLoop._extract_tool_calls(response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "web_search")
        self.assertEqual(calls[0]["function"]["arguments"]["query"], "test")

    def test_format_result(self):
        data = {"result": {"stdout": "ok", "stderr": ""}, "status": "success"}
        formatted = AgentLoop._format_result(data)
        self.assertIn("ok", formatted)

    def test_format_result_string(self):
        data = {"result": "simple result", "status": "success"}
        formatted = AgentLoop._format_result(data)
        self.assertEqual(formatted, "simple result")


class TestPauseState(unittest.TestCase):
    """测试暂停状态�?""

    def test_pause_state_defaults(self):
        state = PauseState()
        self.assertEqual(state.messages, [])
        self.assertIsNone(state.paused_tc)
        self.assertEqual(state.turn, 0)
        self.assertEqual(state.pause_reason, "")
        self.assertIsNone(state.decision)
        self.assertEqual(state.mode, "")
        self.assertEqual(state.available_tools, [])

    def test_pause_state_with_data(self):
        state = PauseState(
            messages=[{"role": "user", "content": "hello"}],
            paused_tc=ToolCall(name="web_search", args={"query": "test"}),
            turn=2,
            pause_reason="consent",
            mode="plan",
        )
        self.assertEqual(len(state.messages), 1)
        self.assertIsNotNone(state.paused_tc)
        self.assertEqual(state.turn, 2)
        self.assertEqual(state.pause_reason, "consent")
        self.assertEqual(state.mode, "plan")


class TestMaxTurns(unittest.TestCase):
    """测试最大轮次限制�?""

    def test_max_turns_constant(self):
        self.assertEqual(AgentLoop.MAX_TURNS, 8)


if __name__ == "__main__":
    unittest.main()
