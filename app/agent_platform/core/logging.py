"""日志初始化模块。

负责在应用启动时配置根日志格式和日志级别，让 API、服务层和后台任务共用同一套日志输出约定。
通过把日志入口收敛到这里，可以减少不同进程或子模块之间的日志格式漂移。
"""

import logging


def configure_logging(log_level: str) -> None:
    """按给定级别配置根日志输出格式。

    Args:
        log_level: 日志级别字符串，比如 `INFO`、`DEBUG` 或 `WARNING`。
    """

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    )
