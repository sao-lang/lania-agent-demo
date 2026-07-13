"""测试 ConsentStore�?""

import unittest

from app.agent_platform.agents.brain.models import ConsentRecord, ConsentScope
from app.agent_platform.agents.brain.consent_store import ConsentStore


class TestConsentStore(unittest.TestCase):
    """测试确认记录存储�?""

    def setUp(self):
        self.store = ConsentStore()

    def test_save_and_get_persistent(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.PERSISTENT,
        )
        self.store.save(record)

        result = self.store.get("user_1", "shell_command")
        self.assertIsNotNone(result)
        self.assertEqual(result.scope, ConsentScope.PERSISTENT)

    def test_save_and_get_session(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.SESSION,
        )
        self.store.save(record)

        result = self.store.get("user_1", "shell_command")
        self.assertIsNotNone(result)

    def test_get_nonexistent(self):
        result = self.store.get("user_unknown", "any_tool")
        self.assertIsNone(result)

    def test_get_wrong_user(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.PERSISTENT,
        )
        self.store.save(record)

        result = self.store.get("user_2", "shell_command")
        self.assertIsNone(result)

    def test_get_wrong_tool(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.PERSISTENT,
        )
        self.store.save(record)

        result = self.store.get("user_1", "web_search")
        self.assertIsNone(result)

    def test_clear_session(self):
        record = ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.SESSION,
        )
        self.store.save(record)
        self.store.bind_session("session_1", "user_1")

        # 验证未清除前可以获取
        self.assertIsNotNone(self.store.get("user_1", "shell_command"))

        # �?session
        self.store.clear_session("session_1")

        # session 级记录被清除
        self.assertIsNone(self.store.get("user_1", "shell_command"))

    def test_clear_session_persistent_remains(self):
        self.store.save(ConsentRecord(
            user_id="user_1", tool_name="tool_a",
            scope=ConsentScope.SESSION,
        ))
        self.store.save(ConsentRecord(
            user_id="user_1", tool_name="tool_b",
            scope=ConsentScope.PERSISTENT,
        ))
        self.store.bind_session("session_1", "user_1")

        self.store.clear_session("session_1")

        # session 级被清除
        self.assertIsNone(self.store.get("user_1", "tool_a"))
        # persistent 级保�?
        self.assertIsNotNone(self.store.get("user_1", "tool_b"))

    def test_clear_user_removes_all(self):
        self.store.save(ConsentRecord(
            user_id="user_1", tool_name="tool_a",
            scope=ConsentScope.PERSISTENT,
        ))
        self.store.save(ConsentRecord(
            user_id="user_1", tool_name="tool_b",
            scope=ConsentScope.PERSISTENT,
        ))

        self.store.clear_user("user_1")

        self.assertIsNone(self.store.get("user_1", "tool_a"))
        self.assertIsNone(self.store.get("user_1", "tool_b"))

    def test_multiple_users(self):
        self.store.save(ConsentRecord(
            user_id="user_1", tool_name="tool_a",
            scope=ConsentScope.PERSISTENT,
        ))
        self.store.save(ConsentRecord(
            user_id="user_2", tool_name="tool_a",
            scope=ConsentScope.PERSISTENT,
        ))

        # 各自独立
        self.assertIsNotNone(self.store.get("user_1", "tool_a"))
        self.assertIsNotNone(self.store.get("user_2", "tool_a"))

        # 清除 user_1
        self.store.clear_user("user_1")
        self.assertIsNone(self.store.get("user_1", "tool_a"))
        self.assertIsNotNone(self.store.get("user_2", "tool_a"))

    def test_bind_session_then_clear_session(self):
        self.store.bind_session("session_1", "user_1")
        self.store.save(ConsentRecord(
            user_id="user_1", tool_name="shell_command",
            scope=ConsentScope.SESSION,
        ))

        self.store.clear_session("session_1")
        self.assertIsNone(self.store.get("user_1", "shell_command"))

    def test_clear_session_unbound_session(self):
        # �?bind �?session 不报�?
        self.store.clear_session("unknown_session")  # should not raise
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
