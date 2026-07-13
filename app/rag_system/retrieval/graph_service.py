"""RAG 系统 GraphRAG 图谱服务模块。

基于规则和 LLM 抽取实体关系，提供图谱增强检索。
与主应用的 `app/services/graph_service.py` 功能一致，但使用独立状态和持久化。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from app.rag_system.models.query import CitationItem
from app.rag_system.observability.trace import TraceRecorder
from app.rag_system.retrieval.graph_parts.extraction import RagGraphExtractionMixin
from app.rag_system.retrieval.graph_parts.builders import RagGraphBuilderMixin
from app.rag_system.store.persistence import RagPersistence
from app.rag_system.store.state import RagState, RagGraphNodeRecord, RagGraphEdgeRecord
from app.rag_system.vector_store.chroma import ChromaClientFactory


class RagGraphService(RagGraphExtractionMixin, RagGraphBuilderMixin):
    """基于规则抽取实体关系，并提供图谱增强检索。"""

    ENTITY_STOPWORDS: set[str] = {
        '可以', '需要', '支持', '相关', '当前', '已经', '这些', '那些',
        '这个', '那个', '内容', '信息', '文档', '章节', '功能', '模块',
        '接口', '步骤', '流程', '问题', '说明', '方法', '如下', '这里',
        '进行', '用于', '通过', '执行', '默认', '系统中', '用户', '能力', '策略',
    }

    RELATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
        ('belongs_to', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:属于|隶属于|归属)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('depends_on', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:依赖|基于)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('uses', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:调用|使用|接入|引用)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('manages', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:负责|维护|管理)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('contains', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:包含|包括)\s*(?P<tgt>[^，。；:：]{2,40})')),
        ('related_to', re.compile(r'(?P<src>[^，。；:：]{2,40}?)(?:关联|联动|连接)\s*(?P<tgt>[^，。；:：]{2,40})')),
    ]

    def __init__(
        self,
        state: RagState,
        vector_store: ChromaClientFactory,
        trace: TraceRecorder,
        persistence: RagPersistence | None = None,
        llm: Any | None = None,
    ) -> None:
        """初始化图谱服务。

        Args:
            state: RAG 系统内存状态。
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

    def retrieve(
        self,
        collection_name: str,
        question: str,
        top_k: int = 5,
        max_hops: int = 1,
        entity_types: list[str] | None = None,
    ) -> list[CitationItem]:
        """图谱增强检索：通过实体关系扩展检索结果。

        Args:
            collection_name: 知识库名称。
            question: 检索问题。
            top_k: 返回数量上限。
            max_hops: 关系扩展跳数。
            entity_types: 实体类型白名单。

        Returns:
            图谱增强检索到的引用列表。
        """
        # 从问题中提取关键词，匹配实体
        candidates: list[CitationItem] = []
        tokens = re.findall(r"[0-9A-Za-z_一-鿿]+", question.lower())
        matched_node_ids: set[str] = set()

        for node_id, node in self.state.graph_nodes.items():
            if node.get('collection_name') != collection_name:
                continue
            label = node.get('label', '').lower()
            if any(token in label for token in tokens):
                matched_node_ids.add(node_id)

        if not matched_node_ids:
            return []

        # 按跳数扩展
        expanded_nodes: set[str] = set(matched_node_ids)
        for hop in range(max_hops):
            for edge_id, edge in list(self.state.graph_edges.items()):
                if edge.get('collection_name') != collection_name:
                    continue
                if edge['source_node_id'] in expanded_nodes:
                    expanded_nodes.add(edge['target_node_id'])
                if edge['target_node_id'] in expanded_nodes:
                    expanded_nodes.add(edge['source_node_id'])

        # 收集关联的 chunk_id
        chunk_ids: set[str] = set()
        for node_id in expanded_nodes:
            node = self.state.graph_nodes.get(node_id)
            if node:
                chunk_ids.update(node.get('chunk_ids', []))

        # 从向量库获取 chunk 内容
        for cid in list(chunk_ids)[:top_k]:
            try:
                collection = self.vector_store.get_or_create_collection(collection_name)
                result = collection.get(ids=[cid], include=['documents', 'metadatas'])
                if result and result['ids']:
                    text = result['documents'][0] if result['documents'] else ''
                    meta = result['metadatas'][0] if result['metadatas'] else {}
                    candidates.append(CitationItem(
                        chunk_id=cid,
                        source=meta.get('file_name', ''),
                        text=text,
                        score=0.8,
                        graph_path=f'hop:{max_hops}',
                    ))
            except Exception:
                continue

        return candidates

    def extract_and_store(
        self,
        collection_name: str,
        doc_id: str,
        segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """为文档提取实体关系并存储到图谱。"""
        now = datetime.now(timezone.utc).isoformat()
        node_updates: dict[str, RagGraphNodeRecord] = {}
        edge_updates: dict[str, RagGraphEdgeRecord] = {}

        for index, segment in enumerate(segments, start=1):
            text = re.sub(r'\s+', ' ', str(segment.get('text') or '')).strip()
            if not text:
                continue

            # 规则抽取实体和关系
            entities = self._extract_entities(text)
            if not entities:
                continue

            chunk_id = f"{doc_id}-segment-{index:04d}"
            entity_records: list[RagGraphNodeRecord] = []

            for entity_text in entities:
                node_id = f"entity:{collection_name}:{hash(entity_text) % 10**10}"
                if node_id in node_updates:
                    record = node_updates[node_id]
                else:
                    record = RagGraphNodeRecord(
                        node_id=node_id,
                        label=entity_text,
                        collection_name=collection_name,
                        chunk_ids=[],
                        metadata={'doc_id': doc_id},
                    )
                if chunk_id not in record['chunk_ids']:
                    record['chunk_ids'].append(chunk_id)
                node_updates[node_id] = record
                entity_records.append(record)

            # 抽取关系
            for relation_type, pattern in self.RELATION_PATTERNS:
                for match in pattern.finditer(text):
                    src_text = match.group('src').strip()
                    tgt_text = match.group('tgt').strip()
                    if src_text in entities and tgt_text in entities:
                        edge_id = f"edge:{hash(src_text) % 10**10}:{hash(tgt_text) % 10**10}:{relation_type}"
                        src_node_id = f"entity:{collection_name}:{hash(src_text) % 10**10}"
                        tgt_node_id = f"entity:{collection_name}:{hash(tgt_text) % 10**10}"
                        edge = RagGraphEdgeRecord(
                            edge_id=edge_id,
                            source_node_id=src_node_id,
                            target_node_id=tgt_node_id,
                            relation=relation_type,
                            collection_name=collection_name,
                            weight=1.0,
                            metadata={'doc_id': doc_id},
                        )
                        edge_updates[edge_id] = edge

        # 写入状态和持久化
        for node in node_updates.values():
            self.state.graph_nodes[node['node_id']] = node
            if self.persistence:
                self.persistence.upsert_graph_node(node)
        for edge in edge_updates.values():
            self.state.graph_edges[edge['edge_id']] = edge
            if self.persistence:
                self.persistence.upsert_graph_edge(edge)

        result = {
            'collection_name': collection_name,
            'doc_id': doc_id,
            'nodes': len(node_updates),
            'edges': len(edge_updates),
            'segments': len(segments),
        }
        self.trace.record('rag_graph_indexed', result)
        return result

    def _extract_entities(self, text: str) -> list[str]:
        """从文本中提取候选实体。"""
        # 提取名词性短语
        candidates = re.findall(r'[A-Za-z一-鿿][A-Za-z一-鿿]{1,20}', text)
        candidates = [c for c in candidates if c.lower() not in self.ENTITY_STOPWORDS and len(c) >= 2]
        return list(set(candidates))[:10]

    def delete_document_graph(self, doc_id: str) -> None:
        """删除文档关联的图谱数据。"""
        for edge_id, edge in list(self.state.graph_edges.items()):
            if edge.get('metadata', {}).get('doc_id') == doc_id:
                self.state.graph_edges.pop(edge_id, None)
                if self.persistence:
                    self.persistence.delete_graph_edge(edge_id)
        for node_id, node in list(self.state.graph_nodes.items()):
            if node.get('metadata', {}).get('doc_id') == doc_id:
                self.state.graph_nodes.pop(node_id, None)
                if self.persistence:
                    self.persistence.delete_graph_node(node_id)

    def delete_collection_graph(self, collection_name: str) -> None:
        """删除集合级图谱数据。"""
        for edge_id, edge in list(self.state.graph_edges.items()):
            if edge.get('collection_name') == collection_name:
                self.state.graph_edges.pop(edge_id, None)
                if self.persistence:
                    self.persistence.delete_graph_edge(edge_id)
        for node_id, node in list(self.state.graph_nodes.items()):
            if node.get('collection_name') == collection_name:
                self.state.graph_nodes.pop(node_id, None)
                if self.persistence:
                    self.persistence.delete_graph_node(node_id)

    def replace_document_graph(self, doc_id: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
        """重建指定文档的图谱节点和边（含 LLM 增强抽取）。

        Args:
            doc_id: 文档 ID。
            segments: 文档片段列表。

        Returns:
            图谱重建统计。
        """
        doc = next((d for d in self.state.documents.values() if d.get('doc_id') == doc_id), None)
        if doc is None:
            raise ValueError(f'文档不存在: {doc_id}')

        # 先删除旧的图谱
        self.delete_document_graph(doc_id)
        collection_name = doc.get('collection_name', '')

        # 用 extract_and_store 重建
        result = self.extract_and_store(collection_name, doc_id, segments)
        result['action'] = 'replaced'
        return result

    def _extract_segment_graph(self, text: str, doc_id: str, collection_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """对单个片段执行图谱抽取（优先 LLM，回退到规则）。

        Returns:
            (抽取的实体列表, 抽取的关系列表, 抽取模式)
        """
        if self.llm:
            try:
                return self._extract_with_llm(text, doc_id, collection_name)
            except Exception:
                pass
        return self._extract_with_rules(text, doc_id, collection_name), [], 'rule'

    def _extract_with_llm(self, text: str, doc_id: str, collection_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """用 LLM 抽取实体和关系。"""
        prompt = f"""从以下文本中提取实体和关系，返回JSON格式：
{{
  "entities": [{{"name": "...", "type": "..."}}],
  "relations": [{{"source": "...", "target": "...", "type": "..."}}]
}}
文本：{text[:2000]}"""
        response = self.llm.complete(prompt)
        try:
            import json
            data = json.loads(str(response).strip())
            entities = data.get('entities', [])
            relations = data.get('relations', [])
            return entities, relations, 'llm'
        except Exception:
            return self._extract_with_rules(text, doc_id, collection_name), [], 'llm_fallback'

    def _extract_with_rules(self, text: str, doc_id: str, collection_name: str) -> list[dict[str, Any]]:
        """用规则抽取实体。"""
        entities = []
        candidates = re.findall(r'[A-Za-z一-鿿][A-Za-z一-鿿]{1,20}', text)
        for c in set(candidates):
            if c.lower() not in self.ENTITY_STOPWORDS and len(c) >= 2:
                entities.append({'name': c, 'type': 'concept', 'doc_id': doc_id})
        return entities

    def get_runtime_status(self) -> dict[str, Any]:
        """返回图谱服务运行状态。"""
        return {
            'total_nodes': len(self.state.graph_nodes),
            'total_edges': len(self.state.graph_edges),
            'llm_enabled': self.llm is not None,
            'collection_count': len(set(
                n.get('collection_name', '') for n in self.state.graph_nodes.values()
            )),
        }
