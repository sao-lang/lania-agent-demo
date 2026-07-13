"""API 总路由模块。

负责把不同业务域的子路由聚合到统一入口，供 `app.main` 在启动阶段一次性挂载。
该模块只描述路由装配关系与业务域边界，不承载具体请求处理逻辑。
"""

from fastapi import APIRouter

from app.agent_platform.api.v1.endpoints.api_contract import router as api_contract_router
from app.agent_platform.api.v1.endpoints.artifacts import router as artifacts_router
from app.agent_platform.api.v1.endpoints.database import router as database_router
from app.agent_platform.api.v1.endpoints.health import router as health_router
from app.agent_platform.api.v1.endpoints.repository import router as repository_router
from app.agent_platform.api.v1.endpoints.sandbox import router as sandbox_router
from app.agent_platform.api.v1.endpoints.sessions import router as sessions_router
from app.agent_platform.api.v1.endpoints.agent import router as agent_router
from app.agent_platform.api.v1.endpoints.auth import router as auth_router
from app.agent_platform.api.v1.endpoints.capabilities import router as capabilities_router
from app.agent_platform.api.v1.endpoints.admin_llm import router as admin_llm_router
from app.agent_platform.api.v1.endpoints.admin_skills import router as admin_skills_router
from app.agent_platform.api.v1.endpoints.admin_agents import router as admin_agents_router
from app.agent_platform.api.v1.endpoints.admin_prompts import router as admin_prompts_router
from app.agent_platform.api.v1.endpoints.admin_mcp import router as admin_mcp_router
from app.agent_platform.api.v1.endpoints.admin_settings import router as admin_settings_router
from app.agent_platform.api.v1.endpoints.admin_instructions import router as admin_instructions_router
from app.agent_platform.api.v1.endpoints.admin_file_instructions import router as admin_file_instructions_router
from app.agent_platform.api.v1.endpoints.admin_hooks import router as admin_hooks_router

api_router = APIRouter()
api_router.include_router(health_router, tags=['health'])
api_router.include_router(api_contract_router, prefix='/api-contract', tags=['api-contract'])
api_router.include_router(artifacts_router, prefix='/artifacts', tags=['artifacts'])
api_router.include_router(database_router, prefix='/database', tags=['database'])
api_router.include_router(repository_router, prefix='/repository', tags=['repository'])
api_router.include_router(sandbox_router, prefix='/sandbox', tags=['sandbox'])
api_router.include_router(sessions_router, tags=['sessions'])
api_router.include_router(agent_router, tags=['agent'])
api_router.include_router(capabilities_router, tags=['capabilities'])
api_router.include_router(auth_router, tags=['auth'])
api_router.include_router(admin_llm_router, tags=['admin'])
api_router.include_router(admin_skills_router, tags=['admin'])
api_router.include_router(admin_agents_router, tags=['admin'])
api_router.include_router(admin_prompts_router, tags=['admin'])
api_router.include_router(admin_mcp_router, tags=['admin'])
api_router.include_router(admin_settings_router, tags=['admin'])
api_router.include_router(admin_instructions_router, tags=['admin'])
api_router.include_router(admin_file_instructions_router, tags=['admin'])
api_router.include_router(admin_hooks_router, tags=['admin'])
