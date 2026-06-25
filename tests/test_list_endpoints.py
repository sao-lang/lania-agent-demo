"""验证列表型接口能够从 SQLite 恢复状态并支持筛选分页。"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.sqlite_store import SQLiteStateStore


class ListEndpointsTests(unittest.TestCase):
    """覆盖文档、会话、反馈和评测候选列表接口。"""

    def setUp(self) -> None:
        """预写入一组有时间先后关系的状态数据，供列表接口读取。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()))
        self.store = SQLiteStateStore(self.settings)
        now = datetime.now(timezone.utc)
        earlier = now - timedelta(minutes=5)
        self.store.upsert_collection(
            {
                'id': 'col-alpha',
                'name': 'alpha',
                'description': 'alpha collection',
                'status': 'created',
                'embedding_model': 'text-embedding-3-small',
                'chunk_size': 800,
                'chunk_overlap': 100,
                'created_at': earlier,
                'updated_at': earlier,
            }
        )
        self.store.upsert_collection(
            {
                'id': 'col-beta',
                'name': 'beta',
                'description': 'beta collection',
                'status': 'created',
                'embedding_model': 'text-embedding-3-small',
                'chunk_size': 800,
                'chunk_overlap': 100,
                'created_at': now,
                'updated_at': now,
            }
        )

        self.store.upsert_document(
            {
                'doc_id': 'doc-1',
                'file_name': 'a.md',
                'file_path': '/tmp/a.md',
                'file_type': 'md',
                'collection_name': 'alpha',
                'tags': ['x'],
                'checksum': 'aaa',
                'status': 'indexed',
                'indexed_chunks': 3,
                'created_at': earlier,
                'updated_at': earlier,
                'indexed_at': earlier,
            }
        )
        self.store.upsert_document(
            {
                'doc_id': 'doc-2',
                'file_name': 'b.md',
                'file_path': '/tmp/b.md',
                'file_type': 'md',
                'collection_name': 'beta',
                'tags': ['y'],
                'checksum': 'bbb',
                'status': 'uploaded',
                'indexed_chunks': 0,
                'created_at': now,
                'updated_at': now,
                'indexed_at': None,
            }
        )
        self.store.upsert_session(
            'session-new',
            {
                'messages': [{'role': 'user', 'content': 'new', 'created_at': now}],
                'summary': 'new summary',
                'summary_updated_at': now,
                'compressed_message_count': 0,
                'updated_at': now,
            },
        )
        self.store.upsert_session(
            'session-old',
            {
                'messages': [{'role': 'user', 'content': 'old', 'created_at': earlier}],
                'summary': 'old summary',
                'summary_updated_at': earlier,
                'compressed_message_count': 2,
                'updated_at': earlier,
            },
        )
        self.store.upsert_feedback_item(
            {
                'feedback_id': 'fb-new',
                'feedback_type': 'upvote',
                'collection_name': 'beta',
                'question': 'q-new',
                'answer': 'a-new',
                'session_id': 'session-new',
                'correction': None,
                'note': 'new note',
                'citations': [],
                'metadata': {'source': 'test'},
                'eval_candidate_created': True,
                'created_at': now,
            }
        )
        self.store.upsert_feedback_item(
            {
                'feedback_id': 'fb-old',
                'feedback_type': 'correction',
                'collection_name': 'alpha',
                'question': 'q-old',
                'answer': 'a-old',
                'session_id': 'session-old',
                'correction': 'fixed',
                'note': 'old note',
                'citations': [],
                'metadata': {'source': 'test'},
                'eval_candidate_created': False,
                'created_at': earlier,
            }
        )
        self.store.upsert_eval_candidate(
            {
                'candidate_id': 'cand-new',
                'feedback_id': 'fb-new',
                'collection_name': 'beta',
                'question': 'q-new',
                'reference': 'ref-new',
                'answer': 'a-new',
                'feedback_type': 'upvote',
                'citations': [],
                'note': 'new note',
                'created_at': now,
            }
        )
        self.store.upsert_eval_candidate(
            {
                'candidate_id': 'cand-old',
                'feedback_id': 'fb-old',
                'collection_name': 'alpha',
                'question': 'q-old',
                'reference': 'ref-old',
                'answer': 'a-old',
                'feedback_type': 'correction',
                'citations': [],
                'note': 'old note',
                'created_at': earlier,
            }
        )

    def _create_client(self) -> TestClient:
        """创建绑定临时 SQLite 数据目录的测试客户端。"""
        with patch('app.main.get_settings', return_value=self.settings):
            app = create_app()
        return TestClient(app)

    def test_document_list_endpoint_reads_restored_sqlite_state(self) -> None:
        """验证文档列表接口会按更新时间倒序读取恢复后的数据。"""
        client = self._create_client()

        response = client.get('/api/v1/documents')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item['doc_id'] for item in body], ['doc-2', 'doc-1'])
        self.assertEqual(body[0]['collection_name'], 'beta')
        self.assertIn('updated_at', body[0])

    def test_document_list_endpoint_supports_collection_filter(self) -> None:
        """验证文档列表接口支持按集合名称过滤结果。"""
        client = self._create_client()

        response = client.get('/api/v1/documents', params={'collection_name': 'alpha'})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['doc_id'], 'doc-1')

    def test_session_list_endpoint_reads_restored_sqlite_state(self) -> None:
        """验证会话列表接口会返回恢复后的摘要和消息统计信息。"""
        client = self._create_client()

        response = client.get('/api/v1/sessions')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([item['session_id'] for item in body], ['session-new', 'session-old'])
        self.assertEqual(body[0]['message_count'], 1)
        self.assertEqual(body[1]['compressed_message_count'], 2)

    def test_collection_documents_endpoint_reads_filtered_documents(self) -> None:
        """验证集合下文档列表接口只返回目标集合内的文档。"""
        client = self._create_client()

        response = client.get('/api/v1/collections/alpha/documents')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]['doc_id'], 'doc-1')
        self.assertEqual(body[0]['collection_name'], 'alpha')

    def test_collection_documents_endpoint_returns_not_found_for_missing_collection(self) -> None:
        """验证访问不存在的集合时会返回标准未找到错误。"""
        client = self._create_client()

        response = client.get('/api/v1/collections/missing/documents')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error']['code'], 'collection_not_found')

    def test_feedback_list_endpoint_supports_filters_and_pagination(self) -> None:
        """验证反馈列表接口支持过滤条件和分页参数。"""
        client = self._create_client()

        response = client.get(
            '/api/v1/feedback',
            params={
                'collection_name': 'beta',
                'feedback_type': 'upvote',
                'eval_candidate_created': 'true',
                'limit': 1,
                'offset': 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['limit'], 1)
        self.assertEqual(body['offset'], 0)
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['items'][0]['feedback_id'], 'fb-new')

    def test_eval_candidate_list_endpoint_supports_filters_and_pagination(self) -> None:
        """验证评测候选列表接口支持按集合过滤并返回分页结果。"""
        client = self._create_client()

        response = client.get(
            '/api/v1/feedback/eval-candidates',
            params={
                'collection_name': 'alpha',
                'feedback_type': 'correction',
                'limit': 1,
                'offset': 0,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['total'], 1)
        self.assertEqual(body['limit'], 1)
        self.assertEqual(body['offset'], 0)
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['items'][0]['candidate_id'], 'cand-old')


if __name__ == '__main__':
    unittest.main()
