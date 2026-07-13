"""RAG 系统语义缓存服务模块。

负责基于问题 embedding 缓存查询结果，支持语义匹配、TTL 失效和集合级清理。
与主应用的 `app/services/semantic_cache.py` 功能一致，但使用独立配置和状态。
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from uuid import uuid4
import re

from app.rag_system.config.settings import RagSettings
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.store.persistence import RagPersistence
from app.rag_system.store.state import RagState


class SemanticCacheService:
    """管理查询结果的语义缓存、持久化与失效。"""

    def __init__(
        self,
        settings: RagSettings,
        state: RagState,
        embed_model: Any,
        trace: TraceRecorder,
        persistence: RagPersistence | None = None,
    ) -> None:
        """初始化语义缓存服务。"""
        self.settings = settings
        self.state = state
        self.embed_model = embed_model
        self.trace = trace
        self.persistence = persistence
        self._lock = RLock()

    def _cache_enabled(self) -> bool:
        return self.settings.enable_semantic_cache

    def lookup(
        self,
        *,
        collection_name: str,
        mode: str,
        question: str,
        filters: dict[str, Any] | None,
        strategy_signature: str,
        context_signature: str | None = None,
    ) -> tuple[Any | None, dict[str, Any]]:
        """按问题语义和请求上下文查找可复用的缓存结果。"""
        if not self._cache_enabled():
            return None, {'cache_enabled': False}

        try:
            query_embedding = self.embed_model.get_query_embedding(question)
        except Exception:
            return None, {'cache_error': 'embedding_failed'}

        best_match: Any = None
        best_score = 0.0
        threshold = self.settings.semantic_cache_similarity_threshold

        for cache_id, record in self.state.semantic_cache.items():
            if record.get('collection_name') != collection_name:
                continue
            if record.get('mode') != mode:
                continue

            # TTL 检查
            expires_at = record.get('expires_at')
            if expires_at:
                try:
                    if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                        continue
                except ValueError:
                    pass

            try:
                cached_emb = record.get('embedding', [])
                if cached_emb and len(cached_emb) == len(query_embedding):
                    score = self._cosine_similarity(query_embedding, cached_emb)
                    if score > best_score:
                        best_score = score
                        best_match = record
            except Exception:
                continue

        if best_match and best_score >= threshold:
            info = {'cache_enabled': True, 'hit': True, 'score': round(best_score, 4)}
            self.trace.record('semantic_cache_hit', info)
            return best_match, info

        return None, {'cache_enabled': True, 'hit': False, 'best_score': round(best_score, 4)}

    def store(
        self,
        *,
        collection_name: str,
        mode: str,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        strategy_signature: str,
        context_signature: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """将查询结果存入语义缓存。"""
        if not self._cache_enabled():
            return None

        try:
            query_embedding = self.embed_model.get_query_embedding(question)
        except Exception:
            return None

        # 检查数量上限
        collection_entries = [
            cid for cid, r in self.state.semantic_cache.items()
            if r.get('collection_name') == collection_name and r.get('mode') == mode
        ]
        max_entries = self.settings.semantic_cache_max_entries_per_collection
        if len(collection_entries) >= max_entries:
            # 删除最早的一条
            oldest = min(collection_entries, key=lambda cid: self.state.semantic_cache[cid].get('created_at', ''))
            self.state.semantic_cache.pop(oldest, None)
            if self.persistence:
                self.persistence.delete_semantic_cache(oldest)

        cache_id = str(uuid4())
        now = datetime.now(timezone.utc)
        ttl = self.settings.semantic_cache_ttl_seconds
        expires_at = now + timedelta(seconds=ttl)

        record = {
            'cache_id': cache_id,
            'question': question,
            'embedding': query_embedding,
            'collection_name': collection_name,
            'mode': mode,
            'strategy_signature': strategy_signature,
            'context_signature': context_signature or '',
            'created_at': now.isoformat(),
            'expires_at': expires_at.isoformat(),
            'hit_count': 0,
        }
        # 把 answer 和 citations 放在外层
        record.update({
            'answer': answer,
            'citations': citations,
        })

        self.state.semantic_cache[cache_id] = record
        if self.persistence:
            self.persistence.upsert_semantic_cache(record)

        return cache_id

    def invalidate_collection(self, collection_name: str) -> int:
        """使指定集合的所有缓存失效。"""
        count = 0
        for cache_id, record in list(self.state.semantic_cache.items()):
            if record.get('collection_name') == collection_name:
                self.state.semantic_cache.pop(cache_id, None)
                count += 1
        if self.persistence:
            self.persistence.delete_semantic_cache_for_collection(collection_name)
        return count

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """计算余弦相似度。"""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def clear(self) -> int:
        """清除所有缓存。"""
        count = len(self.state.semantic_cache)
        self.state.semantic_cache.clear()
        return count

    def get_runtime_status(self) -> dict[str, Any]:
        """返回语义缓存运行状态。"""
        enabled = self._cache_enabled()
        total = len(self.state.semantic_cache)
        by_collection: dict[str, int] = {}
        for record in self.state.semantic_cache.values():
            coll = record.get('collection_name', 'unknown')
            by_collection[coll] = by_collection.get(coll, 0) + 1
        return {
            'enabled': enabled,
            'total_entries': total,
            'collections': by_collection,
            'threshold': self.settings.semantic_cache_similarity_threshold,
            'ttl_seconds': self.settings.semantic_cache_ttl_seconds,
        }

    def _semantic_similarity(self, emb_a: list[float], emb_b: list[float], text_a: str, text_b: str) -> float:
        """综合语义+词法相似度。"""
        cosine = self._cosine_similarity(emb_a, emb_b)
        lexical = self._lexical_similarity(text_a, text_b)
        return max(cosine, lexical * 0.8)

    def _lexical_similarity(self, text_a: str, text_b: str) -> float:
        """计算词法相似度（Jaccard）。"""
        tokens_a = set(self._tokenize_for_similarity(text_a))
        tokens_b = set(self._tokenize_for_similarity(text_b))
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    def _tokenize_for_similarity(self, text: str) -> list[str]:
        """为相似度计算做分词。"""
        return re.findall(r"[0-9A-Za-z_一-鿿]{2,}", text.lower())
