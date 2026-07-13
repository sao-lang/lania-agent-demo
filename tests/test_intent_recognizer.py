"""测试 IntentRecognizer / QuickHeuristicClassifier / LLMIntentClassifier。"""

import unittest

from app.harness.brain.intent_recognizer import (
    IntentRecognizer,
    LLMIntentClassifier,
    QuickHeuristicClassifier,
)
from app.harness.brain.models import (
    Complexity,
    KnowledgeSource,
    RiskLevel,
    SuggestedMode,
)


class TestQuickHeuristicClassifier(unittest.TestCase):
    """测试规则引擎分类器。"""

    def setUp(self):
        self.classifier = QuickHeuristicClassifier()

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    # ── 数学表达式 ──

    def test_math_expression(self):
        result = self._run(self.classifier.classify("1+1等于几"))
        self.assertIsNotNone(result)
        self.assertEqual(result.complexity, Complexity.SIMPLE)
        self.assertIn(KnowledgeSource.CALCULATOR, result.suggested_sources)
        self.assertEqual(result.suggested_mode, SuggestedMode.CHAT)

    def test_math_expression_calc(self):
        result = self._run(self.classifier.classify("计算 2的平方"))
        self.assertIsNotNone(result)

    def test_math_expression_simple(self):
        result = self._run(self.classifier.classify("sqrt(3^2 + 4^2)"))
        self.assertIsNotNone(result)

    # ── 翻译请求 ──

    def test_translate_chinese_to_english(self):
        result = self._run(self.classifier.classify("翻译Hello World到中文"))
        self.assertIsNotNone(result)
        self.assertEqual(result.suggested_mode, SuggestedMode.CHAT)
        self.assertIn(KnowledgeSource.INTERNAL_LLM, result.suggested_sources)

    def test_translate_english_pattern(self):
        result = self._run(self.classifier.classify("translate this to chinese"))
        self.assertIsNotNone(result)

    # ── 问候 ──

    def test_greeting(self):
        result = self._run(self.classifier.classify("你好"))
        self.assertIsNotNone(result)
        self.assertEqual(result.confidence, 0.95)

    def test_greeting_english(self):
        result = self._run(self.classifier.classify("hello"))
        self.assertIsNotNone(result)

    # ── 搜索请求 ──

    def test_search_web(self):
        result = self._run(self.classifier.classify("搜索今天的新闻"))
        self.assertIsNotNone(result)
        self.assertEqual(result.suggested_mode, SuggestedMode.AUTOPILOT)
        self.assertIn(KnowledgeSource.WEB_SEARCH, result.suggested_sources)

    def test_search_weather(self):
        result = self._run(self.classifier.classify("今天天气怎么样"))
        self.assertIsNotNone(result)

    def test_search_english(self):
        result = self._run(self.classifier.classify("what is the meaning of life"))
        self.assertIsNotNone(result)

    # ── 代码审查 ──

    def test_code_review(self):
        result = self._run(self.classifier.classify("帮我审查这段代码的安全漏洞"))
        self.assertIsNotNone(result)
        self.assertEqual(result.complexity, Complexity.COMPLEX)
        self.assertEqual(result.suggested_mode, SuggestedMode.PLAN)
        self.assertTrue(result.needs_planning)
        self.assertEqual(result.risk_level, RiskLevel.HIGH)

    def test_code_review_security(self):
        result = self._run(self.classifier.classify("代码安全审查"))
        self.assertIsNotNone(result)

    # ── 类型报错/修复 ──

    def test_type_error_fix(self):
        result = self._run(self.classifier.classify("修复这个类型报错"))
        self.assertIsNotNone(result)
        self.assertEqual(result.complexity, Complexity.COMPLEX)
        self.assertEqual(result.risk_level, RiskLevel.HIGH)
        self.assertIn(KnowledgeSource.CODE_REPO, result.suggested_sources)
        self.assertIn(KnowledgeSource.SHELL_CMD, result.suggested_sources)

    def test_tsc_check(self):
        result = self._run(self.classifier.classify("运行 tsc 检查类型错误"))
        self.assertIsNotNone(result)

    # ── 数据库操作 ──

    def test_database_query(self):
        result = self._run(self.classifier.classify("查询数据库中的用户表"))
        self.assertIsNotNone(result)
        self.assertEqual(result.suggested_mode, SuggestedMode.PLAN_CONFIRM)
        self.assertEqual(result.risk_level, RiskLevel.HIGH)

    def test_database_sql(self):
        result = self._run(self.classifier.classify("select * from users"))
        self.assertIsNotNone(result)

    # ── 代码仓库操作 ──

    def test_repository_read(self):
        result = self._run(self.classifier.classify("查看代码库中的 src/main.py 文件"))
        self.assertIsNotNone(result)
        self.assertEqual(result.complexity, Complexity.MODERATE)
        self.assertEqual(result.risk_level, RiskLevel.MEDIUM)

    # ── 数据分析 ──

    def test_data_analysis(self):
        result = self._run(self.classifier.classify("帮我分析这组数据的趋势"))
        self.assertIsNotNone(result)
        self.assertEqual(result.complexity, Complexity.MODERATE)

    # ── 沙箱执行 ──

    def test_sandbox_exec(self):
        result = self._run(self.classifier.classify("帮我运行python代码"))
        self.assertIsNotNone(result)
        self.assertEqual(result.complexity, Complexity.COMPLEX)
        self.assertEqual(result.risk_level, RiskLevel.HIGH)

    # ── 不匹配（应返回 None 交给 LLM） ──

    def test_no_match(self):
        result = self._run(self.classifier.classify("你觉得人工智能未来会怎样发展"))
        self.assertIsNone(result)


