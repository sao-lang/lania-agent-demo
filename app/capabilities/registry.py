"""Capability 注册表模块。

管理 Agent 能力的注册、查询和意图匹配。
Capability 是 Agent "会做什么"的抽象，每个 Capability 对应一个或多个 Workflow。
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.models.agent import CapabilityInfo


class CapabilityDefinition(BaseModel):
    """Capability 的完整定义。"""

    name: str
    display_name: str
    description: str
    workflow_type: str | None = None
    requires: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    prompt_instructions: str = ""
    is_default: bool = False
    enabled: bool = True


class CapabilityProvider(Protocol):
    """Capability 的执行提供者协议。

    每个 Capability 可以有自己的执行逻辑（如直接 LLM 调用），
    也可以委托给现有的 TaskWorkflow。
    """

    name: str

    async def execute(
        self,
        message: str,
        context: dict[str, Any],
    ) -> Any:
        """执行 Capability 的逻辑。"""


class CapabilityRegistry:
    """Capability 注册表。

    管理所有注册的 Capability 定义，提供查询和意图匹配功能。
    新增 Capability 只需要 register()，不需要改其他代码。
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._providers: dict[str, CapabilityProvider] = {}

    # ── 注册 ──────────────────────────────────

    def register(self, definition: CapabilityDefinition) -> None:
        """注册一个 Capability 定义。"""
        self._capabilities[definition.name] = definition

    def register_provider(self, provider: CapabilityProvider) -> None:
        """注册一个 Capability 的执行提供者。"""
        self._capabilities[provider.name].enabled = True
        self._providers[provider.name] = provider

    def register_many(self, definitions: list[CapabilityDefinition]) -> None:
        """批量注册 Capability 定义。"""
        for d in definitions:
            self.register(d)

    # ── 查询 ──────────────────────────────────

    def get(self, name: str) -> CapabilityDefinition | None:
        return self._capabilities.get(name)

    def get_provider(self, name: str) -> CapabilityProvider | None:
        return self._providers.get(name)

    def list(self) -> list[CapabilityInfo]:
        return [
            CapabilityInfo(
                name=d.name,
                display_name=d.display_name,
                description=d.description,
                enabled=d.enabled,
                requires=list(d.requires),
                is_default=d.is_default,
            )
            for d in self._capabilities.values()
        ]

    def list_enabled(self) -> list[CapabilityInfo]:
        return [c for c in self.list() if c.enabled]

    def get_default(self) -> str:
        """返回默认 Capability 名称。"""
        for d in self._capabilities.values():
            if d.is_default:
                return d.name
        return "chat"

    # ── 意图匹配 ──────────────────────────────

    def match_by_keywords(self, message: str) -> list[tuple[str, float]]:
        """通过关键词匹配最合适的 Capability。

        Returns:
            (capability_name, confidence) 列表，按置信度降序。
        """
        matches: list[tuple[str, float]] = []

        # 文档分析
        if any(kw in message for kw in ["分析", "总结", "归纳", "评估"]):
            if not any(kw in message for kw in ["代码", "代码审查", "review"]):
                matches.append(("document_analysis", 0.75))

        # 代码审查
        if any(kw in message for kw in ["代码", "代码审查", "review", "审查代码"]):
            matches.append(("code_review", 0.8))

        # 文档摘要
        if any(kw in message for kw in ["摘要", "概括", "精简"]):
            matches.append(("document_summary", 0.8))

        # 联网搜索
        if any(kw in message for kw in ["搜索", "查一下", "网上", "互联网"]):
            matches.append(("web_search", 0.8))

        # 数据分析
        if any(kw in message for kw in ["数据", "统计", "图表", "趋势", "分析数据"]):
            matches.append(("data_analysis", 0.7))

        # 代码助手 (coding)
        if any(kw in message for kw in ["代码分析", "lint", "代码检查", "静态分析", "编码助手", "coding", "代码扫描", "质量检查"]):
            matches.append(("coding", 0.85))

        # 按置信度降序
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches

    def match(self, message: str) -> str:
        """匹配用户输入到最合适的 Capability。

        返回能力名称，无匹配时返回 'chat'。
        """
        matches = self.match_by_keywords(message)
        if matches:
            name, _ = matches[0]
            if self.get(name) and self._capabilities[name].enabled:
                return name
        return "chat"


# ── 默认 Capability 定义 ─────────────────────

def build_default_capabilities() -> list[CapabilityDefinition]:
    """构建内建的 Capability 定义列表。"""
    return [
        CapabilityDefinition(
            name="chat",
            display_name="通用对话",
            description="通用对话能力，直接使用 LLM 回答用户问题",
            workflow_type=None,
            is_default=True,
        ),
        CapabilityDefinition(
            name="document_analysis",
            display_name="文档分析",
            description="对集合中的文档进行深度分析，提取关键发现、风险点并生成结构化报告",
            workflow_type="document_analysis",
            requires=["knowledge", "repository"],
            tools=[
                "rag_load_document_context", "rag_retrieve_evidence",
                "rag_retrieve_graph_evidence", "extract_key_points",
                "extract_risks", "draft_report", "review_report",
            ],
        ),
        CapabilityDefinition(
            name="document_summary",
            display_name="文档摘要",
            description="对文档进行摘要，提取核心内容",
            workflow_type="document_summary",
            requires=["knowledge"],
            tools=["rag_load_document_context"],
        ),
        CapabilityDefinition(
            name="code_review",
            display_name="代码审查",
            description="对仓库代码进行自动化审查，发现潜在问题",
            workflow_type="coding_review",
            requires=["repository"],
            tools=[
                "list_repository_files", "read_repository_file",
                "search_repository",
            ],
            enabled=True,
        ),
        CapabilityDefinition(
            name="data_analysis",
            display_name="数据分析",
            description="对数据进行查询、分析和可视化",
            workflow_type="data_analysis",
            requires=["database"],
            tools=[
                "list_database_tables", "describe_database_table",
                "query_database", "shell_command",
            ],
            enabled=True,
        ),
        CapabilityDefinition(
            name="web_search",
            display_name="联网搜索",
            description="搜索互联网，获取实时信息并生成回答",
            workflow_type=None,
            requires=[],
            tools=[],
            enabled=True,
        ),
        CapabilityDefinition(
            name="coding",
            display_name="代码助手",
            description="对仓库代码进行自动化审查、lint 检查和 LLM 多维度分析，识别潜在问题并给出改进建议",
            workflow_type="coding",
            requires=["repository"],
            tools=[
                "list_repository_files", "read_repository_file",
                "search_repository", "shell_command",
                "extract_code_issues", "run_code_analysis",
            ],
            enabled=True,
        ),
    ]


def build_default_registry() -> CapabilityRegistry:
    """构建包含默认 Capability 的注册表。"""
    registry = CapabilityRegistry()
    registry.register_many(build_default_capabilities())
    return registry
