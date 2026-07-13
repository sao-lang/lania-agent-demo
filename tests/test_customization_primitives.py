"""定制化原语系统测试�?

覆盖 InstructionsManager、FileInstructionManager、CustomizationEngine�?
FrontmatterParser、Hook 系统、Agent import、SessionManager.set_agent_name�?
PolicyEngine.check_agent_tool、ToolContext.file_instructions 等所有新增功能�?
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from app.agent_platform.agents.tools.base import ToolContext, ToolExecutionError
from app.agent_platform.harness.hooks import EventBus, EventPayload, HookEvent
from app.agent_platform.harness.policy import PolicyEngine
from app.agent_platform.models.admin import (
    AgentCreateRequest,
    InstructionsUpdateRequest,
    InstructionsResponse,
    FileInstructionCreate,
    FileInstructionResponse,
    HookCreateRequest,
    HookResponse,
)
from app.models.customization import PrimitiveFrontmatter
from app.agent_platform.services.agent_def_manager import AgentDefManager
from app.agent_platform.services.customization_engine import CustomizationEngine, SessionContext
from app.services.file_instruction_manager import FileInstructionManager
from app.services.frontmatter_parser import FrontmatterParser
from app.services.hook_actions import HookActionEngine
from app.services.hook_adapter import HookRuntimeAdapter
from app.services.hook_loader import FileHookLoader, FileHook, HookAction
from app.services.instructions_manager import InstructionsManager
from app.agent_platform.services.prompt_manager import PromptManager
from app.agent_platform.services.session_manager import SessionManager
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState

from app.agent_platform.core.config import get_settings


# ══════════════════════════════════════════�?
# 工具函数
# ══════════════════════════════════════════�?

def _cleanup_path(*paths: Path) -> None:
    for p in paths:
        if p.exists():
            p.unlink()


# ══════════════════════════════════════════�?
# InstructionsManager
# ══════════════════════════════════════════�?

class TestInstructionsManager(unittest.TestCase):
    def setUp(self):
        self.agents_dir = Path(".lania")
        self.im = InstructionsManager(str(self.agents_dir))

    def test_load_project_instructions_empty_when_no_file(self):
        content = self.im.load_project_instructions()
        self.assertEqual(content, "")

    def test_build_system_prompt_empty_when_no_inputs(self):
        prompt = self.im.build_system_prompt()
        self.assertEqual(prompt, "")

    def test_build_system_prompt_with_project_instructions(self):
        test_file = self.agents_dir / "AGENTS.md"
        try:
            test_file.write_text("# Project Rules\n- Be safe\n", encoding="utf-8")
            # Recreate manager to pick up file
            im2 = InstructionsManager(str(self.agents_dir))
            prompt = im2.build_system_prompt()
            self.assertIn("Project Rules", prompt)
            self.assertIn("Be safe", prompt)
        finally:
            _cleanup_path(test_file)


# ══════════════════════════════════════════�?
# FileInstructionManager
# ══════════════════════════════════════════�?

class TestFileInstructionManager(unittest.TestCase):
    def setUp(self):
        self.instructions_dir = Path(".lania") / "instructions"
        self.instructions_dir.mkdir(parents=True, exist_ok=True)
        self.fm = FileInstructionManager(str(self.instructions_dir))

    def _create_instruction_file(self, name: str, apply_to: str = "**/*.py", body: str = "Use type hints."):
        fpath = self.instructions_dir / f"{name}.instructions.md"
        fpath.write_text(f"---\napplyTo: \"{apply_to}\"\nname: {name}\n---\n\n{body}\n", encoding="utf-8")
        return fpath

    def test_load_all_empty_dir(self):
        self.fm.load_all()
        self.assertEqual(len(self.fm.instructions), 0)

    def test_load_all_with_files(self):
        f1 = self._create_instruction_file("python-rules")
        f2 = self._create_instruction_file("sql-rules", "**/*.sql", "Use transactions.")
        self.fm.load_all()
        names = [i.name for i in self.fm.instructions]
        self.assertIn("python-rules", names)
        self.assertIn("sql-rules", names)
        _cleanup_path(f1, f2)

    def test_match_by_glob(self):
        f1 = self._create_instruction_file("python-rules", "**/*.py")
        f2 = self._create_instruction_file("config-rules", "*.yaml")
        self.fm.load_all()
        matched_py = self.fm.match("app/main.py")
        self.assertEqual(len(matched_py), 1)
        self.assertEqual(matched_py[0].name, "python-rules")
        matched_yaml = self.fm.match("config.yaml")
        self.assertEqual(len(matched_yaml), 1)
        self.assertEqual(matched_yaml[0].name, "config-rules")
        _cleanup_path(f1, f2)

    def test_parse_frontmatter_delegates_to_frontmatter_parser(self):
        raw, body = FileInstructionManager._parse_frontmatter("---\napplyTo: \"**/*\"\nname: test\n---\n\nBody")
        self.assertEqual(raw.get("name"), "test")
        self.assertEqual(body, "Body")


# ══════════════════════════════════════════�?
# FrontmatterParser
# ══════════════════════════════════════════�?

class TestFrontmatterParser(unittest.TestCase):
    def test_parse_agent_frontmatter(self):
        content = """---