class TestIntentRecognizer(unittest.TestCase):
    """测试整体 IntentRecognizer（无 LLM fallback）。"""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_recognizer_rule_only(self):
        recognizer = IntentRecognizer(llm=None, enable_llm_fallback=False)
        decision = self._run(recognizer.recognize("1+1等于几"))
        self.assertEqual(decision.complexity, Complexity.SIMPLE)
        self.assertIn(KnowledgeSource.CALCULATOR, decision.suggested_sources)

    def test_recognizer_no_match_fallback(self):
        recognizer = IntentRecognizer(llm=None, enable_llm_fallback=False)
        decision = self._run(recognizer.recognize("你觉得人类会移民火星吗"))
        self.assertEqual(decision.complexity, Complexity.SIMPLE)
        self.assertEqual(decision.suggested_mode, SuggestedMode.CHAT)
        self.assertEqual(decision.confidence, 0.5)

    def test_recognizer_chinese_greeting(self):
        recognizer = IntentRecognizer(llm=None)
        decision = self._run(recognizer.recognize("您好"))
        self.assertEqual(decision.confidence, 0.95)

    def test_recognizer_code_fix(self):
        recognizer = IntentRecognizer(llm=None)
        decision = self._run(recognizer.recognize("解决当前项目中的类型报错"))
        self.assertTrue(decision.needs_planning)
        self.assertEqual(decision.risk_level, RiskLevel.HIGH)


class TestLLMIntentClassifier(unittest.TestCase):
    """测试 LLM 分类器（模拟 LLM 失败的情况）。"""

    def _run(self, coro):
        import asyncio
        return asyncio.run(coro)

    def test_default_on_no_llm(self):
        classifier = LLMIntentClassifier(llm=None)
        decision = self._run(classifier.classify("some question"))
        self.assertEqual(decision.complexity, Complexity.MODERATE)
        self.assertEqual(decision.suggested_mode, SuggestedMode.CHAT)
        self.assertEqual(decision.risk_level, RiskLevel.LOW)
        self.assertEqual(decision.confidence, 0.4)

    def test_parse_llm_response_valid(self):
        classifier = LLMIntentClassifier(llm=None)
        content = (
            '{"complexity": "complex", "suggested_sources": ["rag", "web_search"], '
            '"suggested_mode": "plan", "needs_planning": true, '
            '"risk_level": "medium", "reasoning": "需要多来源", '
            '"matched_capabilities": ["rag"]}'
        )
        decision = classifier._parse_llm_response(content)
        self.assertEqual(decision.complexity, Complexity.COMPLEX)
        self.assertEqual(len(decision.suggested_sources), 2)
        self.assertEqual(decision.suggested_mode, SuggestedMode.PLAN)
        self.assertTrue(decision.needs_planning)
        self.assertEqual(decision.risk_level, RiskLevel.MEDIUM)

    def test_parse_llm_response_invalid_json(self):
        classifier = LLMIntentClassifier(llm=None)
        decision = classifier._parse_llm_response("not json at all")
        self.assertEqual(decision.complexity, Complexity.MODERATE)  # 兜底

    def test_parse_llm_response_missing_fields(self):
        classifier = LLMIntentClassifier(llm=None)
        content = '{"complexity": "simple"}'
        decision = classifier._parse_llm_response(content)
        self.assertEqual(decision.complexity, Complexity.SIMPLE)
        self.assertEqual(decision.risk_level, RiskLevel.MEDIUM)  # 默认

    def test_safe_enum_valid(self):
        result = LLMIntentClassifier._safe_enum("simple", Complexity, Complexity.MODERATE)
        self.assertEqual(result, Complexity.SIMPLE)

    def test_safe_enum_invalid(self):
        result = LLMIntentClassifier._safe_enum("unknown", Complexity, Complexity.MODERATE)
        self.assertEqual(result, Complexity.MODERATE)

    def test_safe_enum_none(self):
        result = LLMIntentClassifier._safe_enum(None, Complexity, Complexity.SIMPLE)
        self.assertEqual(result, Complexity.SIMPLE)

    def test_safe_sources_valid(self):
        sources = LLMIntentClassifier._safe_sources(["rag", "web_search"])
        self.assertEqual(len(sources), 2)
        self.assertIn(KnowledgeSource.RAG, sources)

    def test_safe_sources_mixed(self):
        sources = LLMIntentClassifier._safe_sources(["rag", "unknown_source"])
        self.assertEqual(len(sources), 1)
        self.assertIn(KnowledgeSource.RAG, sources)

    def test_safe_sources_empty(self):
        sources = LLMIntentClassifier._safe_sources([])
        self.assertEqual(len(sources), 1)
        self.assertIn(KnowledgeSource.INTERNAL_LLM, sources)


if __name__ == "__main__":
    unittest.main()
