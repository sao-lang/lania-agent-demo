"""文档模型模块。

负责定义文档列表、上传结果、导入失败项、目录扫描和重建索引等接口使用的数据模型，
用于规范文档生命周期相关 API 的输入输出结构。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentItem(BaseModel):
    """单个文档的元数据与索引状态。

    文档列表、上传结果、集合详情里都会用到这个模型。
    该模型同时承载文件来源、索引状态、归档来源和权限推断结果，是文档管理链路里最核心
    的展示数据结构之一。
    """

    # 文档身份与来源信息。
    doc_id: str
    file_name: str
    file_path: str
    file_type: str
    collection_name: str
    tags: list[str] = []
    checksum: str | None = None

    # 导入、索引和可观测状态字段。
    status: str = 'uploaded'
    indexed_chunks: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    indexed_at: datetime | None = None

    # 权限与压缩包来源元数据，便于回溯原始输入来源。
    permission: str | None = None
    source_archive: str | None = None
    archive_member_path: str | None = None
    archive_member_display_path: str | None = None


class ImportFailureItem(BaseModel):
    """导入失败时返回的结构化错误项。

    用来告诉调用方是哪个文件出了问题、大概卡在哪一步。
    该模型强调“可定位性”，便于前端逐项展示失败原因，也方便批量导入后的排障回放。
    """

    file_name: str
    reason: str
    code: str
    stage: str
    file_type: str | None = None


class ImportStats(BaseModel):
    """导入任务的聚合统计信息。

    方便前端直接展示这次导入一共成功了多少、失败了多少、跳过了多少。
    它只表达数量级统计，不包含任何单文件明细。
    """

    input_files: int = 0
    imported_documents: int = 0
    failed_files: int = 0
    skipped_files: int = 0


class DocumentUploadResponse(BaseModel):
    """文档上传接口的响应体。

    一次把成功项、失败项和整体统计都带回来，前端展示起来会更省事。
    调用方通常会先读取 `stats` 做总览，再按 `uploaded` 与 `failed` 展开细节。
    """

    uploaded: list[DocumentItem]
    failed: list[ImportFailureItem]
    stats: ImportStats


class ScanRequest(BaseModel):
    """目录扫描导入请求体。

    用来描述要扫哪个目录、导进哪个集合，以及要不要递归往下扫。
    该请求更偏批量导入入口，因此额外允许用 `file_types` 对扫描结果做第一层过滤。
    """

    directory: str
    collection_name: str
    recursive: bool = True
    file_types: list[str] = Field(default_factory=list)


class ReindexRequest(BaseModel):
    """重建索引请求体。

    可以按集合整批重建，也可以只挑一部分文档重建。
    当 `doc_ids` 为空时，一般表示按集合范围全量重建。
    """

    collection_name: str | None = None
    doc_ids: list[str] = Field(default_factory=list)
