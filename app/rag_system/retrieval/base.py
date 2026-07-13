"""RAG 系统检索基础工具模块。

提供检索模块共享的基础类型和工具方法，避免循环导入。
"""

from __future__ import annotations

import re
from typing import Any


class RetrievalTypingMixin:
    """为检索模块提供基础工具方法和类型定义。"""

    QUERY_FILLER_TERMS = ('请问', '麻烦', '帮我', '帮忙', '一下', '一下子', '看看', '看下', '告诉我', '我想知道')
    QUERY_REWRITE_SYNONYMS: dict[str, str] = {
        '怎么': '如何', '咋': '如何', '怎样': '如何',
        '查看': '查看', '看': '查看', '改动': '变更', '更新': '增量更新',
        '删掉': '删除', '会话': 'session', '聊天记忆': '多轮对话上下文',
        '对话历史': '多轮对话上下文', '知识库': 'collection', '重建': '重建索引',
    }
    DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
        'session': ('会话',),
        'sse': ('流式输出', 'stream'),
        'stream': ('流式输出',),
        'rerank': ('重排',),
        'ragas': ('评测', 'evaluation'),
        'eval': ('评测', 'evaluation'),
        'query': ('检索问答',),
        'chat': ('多轮对话',),
        'api': ('接口', 'endpoint'),
        'endpoint': ('接口',),
        'embedding': ('向量嵌入',),
        'llm': ('大模型',),
        'collection': ('知识库',),
        'document': ('文档',),
    }
