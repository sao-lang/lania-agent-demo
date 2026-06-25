"""Compression Strategy 实现。

对长 evidence 进行主题归并、摘要、去重、分桶，减少注意力稀释和 token 消耗。
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from app.harness.context_policy import ContextPolicy


class CompressionStrategy:
    """压缩策略基类。"""
    
    def compress(self, items: list[dict], policy: ContextPolicy) -> list[dict]:
        """压缩上下文项列表。"""
        raise NotImplementedError


class SentenceTruncation(CompressionStrategy):
    """句子级截断压缩策略。"""
    
    def compress(self, items: list[dict], policy: ContextPolicy) -> list[dict]:
        """按句数和字符上限截断每条上下文项。"""

        if not items or not policy.compression_enabled:
            return items
        
        max_sentences = policy.compression_max_sentences
        max_chars = policy.compression_max_chars
        
        compressed = []
        for item in items:
            compressed_item = dict(item)
            text = self._extract_text(item)
            
            if text:
                sentences = self._split_sentences(text)
                selected = sentences[:max_sentences]
                compressed_text = ' '.join(selected)[:max_chars]
                self._set_text(compressed_item, compressed_text)
            
            compressed.append(compressed_item)
        
        return compressed
    
    def _extract_text(self, item: dict) -> str:
        """从字典中提取文本内容。"""
        text_fields = ['text', 'summary', 'content']
        for field in text_fields:
            if field in item and isinstance(item[field], str):
                return item[field]
        return str(item)
    
    def _set_text(self, item: dict, text: str) -> None:
        """设置字典中的文本内容。"""
        text_fields = ['text', 'summary', 'content']
        for field in text_fields:
            if field in item and isinstance(item[field], str):
                item[field] = text
                return
    
    def _split_sentences(self, text: str) -> list[str]:
        """将文本分割为句子列表。"""
        sentences = re.split(r'(?<=[。！？.!?])\s*', text)
        return [s.strip() for s in sentences if s.strip()]


class DeduplicationCompression(CompressionStrategy):
    """去重压缩策略。"""
    
    def compress(self, items: list[dict], policy: ContextPolicy) -> list[dict]:
        """按文本哈希去除重复上下文项。"""

        if not items:
            return items
        
        seen_hashes = set()
        unique_items = []
        
        for item in items:
            item_hash = self._compute_hash(item)
            if item_hash not in seen_hashes:
                seen_hashes.add(item_hash)
                unique_items.append(item)
        
        return unique_items
    
    def _compute_hash(self, item: dict) -> str:
        """计算项的哈希值用于去重。"""
        text_fields = ['text', 'summary', 'content']
        text_content = ''
        for field in text_fields:
            if field in item and isinstance(item[field], str):
                text_content += item[field]
        
        if text_content:
            return hashlib.md5(text_content.encode('utf-8')).hexdigest()
        
        return str(id(item))


class ThematicClustering(CompressionStrategy):
    """主题聚类压缩策略。"""
    
    def compress(self, items: list[dict], policy: ContextPolicy) -> list[dict]:
        """按主题聚类后合并同类项。"""

        if not items:
            return items
        
        clusters = self._cluster_by_topic(items)
        
        merged_items = []
        for topic, cluster_items in clusters.items():
            merged = self._merge_cluster(cluster_items)
            if merged:
                merged_items.append(merged)
        
        merged_items.sort(key=lambda x: x.get('score', 0.0), reverse=True)
        
        return merged_items[:policy.evidence_top_k]
    
    def _cluster_by_topic(self, items: list[dict]) -> dict[str, list[dict]]:
        """根据主题对项进行聚类。"""
        clusters = defaultdict(list)
        
        for item in items:
            topic = self._extract_topic(item)
            clusters[topic].append(item)
        
        return clusters
    
    def _extract_topic(self, item: dict) -> str:
        """从项中提取主题关键词。"""
        text = self._get_text(item)
        if not text:
            return 'other'
        
        topic_keywords = [
            ('风险', ['风险', '风险提示', '隐患', '警告', '注意']),
            ('财务', ['财务', '金额', '费用', '预算', '成本']),
            ('合同', ['合同', '条款', '协议', '签署', '违约']),
            ('技术', ['技术', '架构', '系统', '设计', '开发']),
            ('流程', ['流程', '步骤', '流程', '审批', '流程']),
        ]
        
        for topic, keywords in topic_keywords:
            if any(keyword in text for keyword in keywords):
                return topic
        
        return '其他'
    
    def _get_text(self, item: dict) -> str:
        """获取项的文本内容。"""
        text_fields = ['text', 'summary', 'content', 'title']
        for field in text_fields:
            if field in item and isinstance(item[field], str):
                return item[field]
        return ''
    
    def _merge_cluster(self, items: list[dict]) -> dict | None:
        """合并同一主题的项。"""
        if not items:
            return None
        
        merged = dict(items[0])
        
        texts = []
        scores = []
        sources = set()
        
        for item in items:
            text = self._get_text(item)
            if text:
                texts.append(text)
            if 'score' in item:
                scores.append(item['score'])
            if 'source' in item:
                sources.add(item['source'])
        
        merged['text'] = ' '.join(texts)[:2000]
        if scores:
            merged['score'] = sum(scores) / len(scores)
        if sources:
            merged['sources'] = list(sources)
        
        return merged


class HierarchicalCompression(CompressionStrategy):
    """分层压缩策略 - 按优先级逐层裁剪。"""
    
    def __init__(self):
        """初始化分层压缩所需的子策略。"""

        self.truncation = SentenceTruncation()
        self.deduplication = DeduplicationCompression()
        self.clustering = ThematicClustering()
    
    def compress(self, items: list[dict], policy: ContextPolicy) -> list[dict]:
        """执行分层压缩。"""
        if not items:
            return items
        
        compressed = items
        
        if policy.compression_enabled:
            compressed = self.deduplication.compress(compressed, policy)
            compressed = self.clustering.compress(compressed, policy)
            compressed = self.truncation.compress(compressed, policy)
        
        return compressed


class CompressionEngine:
    """压缩引擎。"""
    
    def __init__(self):
        """初始化默认分层压缩策略。"""

        self.strategy = HierarchicalCompression()
    
    def compress_evidence(self, evidence_items: list[dict], policy: ContextPolicy) -> list[dict]:
        """压缩证据列表。"""
        return self.strategy.compress(evidence_items, policy)
    
    def compress_memory(self, memory_items: list[dict], policy: ContextPolicy) -> list[dict]:
        """压缩记忆列表。"""
        if not policy.compression_enabled:
            return memory_items
        
        max_chars = policy.compression_max_chars // 2
        
        compressed = []
        for item in memory_items:
            compressed_item = dict(item)
            if 'summary' in compressed_item and isinstance(compressed_item['summary'], str):
                compressed_item['summary'] = compressed_item['summary'][:max_chars]
            compressed.append(compressed_item)
        
        return compressed
    
    def compress_artifact(self, artifact: dict | None, policy: ContextPolicy) -> dict | None:
        """压缩产物。"""
        if artifact is None or not policy.compression_enabled:
            return artifact
        
        compressed = dict(artifact)
        
        max_chars = policy.compression_max_chars
        
        if 'summary' in compressed and isinstance(compressed['summary'], str):
            compressed['summary'] = compressed['summary'][:max_chars]
        
        if 'report_markdown' in compressed and isinstance(compressed['report_markdown'], str):
            compressed['report_markdown'] = compressed['report_markdown'][:max_chars * 3]
        
        if 'open_questions' in compressed and isinstance(compressed['open_questions'], list):
            compressed['open_questions'] = compressed['open_questions'][:5]
        
        return compressed
    
    def calculate_compression_ratio(self, original: list[dict], compressed: list[dict]) -> float:
        """计算压缩率。"""
        original_chars = sum(self._count_chars(item) for item in original)
        compressed_chars = sum(self._count_chars(item) for item in compressed)
        
        if original_chars == 0:
            return 0.0
        
        return 1.0 - (compressed_chars / original_chars)
    
    def _count_chars(self, item: dict) -> int:
        """计算项中的字符数。"""
        text_fields = ['text', 'summary', 'content', 'report_markdown']
        count = 0
        for field in text_fields:
            if field in item and isinstance(item[field], str):
                count += len(item[field])
        return count
