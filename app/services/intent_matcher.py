"""意图识别模块。

将用户自然语言输入匹配到最合适的 Capability。
使用关键词匹配 + LLM 兜底的双层策略。
"""

from __future__ import annotations

from typing import Any

from app.capabilities.registry import CapabilityRegistry
from app.models.agent import IntentMatch


class IntentMatcher:
    """意图识别器。

    第一层：关键词快速匹配
    第二层：LLM 分类（可选，当关键词匹配置信度不足时）
    """

    # 关键词规则：capability → (关键词列表, 权重)
    KEYWORD_RULES: dict[str, tuple[list[str], float]] = {
        "document_analysis": (
            ["分析", "总结", "归纳", "评估", "审查文档", "文档审查", "深度分析"],
            0.75,
        ),
        "document_summary": (
            ["摘要", "概括", "精简", "简要说明", "几句话总结"],
            0.80,
        ),
        "code_review": (
            ["代码审查", "review", "审查代码", "代码质量", "代码问题", "review代码"],
            0.85,
        ),
        "data_analysis": (
            ["数据分析", "统计", "图表", "趋势", "分析数据", "数据可视化"],
            0.75,
        ),
        "web_research": (
            ["搜索", "查一下", "网上搜索", "互联网", "网页", "查找资料"],
            0.80,
        ),
    }

    def __init__(
        self, registry: CapabilityRegistry, llm: Any | None = None,
    ) -> None:
        self._registry = registry
        self._llm = llm

    async def match(
        self,
        message: str,
        history: list[dict] | None = None,
    ) -> IntentMatch:
        """识别用户意图，返回最匹配的 Capability。

        Args:
            message: 用户输入。
            history: 对话历史（可选，用于上下文理解）。

        Returns:
            意图匹配结果。
        """
        message_lower = message.lower()

        # 第一层：关键词匹配
        keyword_matches: list[tuple[str, float, list[str]]] = []

        for capability, (keywords, weight) in self.KEYWORD_RULES.items():
            matched = [kw for kw in keywords if kw in message]
            if matched:
                # 排除代码审查中的误匹配
                if capability == "document_analysis" and any(
                    kw in message_lower for kw in ["代码", "code"]
                ):
                    continue
                keyword_matches.append((capability, weight, matched))

        if keyword_matches:
            # 按权重降序
            keyword_matches.sort(key=lambda x: x[1], reverse=True)
            name, confidence, matched_kws = keyword_matches[0]
            return IntentMatch(
                capability=name,
                confidence=confidence,
                matched_keywords=matched_kws,
            )

        # 第二层：LLM 分类（关键词未匹配时）
        if self._llm is not None:
            try:
                return await self._llm_match(message)
            except Exception:
                pass

        # 默认：通用对话
        return IntentMatch(capability="chat", confidence=0.5)

    async def _llm_match(self, message: str) -> IntentMatch:
        """使用 LLM 进行意图分类。"""
        capabilities = self._registry.list_enabled()
        capability_names = [c.name for c in capabilities]

        prompt = (
            f"从以下能力中选择最匹配用户意图的一个，只返回能力名称：\n"
            f"能力列表: {', '.join(capability_names)}\n"
            f"用户输入: {message}\n"
            f"能力名称:"
        )

        try:
            response = self._llm.chat([{"role": "user", "content": prompt}])
            name = response.choices[0].message.content.strip()
            if name in capability_names:
                return IntentMatch(capability=name, confidence=0.6)
        except Exception:
            pass

        return IntentMatch(capability="chat", confidence=0.5)
