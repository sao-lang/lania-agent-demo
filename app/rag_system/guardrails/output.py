"""RAG 系统输出护栏模块。

负责对回答或引用中的常见敏感信息执行正则脱敏。
与主应用的 `app/rag/guardrails.py` 功能一致，但独立于主应用。
"""

from __future__ import annotations

import re
from typing import Any

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


def redact_text(text: str) -> tuple[str, dict[str, Any]]:
    """对文本中的常见敏感信息做脱敏替换。

    Args:
        text: 待脱敏的原始文本。

    Returns:
        (脱敏后的文本, 脱敏记录字典) 的二元组。
    """
    redacted = {}
    for rule_name, pattern, replacement in PII_REDACTION_RULES:
        count_before = len(redacted)
        text, count = pattern.subn(replacement, text)
        if count > 0:
            redacted[rule_name] = count
    if not redacted:
        redacted['none'] = True
    return text, redacted
