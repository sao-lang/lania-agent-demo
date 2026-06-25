"""API 依赖注入模块。

负责给端点函数提供统一、可复用的依赖访问入口，避免每个路由都直接操作 `request.app.state`。
当前这里主要暴露应用级 `AppContainer` 的读取方法，作为 API 层进入服务装配体系的标准入口。
"""

from fastapi import Request

from app.container import AppContainer


def get_container(request: Request) -> AppContainer:
    """从应用状态里取全局容器实例。

    Args:
        request: 当前 FastAPI 请求对象，用来访问 `request.app.state`。

    Returns:
        应用启动阶段挂到 `app.state` 上的全局依赖容器。
    """

    return request.app.state.container
