# Skill 存储重构实施计划

## Context

当前 Skill 系统存在两种割裂的形态：
- **文件型 Skill**（`.agents/skills/` 目录）：实际被 Agent 使用，结构为 `SKILL.md` + `rules/*.instructions.md`，但无法通过 API 管理
- **API 型 Skill**（`POST /admin/skills`）：扁平 `SkillDefinition` 模型，存于 `ConfigStore` 通用 KV 表，无法表达子规则结构

需要统一为：**所有 Skill 内容存入 DB，提供唯一 ID 和版本号，前端可覆盖更新**。无论 JSON 传入还是文件导入，最终都落库。

---

## 1. 数据库 Schema

在 `SQLiteStateStore` 中新增两张表，遵循现有 `{id} TEXT PRIMARY KEY, payload TEXT NOT NULL` 模式：

```sql
CREATE TABLE IF NOT EXISTS skills (
    skill_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_rules (
    rule_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
```

**`skills.payload` JSON 结构：**
```json
{
  "id": "sk-a1b2c3d4e5f6",
  "name": "ai-coding-rules",
  "version": 1,
  "description": "...",
  "instructions": "# AI 编码规则\n...",
  "task_types": [],
  "tools": null,
  "source": "file",
  "created_at": "2026-07-13T10:00:00+00:00",
  "updated_at": "2026-07-13T10:00:00+00:00"
}
```

**`skill_rules.payload` JSON 结构：**
```json
{
  "id": "skr-xyz789abc",
  "skill_id": "sk-a1b2c3d4e5f6",
  "name": "00-base",
  "apply_to": "**/*",
  "content": "# Base Rules\n- R1 不幻觉 API...",
  "order": 0,
  "created_at": "2026-07-13T10:00:00+00:00"
}
```

---

## 2. Pydantic 模型

新增/修改 `app/models/admin.py`：

- **`SkillRule`**：`id`, `skill_id`, `name`, `apply_to`, `content`, `order`, `created_at`
- **`SkillRuleCreate`**：`name`, `apply_to`, `content`, `order`（创建时不需 `id`/`skill_id`）
- **`SkillDefinition`** 重设计：新增 `id`（`sk-{uuid4}`）、`version`（默认 1）、`rules: list[SkillRule]`
- **`SkillCreateRequest`**：`name`, `description`, `instructions`, `task_types`, `tools`, `rules: list[SkillRuleCreate]`

---

## 3. SkillManager 重写

`app/services/skill_manager.py`：从依赖 `ConfigStore` 切换为依赖 `SQLiteStateStore`。

| 方法 | 说明 |
|------|------|
| `create(request)` | 生成 `sk-{uuid4}`，version=1，为每个 rule 生成 `skr-{uuid4}`，写入两表 |
| `update(skill_id, request)` | 读取现有记录，version+1，删除旧 rules，重建新 rules，保持同一 skill_id |
| `get(skill_id)` | 读 skills 表 + 过滤 skill_rules 表，组装返回 |
| `list()` | 加载全部，内存中 join |
| `delete(skill_id)` | 删除 skill 及关联全部 rules |
| `import_from_dir(path)` | 解析 SKILL.md + rules/*.md → 调用 `create()` |
| `import_from_json(data)` | 解析 JSON → 调用 `create()` |

**文件导入流程：** 读取 SKILL.md（YAML frontmatter → name/description，body → instructions）→ 遍历 rules/ 目录（解析每个 .md 的 frontmatter `applyTo` + body）→ 组装 `SkillCreateRequest` → `create()`

---

## 4. API 端点变更

`app/api/v1/endpoints/admin_skills.py`：路径参数从 `{name}` 改为 `{skill_id}`。

| 端点 | 变更 |
|------|------|
| `GET /admin/skills` | 不变，返回 `list[SkillDefinition]`（含 rules） |
| `POST /admin/skills` | 请求体改为 `SkillCreateRequest` |
| `GET /admin/skills/{skill_id}` | 参数改为 `skill_id` |
| `PUT /admin/skills/{skill_id}` | 参数改为 `skill_id`，version 自动递增 |
| `DELETE /admin/skills/{skill_id}` | 参数改为 `skill_id` |
| `POST /admin/skills/import` | **新增**，接受 `{"path": "..."}` 或 `{"format": "json", "data": {...}}` |

---

## 5. ConfigStore 迁移

`SkillManager.__init__` 中检测旧数据并一次性迁移：
- 若 `skills` 表已有数据则跳过
- 从 `ConfigStore`（namespace="skill"）读取旧数据
- 为每条生成 `id`、`version=1`、空 `rules`，写入新表

---

## 6. 实施文件清单

| 文件 | 改动 |
|------|------|
| `app/services/sqlite_store.py` | 新增 `skills` / `skill_rules` 表 DDL + 8 个 CRUD 方法 |
| `