name: test-agent
model: gpt-4o
allowed_tools:
  - read_file
---

Instructions body.
"""
        result = FrontmatterParser.parse(content, validate=True)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.frontmatter.name, "test-agent")
        self.assertEqual(result.frontmatter.model, "gpt-4o")
        self.assertEqual(result.frontmatter.allowed_tools, ["read_file"])
        self.assertIn("Instructions body", result.body)

    def test_parse_instructions_with_apply_to_alias(self):
        content = """---
applyTo: "**/*.py"
name: python-rules
---

Use type hints.
"""
        result = FrontmatterParser.parse(content, validate=True)
        self.assertTrue(result.is_valid)
        self.assertEqual(result.frontmatter.apply_to, "**/*.py")

    def test_parse_plain_text_no_frontmatter(self):
        result = FrontmatterParser.parse("Just plain text")
        self.assertEqual(result.body, "Just plain text")
        self.assertEqual(result.raw, {})
        self.assertIsNone(result.frontmatter)

    def test_parse_invalid_yaml(self):
        content = "---\ninvalid: [unclosed\n---\n\nBody"
        result = FrontmatterParser.parse(content, validate=True)
        self.assertFalse(result.is_valid)
        self.assertTrue(len(result.errors) > 0)

    def test_parse_file_not_found(self):
        result = FrontmatterParser.parse_file("/nonexistent/file.md", validate=True)
        self.assertFalse(result.is_valid)
        self.assertIn("not found", result.errors[0])

    def test_parse_file(self):
        test_file = Path(".lania") / "agents" / "test-parse.agent.md"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            test_file.write_text("---\nname: test-file\n---\n\nBody\n", encoding="utf-8")
            result = FrontmatterParser.parse_file(str(test_file), validate=True)
            self.assertTrue(result.is_valid)
            self.assertEqual(result.frontmatter.name, "test-file")
        finally:
            _cleanup_path(test_file)

    def test_build_with_frontmatter(self):
        built = FrontmatterParser.build("Hello {name}", name="test", apply_to="**/*.py", description="A test")
        self.assertIn("---", built)
        self.assertIn("name: test", built)
        self.assertIn("applyTo:", built)
        self.assertIn("description: A test", built)
        self.assertIn("Hello {name}", built)

    def test_build_skips_none_values(self):
        built = FrontmatterParser.build("Body", name="x", description=None)
        self.assertNotIn("description:", built)

    def test_build_empty_frontmatter(self):
        built = FrontmatterParser.build("Body only")
        self.assertEqual(built, "Body only")


# ══════════════════════════════════════════�?
# PrimitiveFrontmatter Model
# ══════════════════════════════════════════�?

class TestPrimitiveFrontmatterModel(unittest.TestCase):
    def test_agent_fields(self):
        fm = PrimitiveFrontmatter(
            name="reviewer",
            display_name="Code Reviewer",
            model="gpt-4o",
            temperature=0.3,
            allowed_tools=["read_file", "search_repository"],
            skills=["ai-coding-rules"],
        )
        self.assertEqual(fm.name, "reviewer")
        self.assertEqual(fm.display_name, "Code Reviewer")
        self.assertEqual(fm.model, "gpt-4o")
        self.assertEqual(fm.temperature, 0.3)
        self.assertEqual(fm.allowed_tools, ["read_file", "search_repository"])
        self.assertEqual(fm.skills, ["ai-coding-rules"])

    def test_instructions_fields(self):
        # apply_to uses alias "applyTo" in Pydantic
        fm = PrimitiveFrontmatter(name="python-rules", applyTo="**/*.py")
        self.assertEqual(fm.apply_to, "**/*.py")

    def test_prompt_fields(self):
        fm = PrimitiveFrontmatter(name="my-prompt", variables=["name", "file"])
        self.assertEqual(fm.variables, ["name", "file"])

    def test_hook_fields(self):
        fm = PrimitiveFrontmatter(name="guard-hook", events=["before_tool"], conditions={"tool_names": ["shell"]})
        self.assertEqual(fm.events, ["before_tool"])
        self.assertEqual(fm.conditions, {"tool_names": ["shell"]})

    def test_all_fields_optional(self):
        fm = PrimitiveFrontmatter()
        self.assertIsNone(fm.name)


# ══════════════════════════════════════════�?
# CustomizationEngine
# ══════════════════════════════════════════�?

class TestCustomizationEngine(unittest.TestCase):
    def setUp(self):
        self.agents_dir = Path(".lania")

    def test_create_and_build_session_context_no_agent(self):
        engine = CustomizationEngine(agents_dir=self.agents_dir, settings=None)
        sc = asyncio.run(engine.build_session_context())
        self.assertIsInstance(sc, SessionContext)
        self.assertIsNone(sc.agent_def)

    def test_session_context_dataclass(self):
        sc = SessionContext(agent_def=None, system_prompt="Hello", extension_catalog="catalog", allowed_tools=["read_file"])
        self.assertEqual(sc.system_prompt, "Hello")
        self.assertEqual(sc.extension_catalog, "catalog")
        self.assertEqual(sc.allowed_tools, ["read_file"])

    def test_initialize_without_managers_safe(self):
        engine = CustomizationEngine(agents_dir=self.agents_dir, settings=None)
        # Should not raise even with no managers
        asyncio.run(engine.initialize())


# ══════════════════════════════════════════�?
# Hook 系统
# ══════════════════════════════════════════�?

class TestFileHookLoader(unittest.TestCase):
    def setUp(self):
        self.hooks_dir = Path(".lania") / "hooks"
        self.hooks_dir.mkdir(parents=True, exist_ok=True)

    def _create_hook_file(self, name: str, data: dict | None = None) -> Path:
        if data is None:
            data = {
                "name": name,
                "events": ["before_tool"],
                "actions": [{"type": "log", "params": {"level": "info", "message": "test"}}],
            }
        fpath = self.hooks_dir / f"{name}.json"
        fpath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return fpath

    def test_load_all_empty_dir(self):
        loader = FileHookLoader()
        # 使用临时空目�?
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks = loader.load_all(tmpdir)
            self.assertEqual(len(hooks), 0)

    def test_load_dangerous_hook(self):
        loader = FileHookLoader()
        hooks = loader.load_all(self.hooks_dir)
        dangerous = [h for h in hooks if h.name == "dangerous-tool-guard"]
        self.assertEqual(len(dangerous), 1)
        h = dangerous[0]
        self.assertEqual(h.events, ["before_tool"])
        self.assertEqual(len(h.actions), 2)
        self.assertEqual(h.actions[0].type, "log")
        self.assertEqual(h.actions[1].type, "block")

    def test_parse_invalid_json(self):
        fpath = self.hooks_dir / "invalid.json"
        fpath.write_text("{invalid json", encoding="utf-8")
        loader = FileHookLoader()
        hooks = loader.load_all(self.hooks_dir)
        # Should skip invalid and not crash
        names = [h.name for h in hooks]
        self.assertNotIn("invalid", names)
        _cleanup_path(fpath)

    def test_hook_data_models(self):
        hook = FileHook(name="test", events=["before_tool"], conditions={"tool_names": ["shell"]})
        self.assertEqual(hook.name, "test")
        self.assertEqual(hook.events, ["before_tool"])
        action = HookAction(type="block", params={"reason": "blocked"})
        self.assertEqual(action.type, "block")
        self.assertEqual(action.params["reason"], "blocked")


class TestHookActionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = HookActionEngine()

    def test_log_action_does_not_block(self):
        action = HookAction(type="log", params={"level": "info", "message": "test log"})
        payload = EventPayload(event=HookEvent.BEFORE_TOOL)
        result = self.engine.execute([action], payload)
        self.assertTrue(result.allowed)

    def test_block_action_raises_tool_execution_error(self):
        action = HookAction(type="block", params={"reason": "blocked", "error_code": "custom_blocked"})
        payload = EventPayload(event=HookEvent.BEFORE_TOOL)
        with self.assertRaises(ToolExecutionError) as ctx:
            self.engine.execute([action], payload)
        self.assertEqual(ctx.exception.code, "custom_blocked")

    def test_block_stops_subsequent_actions(self):
        actions = [
            HookAction(type="block", params={"reason": "blocked"}),
            HookAction(type="log", params={"level": "info", "message": "should not run"}),
        ]
        payload = EventPayload(event=HookEvent.BEFORE_TOOL)
        with self.assertRaises(ToolExecutionError):
            self.engine.execute(actions, payload)

    def test_audit_action(self):
        action = HookAction(type="audit", params={"category": "test"})
        payload = EventPayload(event=HookEvent.BEFORE_TOOL, workflow_state={"key": "val"})
        result = self.engine.execute([action], payload)
        self.assertTrue(result.allowed)

    def test_notify_action(self):
        action = HookAction(type="notify", params={"channel": "webhook", "template": "alert"})
        payload = EventPayload(event=HookEvent.BEFORE_TOOL)
        result = self.engine.execute([action], payload)
        self.assertTrue(result.allowed)

    def test_template_variable_resolution(self):
        action = HookAction(type="log", params={"message": "Tool: ${tool_name}"})
        payload = EventPayload(event=HookEvent.BEFORE_TOOL, payload={"tool_name": "shell_command"})
        result = self.engine.execute([action], payload)
        self.assertTrue(result.allowed)


class TestHookRuntimeAdapter(unittest.TestCase):
    def test_matching_tool_blocked(self):
        hook = FileHook(name="test-hook", events=["before_tool"], conditions={"tool_names": ["dangerous_tool"]},
                        actions=[HookAction(type="block", params={"reason": "blocked"})])
        adapter = HookRuntimeAdapter(hook)
        payload = EventPayload(event=HookEvent.BEFORE_TOOL, payload={"tool_name": "dangerous_tool"})
        with self.assertRaises(ToolExecutionError):
            adapter.handle(payload)

    def test_non_matching_tool_passes(self):
        hook = FileHook(name="test-hook", events=["before_tool"], conditions={"tool_names": ["dangerous_tool"]},
                        actions=[HookAction(type="block", params={"reason": "blocked"})])
        adapter = HookRuntimeAdapter(hook)
        payload = EventPayload(event=HookEvent.BEFORE_TOOL, payload={"tool_name": "safe_tool"})
        # Should not raise
        adapter.handle(payload)

    def test_no_conditions_always_triggers(self):
        hook = FileHook(name="always-fire", events=["before_tool"],
                        actions=[HookAction(type="block", params={"reason": "always"})])
        adapter = HookRuntimeAdapter(hook)
        payload = EventPayload(event=HookEvent.BEFORE_TOOL, payload={"tool_name": "anything"})
        with self.assertRaises(ToolExecutionError):
            adapter.handle(payload)

    def test_event_bus_integration(self):
        hook = FileHook(name="bus-test", events=["before_tool"], conditions={"tool_names": ["block_me"]},
                        actions=[HookAction(type="block", params={"reason": "bus test blocked"})])
        adapter = HookRuntimeAdapter(hook)
        bus = EventBus()
        bus.register(adapter, HookEvent.BEFORE_TOOL)
        with self.assertRaises(ToolExecutionError):
            bus.emit(HookEvent.BEFORE_TOOL, tool_name="block_me")
        # Non-matching should pass
        bus.emit(HookEvent.BEFORE_TOOL, tool_name="allow_me")


# ══════════════════════════════════════════�?
# AgentDefManager.import_from_file
# ══════════════════════════════════════════�?

class TestAgentDefManagerImportFile(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self.persistence = SQLiteStateStore(self.settings)
        self.adm = AgentDefManager(persistence=self.persistence)
        self.test_dir = Path(".lania") / "agents"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def _create_agent_file(self, name: str, **overrides) -> Path:
        fields = {
            "name": name,
            "display_name": name.replace("-", " ").title(),
            "description": "Test agent",
            "model": "gpt-4o",
            "temperature": 0.3,
            "allowed_tools": ["read_file", "search_repository"],
            "skills": ["ai-coding-rules"],
        }
        fields.update(overrides)
        lines = ["---"]
        for k, v in fields.items():
            if isinstance(v, list):
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {item}")
            else:
                lines.append(f"{k}: {v}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {fields['display_name']} Instructions")
        lines.append("")
        lines.append("You are a test agent.")
        fpath = self.test_dir / f"{name}.agent.md"
        fpath.write_text("\n".join(lines), encoding="utf-8")
        return fpath

    def test_import_from_file_basic(self):
        fpath = self._create_agent_file("test-import-basic")
        agent = None
        try:
            agent = asyncio.run(self.adm.import_from_file(str(fpath)))
            self.assertEqual(agent.name, "test-import-basic")
            self.assertEqual(agent.model, "gpt-4o")
            self.assertEqual(agent.temperature, 0.3)
            self.assertEqual(agent.allowed_tools, ["read_file", "search_repository"])
            self.assertIn("You are a test agent", agent.instructions)
        finally:
            if agent:
                asyncio.run(self.adm.delete(agent.id))
            _cleanup_path(fpath)

    def test_import_from_file_no_frontmatter(self):
        # path.stem for "plain.agent.md" returns "plain.agent"
        fpath = self.test_dir / "plain.agent.md"
        fpath.write_text("# Just a title\n\nBody text\n", encoding="utf-8")
        agent = None
        try:
            agent = asyncio.run(self.adm.import_from_file(str(fpath)))
            self.assertEqual(agent.name, "plain.agent")
            self.assertIn("Just a title", agent.instructions)
        finally:
            if agent:
                asyncio.run(self.adm.delete(agent.id))
            _cleanup_path(fpath)

    def test_import_from_file_file_not_found(self):
        with self.assertRaises(ValueError):
            asyncio.run(self.adm.import_from_file("/nonexistent/path.agent.md"))


# ══════════════════════════════════════════�?
# PromptManager.import_from_file
# ══════════════════════════════════════════�?

class TestPromptManagerImportFile(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self.persistence = SQLiteStateStore(self.settings)
        self.pm = PromptManager(persistence=self.persistence)
        self.test_dir = Path(".lania") / "prompts"
        self.test_dir.mkdir(parents=True, exist_ok=True)

    def _create_prompt_file(self, name: str, variables: list[str] | None = None) -> Path:
        lines = ["---", f"name: {name}", "description: Test prompt"]
        if variables:
            lines.append("variables:")
            for v in variables:
                lines.append(f"  - {v}")
        lines.append("---")
        lines.append("")
        lines.append(f"Hello {'{username}'}!")
        fpath = self.test_dir / f"{name}.prompt.md"
        fpath.write_text("\n".join(lines), encoding="utf-8")
        return fpath

    def test_import_from_file_basic(self):
        fpath = self._create_prompt_file("test-prompt-import", variables=["username"])
        prompt = None
        try:
            prompt = asyncio.run(self.pm.import_from_file(str(fpath)))
            self.assertEqual(prompt.name, "test-prompt-import")
            self.assertIn("Hello", prompt.template)
            self.assertIn("username", prompt.variables)
        finally:
            if prompt:
                asyncio.run(self.pm.delete(prompt.id))
            _cleanup_path(fpath)

    def test_import_from_file_no_frontmatter(self):
        # path.stem for "plain.prompt.md" returns "plain.prompt"
        fpath = self.test_dir / "plain.prompt.md"
        fpath.write_text("Just a template {x}\n", encoding="utf-8")
        prompt = None
        try:
            prompt = asyncio.run(self.pm.import_from_file(str(fpath)))
            self.assertEqual(prompt.name, "plain.prompt")
        finally:
            if prompt:
                asyncio.run(self.pm.delete(prompt.id))
            _cleanup_path(fpath)


# ══════════════════════════════════════════�?
# SessionManager.set_agent_name
# ══════════════════════════════════════════�?

class TestSessionManagerSetAgentName(unittest.TestCase):
    def test_session_model_has_agent_name_field(self):
        from app.agent_platform.services.session_manager import Session
        s = Session(id="test-id")
        self.assertIsNone(s.agent_name)
        s.agent_name = "my-agent"
        self.assertEqual(s.agent_name, "my-agent")

    def test_session_model_serialization_includes_agent_name(self):
        from app.agent_platform.services.session_manager import Session
        s = Session(id="test-id", agent_name="test-agent")
        data = s.model_dump(mode="json")
        self.assertEqual(data.get("agent_name"), "test-agent")

    def test_set_agent_name_integration(self):
        settings = get_settings()
        persistence = SQLiteStateStore(settings)
        state = InMemoryState()
        sm = SessionManager(state, persistence, task_memory=None)

        async def _test():
            session = await sm.get_or_create()
            self.assertIsNone(session.agent_name)
            await sm.set_agent_name(session.id, "my-agent")
            s2 = await sm.get(session.id)
            self.assertEqual(s2.agent_name, "my-agent")
            await sm.delete(session.id)

        asyncio.run(_test())


# ══════════════════════════════════════════�?
# PolicyEngine.check_agent_tool
# ══════════════════════════════════════════�?

class TestPolicyEngineCheckAgentTool(unittest.TestCase):
    def setUp(self):
        self.settings = get_settings()
        self.persistence = SQLiteStateStore(self.settings)
        self.pe = PolicyEngine(self.settings, persistence=self.persistence)
        from app.agent_platform.models.admin import AgentDefinition
        self.test_agent = AgentDefinition(
            name="test-agent",
            allowed_tools=["read_file", "search_repository", "list_files"],
        )

    def test_allowed_tool_passes(self):
        decision = self.pe.check_agent_tool("read_file", agent=self.test_agent)
        self.assertTrue(decision.allowed)

    def test_blocked_tool_fails(self):
        decision = self.pe.check_agent_tool("shell_command", agent=self.test_agent)
        self.assertFalse(decision.allowed)
        self.assertIn("shell_command", decision.reason)

    def test_allowed_tools_param_overrides_agent(self):
        decision = self.pe.check_agent_tool("read_file", allowed_tools=["read_file"])
        self.assertTrue(decision.allowed)

    def test_blocked_tools_param(self):
        decision = self.pe.check_agent_tool("shell", allowed_tools=["read_file"])
        self.assertFalse(decision.allowed)

    def test_none_allowed_tools_means_unrestricted(self):
        decision = self.pe.check_agent_tool("anything", allowed_tools=None)
        self.assertTrue(decision.allowed)

    def test_none_agent_allowed_tools_means_unrestricted(self):
        from app.agent_platform.models.admin import AgentDefinition
        agent = AgentDefinition(name="unrestricted", allowed_tools=None)
        decision = self.pe.check_agent_tool("anything", agent=agent)
        self.assertTrue(decision.allowed)

    def test_empty_list_blocks_all(self):
        decision = self.pe.check_agent_tool("anything", allowed_tools=[])
        self.assertFalse(decision.allowed)


# ══════════════════════════════════════════�?
# ToolContext.file_instructions
# ══════════════════════════════════════════�?

class TestToolContextFileInstructions(unittest.TestCase):
    def test_file_instructions_default_is_none(self):
        tc = ToolContext(state=None, retrieval=None, trace=None, task_memory=None, settings=None)
        self.assertIsNone(tc.file_instructions)

    def test_file_instructions_can_be_set(self):
        from app.services.file_instruction_manager import FileInstruction
        fi = FileInstruction(name="test", apply_to="**/*.py", content="Use types")
        tc = ToolContext(
            state=None, retrieval=None, trace=None, task_memory=None, settings=None,
            file_instructions=[fi],
        )
        self.assertEqual(len(tc.file_instructions), 1)
        self.assertEqual(tc.file_instructions[0].name, "test")

    def test_backward_compatibility(self):
        tc = ToolContext(state="s", retrieval="r", trace="t", task_memory="m", settings="s", llm="l")
        self.assertEqual(tc.state, "s")
        self.assertEqual(tc.llm, "l")


# ══════════════════════════════════════════�?
# Admin API 模型
# ══════════════════════════════════════════�?

class TestAdminApiModels(unittest.TestCase):
    def test_instructions_models(self):
        req = InstructionsUpdateRequest(content="new instructions")
        self.assertEqual(req.content, "new instructions")
        resp = InstructionsResponse(content="hello", length=5)
        self.assertEqual(resp.length, 5)

    def test_file_instruction_models(self):
        create = FileInstructionCreate(name="test", apply_to="**/*.py", content="rules")
        self.assertEqual(create.name, "test")
        self.assertEqual(create.apply_to, "**/*.py")
        resp = FileInstructionResponse(name="test", apply_to="**/*", content="body")
        self.assertEqual(resp.source, "file")

    def test_hook_models(self):
        create = HookCreateRequest(name="my-hook", events=["before_tool"], actions=[{"type": "log", "params": {}}])
        self.assertEqual(create.name, "my-hook")
        self.assertEqual(create.events, ["before_tool"])
        resp = HookResponse(name="my-hook", events=["before_tool"], actions=[{"type": "log", "params": {"level": "info"}}])
        self.assertTrue(resp.enabled)


# ══════════════════════════════════════════�?
# CustomizationEngine.build_session_context
# ══════════════════════════════════════════�?

class TestCustomizationEngineBuildSessionContext(unittest.TestCase):
    def test_with_agent_def_manager(self):
        settings = get_settings()
        persistence = SQLiteStateStore(settings)
        adm = AgentDefManager(persistence=persistence)
        # Create test agent
        import asyncio

        async def _test():
            agent = await adm.create(AgentCreateRequest(
                name="session-test-agent",
                instructions="You are session test agent.",
                allowed_tools=["read_file"],
            ))
            engine = CustomizationEngine(
                agents_dir=Path(".lania"),
                agent_def_manager=adm,
                settings=None,
            )
            sc = await engine.build_session_context(agent_name="session-test-agent")
            self.assertIsNotNone(sc.agent_def)
            self.assertEqual(sc.agent_def.name, "session-test-agent")
            self.assertEqual(sc.allowed_tools, ["read_file"])
            self.assertIn("session test agent", sc.system_prompt)
            # Cleanup
            await adm.delete(agent.id)

        asyncio.run(_test())

    def test_without_agent_returns_default(self):
        engine = CustomizationEngine(agents_dir=Path(".lania"), settings=None)
        import asyncio

        async def _test():
            sc = await engine.build_session_context()
            self.assertIsNone(sc.agent_def)
            self.assertIsNone(sc.allowed_tools)

        asyncio.run(_test())
