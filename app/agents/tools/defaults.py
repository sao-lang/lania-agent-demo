"""默认工具注册辅助模块。

集中定义 query/task runtime 复用的 RAG 主路径工具集合，避免不同入口重复拼装
同一批核心工具，同时确保公开 task schema 与运行时可见工具保持一致。
"""

from __future__ import annotations

from app.agents.tools.base import AgentTool
from app.agents.tools.rag_tools import (
    RagGroundedAnswerTool,
    RagGroundedQueryTool,
    RagLoadDocumentContextTool,
    RagRetrieveEvidenceTool,
)


def build_runtime_rag_tools() -> tuple[AgentTool, ...]:
    """返回运行时默认启用的 RAG 主路径工具集合。

    Returns:
        供 query runtime、task runtime 与任务 schema 共同复用的工具元组。
    """

    return (
        RagLoadDocumentContextTool(),
        RagRetrieveEvidenceTool(),
        RagRetrieveEvidenceTool(use_graph_rag=True),
        RagGroundedAnswerTool(),
        RagGroundedQueryTool(),
    )
