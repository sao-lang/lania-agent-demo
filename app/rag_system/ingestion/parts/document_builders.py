"""RAG 系统文档构建器子模块。

负责构建 LlamaIndex Document、parent_chunk 元数据、chunk pipeline。
与主应用的 `app/rag/ingestion_parts/document_builders.py` 功能一致。
"""

from __future__ import annotations

from typing import Any

from app.rag_system.ingestion.parts._typing import RagIngestionTypingMixin


class RagIngestionDocumentBuilderMixin(RagIngestionTypingMixin):
    """构建可索引的文档分块，支持 parent_chunk 元数据。"""

    def _build_chunk_records(
        self,
        segments: list[dict[str, Any]],
        doc_id: str,
        file_name: str,
        file_type: str,
        extra_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """构建带嵌入和元数据的向量分块记录。"""
        chunks: list[dict[str, Any]] = []
        for seg in segments:
            chunk_id = f'{doc_id}-chunk-{seg["seq"]:04d}'
            meta = {
                'doc_id': doc_id,
                'file_name': file_name,
                'file_type': file_type,
                'seq': seg['seq'],
                'chunk_seq': seg['seq'],
                **(extra_metadata or {}),
            }
            if seg.get('page_number'):
                meta['page'] = seg['page_number']
            if seg.get('block_type'):
                meta['block_type'] = seg['block_type']
            if seg.get('section_title'):
                meta['section_title'] = seg['section_title']
            if seg.get('hierarchy_path'):
                meta['hierarchy_path'] = seg['hierarchy_path']

            try:
                embedding = self.embed_model.get_text_embedding(seg['text'])
            except Exception:
                embedding = None

            chunks.append({
                'id': chunk_id,
                'text': seg['text'],
                'embedding': embedding,
                'metadata': meta,
            })
        return chunks

    def _build_parent_chunk_metadata(self, chunks: list[dict[str, Any]], parent_size: int = 4) -> list[dict[str, Any]]:
        """为分块添加 parent_chunk_id 元数据，支持父块回填。"""
        if parent_size <= 1:
            return chunks
        for i, chunk in enumerate(chunks):
            parent_idx = (i // parent_size) * parent_size
            if parent_idx != i and parent_idx < len(chunks):
                chunk['metadata']['parent_chunk_id'] = chunks[parent_idx]['id']
        return chunks
