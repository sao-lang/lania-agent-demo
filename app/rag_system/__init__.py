"""RAG 独立系统。

该模块将 RAG 相关代码从主应用中抽离为独立子系统，切断与 ``InMemoryState``、
``SQLiteStateStore``、``EventBus``、``TaskMemory`` 等基础设施的共享依赖，
使其具备独立启动和部署的能力，同时可作为主应用的依赖包提供服务。

主要组件：
- ``RagContainer``: DI 容器，统一管理 RAG 组件的生命周期
- ``RagFacade``: 统一入口门面，供外部通过方法调用或 RAG 工具使用
- ``RagQueryEngine``: 查询引擎，支持单轮/多轮/流式
- ``RagRetrievalService``: 检索服务（稠密/词法/GraphRAG）
- ``RagIngestionService``: 文档摄取服务
- ``KnowledgeCapability``: 知识能力统一接口
"""

from __future__ import annotations

from app.rag_system.container import RagContainer
from app.rag_system.config.settings import RagSettings
from app.rag_system.query.facade import RagFacade
from app.rag_system.query.engine import RagQueryEngine
from app.rag_system.retrieval.service import RagRetrievalService
from app.rag_system.ingestion.service import RagIngestionService
from app.rag_system.store.state import RagState
from app.rag_system.store.persistence import RagPersistence

__all__ = [
    'RagContainer',
    'RagSettings',
    'RagFacade',
    'RagQueryEngine',
    'RagRetrievalService',
    'RagIngestionService',
    'RagState',
    'RagPersistence',
]
