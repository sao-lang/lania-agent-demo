"""Skill 管理模块。

管理 Skill 定义的注册、查询、文件导入和版本控制。
支持两种输入：
- JSON 结构（API 传入）
- 文件目录（.agents/skills/{name}/ 含 SKILL.md + rules/）

内置内存缓存：写操作全量清空，读操作命中缓存零 DB 开销。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.models.admin import (
    SkillCreateRequest,
    SkillDefinition,
    SkillRule,
    SkillRuleCreate,
)
from app.services.sqlite_store import SQLiteStateStore


class SkillManager:
    """Skill 管理器。

    使用 SQLiteStateStore 持久化，支持 JSON 和文件两种输入格式。
    内置内存缓存：写操作全量清空，读操作命中缓存零 DB 开销。
    """

    _DEFAULT_SKILLS_DIR = ".lania/skills"

    def __init__(
        self,
        persistence: SQLiteStateStore,
        skills_dir: str | None = None,
    ) -> None:
        self._persistence = persistence
        self._skills_dir = Path(skills_dir) if skills_dir else Path(self._DEFAULT_SKILLS_DIR)
        self._cache: dict[str, object] = {}

    # ── 缓存管理 ──────────────────────────────

    def _invalidate_cache(self) -> None:
        """写操作后全量清空缓存。"""
        self._cache.clear()

    # ── CRUD ──────────────────────────────────

    async def create(self, request: SkillCreateRequest) -> SkillDefinition:
        """创建 Skill。"""
        now = datetime.now()
        skill_id = f"sk-{uuid4().hex[:12]}"

        rules: list[SkillRule] = []
        for r in request.rules:
            rules.append(SkillRule(
                id=f"skr-{uuid4().hex[:12]}",
                skill_id=skill_id,
                name=r.name, apply_to=r.apply_to, content=r.content,
                order=r.order, created_at=now,
            ))

        skill = SkillDefinition(
            id=skill_id, name=request.name, version=1,
            description=request.description, instructions=request.instructions,
            task_types=request.task_types, tools=request.tools,
            source=request.source, rules=rules,
            created_at=now, updated_at=now,
        )
        await self._persist_skill(skill)
        self._invalidate_cache()
        return skill

    async def update(self, skill_id: str, request: SkillCreateRequest) -> SkillDefinition:
        """更新 Skill（覆盖模式，version 递增）。"""
        existing = await self.get(skill_id)
        if existing is None:
            raise ValueError(f"Skill '{skill_id}' not found")

        now = datetime.now()
        for rule in existing.rules:
            self._persistence.delete_skill_rule(rule.id)

        rules: list[SkillRule] = []
        for r in request.rules:
            rules.append(SkillRule(
                id=f"skr-{uuid4().hex[:12]}",
                skill_id=skill_id,
                name=r.name, apply_to=r.apply_to, content=r.content,
                order=r.order, created_at=now,
            ))

        skill = SkillDefinition(
            id=skill_id, name=request.name, version=existing.version + 1,
            description=request.description, instructions=request.instructions,
            task_types=request.task_types, tools=request.tools,
            source=request.source, rules=rules,
            created_at=existing.created_at, updated_at=now,
        )
        await self._persist_skill(skill)
        self._invalidate_cache()
        return skill

    async def get(self, skill_id: str) -> SkillDefinition | None:
        """按 id 获取 Skill（含 rules），优先命中缓存。"""
        cache_key = f"id:{skill_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        payload = self._persistence.get_skill(skill_id)
        if payload is None:
            return None
        skill = self._payload_to_skill(payload)
        self._cache[cache_key] = skill
        return skill

    async def get_by_name(self, name: str) -> SkillDefinition | None:
        """按 name 查找 skill。"""
        cache_key = f"name:{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        skills = await self.list()
        for s in skills:
            if s.name == name:
                self._cache[cache_key] = s
                return s
        return None

    async def list(self) -> list[SkillDefinition]:
        """列出所有 Skill（含 rules），优先命中缓存。"""
        if "list" in self._cache:
            return self._cache["list"]  # type: ignore[return-value]

        skill_payloads = self._persistence.list_skills()
        rule_payloads = self._persistence.list_skill_rules()

        rules_by_skill: dict[str, list[SkillRule]] = {}
        for rp in rule_payloads:
            sid = rp.get("skill_id", "")
            rules_by_skill.setdefault(sid, []).append(SkillRule(**rp))

        skills: list[SkillDefinition] = []
        for sp in skill_payloads:
            skill = SkillDefinition(**sp)
            skill.rules = sorted(rules_by_skill.get(skill.id, []), key=lambda r: r.order)
            skills.append(skill)

        self._cache["list"] = skills
        return skills

    async def delete(self, skill_id: str) -> None:
        """删除 Skill 及其关联的全部 rules。"""
        skill = await self.get(skill_id)
        if skill is None:
            return
        for rule in skill.rules:
            self._persistence.delete_skill_rule(rule.id)
        self._persistence.delete_skill(skill_id)
        self._invalidate_cache()

    # ── 文件导入 ──────────────────────────────

    async def import_from_dir(self, dir_path: str) -> SkillDefinition:
        """从文件目录导入 Skill。"""
        skill_dir = Path(dir_path)
        if not skill_dir.is_dir():
            raise ValueError(f"Directory not found: {dir_path}")

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            raise ValueError(f"SKILL.md not found in {dir_path}")

        frontmatter, body = self._parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        name = frontmatter.get("name", skill_dir.name)
        description = frontmatter.get("description", "")

        rules_dir = skill_dir / "rules"
        rule_creates: list[SkillRuleCreate] = []
        if rules_dir.is_dir():
            for i, rule_file in enumerate(sorted(rules_dir.glob("*.md"))):
                rf_fm, rf_body = self._parse_frontmatter(rule_file.read_text(encoding="utf-8"))
                rule_creates.append(SkillRuleCreate(
                    name=rule_file.stem,
                    apply_to=rf_fm.get("applyTo", "**/*"),
                    content=rf_body.strip(),
                    order=i,
                ))

        request = SkillCreateRequest(
            name=name, description=description,
            instructions=body.strip(), source="file",
            rules=rule_creates,
        )
        return await self.create(request)

    # ── 运行时方法 ────────────────────────────

    async def build_routing_table(self, skill_names: list[str]) -> str:
        """Phase 1: 构建路由表（轻量，只含 instructions + 规则名称列表）。

        输出示例：
        ## Skill: ai-coding-rules
        这是一个通用的 AI 编码规则 skill。
        可用规则: 00-base, 01-typescript, 10-python, 12-debug
        """
        cache_key = f"routing:{','.join(sorted(skill_names))}"
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]

        parts: list[str] = []
        for name in skill_names:
            skill = await self.get_by_name(name)
            if skill:
                rule_names = [r.name for r in skill.rules]
                parts.append(
                    f"## Skill: {skill.name}\n"
                    f"{skill.instructions}\n"
                    f"可用规则: {', '.join(rule_names)}\n"
                )
        result = "\n\n".join(parts)
        self._cache[cache_key] = result
        return result

    async def load_rules(
        self,
        requests: list[tuple[str, str]],  # [(skill_name, rule_name), ...]
        max_tokens: int = 8000,
    ) -> str:
        """Phase 2: 按需加载指定规则，有 token 预算上限。"""
        loaded: list[str] = []
        total_chars = 0
        char_budget = max_tokens * 3

        for skill_name, rule_name in requests:
            skill = await self.get_by_name(skill_name)
            if not skill:
                continue
            for rule in skill.rules:
                if rule.name == rule_name:
                    chunk = f"\n## Rule: {rule.name}\n{rule.content}\n"
                    if total_chars + len(chunk) > char_budget:
                        break
                    loaded.append(chunk)
                    total_chars += len(chunk)
                    break

        return "\n".join(loaded)

    async def get_rules_content(self, skill_id: str) -> str:
        """获取 skill 所有规则的拼接内容。"""
        skill = await self.get(skill_id)
        if not skill:
            return ""
        parts = [skill.instructions]
        for rule in sorted(skill.rules, key=lambda r: r.order):
            parts.append(f"\n## {rule.name}\n{rule.content}")
        return "\n".join(parts)

    # ── 内部方法 ──────────────────────────────

    async def _persist_skill(self, skill: SkillDefinition) -> None:
        skill_dict = skill.model_dump(mode="json")
        skill_dict.pop("rules", None)
        self._persistence.upsert_skill(skill_dict)
        for rule in skill.rules:
            self._persistence.upsert_skill_rule(rule.model_dump(mode="json"))

    def _payload_to_skill(self, payload: dict) -> SkillDefinition:
        rule_payloads = self._persistence.list_skill_rules()
        rules = [
            SkillRule(**rp)
            for rp in rule_payloads
            if rp.get("skill_id") == payload.get("id")
        ]
        rules.sort(key=lambda r: r.order)
        skill = SkillDefinition(**payload)
        skill.rules = rules
        return skill

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict, str]:
        from app.services.frontmatter_parser import FrontmatterParser
        result = FrontmatterParser.parse(content, validate=False)
        return result.raw, result.body
