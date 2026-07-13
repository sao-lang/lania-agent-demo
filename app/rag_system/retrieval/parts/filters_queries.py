"""RAG 系统检索过滤与查询模块。

实现元数据过滤引擎和查询改写逻辑。
与主应用的 `app/rag/retrieval_parts/filters_queries.py` 功能一致。
"""

from __future__ import annotations

import re
from typing import Any

from app.rag_system.retrieval.base import RetrievalTypingMixin


class RetrievalFilterQueryMixin(RetrievalTypingMixin):
    """提供元数据过滤引擎与查询相关能力。"""

    def _matches_filters(self, metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        """检查元数据是否匹配过滤条件。"""
        if not filters:
            return True
        for key, condition in filters.items():
            if key == '_logic' or key.startswith('_'):
                continue
            value = metadata.get(key)
            if isinstance(condition, dict):
                for op, op_val in condition.items():
                    if not self._apply_operator(value, op, op_val):
                        return False
            else:
                if value != condition:
                    return False
        return True

    def _apply_operator(self, value: Any, op: str, target: Any) -> bool:
        """应用单个过滤运算符。"""
        if op == 'eq':
            return value == target
        elif op == 'ne':
            return value != target
        elif op == 'gte' or op == 'ge':
            try:
                return float(value or 0) >= float(target)
            except (ValueError, TypeError):
                return False
        elif op == 'lte' or op == 'le':
            try:
                return float(value or 0) <= float(target)
            except (ValueError, TypeError):
                return False
        elif op == 'gt':
            try:
                return float(value or 0) > float(target)
            except (ValueError, TypeError):
                return False
        elif op == 'lt':
            try:
                return float(value or 0) < float(target)
            except (ValueError, TypeError):
                return False
        elif op == 'in':
            return value in (target if isinstance(target, (list, tuple)) else [target])
        elif op == 'nin':
            return value not in (target if isinstance(target, (list, tuple)) else [target])
        elif op == 'prefix':
            return str(value or '').startswith(str(target))
        elif op == 'contains':
            return str(target).lower() in str(value or '').lower()
        return True

    def _normalize_permission(self, value: str) -> str:
        """权限别名归一化。"""
        mapping = {
            'public': 'public',
            '公开': 'public',
            'internal': 'internal',
            '内部': 'internal',
            'private': 'private',
            '私有': 'private',
            'restricted': 'restricted',
            'confidential': 'confidential',
            '机密': 'confidential',
        }
        return mapping.get(value.lower().strip(), value)

    # ── 查询改写工具 ──

    def _keyword_only_query(self, question: str) -> str:
        """提取关键词构成精简查询。"""
        tokens = re.findall(r"[0-9A-Za-z_一-鿿]+", question)
        return ' '.join(tokens)

    def _split_query_segments(self, question: str) -> list[str]:
        """按标点拆分查询片段。"""
        segments = re.split(r'[，。；：、？\n,!?:;]+', question)
        return [s.strip() for s in segments if len(s.strip()) > 1]

    def _drop_generic_terms(self, tokens: list[str]) -> list[str]:
        """丢弃泛用词。"""
        GENERIC = {'请问', '麻烦', '帮我', '帮忙', '一下', '看看', '告诉我', '我想知道',
                   'please', 'help', 'could', 'would', 'want', 'need'}
        return [t for t in tokens if t.lower() not in GENERIC]

    def _normalize_query_text(self, question: str) -> str:
        """统一查询文本格式。"""
        question = re.sub(r'\s+', ' ', question).strip()
        return self._remove_filler_terms(question)

    def _remove_filler_terms(self, text: str) -> str:
        """移除填充词。"""
        for filler in self.QUERY_FILLER_TERMS:
            text = text.replace(filler, '')
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _apply_synonym_replacements(self, text: str) -> str:
        """应用同义替换。"""
        for old, new in self.QUERY_REWRITE_SYNONYMS.items():
            text = text.replace(old, new)
        return text

    def _expand_domain_hints(self, question: str) -> str:
        """扩展领域提示词。"""
        expanded = [question]
        q_lower = question.lower()
        for hint_key in self.DOMAIN_HINTS:
            if hint_key in q_lower:
                for hint_val in self.DOMAIN_HINTS[hint_key]:
                    if hint_val not in question:
                        expanded.append(hint_val)
        return ' '.join(expanded)

    def _deduplicate_query_terms(self, tokens: list[str]) -> list[str]:
        """去重查询词。"""
        seen: set[str] = set()
        result: list[str] = []
        for t in tokens:
            if t.lower() not in seen:
                seen.add(t.lower())
                result.append(t)
        return result

    def _candidate_key(self, citation: Any) -> str:
        """生成引用的去重键。"""
        return getattr(citation, 'chunk_id', str(id(citation)))

    def _format_citation_source(self, meta: dict[str, Any]) -> str:
        """格式化引用来源。"""
        parts = []
        if meta.get('file_name'):
            parts.append(str(meta['file_name']))
        if meta.get('section_title'):
            parts.append(f"§{meta['section_title']}")
        if meta.get('page'):
            parts.append(f"p{meta['page']}")
        return ' > '.join(parts) if parts else 'unknown'
