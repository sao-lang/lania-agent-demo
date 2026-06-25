"""`graph_service.py` 的图谱抽取子模块。

集中处理实体识别、LLM 关系抽取、规则边生成和实体归并，
让图谱服务主类更聚焦于生命周期和检索入口。
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
from app.services.graph_service_parts._typing import GraphServiceTypingMixin
from app.types import DocumentRecord, GraphEdgeRecord, GraphNodeRecord

class GraphExtractionMixin(GraphServiceTypingMixin):
    """放图谱抽取里和实体、关系识别相关的逻辑。"""

    def _extract_segment_entities(
        self,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
    ) -> list[dict[str, str]]:
        """先用规则从片段和文档元数据里捞一批候选实体。"""
        raw_candidates: list[str] = []
        raw_candidates.extend(self._candidate_entities_from_text(text))
        for field in (
            record.get('document_title'),
            segment.get('section_title'),
            record.get('version'),
            record.get('year'),
            record.get('quarter'),
        ):
            if field:
                raw_candidates.append(str(field))
        for field in (record.get('document_keywords') or [])[:8]:
            raw_candidates.append(str(field))
        entities: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in raw_candidates:
            normalized = self._normalize_entity(candidate)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            entities.append(
                {
                    'name': self._display_entity(candidate),
                    'normalized_name': normalized,
                    'entity_type': self._guess_entity_type(candidate),
                }
            )
            if len(entities) >= 16:
                break
        return entities

    def _extract_segment_graph(
        self,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]], str]:
        """抽取片段里的实体和关系，必要时从 LLM 回退到规则模式。"""
        rule_entities = self._extract_segment_entities(record, segment, text)
        if self.llm is None:
            return rule_entities, [], 'rule'

        prompt = self._build_llm_graph_prompt(record, segment, text)
        try:
            response = self.llm.complete(prompt)
            raw = str(response).strip()
        except Exception as exc:
            self.trace.record(
                'graph_llm_extraction_failed',
                {
                    'collection_name': record['collection_name'],
                    'doc_id': record['doc_id'],
                    'section_title': segment.get('section_title'),
                    'reason': str(exc),
                },
            )
            return rule_entities, [], 'rule_fallback'

        payload = self._parse_json_object(raw)
        if payload is None:
            self.trace.record(
                'graph_llm_extraction_failed',
                {
                    'collection_name': record['collection_name'],
                    'doc_id': record['doc_id'],
                    'section_title': segment.get('section_title'),
                    'reason': 'invalid_json',
                },
            )
            return rule_entities, [], 'rule_fallback'

        # LLM 结果不一定完整，所以会把实体列表和关系里隐含的实体一起合并。
        llm_entities = self._normalize_llm_entities(payload.get('entities'))
        llm_relations = self._normalize_llm_relations(payload.get('relations'))
        relation_entities = self._build_entities_from_relations(llm_relations)
        merged_entities = self._merge_entities(llm_entities, relation_entities)
        if not merged_entities and not llm_relations:
            self.trace.record(
                'graph_llm_extraction_empty',
                {
                    'collection_name': record['collection_name'],
                    'doc_id': record['doc_id'],
                    'section_title': segment.get('section_title'),
                },
            )
            return rule_entities, [], 'rule_fallback'

        return merged_entities, llm_relations, 'llm'

    def _build_llm_graph_prompt(
        self,
        record: DocumentRecord,
        segment: dict[str, Any],
        text: str,
    ) -> str:
        """拼出给 LLM 的图谱抽取提示词。"""
        relation_names = ', '.join(self.RELATION_KEYWORDS.keys())
        return (
            '你是 RAG 图谱抽取器。请从给定文档片段中抽取实体和关系，只输出 JSON 对象。\n'
            '要求：\n'
            '1) 只输出一个 JSON object，不要输出解释。\n'
            '2) JSON 格式为 {"entities":[...],"relations":[...]}。\n'
            '3) entities 每项格式：{"name":"实体名","entity_type":"concept/system/interface/version/time/organization","aliases":["别名1"]}。\n'
            '4) relations 每项格式：{"source":"源实体","target":"目标实体","relation":"关系类型","evidence":"原文证据"}。\n'
            f'5) relation 只能取：{relation_names}。\n'
            '6) 仅抽取片段中明确出现且有证据支撑的实体和关系；不确定就留空。\n'
            f'文档标题：{record.get("document_title") or ""}\n'
            f'文档关键词：{", ".join(str(item) for item in (record.get("document_keywords") or [])[:8])}\n'
            f'章节标题：{segment.get("section_title") or ""}\n'
            f'层级路径：{segment.get("hierarchy_path") or ""}\n'
            f'正文：{text[:1800]}\n'
            '输出：'
        )

    def _parse_json_object(self, raw: str) -> dict[str, Any] | None:
        """尽量从 LLM 返回文本里抠出一个 JSON object。"""
        cleaned = raw.strip()
        fenced = re.search(r'```(?:json)?\s*(\{[\s\S]*\})\s*```', cleaned, re.IGNORECASE)
        if fenced:
            cleaned = fenced.group(1).strip()
        if not cleaned.startswith('{'):
            start = cleaned.find('{')
            end = cleaned.rfind('}')
            if start >= 0 and end > start:
                cleaned = cleaned[start : end + 1]
        try:
            payload = json.loads(cleaned)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _normalize_llm_entities(self, raw_entities: Any) -> list[dict[str, Any]]:
        """把 LLM 返回的实体列表规整成内部统一结构。"""
        if not isinstance(raw_entities, list):
            return []
        entities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_entities:
            name = ''
            entity_type = 'concept'
            aliases: list[str] = []
            if isinstance(item, dict):
                name = str(item.get('name') or '').strip()
                entity_type = str(item.get('entity_type') or 'concept').strip().lower() or 'concept'
                raw_aliases = item.get('aliases')
                if isinstance(raw_aliases, list):
                    aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
            elif isinstance(item, str):
                name = item.strip()
            normalized = self._normalize_entity(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            entities.append(
                {
                    'name': self._display_entity(name),
                    'normalized_name': normalized,
                    'entity_type': entity_type,
                    'aliases': aliases,
                }
            )
        return entities[:20]

    def _normalize_llm_relations(self, raw_relations: Any) -> list[dict[str, str]]:
        """把 LLM 返回的关系列表规整成内部统一结构。"""
        if not isinstance(raw_relations, list):
            return []
        relations: list[dict[str, str]] = []
        for item in raw_relations:
            if not isinstance(item, dict):
                continue
            source = self._display_entity(item.get('source') or '')
            target = self._display_entity(item.get('target') or '')
            if not self._normalize_entity(source) or not self._normalize_entity(target):
                continue
            if self._normalize_entity(source) == self._normalize_entity(target):
                continue
            relations.append(
                {
                    'source': source,
                    'target': target,
                    'relation': self._normalize_relation_name(item.get('relation')),
                    'evidence': re.sub(r'\s+', ' ', str(item.get('evidence') or '')).strip(),
                }
            )
        return relations[:24]

    def _build_entities_from_relations(self, relations: list[dict[str, str]]) -> list[dict[str, Any]]:
        """从关系两端反推实体，补齐只给了 relation 没给 entities 的情况。"""
        entities: list[dict[str, Any]] = []
        seen: set[str] = set()
        for relation in relations:
            for name in (relation['source'], relation['target']):
                normalized = self._normalize_entity(name)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                entities.append(
                    {
                        'name': self._display_entity(name),
                        'normalized_name': normalized,
                        'entity_type': self._guess_entity_type(name),
                        'aliases': [],
                    }
                )
        return entities

    def _merge_entities(self, *groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按归一化名称合并多批实体，顺手去重别名。"""
        merged: dict[str, dict[str, Any]] = {}
        for group in groups:
            for item in group:
                normalized = str(item.get('normalized_name') or '')
                if not normalized:
                    continue
                current = merged.get(normalized)
                aliases = [str(alias).strip() for alias in item.get('aliases', []) if str(alias).strip()]
                if current is None:
                    merged[normalized] = {
                        'name': self._display_entity(item.get('name') or normalized),
                        'normalized_name': normalized,
                        'entity_type': str(item.get('entity_type') or 'concept'),
                        'aliases': self._dedupe_aliases(aliases),
                    }
                    continue
                current_aliases = [*current.get('aliases', []), *aliases]
                current['aliases'] = self._dedupe_aliases(current_aliases)
        return list(merged.values())[:20]

    def _candidate_entities_from_text(self, text: str) -> list[str]:
        """从原始文本中粗提一批候选实体片段。

        Args:
            text: 待分析文本。

        Returns:
            基于规则命中的候选实体字符串列表。
        """
        candidates: list[str] = []
        patterns = [
            r'[\u4e00-\u9fff]{2,12}(?:系统|平台|模块|服务|接口|流程|策略|摘要|会话|知识库|文档|版本|部门|团队|中心)',
            r'[A-Za-z][A-Za-z0-9_/\.-]{2,40}',
            r'v?\d+\.\d+(?:\.\d+)?',
            r'20\d{2}Q[1-4]',
        ]
        for pattern in patterns:
            candidates.extend(re.findall(pattern, text))
        return candidates

    def _extract_query_entities(self, question: str) -> list[str]:
        """从用户问题里提取图检索的种子实体。

        Args:
            question: 用户问题。

        Returns:
            去重后的展示态实体列表。
        """
        entities = [self._display_entity(item) for item in self._candidate_entities_from_text(question)]
        if not entities:
            entities = [part for part in re.split(r'[\s,，。？?；;]+', question) if len(part.strip()) >= 2]
        deduped: list[str] = []
        seen: set[str] = set()
        for entity in entities:
            normalized = self._normalize_entity(entity)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(self._display_entity(entity))
            if len(deduped) >= 8:
                break
        return deduped

    def _match_seed_nodes(
        self,
        collection_name: str,
        query_entities: list[str],
        question: str,
        entity_types: list[str] | None,
    ) -> dict[str, float]:
        """把问题中的候选实体映射到图谱中的起始节点。

        Args:
            collection_name: 当前集合名称。
            query_entities: 从问题中抽取出的实体列表。
            question: 原始用户问题。
            entity_types: 可选实体类型白名单。

        Returns:
            节点 ID 到种子匹配分数的映射。
        """
        allowed_types = {item.strip().lower() for item in entity_types or [] if item.strip()}
        matches: dict[str, float] = {}
        for node in self.state.graph_nodes.values():
            if node['collection_name'] != collection_name:
                continue
            if allowed_types and str(node.get('entity_type') or '').strip().lower() not in allowed_types:
                continue
            score = 0.0
            node_name = str(node['name'])
            normalized_name = str(node['normalized_name'])
            aliases = [self._normalize_entity(item) for item in node.get('aliases', [])]
            for entity in query_entities:
                normalized_entity = self._normalize_entity(entity)
                if not normalized_entity:
                    continue
                if normalized_entity == normalized_name:
                    score = max(score, 1.0)
                elif normalized_entity in aliases:
                    score = max(score, 0.92)
                elif normalized_entity in normalized_name or normalized_name in normalized_entity:
                    score = max(score, 0.84)
            if not score and node_name in question:
                score = 0.72
            if score > 0:
                matches[node['node_id']] = score
        return dict(sorted(matches.items(), key=lambda item: item[1], reverse=True)[:12])

    def _extract_relation_terms(self, question: str) -> list[str]:
        """从问题中识别用户显式表达的关系意图。"""
        found: list[str] = []
        lowered = question.lower()
        for relation, keywords in self.RELATION_KEYWORDS.items():
            if any(keyword in question or keyword.lower() in lowered for keyword in keywords):
                found.append(relation)
        return found

    def _expand_edges(
        self,
        *,
        collection_name: str,
        seed_matches: dict[str, float],
        max_hops: int,
        filters: dict[str, Any] | None,
        entity_types: list[str] | None,
        relation_terms: list[str],
    ) -> list[dict[str, Any]]:
        """从种子节点出发扩展图边，并为每条证据路径打分。

        Args:
            collection_name: 当前集合名称。
            seed_matches: 起始节点及其匹配分数。
            max_hops: 最大扩展跳数。
            filters: 可选业务过滤条件。
            entity_types: 可选实体类型白名单。
            relation_terms: 从问题中识别出的关系词。

        Returns:
            按分数倒序排列的图检索候选路径列表。
        """
        adjacency: dict[str, list[GraphEdgeRecord]] = defaultdict(list)
        allowed_types = {item.strip().lower() for item in entity_types or [] if item.strip()}
        for edge in self.state.graph_edges.values():
            if edge['collection_name'] != collection_name:
                continue
            if not self._matches_filters(edge.get('metadata') or {}, filters):
                continue
            adjacency[edge['source_node_id']].append(edge)
            adjacency[edge['target_node_id']].append(edge)

        queue: list[tuple[str, float, int, list[GraphEdgeRecord]]] = [
            (node_id, score, 0, []) for node_id, score in seed_matches.items()
        ]
        best_paths: dict[str, dict[str, Any]] = {}
        visited_depth: dict[tuple[str, str], int] = {}

        while queue:
            node_id, seed_score, depth, path = queue.pop(0)
            if depth >= max_hops:
                continue
            for edge in adjacency.get(node_id, []):
                next_node_id = edge['target_node_id'] if edge['source_node_id'] == node_id else edge['source_node_id']
                next_node = self.state.graph_nodes.get(next_node_id)
                if next_node is None:
                    continue
                if allowed_types and str(next_node.get('entity_type') or '').strip().lower() not in allowed_types:
                    continue
                state_key = (next_node_id, edge['edge_id'])
                current_depth = depth + 1
                if state_key in visited_depth and visited_depth[state_key] <= current_depth:
                    continue
                visited_depth[state_key] = current_depth
                relation_bonus = 0.18 if edge['normalized_relation'] in relation_terms else 0.0
                score = round(seed_score * (0.92 ** depth) + float(edge.get('weight') or 0.0) + relation_bonus, 4)
                path_edges = [*path, edge]
                path_summary = self._build_path_summary(path_edges)
                evidence_id = edge['evidence_chunk_id']
                existing = best_paths.get(evidence_id)
                candidate = {
                    'edge_id': edge['edge_id'],
                    'evidence_chunk_id': evidence_id,
                    'score': score,
                    'graph_path': path_summary,
                    'graph_relation': edge['relation'],
                    'graph_start_entity': path_edges[0]['source_name'],
                    'graph_end_entity': path_edges[-1]['target_name'],
                    'graph_path_hops': len(path_edges),
                }
                if existing is None or score > existing['score']:
                    best_paths[evidence_id] = candidate
                queue.append((next_node_id, seed_score * 0.88, current_depth, path_edges))

        return sorted(best_paths.values(), key=lambda item: item['score'], reverse=True)
