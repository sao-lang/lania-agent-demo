"""Knowledge 能力导出模块。

集中导出知识检索、grounded answer、provider 注册器以及默认本地与远程实现，
让上层调用方通过稳定入口接入不同知识能力提供方式。
"""


from app.capabilities.knowledge.base import (
    DocumentContextCall,
    DocumentContextItem,
    DocumentContextRequest,
    DocumentContextResult,
    GroundedAnswerCall,
    GroundedAnswerRequest,
    GroundedAnswerResult,
    KnowledgeCapability,
    KnowledgeSearchCall,
    KnowledgeSearchRequest,
)
from app.capabilities.knowledge.contracts import GroundedAnswerStrategy, RetrievalQualityReport
from app.capabilities.knowledge.factory import (
    DefaultKnowledgeCapabilityProvider,
    KnowledgeCapabilityProvider,
    KnowledgeCapabilityRegistry,
    build_default_knowledge_capability_registry,
    build_knowledge_capability,
)
from app.capabilities.knowledge.service import DefaultKnowledgeCapability
from app.capabilities.knowledge.remote import RemoteKnowledgeCapability, RemoteKnowledgeProviderError

__all__ = [
    'DefaultKnowledgeCapability',
    'DefaultKnowledgeCapabilityProvider',
    'DocumentContextCall',
    'DocumentContextItem',
    'DocumentContextRequest',
    'DocumentContextResult',
    'GroundedAnswerCall',
    'GroundedAnswerRequest',
    'GroundedAnswerResult',
    'GroundedAnswerStrategy',
    'KnowledgeCapability',
    'KnowledgeCapabilityProvider',
    'KnowledgeCapabilityRegistry',
    'KnowledgeSearchCall',
    'KnowledgeSearchRequest',
    'RemoteKnowledgeCapability',
    'RetrievalQualityReport',
    'RemoteKnowledgeProviderError',
    'build_default_knowledge_capability_registry',
    'build_knowledge_capability',
]
