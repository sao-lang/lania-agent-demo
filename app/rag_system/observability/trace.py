"""RAG 系统可观测性模块。

负责把检索、压缩、缓存、查询等关键阶段的事件记录到内存缓冲区。
与主应用的 `app/rag/observability.py` 功能一致，但独立于主应用。
不包含 agent/task 事件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import time
from typing import Any


@dataclass
class TraceEvent:
    """描述一次被记录的链路事件。"""

    name: str
    payload: dict
    timestamp: float = field(default_factory=time)


class TraceRecorder:
    """RAG 系统内存态 trace 记录器。

    只记录检索、压缩、缓存、查询等 RAG 相关事件。
    """

    def __init__(self) -> None:
        """初始化空的事件缓冲区。"""
        self.events: list[TraceEvent] = []

    def record(self, name: str, payload: dict) -> None:
        """记录一条带名称和载荷的追踪事件。"""
        self.events.append(TraceEvent(name=name, payload=payload))

    def summarize_context_compression(self, last_n: int | None = None) -> dict:
        """聚合上下文压缩的累计或最近窗口效果指标。"""
        compression_events = [event.payload for event in self.events if event.name == 'context_compressed']
        if last_n is not None and last_n > 0:
            compression_events = compression_events[-last_n:]
        if not compression_events:
            return {
                'compressed_requests': 0,
                'avg_char_reduction_ratio': 0.0,
                'avg_sentence_reduction_ratio': 0.0,
                'avg_chunk_reduction_ratio': 0.0,
                'total_original_char_count': 0,
                'total_compressed_char_count': 0,
                'total_original_sentence_count': 0,
                'total_compressed_sentence_count': 0,
                'total_original_chunk_count': 0,
                'total_compressed_chunk_count': 0,
                'strategy_breakdown': {},
            }

        total_original_chars = sum(int(item.get('original_char_count', 0)) for item in compression_events)
        total_compressed_chars = sum(int(item.get('compressed_char_count', 0)) for item in compression_events)
        total_original_sentences = sum(int(item.get('original_sentence_count', 0)) for item in compression_events)
        total_compressed_sentences = sum(int(item.get('compressed_sentence_count', 0)) for item in compression_events)
        total_original_chunks = sum(int(item.get('original_chunk_count', 0)) for item in compression_events)
        total_compressed_chunks = sum(int(item.get('compressed_chunk_count', 0)) for item in compression_events)

        def _reduction_ratio(original: int, compressed: int) -> float:
            if original <= 0:
                return 0.0
            return round(max(original - compressed, 0) / original, 4)

        strategy_breakdown: dict[str, int] = {}
        for item in compression_events:
            strategy = str(item.get('strategy') or 'unknown')
            strategy_breakdown[strategy] = strategy_breakdown.get(strategy, 0) + 1

        return {
            'compressed_requests': len(compression_events),
            'avg_char_reduction_ratio': _reduction_ratio(total_original_chars, total_compressed_chars),
            'avg_sentence_reduction_ratio': _reduction_ratio(total_original_sentences, total_compressed_sentences),
            'avg_chunk_reduction_ratio': _reduction_ratio(total_original_chunks, total_compressed_chunks),
            'total_original_char_count': total_original_chars,
            'total_compressed_char_count': total_compressed_chars,
            'total_original_sentence_count': total_original_sentences,
            'total_compressed_sentence_count': total_compressed_sentences,
            'total_original_chunk_count': total_original_chunks,
            'total_compressed_chunk_count': total_compressed_chunks,
            'strategy_breakdown': strategy_breakdown,
        }

    def summarize_semantic_cache(self, last_n: int | None = None) -> dict:
        """聚合语义缓存的命中与存储指标。"""
        lookup_events = [e.payload for e in self.events if e.name == 'semantic_cache_hit']
        if last_n is not None and last_n > 0:
            lookup_events = lookup_events[-last_n:]
        return {
            'hits': len(lookup_events),
            'hit_rate': len(lookup_events) / max(len([e for e in self.events if 'cache' in e.name]), 1),
        }

    def summarize_semantic_chunking(self, last_n: int | None = None) -> dict:
        """聚合语义分块的指标。"""
        chunk_events = [e.payload for e in self.events if 'chunk' in e.name and e.name != 'context_compressed']
        if last_n is not None and last_n > 0:
            chunk_events = chunk_events[-last_n:]
        return {
            'total_chunk_operations': len(chunk_events),
            'avg_chunks_per_doc': sum(p.get('chunks', 1) for p in chunk_events) / max(len(chunk_events), 1),
        }

    def summarize_retrieval_enhancements(self, last_n: int | None = None) -> dict:
        """聚合检索增强的指标（多查询、HyDE、改写等）。"""
        events = [e for e in self.events if e.name.startswith('retrieval_') or e.name.startswith('multi_') or e.name == 'hyde_generated']
        if last_n is not None and last_n > 0:
            events = events[-last_n:]
        counts: dict[str, int] = {}
        for e in events:
            counts[e.name] = counts.get(e.name, 0) + 1
        return {'total': len(events), 'by_type': counts}

    def summarize_graph_retrieval(self, last_n: int | None = None) -> dict:
        """聚合图谱检索的指标。"""
        graph_events = [e.payload for e in self.events if e.name.startswith('graph_') or e.name == 'retrieval' and e.payload.get('use_graph_rag')]
        if last_n is not None and last_n > 0:
            graph_events = graph_events[-last_n:]
        total_nodes = sum(p.get('nodes', 0) for p in graph_events if 'nodes' in p)
        total_edges = sum(p.get('edges', 0) for p in graph_events if 'edges' in p)
        return {
            'total_events': len(graph_events),
            'total_nodes': total_nodes,
            'total_edges': total_edges,
        }

    def summarize_model_routes(self, last_n: int | None = None) -> dict:
        """聚合 LLM 路由的指标。"""
        llm_events = [e.payload for e in self.events if 'llm' in e.name]
        if last_n is not None and last_n > 0:
            llm_events = llm_events[-last_n:]
        return {'total_llm_calls': len(llm_events)}

    def get_recent_events(self, n: int = 50) -> list[TraceEvent]:
        """返回最近 N 条事件。"""
        return self.events[-n:]

    def clear(self) -> None:
        """清除所有事件。"""
        self.events.clear()
