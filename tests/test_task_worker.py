"""验证持久化任务队列会被后台任务工作线程消费。"""

import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.agents.memory import TaskMemory
from app.agents.planner import TaskPlanner
from app.agents.runtime import AgentRuntime
from app.agents.tools.analysis_tools import ExtractKeyPointsTool, ExtractRisksTool
from app.agents.tools.artifact_tools import DraftReportTool, FinalizeReportTool, ReviewReportTool
from app.agents.tools.defaults import build_runtime_rag_tools
from app.agents.tools.registry import ToolRegistry
from app.core.config import Settings
from app.models.query import CitationItem
from app.models.task import TaskRequest
from app.rag.observability import TraceRecorder
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState
from app.services.task_dispatcher import PersistentTaskDispatcher, TaskWorker
from app.services.task_service import TaskService
from app.workflows.tasks.task_orchestrator import TaskWorkflowOrchestrator


class FakeCollection:
    """模拟向量集合元数据读取接口。"""

    def get(self, ids, include):
        return {'metadatas': [{'section_title': '架构设计'}, {'section_title': '风险控制'}]}


class FakeVectorStore:
    """返回固定集合桩对象的向量库。"""

    def get_or_create_collection(self, collection_name: str):
        return FakeCollection()


class FakeRetrievalService:
    """返回固定证据引用，供任务工作流生成产物。"""

    def retrieve(self, collection_name, question, top_k, **kwargs):
        return [
            CitationItem(
                chunk_id='chunk-1',
                source='design.md',
                text='系统核心模块包括调度、检索和缓存，接口依赖集中在任务服务与工作流之间。',
                score=0.92,
                section_title='架构设计',
                index_kind='hybrid',
                context_scope='chunk',
            ),
            CitationItem(
                chunk_id='chunk-2',
                source='design.md',
                text='当前风险包括异常处理缺失、容量评估不足以及失败重试策略仍需补充。',
                score=0.88,
                section_title='风险控制',
                index_kind='hybrid',
                context_scope='chunk',
            ),
        ][:top_k]


class TaskWorkerTests(unittest.TestCase):
    """覆盖持久化任务从排队到完成的后台执行路径。"""

    def setUp(self) -> None:
        """初始化 SQLite 持久化状态，并预置集合与文档。"""
        self.settings = Settings(DATA_DIR=Path(tempfile.mkdtemp()), ENABLE_EMBEDDED_TASK_WORKER=False)
        self.persistence = SQLiteStateStore(self.settings)
        now = datetime.now(timezone.utc)
        self.persistence.upsert_collection(
            {
                'id': 'col-demo',
                'name': 'demo',
                'description': 'demo collection',
                'status': 'created',
                'embedding_model': 'text-embedding-3-small',
                'chunk_size': 800,
                'chunk_overlap': 100,
                'created_at': now,
                'updated_at': now,
            }
        )
        self.persistence.upsert_document(
            {
                'doc_id': 'doc-1',
                'file_name': 'design.md',
                'file_path': '/tmp/design.md',
                'file_type': 'md',
                'collection_name': 'demo',
                'tags': [],
                'checksum': 'x',
                'status': 'indexed',
                'chunk_ids': ['chunk-1', 'chunk-2'],
                'indexed_chunks': 2,
                'created_at': now,
                'updated_at': now,
                'indexed_at': now,
                'document_title': '系统设计文档',
                'document_summary': '文档介绍了系统模块、接口依赖和风险控制。',
                'document_hierarchy': 'demo / 系统设计文档',
            }
        )

    def _build_registry(self) -> ToolRegistry:
        """构造与生产默认一致的工具注册表。"""
        registry = ToolRegistry()
        for tool in [
            *build_runtime_rag_tools(),
            ExtractKeyPointsTool(),
            ExtractRisksTool(),
            DraftReportTool(),
            ReviewReportTool(),
            FinalizeReportTool(),
        ]:
            registry.register(tool)
        return registry

    def test_worker_processes_persisted_queued_task(self) -> None:
        """验证后台 worker 会消费持久化队列中的任务并写回最终产物。"""
        api_state = InMemoryState()
        self.persistence.load_into(api_state)
        api_memory = TaskMemory(api_state, self.persistence)
        trace = TraceRecorder()
        service = TaskService(
            runtime=AgentRuntime(
                TaskWorkflowOrchestrator(
                    TaskPlanner(),
                    self._build_registry(),
                    api_memory,
                    trace,
                    self.settings,
                    api_state,
                    FakeRetrievalService(),
                    FakeVectorStore(),
                    None,
                ),
                api_memory,
                trace,
            ),
            memory=api_memory,
            state=api_state,
            dispatcher=PersistentTaskDispatcher(),
            registry=self._build_registry(),
        )

        worker_state = InMemoryState()
        self.persistence.load_into(worker_state)
        worker_memory = TaskMemory(worker_state, self.persistence)
        worker_trace = TraceRecorder()
        worker_runtime = AgentRuntime(
            TaskWorkflowOrchestrator(
                TaskPlanner(),
                self._build_registry(),
                worker_memory,
                worker_trace,
                self.settings,
                worker_state,
                FakeRetrievalService(),
                FakeVectorStore(),
                None,
            ),
            worker_memory,
            worker_trace,
        )
        worker = TaskWorker(worker_memory, worker_runtime, poll_interval_seconds=0.05, lease_seconds=30, max_workers=1)
        worker.start_background()
        try:
            created = service.create_document_analysis(
                TaskRequest(collection_name='demo', doc_ids=['doc-1'], instructions='总结核心模块和风险点')
            )
            self.assertEqual(created.status, 'queued')

            deadline = time.time() + 5
            latest = created
            while time.time() < deadline:
                latest = service.get_task(created.task_id)
                if latest.status == 'completed':
                    break
                time.sleep(0.1)

            self.assertEqual(latest.status, 'completed')
            self.assertIsNotNone(latest.final_artifact_id)
            self.assertGreaterEqual(len(service.list_artifacts(created.task_id)), 2)
        finally:
            worker.shutdown()


if __name__ == '__main__':
    unittest.main()
