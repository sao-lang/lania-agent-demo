"""任务 worker 启动模块。

负责以独立进程方式启动 Document Analysis task worker。这个文件是任务系统的进程入口，
主要做配置加载、日志初始化和 worker 生命周期托管。
"""

from __future__ import annotations

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging


def main() -> None:
    """启动独立任务 worker 进程。

    该入口复用应用容器的依赖装配，但显式关闭内嵌 worker 启动逻辑，避免形成重复消费。
    worker 以前台模式运行，直到进程退出时再统一释放容器托管资源。
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings, start_worker=False)
    try:
        # 独立 worker 进程只负责消费任务，不在这里再启动内嵌 worker。
        container.task_worker.run_foreground()
    finally:
        container.shutdown()


if __name__ == '__main__':
    main()
