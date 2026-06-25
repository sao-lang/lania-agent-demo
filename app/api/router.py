"""API 总路由模块。

负责把不同业务域的子路由聚合到统一入口，供 `app.main` 在启动阶段一次性挂载。
该模块只描述路由装配关系与业务域边界，不承载具体请求处理逻辑。
"""

from fastapi import APIRouter

from app.api.v1.endpoints.api_contract import router as api_contract_router
from app.api.v1.endpoints.artifacts import router as artifacts_router
from app.api.v1.endpoints.collections import router as collections_router
from app.api.v1.endpoints.database import router as database_router
from app.api.v1.endpoints.documents import router as documents_router
from app.api.v1.endpoints.eval import router as eval_router
from app.api.v1.endpoints.feedback import router as feedback_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.knowledge import router as knowledge_router
from app.api.v1.endpoints.query import router as query_router
from app.api.v1.endpoints.repository import router as repository_router
from app.api.v1.endpoints.sandbox import router as sandbox_router
from app.api.v1.endpoints.sessions import router as sessions_router
from app.api.v1.endpoints.tasks import router as tasks_router

api_router = APIRouter()
# 所有对外接口统一在这里完成分组装配，便于追踪 API 暴露面、前缀和标签组织方式。
api_router.include_router(health_router, tags=['health'])
api_router.include_router(api_contract_router, prefix='/api-contract', tags=['api-contract'])
api_router.include_router(artifacts_router, prefix='/artifacts', tags=['artifacts'])
api_router.include_router(database_router, prefix='/database', tags=['database'])
api_router.include_router(collections_router, prefix='/collections', tags=['collections'])
api_router.include_router(documents_router, prefix='/documents', tags=['documents'])
api_router.include_router(knowledge_router, prefix='/knowledge', tags=['knowledge'])
api_router.include_router(repository_router, prefix='/repository', tags=['repository'])
api_router.include_router(sandbox_router, prefix='/sandbox', tags=['sandbox'])
api_router.include_router(query_router, tags=['query'])
api_router.include_router(sessions_router, tags=['sessions'])
api_router.include_router(tasks_router, tags=['tasks'])
api_router.include_router(feedback_router, tags=['feedback'])
api_router.include_router(eval_router, prefix='/eval', tags=['evaluation'])
