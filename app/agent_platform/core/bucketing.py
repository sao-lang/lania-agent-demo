"""评测分桶工具模块。

负责把反馈样本归到较稳定的 bucket，方便后续评测统计和报表按问题类型看分布。
"""

from __future__ import annotations

import re


def normalize_bucket(value: str) -> str:
    """把 bucket 名称清洗成稳定可比较的格式。

    Args:
        value: 原始 bucket 名称。

    Returns:
        只包含小写字母、数字、下划线和连字符的规范化 bucket 名称。
    """
    cleaned = re.sub(r'[^0-9A-Za-z_-]+', '_', str(value or '').strip().lower())
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or 'default'


def infer_bucket(
    question: str,
    reference: str,
    *,
    feedback_type: str | None = None,
    note: str | None = None,
    metadata: dict | None = None,
) -> str:
    """根据问题、参考答案和补充信息推断样本 bucket。

    Args:
        question: 原始问题文本。
        reference: 参考答案文本。
        feedback_type: 可选反馈类型。
        note: 可选补充说明。
        metadata: 可选结构化元数据，支持显式覆盖 bucket。

    Returns:
        推断出的稳定 bucket 名称。
    """
    metadata = metadata or {}
    override = metadata.get('bucket')
    if override is not None:
        return normalize_bucket(str(override))

    note_text = str(note or '')
    match = re.search(r'(?:^|\s)bucket\s*:\s*([0-9A-Za-z_-]{2,40})(?:\s|$)', note_text, flags=re.IGNORECASE)
    if match:
        return normalize_bucket(match.group(1))

    q = str(question or '').strip().lower()
    r = str(reference or '').strip().lower()

    api_hints = ('/api', 'http', 'curl', 'get ', 'post ', 'put ', 'delete ', 'endpoint', '接口', 'stream', 'sse')
    if any(hint in q or hint in r for hint in api_hints):
        return 'api'

    policy_hints = ('制度', '规定', '条例', '合规', '审批', '权限', '保密', '风控', '审计')
    if any(hint in q or hint in r for hint in policy_hints):
        return 'policy'

    if any(hint in q for hint in ('总结', '概括', '梳理', '归纳', '提炼')) or len(reference) >= 220:
        return 'summary'

    howto_hints = ('如何', '怎么', '步骤', '配置', '安装', '部署', '排查', '修复', '解决')
    if any(hint in q for hint in howto_hints):
        return 'howto'

    if any(hint in q for hint in ('是什么', '定义', '含义')) or len(question.strip()) <= 12:
        return 'faq'

    if feedback_type:
        return normalize_bucket(f'feedback_{feedback_type}')

    return 'default'
