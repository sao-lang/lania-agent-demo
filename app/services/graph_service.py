"""GraphRAG 图谱服务主入口。

该文件保留图谱生命周期管理和对外检索入口，实体关系抽取、边节点构建、
引用回填与过滤判断已经下沉到 `graph_service_parts` 子模块中，
方便把图谱编排、抽取策略和持久化细节分开维护。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.models.query import CitationItem
from app.rag.observability import TraceRecorder
from app.rag.vector_store import ChromaClientFactory
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.types import DocumentRecord, GraphEdgeRecord, GraphNodeRecord

from app.services.graph_service_parts.extraction import GraphExtractionMixin
from app.services.graph_service_parts.builders import GraphBuilderMixin


class GraphService(GraphExtractionMixin, GraphBuilderMixin):
    """基于 LLM 或规则抽取实体关系，并提供图谱增强检索。"""

    ENTITY_STOPWORDS = {
        '可以',
        '需要',
        '支持',
        '相关',
        '当前',
        '已经',
        '这些',
        '那些',
        '这个',
        '那个',
        '内容',
        '信息',
        '文档',
        '章节',
        '功能',
        '模块',
        '接口',
        '步骤',
        '流程',
        '问题',
        '说明',
        '方法',
        '如下',
        '这里',
        '进行',
        '用于',
        '通过',
        '执行',
        '默认',
        '系统中',
        '用户',
        '用户侧',
        '能力',
        '策略',
    }
    RELATION_KEYWORDS = {
        'belongs_to': ('属于', '归属', '隶属'),
        'depends_on': ('依赖', '依存', '基于'),
        'uses': ('使用', '调用', '接入', '引用'),
        'manages': ('负责', '维护', '管理'),
        'applies_to': ('适用', '面向'),
        'contains': ('包含', '包括', '由'),
        'related_to': ('关联', '相关', '连接', '联动'),
        'updates': ('更新', '升级', '变更', '替换'),
    }
    RELATION_PATTERNS = [
        ('belongs_to', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:属于|隶属于|归属)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('depends_on', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:依赖|基于)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('uses', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:调用|使用|接入|引用)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('manages', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:负责|维护|管理)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('applies_to', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:适用于|面向)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('contains', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:包含|包括)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('updates', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:更新|升级|替换)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('related_to', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:关联|联动|连接)\s*(?P<tgt>[^，。；:：]{2,40})')),
    ]

    def __init__(
        self,
        state: InMemoryState,
        vector_store: ChromaClientFactory,
        trace: TraceRecorder,
        persistence: SQLiteStateStore | None = None,
        llm: Any | None = None,
    ) -> None:
        """初始化图谱服务。

        Args:
            state: 内存态业务数据。
            vector_store: 向量存储工厂。
            trace: 链路追踪记录器。
            persistence: 可选持久化存储。
            llm: 可选大模型，用于增强实体关系抽取。
        """
        self.state = state
        self.vector_store = vector_store
        self.trace = trace
        self.persistence = persistence
        self.llm = llm

    def replace_document_graph(self, record: DocumentRecord, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """重建指定文档的图谱节点和边。

        Args:
            record: 文档记录。
            segments: 已规整的文档片段列表。

        Returns:
            本次图谱重建的统计结果。
        """
        self.delete_document_graph(record['doc_id'])
        now = datetime.now(timezone.utc)
        collection_name = record['collection_name']
        node_updates: dict[str, GraphNodeRecord] = {}
        edge_updates: dict[str, GraphEdgeRecord] = {}
        llm_segments = 0
        rule_segments = 0
        fallback_segments = 0

        for index, segment in enumerate(segments, start=1):
            text = re.sub(r'\s+', ' ', str(segment.get('text') or '')).strip()
            if not text:
                continue
            entities, llm_relations, extraction_mode = self._extract_segment_graph(record, segment, text)
            if not entities:
                continue
            if extraction_mode == 'llm':
                # 记录抽取模式，便于后续分析图谱质量问题来自规则还是 LLM 分支。
                llm_segments += 1
            elif extraction_mode == 'rule_fallback':
                fallback_segments += 1
            else:
                rule_segments += 1
            chunk_id = f"{record['doc_id']}-segment-{index:04d}"
            entity_records: list[GraphNodeRecord] = []
            for entity in entities:
                entity_records.append(
                    self._build_or_merge_node(
                        node_updates=node_updates,
                        collection_name=collection_name,
                        doc_id=record['doc_id'],
                        entity=entity,
                        segment=segment,
                        now=now,
                    )
                )
            if llm_relations:
                edge_candidates = self._build_edges_from_relations(
                    record=record,
                    segment=segment,
                    text=text,
                    chunk_id=chunk_id,
                    entities=entity_records,
                    relations=llm_relations,
                    now=now,
                )
            else:
                edge_candidates = self._extract_edges(
                    record=record,
                    segment=segment,
                    text=text,
                    chunk_id=chunk_id,
                    entities=entity_records,
                    now=now,
                )
            for edge in edge_candidates:
                edge_updates[edge['edge_id']] = edge

        for node in node_updates.values():
            self.state.graph_nodes[node['node_id']] = node
            if self.persistence is not None:
                self.persistence.upsert_graph_node(node)
        for edge in edge_updates.values():
            # 先更新内存态，再镜像到持久层，保证进程内检索看到的是本次重建后的完整结果。
            self.state.graph_edges[edge['edge_id']] = edge
            if self.persistence is not None:
                self.persistence.upsert_graph_edge(edge)

        payload = {
            'collection_name': collection_name,
            'doc_id': record['doc_id'],
            'nodes': len(node_updates),
            'edges': len(edge_updates),
            'segments': len(segments),
            'llm_enabled': self.llm is not None,
            'llm_segments': llm_segments,
            'rule_segments': rule_segments,
            'fallback_segments': fallback_segments,
        }
        self.trace.record('graph_document_indexed', payload)
        return payload

    def delete_document_graph(self, doc_id: str) -> None:
        """删除指定文档关联的所有图边，并清理孤立节点。"""
        edges_to_delete = [edge_id for edge_id, edge in self.state.graph_edges.items() if edge.get('doc_id') == doc_id]
        touched_nodes: set[str] = set()
        for edge_id in edges_to_delete:
            edge = self.state.graph_edges.pop(edge_id, None)
            if edge is None:
                continue
            touched_nodes.update({edge['source_node_id'], edge['target_node_id']})
            if self.persistence is not None:
                self.persistence.delete_graph_edge(edge_id)

        for node_id, node in list(self.state.graph_nodes.items()):
            if node_id not in touched_nodes and doc_id not in set(node.get('doc_ids', [])):
                continue
            remaining_doc_ids = sorted(
                {
                    edge['doc_id']
                    for edge in self.state.graph_edges.values()
                    if edge['source_node_id'] == node_id or edge['target_node_id'] == node_id
                }
            )
            if not remaining_doc_ids:
                self.state.graph_nodes.pop(node_id, None)
                if self.persistence is not None:
                    self.persistence.delete_graph_node(node_id)
                continue
            updated: GraphNodeRecord = {
                **node,
                'doc_ids': remaining_doc_ids,
                'mention_count': max(len(remaining_doc_ids), 1),
                'updated_at': datetime.now(timezone.utc),
            }
            self.state.graph_nodes[node_id] = updated
            if self.persistence is not None:
                self.persistence.upsert_graph_node(updated)

    def delete_collection_graph(self, collection_name: str) -> None:
        """删除集合级图谱数据。"""
        for edge_id, edge in list(self.state.graph_edges.items()):
            if edge['collection_name'] != collection_name:
                continue
            self.state.graph_edges.pop(edge_id, None)
            if self.persistence is not None:
                self.persistence.delete_graph_edge(edge_id)
        for node_id, node in list(self.state.graph_nodes.items()):
            if node['collection_name'] != collection_name:
                continue
            self.state.graph_nodes.pop(node_id, None)
            if self.persistence is not None:
                self.persistence.delete_graph_node(node_id)

    def get_runtime_status(self) -> dict[str, Any]:
        """返回图谱运行时规模信息。"""
        return {
            'node_count': len(self.state.graph_nodes),
            'edge_count': len(self.state.graph_edges),
            'collections': len({item['collection_name'] for item in self.state.graph_nodes.values()}),
            'llm_extraction_enabled': self.llm is not None,
        }

    def retrieve(
        self,
        *,
        collection_name: str,
        question: str,
        top_k: int,
        max_hops: int = 1,
        filters: dict[str, Any] | None = None,
        entity_types: list[str] | None = None,
    ) -> tuple[list[CitationItem], dict[str, Any]]:
        """执行图谱增强检索，并返回证据引用。

        Args:
            collection_name: 集合名称。
            question: 用户问题。
            top_k: 返回引用上限。
            max_hops: 图扩展最大跳数。
            filters: 可选业务过滤条件。
            entity_types: 可选实体类型白名单。

        Returns:
            第一项为图谱增强引用列表，第二项为图谱侧观测信息。
        """
        query_entities = self._extract_query_entities(question)
        seed_matches = self._match_seed_nodes(collection_name, query_entities, question, entity_types)
        if not seed_matches:
            info = {
                'enabled': False,
                'seed_entities': query_entities,
                'seed_node_count': 0,
                'expanded_edge_count': 0,
                'returned_citations': 0,
                'max_hops': max_hops,
                'reason': 'no_seed_nodes',
            }
            self.trace.record('graph_retrieval', {'collection_name': collection_name, **info})
            return [], info

        relation_terms = self._extract_relation_terms(question)
        edge_matches = self._expand_edges(
            collection_name=collection_name,
            seed_matches=seed_matches,
            max_hops=max(1, min(int(max_hops or 1), 3)),
            filters=filters,
            entity_types=entity_types,
            relation_terms=relation_terms,
        )
        citations = self._build_graph_citations(
            collection_name=collection_name,
            edge_matches=edge_matches[: max(top_k * 3, top_k)],
            top_k=top_k,
        )
        info = {
            'enabled': True,
            'seed_entities': query_entities,
            'seed_node_count': len(seed_matches),
            'expanded_edge_count': len(edge_matches),
            'returned_citations': len(citations),
            'max_hops': max_hops,
            'relation_terms': relation_terms,
            'paths': [item.get('graph_path') for item in edge_matches[: min(len(edge_matches), 8)]],
        }
        self.trace.record('graph_retrieval', {'collection_name': collection_name, **info})
        return citations, info
