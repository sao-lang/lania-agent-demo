"""RAG 系统图谱抽取子模块。

负责 LLM 实体关系抽取、规则边生成、实体归并、候选实体识别。
与主应用的 `app/services/graph_service_parts/extraction.py` 功能一致。
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.rag_system.retrieval.graph_parts._typing import RagGraphServiceTypingMixin


class RagGraphExtractionMixin(RagGraphServiceTypingMixin):
    """LLM 实体关系抽取、规则边生成、实体归并。"""

    RELATION_KEYWORDS: dict[str, tuple[str, ...]] = {
        'belongs_to': ('属于', '归属', '隶属'),
        'depends_on': ('依赖', '依存', '基于'),
        'uses': ('使用', '调用', '接入', '引用'),
        'manages': ('负责', '维护', '管理'),
        'applies_to': ('适用', '面向'),
        'contains': ('包含', '包括', '由'),
        'related_to': ('关联', '相关', '连接', '联动'),
        'updates': ('更新', '升级', '变更', '替换'),
    }

    def _extract_with_llm_enhanced(self, text: str, doc_id: str, collection_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        """用 LLM 抽取实体和关系（增强版）。"""
        if not self.llm:
            return self._extract_entities_rules(text, doc_id, collection_name), [], 'rule'

        prompt = f"""从以下文本中提取实体和关系，返回 JSON 格式。

要求：
1. entities: 每个实体有 name（名称）和 type（类型：concept/module/function/data/person）
2. relations: 每个关系有 source（源实体名）、target（目标实体名）、type（关系类型）

文本：{text[:2000]}

JSON 输出："""
        try:
            response = self.llm.complete(prompt)
            data = json.loads(str(response).strip())
            entities = data.get('entities', [])
            relations = data.get('relations', [])
            if entities:
                return entities, relations, 'llm'
        except Exception:
            pass
        return self._extract_entities_rules(text, doc_id, collection_name), [], 'rule_fallback'

    def _extract_entities_rules(self, text: str, doc_id: str, collection_name: str) -> list[dict[str, Any]]:
        """用规则从文本中提取候选实体。"""
        candidates = set()
        # 提取中英文名词性短语
        for match in re.finditer(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*', text):
            candidates.add(match.group().strip())
        for match in re.finditer(r'[一-鿿][一-鿿]{1,10}', text):
            word = match.group().strip()
            if word not in self.ENTITY_STOPWORDS and len(word) >= 2:
                candidates.add(word)
        return [
            {'name': c, 'type': 'concept', 'doc_id': doc_id}
            for c in candidates
            if c.lower() not in {s.lower() for s in self.ENTITY_STOPWORDS} and len(c) >= 2
        ][:20]

    def _extract_edges_rules(self, text: str, doc_id: str, entities: list[dict[str, Any]]) -> list[dict[str, str]]:
        """用规则从文本中抽取关系边。"""
        edges: list[dict[str, str]] = []
        entity_names = {e['name'] for e in entities}
        for rel_type, pattern in self.RELATION_PATTERNS:
            for match in pattern.finditer(text):
                src = match.group('src').strip()
                tgt = match.group('tgt').strip()
                if src in entity_names and tgt in entity_names:
                    edges.append({'source': src, 'target': tgt, 'type': rel_type, 'doc_id': doc_id})
        return edges

    def _merge_entities(self, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """合并同名的实体。"""
        merged: dict[str, dict[str, Any]] = {}
        for e in entities:
            name = e['name']
            if name in merged:
                if e.get('type') != 'concept':
                    merged[name]['type'] = e['type']
            else:
                merged[name] = dict(e)
        return list(merged.values())
