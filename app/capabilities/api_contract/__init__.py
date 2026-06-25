"""API Contract 能力导出模块。

统一收拢 API 契约检索、读取、provider 注册与默认实现，避免上层调用方
分别感知 base、factory 与 service 的内部拆分。
"""


from app.capabilities.api_contract.base import (
    ApiContractCapability,
    ApiContractDocument,
    ApiContractListRequest,
    ApiContractListResult,
    ApiContractOperation,
    ApiContractOperationMatch,
    ApiContractReadRequest,
    ApiContractReadResult,
    ApiContractSearchOperationsRequest,
    ApiContractSearchOperationsResult,
)
from app.capabilities.api_contract.factory import (
    ApiContractCapabilityProvider,
    ApiContractCapabilityRegistry,
    DefaultApiContractCapabilityProvider,
    build_api_contract_capability_from_provider,
    build_default_api_contract_capability_registry,
)
from app.capabilities.api_contract.service import LocalApiContractCapability, build_api_contract_capability

__all__ = [
    'ApiContractCapability',
    'ApiContractCapabilityProvider',
    'ApiContractCapabilityRegistry',
    'ApiContractDocument',
    'ApiContractListRequest',
    'ApiContractListResult',
    'ApiContractOperation',
    'ApiContractOperationMatch',
    'ApiContractReadRequest',
    'ApiContractReadResult',
    'ApiContractSearchOperationsRequest',
    'ApiContractSearchOperationsResult',
    'DefaultApiContractCapabilityProvider',
    'LocalApiContractCapability',
    'build_api_contract_capability',
    'build_api_contract_capability_from_provider',
    'build_default_api_contract_capability_registry',
]
