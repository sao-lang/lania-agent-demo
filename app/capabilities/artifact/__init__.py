"""Artifact 能力导出模块。

统一导出 artifact 查询契约、provider 注册器与本地默认实现，供任务结果读取、
状态编排与工具层访问时复用同一组能力定义。
"""


from app.capabilities.artifact.base import ArtifactCapability, ArtifactListRequest, ArtifactListResult, ArtifactSummaryItem
from app.capabilities.artifact.factory import (
    ArtifactCapabilityProvider,
    ArtifactCapabilityRegistry,
    DefaultArtifactCapabilityProvider,
    build_artifact_capability_from_provider,
    build_default_artifact_capability_registry,
)
from app.capabilities.artifact.service import LocalArtifactCapability

__all__ = [
    'ArtifactCapability',
    'ArtifactCapabilityProvider',
    'ArtifactCapabilityRegistry',
    'ArtifactListRequest',
    'ArtifactListResult',
    'ArtifactSummaryItem',
    'DefaultArtifactCapabilityProvider',
    'LocalArtifactCapability',
    'build_artifact_capability_from_provider',
    'build_default_artifact_capability_registry',
]
