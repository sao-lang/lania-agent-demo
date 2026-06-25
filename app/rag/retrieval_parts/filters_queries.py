"""`retrieval.py` 的过滤解释与查询改写子模块。

负责 metadata filter 解释、词法打分、查询归一化与多查询扩展，
降低主文件在规则细节上的体量。
"""

from __future__ import annotations

import importlib
from importlib.util import find_spec
import math
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from typing import Any

from llama_index.core import VectorStoreIndex

from app.core.config import Settings
from app.models.query import CitationItem
from app.rag.llamaindex_components import build_embed_model, build_metadata_filters, build_vector_store
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.graph_service import GraphService
from app.services.state import InMemoryState
from app.types import MetadataFilters as MetadataFiltersMap
from app.rag.retrieval_parts._typing import RetrievalTypingMixin


class RetrievalFilterQueryMixin(RetrievalTypingMixin):
    """封装检索服务中的过滤解释、查询规整和词法评分辅助逻辑。"""

    def _matches_filters(self, metadata: dict, filters: dict | None) -> bool:
        """判断文档元数据是否满足接口层过滤条件。"""
        if not filters:
            return True

        for key, value in filters.items():
            current = metadata.get(key)
            if current is None:
                return False

            if key == 'tags' or key.endswith('tags'):
                # tags 支持“包含全部”与“包含任意”两种模式，便于 API 层表达多标签筛选。
                current_tags = {item for item in str(current).split('|') if item}
                if isinstance(value, dict):
                    mode = value.get('mode') or value.get('op') or 'all'
                    expected = value.get('values') or value.get('in') or []
                    expected_tags = {str(item) for item in expected}
                    if mode in ('any', 'contains_any'):
                        if not (current_tags & expected_tags):
                            return False
                    else:
                        if not expected_tags.issubset(current_tags):
                            return False
                else:
                    expected_tags = {str(item) for item in value} if isinstance(value, list) else {str(value)}
                    if not expected_tags.issubset(current_tags):
                        return False
                continue

            normalized_value = value
            normalized_current = current
            if key == 'year':
                try:
                    normalized_current = int(str(current))
                except Exception:
                    return False
                if isinstance(value, list):
                    normalized_value = [int(str(item)) for item in value]
                elif isinstance(value, dict):
                    normalized_value = {
                        k: [int(str(item)) for item in v] if k == 'in' and isinstance(v, list) else int(str(v))
                        for k, v in value.items()
                        if k in {'gte', 'lte', 'eq', 'in'}
                    }
                else:
                    normalized_value = int(str(value))
            elif key == 'quarter':
                normalized_current = self._normalize_quarter(current)
                if normalized_current is None:
                    return False

                def _normalize_quarter(item: Any) -> str:
                    return self._normalize_quarter(item) or ''

                if isinstance(value, list):
                    normalized_value = [_normalize_quarter(item) for item in value]
                elif isinstance(value, dict):
                    normalized_value = {
                        k: [_normalize_quarter(item) for item in v] if k == 'in' and isinstance(v, list) else _normalize_quarter(v)
                        for k, v in value.items()
                        if k in {'eq', 'in'}
                    }
                else:
                    normalized_value = _normalize_quarter(value)
            elif key == 'permission':
                normalized_current = self._normalize_permission(current)
                if normalized_current is None:
                    return False
                if isinstance(value, list):
                    normalized_value = [self._normalize_permission(item) or '' for item in value]
                elif isinstance(value, dict):
                    normalized_value = {
                        k: [self._normalize_permission(item) or '' for item in v]
                        if k == 'in' and isinstance(v, list)
                        else self._normalize_permission(v)
                        for k, v in value.items()
                        if k in {'eq', 'in'}
                    }
                else:
                    normalized_value = self._normalize_permission(value)

            if key == 'version' and isinstance(value, dict):
                prefix = value.get('prefix')
                if prefix is not None and not str(current).startswith(str(prefix)):
                    return False
                expected = value.get('eq')
                if expected is not None and str(current) != str(expected):
                    return False
                if 'in' in value:
                    normalized_in = {str(item) for item in value.get('in', [])}
                    if str(current) not in normalized_in:
                        return False
                continue

            if isinstance(normalized_value, dict):
                if 'eq' in normalized_value and str(normalized_current) != str(normalized_value['eq']):
                    return False
                expected_in = normalized_value.get('in')
                if expected_in is not None:
                    if isinstance(expected_in, list):
                        normalized_in = {str(item) for item in expected_in}
                    else:
                        normalized_in = {str(expected_in)}
                    if str(normalized_current) not in normalized_in:
                        return False
                if 'gte' in normalized_value:
                    threshold = normalized_value['gte']
                    if isinstance(normalized_current, int) and isinstance(threshold, int):
                        if normalized_current < threshold:
                            return False
                    elif str(normalized_current) < str(threshold):
                        return False
                if 'lte' in normalized_value:
                    threshold = normalized_value['lte']
                    if isinstance(normalized_current, int) and isinstance(threshold, int):
                        if normalized_current > threshold:
                            return False
                    elif str(normalized_current) > str(threshold):
                        return False
                continue

            if isinstance(normalized_value, list):
                if str(normalized_current) not in {str(item) for item in normalized_value}:
                    return False
            elif str(normalized_current) != str(normalized_value):
                return False
        return True

    def _normalize_quarter(self, value: Any) -> str | None:
        """把季度值标准化为 Q1-Q4。"""
        if value in (None, ''):
            return None
        text = str(value).upper().strip()
        if text.isdigit():
            text = f"Q{text}"
        if text in {'Q1', 'Q2', 'Q3', 'Q4'}:
            return text
        return None

    def _normalize_permission(self, value: Any) -> str | None:
        """把权限值标准化为统一枚举。"""
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        alias_map = {
            'public': 'public',
            'open': 'public',
            '公开': 'public',
            'internal': 'internal',
            'intranet': 'internal',
            '内部': 'internal',
            'private': 'private',
            '私有': 'private',
            'restricted': 'restricted',
            'sensitive': 'restricted',
            '受限': 'restricted',
            '敏感': 'restricted',
            'confidential': 'confidential',
            'secret': 'confidential',
            '机密': 'confidential',
            '保密': 'confidential',
        }
        return alias_map.get(text, text)

    def _lexical_retrieval_score(self, question: str, text: str, metadata: dict) -> float:
        """依据词项覆盖、命中密度和来源信息计算词法得分。"""
        question_tokens = list(dict.fromkeys(self._tokenize(question)))
        if not question_tokens:
            return 0.0

        text_tokens = self._tokenize(text)
        if not text_tokens:
            return 0.0

        token_counts = Counter(text_tokens)
        matched_tokens = [token for token in question_tokens if token_counts.get(token, 0) > 0]
        if not matched_tokens:
            return 0.0

        coverage = len(matched_tokens) / len(question_tokens)
        hit_strength = sum(min(token_counts[token], 3) for token in matched_tokens)
        density = min(hit_strength / max(len(question_tokens) * 2, 1), 1.0)
        normalized_question = re.sub(r'\s+', ' ', question.lower()).strip()
        normalized_text = re.sub(r'\s+', ' ', text.lower())
        exact_bonus = 0.15 if normalized_question and normalized_question in normalized_text else 0.0
        source_text = f"{metadata.get('source', '')} {metadata.get('file_name', '')}".lower()
        source_bonus = 0.1 if any(token in source_text for token in matched_tokens) else 0.0
        return coverage * 0.55 + density * 0.2 + exact_bonus + source_bonus

    def _candidate_key(self, citation: CitationItem) -> str:
        """生成用于去重和融合的候选唯一键。"""
        return citation.matched_chunk_id or citation.chunk_id or f"{citation.source}:{citation.text.strip()}"

    def _format_citation_source(self, metadata: dict[str, Any]) -> str:
        """把原始来源渲染为对用户更友好的引用展示名。"""
        base_source = (
            self._metadata_text(metadata, 'source')
            or self._metadata_text(metadata, 'file_name')
            or 'unknown'
        )
        source_archive = self._metadata_text(metadata, 'source_archive')
        archive_member_display_path = self._metadata_text(metadata, 'archive_member_display_path')
        archive_member_path = self._metadata_text(metadata, 'archive_member_path')
        member_display = archive_member_display_path or archive_member_path
        if source_archive and member_display:
            return f'{source_archive} :: {member_display}'
        if source_archive:
            return f'{source_archive} :: {base_source}'
        return base_source

    def _metadata_text(self, metadata: dict[str, Any], key: str) -> str | None:
        """安全读取 metadata 中的字符串字段。"""
        value = metadata.get(key)
        if value in (None, ''):
            return None
        text = str(value).strip()
        return text or None

    def _metadata_bool(self, metadata: dict[str, Any], key: str) -> bool | None:
        """安全读取 metadata 中的布尔字段。"""
        value = metadata.get(key)
        if value in (None, ''):
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {'true', '1', 'yes'}:
                return True
            if lowered in {'false', '0', 'no'}:
                return False
        return bool(value)

    def _metadata_int(self, metadata: dict[str, Any], key: str) -> int | None:
        """安全读取 metadata 中的整数字段。"""
        value = metadata.get(key)
        if value in (None, ''):
            return None
        try:
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, (int, float, str)):
                return int(value)
            return None
        except (TypeError, ValueError):
            return None

    def _tokenize(self, text: str) -> list[str]:
        """把中英文文本拆分为统一 token 序列。"""
        return re.findall(r"[0-9A-Za-z_一-鿿]+", text.lower())

    def _keyword_only_query(self, text: str) -> str:
        """把自然语言问题压缩为更偏关键词检索的查询串。"""
        tokens = self._tokenize(text)
        stopwords = {
            '什么',
            '如何',
            '怎么',
            '怎样',
            '是否',
            '可以',
            '一下',
            '请问',
            '麻烦',
            '帮我',
            '帮忙',
            '接口',
            '功能',
            '用法',
            '说明',
        }
        kept: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in stopwords:
                continue
            if len(token) == 1 and not token.isdigit():
                continue
            if token in seen:
                continue
            kept.append(token)
            seen.add(token)
            if len(kept) >= 10:
                break
        return ' '.join(kept).strip()

    def _split_query_segments(self, text: str) -> list[str]:
        """按中文连接词和分隔符拆分复合问题。"""
        normalized = re.sub(r'[，。；;、/]+', '\n', text)
        normalized = re.sub(r'\s*(和|以及|并且|或者)\s*', '\n', normalized)
        parts = [part.strip() for part in normalized.splitlines() if part.strip()]
        return parts[:6]

    def _drop_generic_terms(self, text: str) -> str:
        """移除弱语义通用词，降低查询改写时的噪声。"""
        updated = text
        for term in ('接口', '怎么', '如何', '是什么', '请问', '麻烦', '帮我', '帮忙'):
            updated = updated.replace(term, ' ')
        updated = re.sub(r'\s+', ' ', updated).strip()
        return updated

    def _normalize_query_text(self, text: str) -> str:
        """清洗查询中的标点和冗余空白。"""
        normalized = text.strip()
        normalized = normalized.replace('？', '?').replace('，', ' ').replace('。', ' ')
        normalized = normalized.replace('、', ' ').replace('/', ' / ').replace(':', ' ')
        normalized = re.sub(r'\s+', ' ', normalized)
        return normalized.strip()

    def _remove_filler_terms(self, text: str) -> str:
        """移除口语化填充词，保留核心检索意图。"""
        updated = text
        for term in self.QUERY_FILLER_TERMS:
            updated = updated.replace(term, ' ')
        updated = re.sub(r'\s+', ' ', updated).strip()
        return updated

    def _apply_synonym_replacements(self, text: str) -> tuple[str, list[str]]:
        """把常见口语表达替换为更稳定的检索表达。"""
        updated = text
        rules: list[str] = []
        for source, target in self.QUERY_REWRITE_SYNONYMS.items():
            replaced = updated.replace(source, target)
            if replaced != updated:
                updated = replaced
                rules.append(f'synonym:{source}->{target}')
        updated = re.sub(r'\s+', ' ', updated).strip()
        return updated, rules

    def _expand_domain_hints(self, text: str) -> tuple[str, list[str]]:
        """根据领域关键词补充同义术语，提升召回概率。"""
        lowered = text.lower()
        expansions: list[str] = []
        for term, related_terms in self.DOMAIN_HINTS.items():
            if term not in lowered:
                continue
            for related in related_terms:
                if related.lower() in lowered:
                    continue
                expansions.append(related)
        if not expansions:
            return text, []
        updated = f"{text} {' '.join(expansions)}".strip()
        updated = re.sub(r'\s+', ' ', updated)
        return updated, expansions

    def _deduplicate_query_terms(self, text: str) -> str:
        """移除重复或近似重复的查询词。"""
        terms = text.split()
        if not terms:
            return text

        deduplicated: list[str] = []
        seen: list[str] = []
        for term in terms:
            lowered = term.lower()
            if lowered in seen:
                continue
            if any(self._is_similar_query_term(lowered, item) for item in seen):
                continue
            seen.append(lowered)
            deduplicated.append(term)
        return ' '.join(deduplicated)

    def _is_similar_query_term(self, current: str, existing: str) -> bool:
        """判断两个查询词是否近似到可视为重复。"""
        if current == existing:
            return True
        if len(current) <= 2 or len(existing) <= 2:
            return False
        return SequenceMatcher(None, current, existing).ratio() >= 0.9

    def _rank_snapshot(self, citations: list[CitationItem]) -> list[dict]:
        """截取排序结果快照，供观测日志记录。"""
        return [
            {
                'chunk_id': citation.chunk_id,
                'matched_chunk_id': citation.matched_chunk_id,
                'source': citation.source,
                'score': citation.score,
                'index_kind': citation.index_kind,
                'node_level': citation.node_level,
                'matched_via': citation.matched_via,
                'context_scope': citation.context_scope,
                'text_preview': citation.text[:120],
            }
            for citation in citations
        ]
