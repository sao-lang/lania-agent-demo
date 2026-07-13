"""RAG 系统 RAGAS 评测模块。

负责对 RAG 查询结果进行 RAGAS 指标评测。
独立于主应用的评测系统，仅包含 RAG 专有评测。
"""

from __future__ import annotations

from typing import Any


class RagasEvaluator:
    """RAGAS 评测适配器。

    独立于主应用的 EvalService，只关注 RAG 质量指标。
    """

    def __init__(self, llm: Any | None = None):
        """初始化 RAGAS 评测器。"""
        self.llm = llm

    def evaluate(
        self,
        questions: list[str],
        answers: list[str],
        contexts: list[list[str]],
        ground_truths: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行 RAGAS 评测。

        Args:
            questions: 问题列表。
            answers: 回答列表。
            contexts: 每个问题对应的检索上下文列表。
            ground_truths: 可选的参考答案列表。

        Returns:
            评测结果字典。
        """
        try:
            from ragas import evaluate
            from ragas.metrics import (
                answer_relevancy,
                context_precision,
                context_recall,
                faithfulness,
            )

            dataset = {
                'question': questions,
                'answer': answers,
                'contexts': contexts,
            }
            if ground_truths:
                dataset['ground_truth'] = ground_truths

            result = evaluate(
                dataset,
                metrics=[
                    faithfulness,
                    answer_relevancy,
                    context_precision,
                    context_recall,
                ],
                llm=self.llm,
            )
            return {
                'faithfulness': result.get('faithfulness', 0),
                'answer_relevancy': result.get('answer_relevancy', 0),
                'context_precision': result.get('context_precision', 0),
                'context_recall': result.get('context_recall', 0),
            }
        except Exception as exc:
            return {'error': str(exc)}
