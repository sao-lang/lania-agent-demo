"""RAG 系统输入护栏模块。

负责对用户输入做规则级 Prompt Injection 检测。
与主应用的 `app/rag/guardrails.py` 功能一致，但独立于主应用。
"""

from __future__ import annotations

import re
from typing import Any

PROMPT_INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        'ignore_previous_instructions',
        re.compile(
            r'(?is)\b(ignore|disregard|forget|bypass|override)\b.{0,40}\b(previous|above|earlier|system|developer|instruction|rule)s?\b'
        ),
    ),
    (
        'system_prompt_exfiltration',
        re.compile(
            r'(?is)\b(reveal|show|print|dump|expose|display)\b.{0,40}\b(system prompt|developer message|hidden instruction|secret prompt)\b'
        ),
    ),
    (
        'role_override',
        re.compile(r'(?is)\b(act as|pretend to be|you are now|jailbreak|dan mode)\b'),
    ),
    (
        'chinese_ignore_rules',
        re.compile(r'(?:忽略|无视|绕过|跳过).{0,20}(?:之前|上面|以上|系统|开发者).{0,20}(?:提示|指令|规则|要求)'),
    ),
    (
        'chinese_prompt_exfiltration',
        re.compile(r'(?:输出|打印|展示|泄露|透露|显示).{0,24}(?:系统提示词|提示词|开发者消息|隐藏指令|系统指令)'),
    ),
    (
        'secret_exfiltration',
        re.compile(r'(?is)\b(api[_ -]?key|token|secret|password|credential)s?\b.{0,24}\b(show|reveal|print|dump|export)\b'),
    ),
)


def inspect_prompt_injection(text: str) -> dict[str, Any]:
    """检测输入里有没有明显的 Prompt Injection 或越权指令。

    Args:
        text: 待检查文本。

    Returns:
        包含是否阻断、风险等级、命中规则和主原因的检查结果字典。
    """
    matched_rules = [name for name, pattern in PROMPT_INJECTION_RULES if pattern.search(text or '')]
    blocked = bool(matched_rules)
    return {
        'blocked': blocked,
        'risk': 'high' if blocked else 'none',
        'matched_rule': matched_rules[0] if matched_rules else None,
        'matched_rules': matched_rules,
        'reason': f'触发规则: {matched_rules[0]}' if matched_rules else '',
    }
