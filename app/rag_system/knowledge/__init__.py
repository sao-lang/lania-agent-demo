"""RAG 系统 Knowledge 能力模块。"""

from app.rag_system.knowledge.base import (
    DocumentContextItem,
    DocumentContextRequest,
    DocumentContextResult,
    EvidencePack,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeCapability,
    KnowledgeSearchRequest,
)
from app.rag_system.knowledge.contracts import (
    GroundedAnswerStrategy,
    RetrievalQualityReport,
)
from app.rag_system.knowledge.factory import (
    build_knowledge_capability,
    DefaultKnowledgeCapabilityProvider,
    KnowledgeCapabilityProvider,
    KnowledgeCapabilityRegistry,
    RemoteHttpKnowledgeCapabilityProvider,
)
from app.rag_system.knowledge.remote import RemoteKnowledgeCapability, RemoteKnowledgeProviderError
from app.rag_system.knowledge.service import RagKnowledgeCapability

__all__ = [
    'DocumentContextItem', 'DocumentContextRequest', 'DocumentContextResult',
    'EvidencePack', 'GroundedAnswerRequest', 'GroundedAnswerResult',
    'KnowledgeCapability', 'KnowledgeSearchRequest',
    'GroundedAnswerStrategy', 'RetrievalQualityReport',
    'build_knowledge_capability', 'DefaultKnowledgeCapabilityProvider',
    'KnowledgeCapabilityProvider', 'KnowledgeCapabilityRegistry',
    'RemoteHttpKnowledgeCapabilityProvider',
    'RemoteKnowledgeCapability', 'RemoteKnowledgeProviderError',
    'RagKnowledgeCapability',
]

