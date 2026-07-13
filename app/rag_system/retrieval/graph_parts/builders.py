"""RAG 系统图谱构建器子模块。

负责图谱 citation 回填、节点合并/去重、边构建、权限过滤判断。
与主应用的 `app/services/graph_service_parts/builders.py` 功能一致。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.rag_system.retrieval.graph_parts._typing import RagGraphServiceTypingMixin


class RagGraphBuilderMixin(RagGraphServiceTypingMixin):
    """图谱构建：节点合并、边构建、权限过滤。"""

    def _build_or_merge_node(
        self,
        node_updates: dict[str, dict[str, Any]],
        collection_name: str,
        doc_id: str,
        entity: dict[str, Any],
        chunk_id: str,
        now: str,
    ) -> dict[str, Any]:
        """构建或合并图谱节点。"""
        node_id = f'entity:{collection_name}:{hash(entity.get("name", "")) % 10**10}'
        if node_id in node_updates:
            record = node_updates[node_id]
            if chunk_id not in record.get('chunk_ids', []):
                record.setdefault('chunk_ids', []).append(chunk_id)
            return record

        record: dict[str, Any] = {
            'node_id': node_id,
            'label': entity.get('name', ''),
            'type': entity.get('type', 'concept'),
            'collection_name': collection_name,
            'chunk_ids': [chunk_id],
            'metadata': {'doc_id': doc_id, 'source': entity.get('source', 'rule')},
            'created_at': now,
        }
        node_updates[node_id] = record
        return record

    def _build_edge(
        self,
        source_node_id: str,
        target_node_id: str,
        relation: str,
        collection_name: str,
        doc_id: str,
        now: str,
    ) -> dict[str, Any]:
        """构建一条图谱边。"""
        edge_id = f'edge:{source_node_id}:{target_node_id}:{relation}'
        return {
            'edge_id': edge_id,
            'source_node_id': source_node_id,
            'target_node_id': target_node_id,
            'relation': relation,
            'collection_name': collection_name,
            'weight': 1.0,
            'metadata': {'doc_id': doc_id},
            'created_at': now,
        }

    def _check_graph_permission(self, collection_name: str, permission_scope: str | None) -> bool:
        """检查图谱数据访问权限。"""
        if permission_scope is None:
            return True
        # 简化实现：仅检查集合是否存在
        return collection_name in self.state.collections
