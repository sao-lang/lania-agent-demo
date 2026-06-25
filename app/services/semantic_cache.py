"""语义缓存服务模块。

负责基于问题 embedding、过滤条件和策略签名缓存查询结果，并支持精确匹配、
语义匹配、TTL 失效和集合级清理。该模块用于降低重复问答成本并提升响应速度。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from app.core.config import Settings
from app.rag.observability import TraceRecorder
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import MetadataFilters, SemanticCacheRecord


class SemanticCacheService:
    """管理查询结果的语义缓存、持久化与失效。"""

    def __init__(
        self,
        settings: Settings,
        state: InMemoryState,
        embed_model: Any,
        trace: TraceRecorder,
        persistence: SQLiteStateStore | None = None,
    ) -> None:
        """初始化语义缓存服务。

        Args:
            settings: 全局配置对象，决定缓存阈值、TTL 和容量上限。
            state: 内存态业务数据，实际承载缓存记录。
            embed_model: 底层 embedding 模型，用于计算问题向量。
            trace: 链路追踪记录器，用于记录缓存命中与失效事件。
            persistence: 可选持久化存储，用于同步缓存记录。
        """
        self.settings = settings
        self.state = state
        self.embed_model = embed_model
        self.trace = trace
        self.persistence = persistence
        self._lock = RLock()

    def lookup(
        self,
        *,
        collection_name: str,
        mode: str,
        question: str,
        filters: MetadataFilters | None,
        strategy_signature: str,
        context_signature: str | None,
    ) -> tuple[SemanticCacheRecord | None, dict[str, Any]]:
        """按问题语义和请求上下文查找可复用的缓存结果。

        Args:
            collection_name: 当前知识库名称。
            mode: 当前查询模式，例如 `query`、`chat`。
            question: 用户问题。
            filters: 当前请求的过滤条件。
            strategy_signature: 当前检索/回答策略签名。
            context_signature: 当前上下文签名，用于区分不同聊天状态。

        Returns:
            第一项为命中的缓存记录，第二项为查找过程的统计信息。
        """
        info = {
            'enabled': self.settings.enable_semantic_cache,
            'collection_name': collection_name,
            'mode': mode,
            'hit': False,
            'match_type': None,
            'similarity': 0.0,
            'candidate_count': 0,
            'reason': '',
        }
        normalized_question = self._normalize_question(question)
        if not self.settings.enable_semantic_cache:
            info['reason'] = 'disabled'
            self.trace.record('semantic_cache_lookup', info)
            return None, info
        if len(normalized_question) < max(1, self.settings.semantic_cache_min_query_length):
            info['reason'] = 'question_too_short'
            self.trace.record('semantic_cache_lookup', info)
            return None, info

        filters_signature = self._signature(filters or {})
        with self._lock:
            candidates = [
                entry
                for entry in self.state.semantic_cache.values()
                if entry['collection_name'] == collection_name
                and entry['mode'] == mode
                and entry['strategy_signature'] == strategy_signature
                and entry['filters_signature'] == filters_signature
                and entry.get('context_signature') == context_signature
            ]
            info['candidate_count'] = len(candidates)
            # 先清理 TTL 过期项，再基于剩余候选做精确匹配和语义匹配。
            self._evict_expired(candidates)
            candidates = [
                entry
                for entry in self.state.semantic_cache.values()
                if entry['collection_name'] == collection_name
                and entry['mode'] == mode
                and entry['strategy_signature'] == strategy_signature
                and entry['filters_signature'] == filters_signature
                and entry.get('context_signature') == context_signature
            ]
            info['candidate_count'] = len(candidates)
            if not candidates:
                info['reason'] = 'no_candidates'
                self.trace.record('semantic_cache_lookup', info)
                return None, info

            # 先走精确匹配，命中时无需再额外调用 embedding。
            exact = next((entry for entry in candidates if entry['normalized_question'] == normalized_question), None)
            if exact is not None:
                self._touch(exact)
                info.update({'hit': True, 'match_type': 'exact', 'similarity': 1.0, 'reason': 'exact_match'})
                self.trace.record('semantic_cache_lookup', info)
                return exact, info

        embedding = self._embed(question)
        if embedding is None:
            info['reason'] = 'embedding_unavailable'
            self.trace.record('semantic_cache_lookup', info)
            return None, info

        best_entry: SemanticCacheRecord | None = None
        best_similarity = 0.0
        for entry in candidates:
            similarity = self._semantic_similarity(
                question,
                embedding,
                str(entry.get('question') or ''),
                entry['question_embedding'],
            )
            if similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        threshold = float(self.settings.semantic_cache_similarity_threshold)
        if best_entry is None or best_similarity < threshold:
            info.update(
                {
                    'reason': 'similarity_below_threshold',
                    'similarity': round(best_similarity, 4),
                }
            )
            self.trace.record('semantic_cache_lookup', info)
            return None, info

        with self._lock:
            current = self.state.semantic_cache.get(best_entry['cache_id'])
            if current is None:
                info['reason'] = 'candidate_evicted'
                self.trace.record('semantic_cache_lookup', info)
                return None, info
            self._touch(current)
        info.update(
            {
                'hit': True,
                'match_type': 'semantic',
                'similarity': round(best_similarity, 4),
                'reason': 'semantic_match',
            }
        )
        self.trace.record('semantic_cache_lookup', info)
        return current, info

    def store(
        self,
        *,
        collection_name: str,
        mode: str,
        question: str,
        filters: MetadataFilters | None,
        strategy_signature: str,
        context_signature: str | None,
        answer: str,
        answer_mode: str,
        citations: list[dict[str, Any]],
        source_doc_ids: list[str],
        metadata: dict[str, Any],
    ) -> SemanticCacheRecord | None:
        """将新鲜查询结果写入语义缓存。

        Args:
            collection_name: 当前知识库名称。
            mode: 当前查询模式。
            question: 用户问题。
            filters: 当前请求的过滤条件。
            strategy_signature: 当前检索/回答策略签名。
            context_signature: 当前上下文签名。
            answer: 当前答案文本。
            answer_mode: 当前答案生成模式。
            citations: 当前答案对应的引用列表。
            source_doc_ids: 当前答案涉及的文档 ID 列表。
            metadata: 需要附加保存的额外元数据。

        Returns:
            实际写入的缓存记录；未写入时返回 `None`。
        """
        if not self.settings.enable_semantic_cache:
            return None

        normalized_question = self._normalize_question(question)
        if len(normalized_question) < max(1, self.settings.semantic_cache_min_query_length):
            return None

        embedding = self._embed(question)
        if embedding is None:
            self.trace.record(
                'semantic_cache_store',
                {
                    'collection_name': collection_name,
                    'mode': mode,
                    'stored': False,
                    'reason': 'embedding_unavailable',
                },
            )
            return None

        filters_signature = self._signature(filters or {})
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = next(
                (
                    entry
                    for entry in self.state.semantic_cache.values()
                    if entry['collection_name'] == collection_name
                    and entry['mode'] == mode
                    and entry['normalized_question'] == normalized_question
                    and entry['filters_signature'] == filters_signature
                    and entry['strategy_signature'] == strategy_signature
                    and entry.get('context_signature') == context_signature
                ),
                None,
            )
            # 相同问题 + 相同过滤/策略/上下文命中时直接覆盖旧记录，避免缓存碎片化增长。
            cache_id = existing['cache_id'] if existing is not None else f"sc-{uuid4().hex[:12]}"
            record: SemanticCacheRecord = {
                'cache_id': cache_id,
                'collection_name': collection_name,
                'mode': mode,
                'question': question.strip(),
                'normalized_question': normalized_question,
                'question_embedding': embedding,
                'context_signature': context_signature,
                'filters': filters,
                'filters_signature': filters_signature,
                'strategy_signature': strategy_signature,
                'answer': answer,
                'answer_mode': answer_mode,
                'citations': citations,
                'source_doc_ids': sorted({item for item in source_doc_ids if item}),
                'metadata': metadata,
                'hit_count': existing['hit_count'] if existing is not None else 0,
                'created_at': existing['created_at'] if existing is not None else now,
                'updated_at': now,
                'last_hit_at': existing.get('last_hit_at') if existing is not None else None,
            }
            self.state.semantic_cache[cache_id] = record
            self._persist_upsert(record)
            self._prune_collection(collection_name)

        self.trace.record(
            'semantic_cache_store',
            {
                'collection_name': collection_name,
                'mode': mode,
                'stored': True,
                'cache_id': cache_id,
                'source_doc_count': len(record['source_doc_ids']),
            },
        )
        return record

    def invalidate_collection(self, collection_name: str, reason: str) -> int:
        """按集合失效其全部语义缓存记录。

        Args:
            collection_name: 目标知识库名称。
            reason: 失效原因，例如 `documents_uploaded`。

        Returns:
            实际删除的缓存记录数量。
        """
        with self._lock:
            cache_ids = [
                cache_id
                for cache_id, entry in self.state.semantic_cache.items()
                if entry['collection_name'] == collection_name
            ]
            for cache_id in cache_ids:
                self.state.semantic_cache.pop(cache_id, None)
                self._persist_delete(cache_id)
        if cache_ids:
            self.trace.record(
                'semantic_cache_invalidate',
                {
                    'collection_name': collection_name,
                    'invalidated_entries': len(cache_ids),
                    'reason': reason,
                },
            )
        return len(cache_ids)

    def get_runtime_status(self) -> dict[str, Any]:
        """返回语义缓存运行时配置与规模信息。

        Returns:
            便于调试和观测的缓存运行时状态字典。
        """
        with self._lock:
            entry_count = len(self.state.semantic_cache)
            collections = sorted({entry['collection_name'] for entry in self.state.semantic_cache.values()})
            total_hits = sum(int(entry.get('hit_count', 0)) for entry in self.state.semantic_cache.values())
        return {
            'enabled_by_default': self.settings.enable_semantic_cache,
            'similarity_threshold': float(self.settings.semantic_cache_similarity_threshold),
            'ttl_seconds': int(self.settings.semantic_cache_ttl_seconds),
            'max_entries_per_collection': int(self.settings.semantic_cache_max_entries_per_collection),
            'entry_count': entry_count,
            'collection_count': len(collections),
            'collections': collections,
            'lifetime_hits': total_hits,
        }

    def _touch(self, record: SemanticCacheRecord) -> None:
        """更新缓存命中时间和次数。"""
        record['hit_count'] = int(record.get('hit_count', 0)) + 1
        last_hit_at = datetime.now(timezone.utc)
        record['last_hit_at'] = last_hit_at
        record['updated_at'] = last_hit_at
        self._persist_upsert(record)

    def _persist_upsert(self, record: SemanticCacheRecord) -> None:
        """把缓存记录同步到持久化层。"""
        if self.persistence is not None:
            self.persistence.upsert_semantic_cache_entry(record)

    def _persist_delete(self, cache_id: str) -> None:
        """从持久化层删除缓存记录。"""
        if self.persistence is not None:
            self.persistence.delete_semantic_cache_entry(cache_id)

    def _evict_expired(self, candidates: list[SemanticCacheRecord]) -> None:
        """移除超出 TTL 的缓存项。

        Args:
            candidates: 当前候选缓存记录列表。
        """
        if self.settings.semantic_cache_ttl_seconds <= 0:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=int(self.settings.semantic_cache_ttl_seconds))
        expired_ids = [
            entry['cache_id']
            for entry in candidates
            if entry.get('updated_at') is not None and entry['updated_at'] < cutoff
        ]
        for cache_id in expired_ids:
            self.state.semantic_cache.pop(cache_id, None)
            self._persist_delete(cache_id)
        if expired_ids:
            self.trace.record(
                'semantic_cache_invalidate',
                {
                    'collection_name': candidates[0]['collection_name'] if candidates else None,
                    'invalidated_entries': len(expired_ids),
                    'reason': 'ttl_expired',
                },
            )

    def _prune_collection(self, collection_name: str) -> None:
        """控制单集合缓存规模，优先保留最近访问项。

        Args:
            collection_name: 目标知识库名称。
        """
        limit = max(1, int(self.settings.semantic_cache_max_entries_per_collection))
        entries = [entry for entry in self.state.semantic_cache.values() if entry['collection_name'] == collection_name]
        if len(entries) <= limit:
            return
        # 优先淘汰“最近没被访问、最近没更新、历史命中少”的项，尽量保留高价值缓存。
        entries.sort(
            key=lambda item: (
                item.get('last_hit_at') or datetime.min.replace(tzinfo=timezone.utc),
                item.get('updated_at') or datetime.min.replace(tzinfo=timezone.utc),
                item.get('hit_count', 0),
            )
        )
        victims = entries[: max(0, len(entries) - limit)]
        for entry in victims:
            self.state.semantic_cache.pop(entry['cache_id'], None)
            self._persist_delete(entry['cache_id'])
        if victims:
            self.trace.record(
                'semantic_cache_invalidate',
                {
                    'collection_name': collection_name,
                    'invalidated_entries': len(victims),
                    'reason': 'collection_pruned',
                },
            )

    def _embed(self, text: str) -> list[float] | None:
        """调用底层 embedding 模型获取归一化向量。

        Args:
            text: 待向量化文本。

        Returns:
            成功时返回归一化向量，否则返回 `None`。
        """
        method_names = (
            'get_query_embedding',
            'get_text_embedding',
            '_get_query_embedding',
            '_get_text_embedding',
        )
        # 兼容不同 embedding 实现的公开/私有接口命名。
        for name in method_names:
            method = getattr(self.embed_model, name, None)
            if callable(method):
                try:
                    vector = list(cast(Any, method(text)))
                except TypeError:
                    continue
                if vector:
                    return self._normalize_vector(vector)
        return None

    def _normalize_vector(self, vector: list[float]) -> list[float]:
        """把向量标准化为单位长度。"""
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm <= 0:
            return [0.0 for _ in vector]
        return [round(float(value) / norm, 8) for value in vector]

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        """计算两个单位向量的余弦相似度。"""
        if not left or not right or len(left) != len(right):
            return 0.0
        return max(min(sum(a * b for a, b in zip(left, right, strict=False)), 1.0), -1.0)

    def _semantic_similarity(
        self,
        left_text: str,
        left_embedding: list[float],
        right_text: str,
        right_embedding: list[float],
    ) -> float:
        """结合向量相似度与轻量词法相似度，提升短中文问句缓存命中率。

        对中文短问句来说，仅依赖 embedding 容易因为表达简短而不稳定，因此这里会取
        向量相似度和词法相似度中的较大值作为最终判断依据。
        """
        return max(
            self._cosine_similarity(left_embedding, right_embedding),
            self._lexical_similarity(left_text, right_text),
        )

    def _lexical_similarity(self, left: str, right: str) -> float:
        """基于 token / 汉字集合估算轻量词法相似度。"""
        normalized_left = self._normalize_question(left)
        normalized_right = self._normalize_question(right)
        if not normalized_left or not normalized_right:
            return 0.0
        token_score = self._dice_similarity(self._tokenize_for_similarity(normalized_left), self._tokenize_for_similarity(normalized_right))
        char_score = self._dice_similarity(self._char_terms(normalized_left), self._char_terms(normalized_right))
        return max(token_score, char_score)

    def _tokenize_for_similarity(self, text: str) -> list[str]:
        """提取适合做相似度比对的 token。"""
        tokens = re.findall(r'[0-9a-z_]+|[一-鿿]+', text.lower())
        if len(tokens) == 1 and re.search(r'[一-鿿]', tokens[0]):
            return list(tokens[0])
        return tokens

    def _char_terms(self, text: str) -> list[str]:
        """把文本切成字符级 term，兼容无空格中文短问句。"""
        compact = ''.join(text.split())
        return [char for char in compact if char]

    def _dice_similarity(self, left_terms: list[str], right_terms: list[str]) -> float:
        """计算两个 term 集合的 Dice 相似度。"""
        if not left_terms or not right_terms:
            return 0.0
        left_counts: dict[str, int] = {}
        right_counts: dict[str, int] = {}
        for term in left_terms:
            left_counts[term] = left_counts.get(term, 0) + 1
        for term in right_terms:
            right_counts[term] = right_counts.get(term, 0) + 1
        overlap = sum(min(count, right_counts.get(term, 0)) for term, count in left_counts.items())
        return (2 * overlap) / (len(left_terms) + len(right_terms))

    def _normalize_question(self, question: str) -> str:
        """对问题做轻量归一化，便于精确匹配。"""
        return ' '.join(question.strip().lower().split())

    def _signature(self, value: Any) -> str:
        """把任意 JSON 兼容结构稳定地编码为签名。"""
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()
