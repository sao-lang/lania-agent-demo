"""工具输出内容安全策略：检查工具输出是否包含可注入 LLM 的恶意内容。"""

from __future__ import annotations

import re
from typing import Any

from app.harness.brain.models import SafetyContext, SafetyDecision
from app.harness.safety.engine import SafetyPolicy


class ToolOutputContentPolicy(SafetyPolicy):
    """检查工具输出是否包含可注入 LLM 的恶意内容。

    这个策略解决：web_search 抓取到的网页、read_file 读取的代码
    可能包含 Prompt Injection 文本，直接喂给 LLM 会污染后续行为。
    """

    name = "tool_output_content"
    description = "检查工具输出（网页/文件内容）是否包含 prompt injection 模式"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        tc = cfg.get("tool_output_content", {})

        self.injection_patterns: list[str] = tc.get("injection_patterns", [
            r"(?i)(ignore|disregard|forget).{0,20}(previous|above|instruction)",
            r"(?i)(you are now|act as|pretend to be)",
            r"(?i)(system prompt|developer message|hidden instruction)",
        ])

    async def check(self, context: SafetyContext) -> SafetyDecision:
        output_text = context.raw.get("output_text", "")
        if not output_text:
            return SafetyDecision(allowed=True, level="pass")

        for pattern in self.injection_patterns:
            if re.search(pattern, output_text):
                return SafetyDecision(
                    allowed=False, level="block",
                    reason="工具输出包含潜在的 prompt injection 内容",
                    details={"pattern": pattern, "category": "tool_output_content"},
                )

        return SafetyDecision(allowed=True, level="pass")
