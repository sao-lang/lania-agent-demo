"""不可变审计日志模块。

记录"谁在什么时候调了什么工具"，append-only 不可篡改。
写入独立 DB（audit.sqlite3），与 app.sqlite3 分离。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AuditEvent:
    """审计事件记录。"""
    timestamp: str = ""
    user_id: str = ""
    tenant_id: str = ""
    session_id: str = ""
    agent_name: str = ""
    action: str = ""      # tool_call / consent / escalation / config_change
    target: str = ""      # 工具名 / 配置名
    result: str = ""      # success / denied / error
    detail: str = ""


class AuditLogger:
    """不可变审计日志。

    写入独立 DB（非 app.sqlite3），append-only。
    """

    def __init__(self, db_path: str = "audit.sqlite3") -> None:
        self._db = sqlite3.connect(db_path)
        self._init_table()

    def _init_table(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL,
                result TEXT NOT NULL,
                detail TEXT
            )
        """)

    async def log(self, event: AuditEvent) -> None:
        """写入审计日志（INSERT ONLY）。"""
        self._db.execute(
            "INSERT INTO audit_log (timestamp, user_id, tenant_id, session_id, "
            "agent_name, action, target, result, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                event.timestamp or datetime.utcnow().isoformat(),
                event.user_id,
                event.tenant_id,
                event.session_id,
                event.agent_name,
                event.action,
                event.target,
                event.result,
                event.detail,
            ],
        )

    async def query(
        self,
        user_id: str | None = None,
        tenant_id: str | None = None,
        action: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """审计查询，不可修改已有记录。

        Args:
            user_id: 按用户筛选。
            tenant_id: 按租户筛选。
            action: 按操作类型筛选。
            start_time: 开始时间。
            end_time: 结束时间。
            limit: 最大返回条数。

        Returns:
            AuditEvent 列表。
        """
        sql = "SELECT * FROM audit_log WHERE 1=1"
        params: list[Any] = []
        if user_id:
            sql += " AND user_id=?"
            params.append(user_id)
        if tenant_id:
            sql += " AND tenant_id=?"
            params.append(tenant_id)
        if action:
            sql += " AND action=?"
            params.append(action)
        if start_time:
            sql += " AND timestamp>=?"
            params.append(start_time)
        if end_time:
            sql += " AND timestamp<=?"
            params.append(end_time)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = self._db.execute(sql, params).fetchall()
        return [
            AuditEvent(
                timestamp=row[1], user_id=row[2], tenant_id=row[3],
                session_id=row[4], agent_name=row[5], action=row[6],
                target=row[7], result=row[8], detail=row[9] or "",
            )
            for row in rows
        ]
