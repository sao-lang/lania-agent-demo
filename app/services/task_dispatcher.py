"""任务调度模块。

负责定义任务调度协议、同步执行实现、持久化排队实现和后台 worker。该模块位于任务服务与
任务运行时之间，负责把“创建任务”与“真正执行任务”解耦，便于在测试、单进程和独立 worker
模式之间切换。
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from time import sleep
from typing import Protocol

from app.agents.memory import TaskMemory
from app.agents.runtime import AgentRuntime
from app.models.task import TaskDetail

logger = logging.getLogger(__name__)


class TaskDispatcher(Protocol):
    """任务调度器协议。"""

    def submit(self, task: TaskDetail) -> None:
        """提交任务到调度器。

        Args:
            task: 待执行的任务详情对象。
        """

        ...

    def shutdown(self) -> None:
        """关闭调度器并释放相关资源。"""

        ...


class InlineTaskDispatcher:
    """同步执行任务，适合测试环境。"""

    def __init__(self, runtime: AgentRuntime) -> None:
        """初始化同步任务调度器。

        Args:
            runtime: 任务运行时。
        """
        self.runtime = runtime

    def submit(self, task: TaskDetail) -> None:
        """立即在当前线程执行任务。"""
        self.runtime.run(task.task_id)

    def shutdown(self) -> None:
        """同步调度器无需额外清理资源。"""
        return None


class PersistentTaskDispatcher:
    """仅负责把任务保持为 queued 状态，供 worker 领取。"""

    def __init__(self, wake_callback=None) -> None:
        """初始化持久化任务调度器。

        Args:
            wake_callback: 可选唤醒回调，用于通知后台 worker 尽快拉取新任务。
        """
        self.wake_callback = wake_callback

    def submit(self, task: TaskDetail) -> None:
        """保持任务为 queued 状态，并在必要时唤醒 worker。"""
        logger.info('task queued', extra={'task_id': task.task_id})
        if self.wake_callback is not None:
            self.wake_callback()

    def shutdown(self) -> None:
        """持久化调度器本身无需清理额外资源。"""
        return None


class TaskWorker:
    """轮询持久化任务队列并执行任务。"""

    def __init__(
        self,
        memory: TaskMemory,
        runtime: AgentRuntime,
        poll_interval_seconds: float,
        lease_seconds: int,
        max_workers: int = 1,
        worker_name: str | None = None,
    ) -> None:
        """初始化后台任务 worker。

        Args:
            memory: 任务记忆服务，用于领取和续租任务。
            runtime: 任务运行时。
            poll_interval_seconds: 轮询间隔。
            lease_seconds: 单次任务租约时长。
            max_workers: 并发 worker 线程数。
            worker_name: 可选 worker 名称前缀。
        """
        self.memory = memory
        self.runtime = runtime
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self.lease_seconds = max(1, lease_seconds)
        self.max_workers = max(1, max_workers)
        base_name = worker_name or f'{socket.gethostname()}-{os.getpid()}'
        self.worker_ids = [f'{base_name}-w{i + 1}' for i in range(self.max_workers)]
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def start_background(self) -> None:
        """在后台线程中启动 worker。"""

        if self._threads:
            return
        for worker_id in self.worker_ids:
            thread = threading.Thread(
                target=self._run_loop,
                args=(worker_id,),
                name=f'task-worker-{worker_id}',
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def run_foreground(self) -> None:
        """前台运行 worker，通常用于独立进程。"""

        try:
            self.start_background()
            while not self._stop_event.is_set():
                sleep(1.0)
        except KeyboardInterrupt:
            logger.info('task worker interrupted')
            self.shutdown()

    def wake(self) -> None:
        """唤醒正在等待的 worker。"""

        self._wake_event.set()

    def shutdown(self) -> None:
        """停止 worker 线程。"""

        self._stop_event.set()
        self._wake_event.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self._threads.clear()

    def _run_loop(self, worker_id: str) -> None:
        """执行单个 worker 的轮询与任务消费循环。

        Args:
            worker_id: 当前 worker 标识。
        """
        while not self._stop_event.is_set():
            task = self.memory.claim_next_task(worker_id=worker_id, lease_seconds=self.lease_seconds)
            if task is None:
                self._wake_event.wait(self.poll_interval_seconds)
                self._wake_event.clear()
                continue
            logger.info('task claimed', extra={'task_id': task.task_id, 'worker_id': worker_id})
            try:
                # 开始执行前先续一次租，降低长轮询与执行切换间隙导致的误抢占概率。
                self.memory.touch_task_heartbeat(task.task_id, worker_id, self.lease_seconds)
                self.runtime.run(task.task_id)
            except Exception:
                logger.exception('task worker execution failed', extra={'task_id': task.task_id, 'worker_id': worker_id})
            finally:
                self._wake_event.clear()
