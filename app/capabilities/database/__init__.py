"""数据库能力导出模块。

统一收拢数据库只读查询能力的契约、provider 注册器与默认 SQLite 实现，
供工具层和 workflow 在不感知底层细节的情况下访问数据库。
"""


from app.capabilities.database.base import (
    DatabaseCapability,
    DatabaseColumnInfo,
    DatabaseDescribeTableRequest,
    DatabaseDescribeTableResult,
    DatabaseListTablesRequest,
    DatabaseListTablesResult,
    DatabaseQueryRequest,
    DatabaseQueryResult,
    DatabaseTableItem,
)
from app.capabilities.database.factory import (
    DatabaseCapabilityProvider,
    DatabaseCapabilityRegistry,
    LocalSQLiteDatabaseCapabilityProvider,
    build_database_capability_from_provider,
    build_default_database_capability_registry,
)
from app.capabilities.database.service import LocalSQLiteDatabaseCapability, build_database_capability

__all__ = [
    'DatabaseCapability',
    'DatabaseCapabilityProvider',
    'DatabaseCapabilityRegistry',
    'DatabaseColumnInfo',
    'DatabaseDescribeTableRequest',
    'DatabaseDescribeTableResult',
    'DatabaseListTablesRequest',
    'DatabaseListTablesResult',
    'DatabaseQueryRequest',
    'DatabaseQueryResult',
    'DatabaseTableItem',
    'LocalSQLiteDatabaseCapability',
    'LocalSQLiteDatabaseCapabilityProvider',
    'build_database_capability',
    'build_database_capability_from_provider',
    'build_default_database_capability_registry',
]
