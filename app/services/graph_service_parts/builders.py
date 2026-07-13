"""`graph_service.py` 的边节点构建与引用组装子模块。

负责 citation 回填、节点合并、边构建、过滤判断以及若干标准化工具函数，
用于压缩原始大文件的后半段实现。
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from app.models.query import CitationItem
from app.services.graph_service_parts._typing import GraphServiceTypingMixin
from app.types import DocumentRecord, GraphEdgeRecord, GraphNodeRecord


class GraphBuilderMixin(GraphServiceTypingMixin):
    """放图谱节点、边和引用结果的组装逻辑。"""

    def _build_graph_citations(
        self,
        *,
        collection_name: str,
        edge_matches: list[dict[str, Any]],
        top_k: int,
    ) -> list[CitationItem]:
        """把图谱命中的边重新组装成标准 Citation 列表。"""
        if not edge_matches:
            return []
        evidence_ids = [item['evidence_chunk_id'] for item in edge_matches]
        collection = self.vector_store.get_or_create_collection(collection_name)
        payload = collection.get(ids=evidence_ids, include=['documents', 'metadatas'])
        documents = payload.get('documents') or []
        metadatas = payload.get('metadatas') or []
        match_by_chunk = {item['evidence_chunk_id']: item for item in edge_matches}
        citations: list[CitationItem] = []
        for chunk_id, text, metadata in zip(evidence_ids, documents, metadatas):
            match = match_by_chunk.get(str(chunk_id))
            if match is None:
                continue
            metadata = metadata or {}
            resolved_text = str(text or match.get('graph_path') or '').strip()
            if not resolved_text:
                resolved_text = str(match.get('graph_path') or '')
            citations.append(
                CitationItem(
                    chunk_id=str(chunk_id),
                    matched_chunk_id=str(chunk_id),
                    source=str(metadata.get('source') or metadata.get('file_name') or 'graph'),
                    file_path=metadata.get('file_path'),
                    page=metadata.get('page'),
                    score=round(float(match['score']), 4),
                    text=resolved_text,
                    index_kind='graph',
                    node_level='graph_evidence',
                    matched_via=['graph_entity', 'graph_path'],
                    context_scope='graph_evidence',
                    section_title=metadata.get('section_title'),
                    hierarchy_path=metadata.get('hierarchy_path'),
                    source_archive=metadata.get('source_archive'),
                    archive_member_path=metadata.get('archive_member_path'),
                    archive_member_display_path=metadata.get('archive_member_display_path'),
                    graph_path=str(match.get('graph_path') or ''),
                    graph_relation=str(match.get('graph_relation') or ''),
                    graph_start_entity=str(match.get('graph_start_entity') or ''),
                    graph_end_entity=str(match.get('graph_end_entity') or ''),
                    graph_path_hops=int(match.get('graph_path_hops') or 0),
                )
            )
        return sorted(citations, key=lambda item: item.score or 0.0, reverse=True)[:top_k]

    def _build_or_merge_node(
        self,
        *,
        node_updates: dict[str, GraphNodeRecord],
        collection_name: str,
        doc_id: str,
        entity: dict[str, str],
        segment: dict[str, Any],
        now: datetime,
    ) -> GraphNodeRecord:
        """新建节点，或者把这次命中的信息并回已有节点。"""
        node_id = self._node_id(collection_name, entity['normalized_name'])
        existing = node_updates.get(node_id) or self.state.graph_nodes.get(node_id)
        aliases = [entity['name'], *(str(item) for item in entity.get('aliases', []))]
        if segment.get('section_title'):
            aliases.append(str(segment.get('section_title')))
        if existing is None:
            node: GraphNodeRecord = {
                'node_id': node_id,
                'collection_name': collection_name,
                'name': entity['name'],
                'normalized_name': entity['normalized_name'],
                'entity_type': entity['entity_type'],
                'aliases': self._dedupe_aliases(aliases),
                'doc_ids': [doc_id],
                'mention_count': 1,
                'metadata': {
                    'section_title': segment.get('section_title'),
                    'hierarchy_path': segment.get('hierarchy_path'),
                },
                'created_at': now,
                'updated_at': now,
            }
            node_updates[node_id] = node
            return node

        merged_aliases = self._dedupe_aliases([*(existing.get('aliases') or []), *aliases])
        merged_doc_ids = sorted(str(item) for item in {*(existing.get('doc_ids') or []), doc_id})
        merged: GraphNodeRecord = {
            **existing,
            'name': existing.get('name') or entity['name'],
            'entity_type': existing.get('entity_type') or entity['entity_type'],
            'aliases': merged_aliases,
            'doc_ids': merged_doc_ids,
            'mention_count': int(existing.get('mention_count') or 0) + 1,
            'updated_at': now,
        }
        node_updates[node_id] = merged
        return merged

    def _extract_edges(
        self,
        *,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
        chunk_id: str,
        entities: list[GraphNodeRecord],
        now: datetime,
    ) -> list[GraphEdgeRecord]:
        """根据句子共现关系给一批实体补边。"""
        sentences = self._split_sentences(text)
        edge_updates: dict[str, GraphEdgeRecord] = {}
        for sentence in sentences[:8]:
            relation = self._relation_from_sentence(sentence)
            sentence_entities = [
                entity
                for entity in entities
                if entity['name'] in sentence or entity['normalized_name'] in self._normalize_entity(sentence)
            ]
            if len(sentence_entities) < 2:
                continue
            for index in range(len(sentence_entities) - 1):
                source = sentence_entities[index]
                target = sentence_entities[index + 1]
                if source['node_id'] == target['node_id']:
                    continue
                edge = self._build_edge(
                    record=record,
                    segment=segment,
                    chunk_id=chunk_id,
                    sentence=sentence,
                    source=source,
                    target=target,
                    relation=relation,
                    now=now,
                )
                edge_updates[edge['edge_id']] = edge

        if edge_updates or len(entities) < 2:
            return list(edge_updates.values())

        # 当句子关系不明显时，保留一个弱关系边，避免图谱完全断开。
        fallback = self._build_edge(
            record=record,
            segment=segment,
            chunk_id=chunk_id,
            sentence=text[:240],
            source=entities[0],
            target=entities[1],
            relation='related_to',
            now=now,
        )
        return [fallback]

    def _build_edges_from_relations(
        self,
        *,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
        chunk_id: str,
        entities: list[GraphNodeRecord],
        relations: list[dict[str, str]],
        now: datetime,
    ) -> list[GraphEdgeRecord]:
        """优先吃 LLM 抽到的关系；如果没有，再回退到规则抽边。"""
        entity_by_name = {entity['normalized_name']: entity for entity in entities}
        edge_updates: dict[str, GraphEdgeRecord] = {}
        for relation in relations:
            source = entity_by_name.get(self._normalize_entity(relation['source']))
            target = entity_by_name.get(self._normalize_entity(relation['target']))
            if source is None or target is None or source['node_id'] == target['node_id']:
                continue
            sentence = relation['evidence'] or text[:240]
            edge = self._build_edge(
                record=record,
                segment=segment,
                chunk_id=chunk_id,
                sentence=sentence,
                source=source,
                target=target,
                relation=relation['relation'],
                now=now,
            )
            edge_updates[edge['edge_id']] = edge
        if edge_updates:
            return list(edge_updates.values())
        return self._extract_edges(
            record=record,
            segment=segment,
            text=text,
            chunk_id=chunk_id,
            entities=entities,
            now=now,
        )

    def _build_edge(
        self,
        *,
        record: DocumentRecord,
        segment: dict[str, Any],
        chunk_id: str,
        sentence: str,
        source: GraphNodeRecord,
        target: GraphNodeRecord,
        relation: str,
        now: datetime,
    ) -> GraphEdgeRecord:
        """把一条实体关系组装成标准图谱边记录。"""
        normalized_relation = relation.strip().lower()
        edge_seed = '|'.join(
            [
                record['collection_name'],
                record['doc_id'],
                source['normalized_name'],
                normalized_relation,
                target['normalized_name'],
                chunk_id,
            ]
        )
        edge_id = f"ge-{hashlib.sha1(edge_seed.encode('utf-8')).hexdigest()[:16]}"
        return {
            'edge_id': edge_id,
            'collection_name': record['collection_name'],
            'doc_id': record['doc_id'],
            'source_node_id': source['node_id'],
            'source_name': source['name'],
            'target_node_id': target['node_id'],
            'target_name': target['name'],
            'relation': relation,
            'normalized_relation': normalized_relation,
            'evidence_chunk_id': chunk_id,
            'evidence_text': sentence.strip(),
            'weight': self._relation_weight(relation),
            'metadata': {
                'doc_id': record['doc_id'],
                'file_name': record['file_name'],
                'file_path': record['file_path'],
                'file_type': record['file_type'],
                'collection_name': record['collection_name'],
                'year': record.get('year'),
                'quarter': record.get('quarter'),
                'version': record.get('version'),
                'permission': record.get('permission'),
                'section_title': segment.get('section_title'),
                'hierarchy_path': segment.get('hierarchy_path'),
                'page': segment.get('page'),
            },
            'created_at': now,
            'updated_at': now,
        }

    def _relation_from_sentence(self, sentence: str) -> str:
        """从一句文本里猜一个最像的关系类型。"""
        for relation, pattern in self.RELATION_PATTERNS:
            if pattern.search(sentence):
                return relation
        for relation, keywords in self.RELATION_KEYWORDS.items():
            if any(keyword in sentence for keyword in keywords):
                return relation
        return 'related_to'

    def _normalize_relation_name(self, value: Any) -> str:
        """把外部传进来的关系名统一规整到内部枚举。"""
        cleaned = re.sub(r'[\s-]+', '_', str(value or '')).strip().lower()
        if cleaned in self.RELATION_KEYWORDS:
            return cleaned
        for relation, keywords in self.RELATION_KEYWORDS.items():
            if cleaned == relation.lower():
                return relation
            if cleaned in {keyword.lower() for keyword in keywords}:
                return relation
        return 'related_to'

    def _relation_weight(self, relation: str) -> float:
        """给不同关系类型一个粗粒度权重。"""
        if relation in {'belongs_to', 'depends_on', 'uses', 'manages', 'applies_to', 'updates'}:
            return 0.26
        if relation == 'contains':
            return 0.18
        return 0.12

    def _build_path_summary(self, edges: list[GraphEdgeRecord]) -> str:
        """把一段图路径压成适合展示的文本摘要。"""
        parts: list[str] = []
        for edge in edges:
            parts.append(f"{edge['source_name']} --{edge['relation']}--> {edge['target_name']}")
        return ' | '.join(parts)

    def _matches_filters(self, metadata: dict[str, Any], filters: dict[str, Any] | None) -> bool:
        """判断一条图谱命中是否满足检索过滤条件。"""
        if not filters:
            return True
        for key, expected in filters.items():
            current = metadata.get(key)
            if isinstance(expected, dict):
                if 'eq' in expected and current != expected.get('eq'):
                    return False
                if 'in' in expected:
                    allowed = {str(item) for item in expected.get('in') or []}
                    if str(current) not in allowed:
                        return False
            elif isinstance(expected, list):
                if current not in expected:
                    return False
            elif current != expected:
                return False
        return True

    def _guess_entity_type(self, value: str) -> str:
        """根据实体文本形态猜测实体类型。

        Args:
            value: 原始实体文本。

        Returns:
            归纳后的实体类型标签。
        """
        lowered = value.lower()
        if re.fullmatch(r'v?\d+\.\d+(?:\.\d+)?', lowered):
            return 'version'
        if re.fullmatch(r'20\d{2}(?:q[1-4])?', lowered):
            return 'time'
        if '/' in value or 'api' in lowered or 'endpoint' in lowered:
            return 'interface'
        if any(token in value for token in ('系统', '平台', '服务', '模块')):
            return 'system'
        if any(token in value for token in ('部门', '团队', '中心', '小组')):
            return 'organization'
        return 'concept'

    def _normalize_entity(self, value: str) -> str:
        """对实体名称做归一化，便于去重和构建稳定 ID。

        Args:
            value: 原始实体文本。

        Returns:
            适合比较与存储的归一化实体名称；无效时返回空字符串。
        """
        cleaned = re.sub(r'[\s`"\'“”‘’\(\)\[\]\{\}<>]+', ' ', str(value)).strip().lower()
        cleaned = re.sub(r'[^\w\u4e00-\u9fff/\.-]+', ' ', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -_/.,')
        if not cleaned or cleaned in self.ENTITY_STOPWORDS or len(cleaned) < 2:
            return ''
        return cleaned

    def _display_entity(self, value: str) -> str:
        """把实体文本裁剪成适合展示的形式。"""
        return re.sub(r'\s+', ' ', str(value)).strip()[:80]

    def _dedupe_aliases(self, aliases: list[str]) -> list[str]:
        """按归一化名称去重实体别名，并限制别名数量。"""
        result: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            display = self._display_entity(alias)
            normalized = self._normalize_entity(display)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(display)
        return result[:12]

    def _node_id(self, collection_name: str, normalized_name: str) -> str:
        """根据集合名和归一化实体名生成稳定节点 ID。"""
        seed = f'{collection_name}|{normalized_name}'
        return f"gn-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:16]}"

    def _split_sentences(self, text: str) -> list[str]:
        """按中英文句末标点拆分文本，保留适合摘要构建的句子片段。"""
        items = re.split(r'(?<=[。！？!?；;\n])', text)
        return [re.sub(r'\s+', ' ', item).strip() for item in items if re.sub(r'\s+', ' ', item).strip()]
