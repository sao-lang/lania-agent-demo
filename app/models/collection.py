"""集合模型模块。

负责定义知识库集合创建与列表展示相关的数据模型，用于集合 API、服务层和前端之间共享
统一的输入输出结构。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CollectionCreateRequest(BaseModel):
    """创建知识库集合的请求体。

    用于声明集合名称、描述，以及默认的 embedding 和分块参数。它是集合生命周期的入口模型，
    会影响后续文档导入和检索索引的基础策略。

    字段分为两组：
    - 业务标识：集合名称与描述，决定集合的对外可见身份。
    - 索引默认值：embedding 模型、chunk 大小与重叠量，作为导入阶段的默认切块配置。
    """

    # 集合的业务标识信息。
    name: str
    description: str | None = None

    # 集合级默认索引参数；文档导入时若未单独指定，将沿用这里的配置。
    embedding_model: str = 'text-embedding-3-small'
    chunk_size: int = Field(default=800, ge=100)
    chunk_overlap: int = Field(default=100, ge=0)


class CollectionSummary(BaseModel):
    """知识库集合的摘要信息。

    用于列表展示和集合详情概览场景，聚焦集合状态、索引配置和文档规模这些高频查看字段。
    它不承载导入明细，而是作为集合管理页最常用的“概览面”。
    """

    # 集合基础身份字段。
    id: str
    name: str
    description: str | None = None
    status: str = 'created'

    # 当前集合绑定的索引默认参数。
    embedding_model: str
    chunk_size: int
    chunk_overlap: int

    # 面向管理页展示的规模与时间信息。
    document_count: int = 0
    indexed_chunks: int = 0
    created_at: datetime
    updated_at: datetime | None = None
