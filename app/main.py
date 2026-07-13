"""应用入口模块。

负责创建并配置 FastAPI 实例，并在启动阶段串联配置加载、日志初始化、依赖容器装配、
异常处理注册和 API 路由挂载。对外部调用方而言，本文件就是整个 Web 服务的组装入口。
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.container import build_container
from app.core.config import get_settings
from app.core.auth import AuthMiddleware
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    """创建并返回 FastAPI 应用实例。

    该函数集中完成应用启动阶段的基础装配，确保配置、日志、依赖容器和路由在同一处
    初始化，方便测试环境和生产环境复用一致的启动逻辑。
    对外来看，它定义了整个 Web 服务的装配边界和生命周期管理入口。

    Returns:
        完成基础配置和路由注册后的 FastAPI 应用实例。
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings, start_worker=settings.enable_embedded_task_worker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """管理应用生命周期内共享资源的注册与释放。

        Args:
            app: 当前运行中的 FastAPI 应用实例。

        Yields:
            把控制权交还给 FastAPI，允许其继续处理启动后的请求生命周期。
        """

        # 在 lifespan 中再次挂载容器，确保测试场景和运行时都能从 app.state 读取依赖。
        app.state.container = container
        # 初始化定制化原语引擎（扫描 .lania/ 目录，同步 Skills/Agents/Prompts/MCPs）
        if hasattr(container, 'customization_engine') and container.customization_engine:
            await container.customization_engine.initialize()
        try:
            yield
        finally:
            # 容器可能托管后台 worker、调度器等资源，关闭时统一释放。
            if hasattr(container, 'shutdown'):
                container.shutdown()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Personal RAG App built with FastAPI, LlamaIndex and ChromaDB.",
        lifespan=lifespan,
    )
    app.state.container = container
    register_exception_handlers(app)

    # 注册认证中间件（开发环境可通过配置关闭）
    if settings.enable_auth:
        app.add_middleware(AuthMiddleware)

    @app.get("/", tags=["root"])
    async def root() -> dict[str, str]:
        """返回服务基础状态信息。

        Returns:
            包含应用名称、服务状态和文档入口地址的简单字典。
        """

        return {
            "name": settings.app_name,
            "status": "ok",
            "docs": "/docs",
        }

    # 所有版本化 API 均通过统一前缀挂载，避免入口文件感知各个子路由细节。
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
