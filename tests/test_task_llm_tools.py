"""验证依赖 LLM 的任务分析工具优先采用模型输出。"""

import unittest
from pathlib import Path

from app.agents.memory import TaskMemory
from app.agents.tools.analysis_tools import ExtractKeyPointsInput, ExtractKeyPointsTool, ExtractRisksInput, ExtractRisksTool
from app.agents.tools.artifact_tools import ReviewReportInput, ReviewReportTool
from app.agents.tools.base import ToolContext
from app.core.config import Settings
from app.models.artifact import EvidenceItem, EvidencePack, FindingItem, ReportArtifactContent, RiskItem
from app.rag.observability import TraceRecorder
from app.services.state import InMemoryState


class FakeLLM:
    """根据提示内容返回固定 JSON，便于验证工具解析行为。"""

    def complete(self, prompt: str):
        if '"key_findings"' in prompt:
            return '{"summary":"LLM 总结","key_findings":[{"title":"核心模块稳定","summary":"系统由调度、检索和缓存构成。","citation_ids":["c1"],"tags":["architecture"]}],"open_questions":["异常处理边界待确认"],"confidence":0.86}'
        if '"risks"' in prompt:
            return '{"risks":[{"title":"异常处理不足","description":"缺少系统化异常处理策略。","severity":"high","citation_ids":["c2"],"recommendation":"补充失败重试与告警闭环。"}]}'
        return '{"passed":true,"unsupported_claims":[],"missing_sections":[],"review_notes":["LLM 审查通过。"]}'


class TaskLLMToolsTests(unittest.TestCase):
    """覆盖关键信息提取、风险提取和报告审查工具。"""

    def setUp(self) -> None:
        """构造带有假 LLM 与证据包的工具执行上下文。"""
        self.settings = Settings(DATA_DIR=Path('/tmp/task-llm-tools'))
        self.trace = TraceRecorder()
        self.context = ToolContext(
            state=InMemoryState(),
            retrieval=None,
            trace=self.trace,
            task_memory=TaskMemory(InMemoryState()),
            settings=self.settings,
            llm=FakeLLM(),
            task_id='task-1',
        )
        self.evidence_pack = EvidencePack(
            task_id='task-1',
            evidence_items=[
                EvidenceItem(citation_id='c1', source='design.md', chunk_id='chunk-1', text='系统由调度、检索和缓存构成。'),
                EvidenceItem(citation_id='c2', source='design.md', chunk_id='chunk-2', text='当前风险包括异常处理缺失。'),
            ],
            coverage_score=0.8,
            missing_aspects=['容量评估'],
        )

    def test_extract_key_points_prefers_llm_output(self) -> None:
        """验证关键点提取工具会优先采用 LLM 生成的结构化结果。"""
        result = ExtractKeyPointsTool().run(
            ExtractKeyPointsInput(instructions='总结核心模块', evidence_pack=self.evidence_pack),
            self.context,
        )

        self.assertEqual(result.summary, 'LLM 总结')
        self.assertEqual(result.key_findings[0].title, '核心模块稳定')
        self.assertEqual(result.key_findings[0].citation_ids, ['c1'])

    def test_extract_risks_prefers_llm_output(self) -> None:
        """验证风险提取工具会优先采用 LLM 返回的风险列表。"""
        result = ExtractRisksTool().run(
            ExtractRisksInput(instructions='识别风险点', evidence_pack=self.evidence_pack),
            self.context,
        )

        self.assertEqual(result.risks[0].title, '异常处理不足')
        self.assertEqual(result.risks[0].severity, 'high')

    def test_review_report_merges_llm_notes(self) -> None:
        """验证报告审查工具会把 LLM 审查意见合并进最终结果。"""
        content = ReportArtifactContent(
            summary='报告摘要',
            key_findings=[FindingItem(finding_id='finding-1', title='发现', summary='摘要', citation_ids=['c1'])],
            risks=[RiskItem(risk_id='risk-1', title='风险', description='描述', severity='medium', citation_ids=['c2'])],
            evidence=self.evidence_pack.evidence_items,
            open_questions=['待确认 1'],
            confidence=0.7,
            report_markdown='# 文档分析报告',
            report_json={'summary': '报告摘要'},
        )
        result = ReviewReportTool().run(ReviewReportInput(content=content), self.context)

        self.assertTrue(result.passed)
        self.assertIn('LLM 审查通过。', result.review_notes)


if __name__ == '__main__':
    unittest.main()
