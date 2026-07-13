"""用户确认记录存储。

保存用户的"记住此选择"决定，支持 session 级和 persistent 级两种范围。
"""

from __future__ import annotations


from app.harness.brain.models import ConsentRecord, ConsentScope


class ConsentStore:
    """用户确认记录存储。

    用于 StepExecutor 中的"记住此选择"功能。
    session 级：当前会话有效
    persistent 级：持久有效
    """

    def __init__(self) -> None:
        # user_id → tool_name → ConsentRecord
        self._records: dict[str, dict[str, ConsentRecord]] = {}
        # session_id → user_id
        self._session_map: dict[str, str] = {}

    # ── 公开接口 ──────────────────────────────

    def save(self, record: ConsentRecord) -> None:
        """保存确认记录。

        Args:
            record: 确认记录。
        """
        if record.user_id not in self._records:
            self._records[record.user_id] = {}
        self._records[record.user_id][record.tool_name] = record

    def get(self, user_id: str, tool_name: str) -> ConsentRecord | None:
        """获取用户对某工具的历史确认记录。

        Args:
            user_id: 用户 ID。
            tool_name: 工具名称。

        Returns:
            有效记录或 None。
        """
        user_records = self._records.get(user_id, {})
        record = user_records.get(tool_name)
        if record is not None and record.is_valid():
            return record
        return None

    def clear_session(self, session_id: str) -> None:
        """清除某会话中的 session 级记录。

        Args:
            session_id: 会话 ID。
        """
        user_id = self._session_map.get(session_id)
        if user_id is None:
            return
        user_records = self._records.get(user_id, {})
        expired = [
            name for name, record in user_records.items()
            if record.scope == ConsentScope.SESSION
        ]
        for name in expired:
            del user_records[name]

    def clear_user(self, user_id: str) -> None:
        """清除用户的所有记录。

        Args:
            user_id: 用户 ID。
        """
        self._records.pop(user_id, None)

    def bind_session(self, session_id: str, user_id: str) -> None:
        """将会话绑定到用户。

        Args:
            session_id: 会话 ID。
            user_id: 用户 ID。
        """
        self._session_map[session_id] = user_id
