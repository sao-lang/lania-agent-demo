"""RAG 系统 Corrective RAG 模块。

实现 Self-RAG 纠正循环：检索 → 生成 → 自检 → 重写（if/else，非 LangGraph）。
"""

from __future__ import annotations

from typing import Any

from app.rag_system.answer.service import AnswerService
from app.rag_system.retrieval.service import RagRetrievalService


class CorrectiveRag:
    """Self-RAG 纠正循环。"""

    def __init__(self, retrieval: RagRetrievalService, answer: AnswerService):
        self.retrieval = retrieval
        self.answer = answer

    def run(
        self,
        question: str,
        collection_name: str,
        top_k: int = 5,
        max_retries: int = 1,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """执行完整的 Corrective RAG 循环。

        Args:
            question: 用户问题。
            collection_name: 知识库名称。
            top_k: 检索数量。
            max_retries: 最大重试次数。

        Returns:
            (最终答案, 引用列表, 执行信息) 的三元组。
        """
        citations = self.retrieval.retrieve(collection_name, question, top_k)
        contexts = [c.text for c in citations]

        answer, info = self.answer.generate_corrective_answer(question, contexts, max_retries)

        citation_dicts = [
            {'chunk_id': c.chunk_id, 'text': c.text[:200], 'source': c.source, 'score': c.score}
            for c in citations
        ]

        return answer, citation_dicts, info
