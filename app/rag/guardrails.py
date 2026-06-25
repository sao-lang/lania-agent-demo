"""轻量 guardrails 与脱敏工具模块。

该模块提供两类基础安全能力：
1. 对用户输入或上下文做规则级 Prompt Injection 检测。
2. 对回答或引用中的常见敏感信息执行正则脱敏。
"""

from __future__ import annotations

import re
from collections import Counter
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

PII_REDACTION_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        'email',
        re.compile(r'(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])'),
        '[REDACTED_EMAIL]',
    ),
    (
        'phone',
        re.compile(r'(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)'),
        '[REDACTED_PHONE]',
    ),
    (
        'id_card',
        re.compile(r'(?<![\dXx])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?![\dXx])'),
        '[REDACTED_ID_CARD]',
    ),
    (
        'secret_key',
        re.compile(r'\b(?:sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{20,})\b'),
        '[REDACTED_SECRET]',
    ),
)


def inspect_prompt_injection(text: str) -> dict[str, Any]:
    """检测输入里有没有明显的 Prompt Injection 或越权指令。

    Args:
        text: 待检查文本，通常来自用户问题或拼接后的上下文。

    Returns:
        包含是否阻断、风险等级、命中规则和主原因的检查结果字典。
    """
    matched_rules = [name for name, pattern in PROMPT_INJECTION_RULES if pattern.search(text or '')]
    blocked = bool(matched_rules)
    return {
        'blocked': blocked,
        'risk': 'high' if blocked else 'low',
        'matched_rules': matched_rules,
        'reason': matched_rules[0] if matched_rules else 'clean',
    }


def redact_text(text: str) -> tuple[str, dict[str, Any]]:
    """对文本里的常见敏感信息做正则脱敏。

    Args:
        text: 待脱敏文本。

    Returns:
        第一项是脱敏后的文本，第二项是脱敏统计摘要。
    """
    redacted = text
    counts: Counter[str] = Counter()
    for name, pattern, replacement in PII_REDACTION_RULES:
        redacted, matched = pattern.subn(replacement, redacted)
        if matched:
            counts[name] += matched
    summary = {
        'applied': bool(counts),
        'replacement_count': sum(counts.values()),
        'matched_types': sorted(counts.keys()),
        'counts': dict(counts),
    }
    return redacted, summary
