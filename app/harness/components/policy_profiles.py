"""策略 Profile 加载与解析模块。

负责从内置默认值、SQLite 持久化或外部配置文件中加载策略 profile，并根据
请求上下文选择最匹配的一条策略。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.models.task import TaskRequest
from app.services.sqlite_store import SQLiteStateStore


@dataclass(frozen=True)
class PolicyProfile:
    """描述一组可复用的任务策略。"""

    name: str
    version: str = 'v1'
    organization_id: str | None = None
    tenant_id: str | None = None
    allowed_roles: tuple[str, ...] = ()
    match_keywords: tuple[str, ...] = ()
    require_evidence: bool = True
    min_coverage: float = 0.0
    confidence_threshold: float = 0.0
    require_review_passed: bool = False
    max_plan_steps: int = 16
    max_open_questions: int = 10
    allowed_output_formats: tuple[str, ...] = ('markdown', 'json', 'markdown+json')
    blocked_tools: tuple[str, ...] = ()
    denied_permissions: tuple[str, ...] = ()
    evaluation_baseline_order: tuple[str, ...] = ('benchmark', 'report', 'version', 'task')
    evaluation_report_path: str | None = None


class PolicyProfileStore:
    """从内置、数据库或配置文件加载策略 profile。"""

    BUILTIN_DEFAULTS = {
        'default_profile': 'document_analysis_default',
        'profiles': [
            {
                'name': 'document_analysis_default',
                'version': 'v1',
                'match_keywords': [],
                'require_evidence': False,
                'min_coverage': 0.0,
                'confidence_threshold': 0.0,
                'require_review_passed': False,
                'max_plan_steps': 16,
                'max_open_questions': 10,
                'allowed_output_formats': ['markdown', 'json', 'markdown+json'],
                'blocked_tools': [],
                'evaluation_baseline_order': ['benchmark', 'report', 'version', 'task'],
            },
            {
                'name': 'contract_review',
                'version': 'v1',
                'match_keywords': ['contract', '合同'],
                'require_evidence': True,
                'min_coverage': 0.7,
                'confidence_threshold': 0.5,
                'require_review_passed': True,
                'max_open_questions': 6,
                'allowed_output_formats': ['markdown', 'json', 'markdown+json'],
                'blocked_tools': [],
                'evaluation_baseline_order': ['benchmark', 'report', 'version', 'task'],
            },
            {
                'name': 'financial_report',
                'version': 'v1',
                'match_keywords': ['finance', 'financial', '财务', '金融'],
                'require_evidence': True,
                'min_coverage': 0.75,
                'confidence_threshold': 0.8,
                'require_review_passed': True,
                'max_open_questions': 4,
                'allowed_output_formats': ['markdown', 'json', 'markdown+json'],
                'blocked_tools': [],
                'evaluation_baseline_order': ['benchmark', 'report', 'version', 'task'],
            },
            {
                'name': 'technical_analysis',
                'version': 'v1',
                'match_keywords': ['technical', 'tech', '技术'],
                'require_evidence': True,
                'min_coverage': 0.7,
                'confidence_threshold': 0.4,
                'require_review_passed': False,
                'max_open_questions': 8,
                'allowed_output_formats': ['markdown', 'json', 'markdown+json'],
                'blocked_tools': [],
                'evaluation_baseline_order': ['benchmark', 'report', 'version', 'task'],
            },
        ],
    }

    def __init__(
        self,
        settings: Settings | None = None,
        config_path: Path | None = None,
        persistence: SQLiteStateStore | None = None,
    ) -> None:
        """初始化策略 profile 存储适配器。"""

        self.settings = settings
        self.persistence = persistence
        self.config_path = config_path or (settings.resolved_policy_config_path if settings is not None else None)

    def load_profiles(self) -> tuple[str, dict[str, PolicyProfile]]:
        """加载全部 profile，并返回默认 profile 名称与映射表。"""

        payload = self.BUILTIN_DEFAULTS
        db_payload = self._load_profiles_from_db()
        if db_payload is not None:
            payload = db_payload
        if self.config_path is not None and self.config_path.exists():
            payload = db_payload or self._load_profile_payload(self.config_path)
        default_profile_name = str(payload.get('default_profile') or 'document_analysis_default').strip() or 'document_analysis_default'
        profiles: dict[str, PolicyProfile] = {}
        for item in payload.get('profiles', []):
            if not isinstance(item, dict):
                continue
            profile = self._build_profile(item)
            profiles[profile.name] = profile
        if not profiles:
            for item in self.BUILTIN_DEFAULTS['profiles']:
                profile = self._build_profile(item)
                profiles[profile.name] = profile
        if default_profile_name not in profiles:
            default_profile_name = next(iter(profiles.keys()))
        return default_profile_name, profiles

    def read_config_mtime_ns(self) -> int | None:
        """读取配置文件修改时间，用于判断是否需要热更新。"""

        if self.config_path is None or not self.config_path.exists():
            return None
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def read_db_signature(self) -> tuple[int, tuple[str, ...]] | None:
        """读取数据库中的 profile 版本签名。"""

        if self.persistence is None:
            return None
        records = self.persistence.list_policy_profiles()
        if not records:
            return (0, ())
        version_markers: list[str] = []
        for item in records:
            identifier = str(item.get('profile_id') or item.get('name') or '').strip()
            updated_at = str(item.get('updated_at') or '').strip()
            version = str(item.get('version') or '').strip()
            version_markers.append(f'{identifier}:{version}:{updated_at}')
        version_markers.sort()
        return len(version_markers), tuple(version_markers)

    def _load_profile_payload(self, path: Path) -> dict[str, Any]:
        """从 JSON/YAML 文件读取 profile 配置。"""

        raw = path.read_text(encoding='utf-8').strip()
        if not raw:
            return self.BUILTIN_DEFAULTS
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                import yaml  # type: ignore

                loaded = yaml.safe_load(raw)
                if isinstance(loaded, dict):
                    return loaded
            except Exception:
                pass
        return self.BUILTIN_DEFAULTS

    def _load_profiles_from_db(self) -> dict[str, Any] | None:
        """从 SQLite 持久化层读取 profile 配置。"""

        if self.persistence is None:
            return None
        records = self.persistence.list_policy_profiles()
        if not records:
            return None
        default_profile = next(
            (str(item.get('name') or '').strip() for item in records if bool(item.get('is_default'))),
            '',
        )
        return {
            'default_profile': default_profile or str(records[0].get('name') or 'document_analysis_default').strip(),
            'profiles': records,
        }

    def _build_profile(self, payload: dict[str, Any]) -> PolicyProfile:
        """把原始配置字典标准化为 ``PolicyProfile``。"""

        return PolicyProfile(
            name=str(payload.get('name') or 'document_analysis_default').strip() or 'document_analysis_default',
            version=str(payload.get('version') or 'v1').strip() or 'v1',
            organization_id=str(payload.get('organization_id')).strip() if str(payload.get('organization_id') or '').strip() else None,
            tenant_id=str(payload.get('tenant_id')).strip() if str(payload.get('tenant_id') or '').strip() else None,
            allowed_roles=tuple(str(item).strip().lower() for item in payload.get('allowed_roles', []) if str(item).strip()),
            match_keywords=tuple(str(item).strip() for item in payload.get('match_keywords', []) if str(item).strip()),
            require_evidence=bool(payload.get('require_evidence', True)),
            min_coverage=float(payload.get('min_coverage', 0.0) or 0.0),
            confidence_threshold=float(payload.get('confidence_threshold', 0.0) or 0.0),
            require_review_passed=bool(payload.get('require_review_passed', False)),
            max_plan_steps=int(payload.get('max_plan_steps', 16) or 16),
            max_open_questions=int(payload.get('max_open_questions', 10) or 10),
            allowed_output_formats=tuple(
                str(item).strip() for item in payload.get('allowed_output_formats', ['markdown', 'json', 'markdown+json']) if str(item).strip()
            ),
            blocked_tools=tuple(str(item).strip() for item in payload.get('blocked_tools', []) if str(item).strip()),
            denied_permissions=tuple(str(item).strip().lower() for item in payload.get('denied_permissions', []) if str(item).strip()),
            evaluation_baseline_order=tuple(
                str(item).strip()
                for item in payload.get('evaluation_baseline_order', ['benchmark', 'report', 'version', 'task'])
                if str(item).strip()
            ),
            evaluation_report_path=str(payload.get('evaluation_report_path')).strip()
            if str(payload.get('evaluation_report_path') or '').strip()
            else None,
        )


class PolicyProfileResolver:
    """为任务请求选择最匹配的策略 profile。"""

    def resolve_profile(
        self,
        request: TaskRequest,
        *,
        default_profile_name: str,
        profiles: dict[str, PolicyProfile],
    ) -> PolicyProfile:
        """按作用域与关键词匹配度选择最佳 profile。"""

        collection_name = request.collection_name.lower()
        instructions = request.instructions.lower()
        candidates: list[tuple[int, PolicyProfile]] = []
        default_profile = profiles.get(default_profile_name)
        for profile in profiles.values():
            if not self._profile_scope_matches(profile, request):
                continue
            specificity = self._profile_specificity(profile, request)
            keyword_score = sum(
                1 for keyword in profile.match_keywords if keyword.lower() in collection_name or keyword.lower() in instructions
            )
            if profile.name == default_profile_name:
                default_profile = profile
                candidates.append((specificity, profile))
                continue
            if keyword_score <= 0 and profile.match_keywords:
                continue
            candidates.append((specificity * 100 + keyword_score, profile))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]
        return default_profile or next(iter(profiles.values()))

    def _profile_scope_matches(self, profile: PolicyProfile, request: TaskRequest) -> bool:
        """判断 profile 的组织、租户和角色范围是否命中请求。"""

        if profile.organization_id and profile.organization_id != request.organization_id:
            return False
        if profile.tenant_id and profile.tenant_id != request.tenant_id:
            return False
        if profile.allowed_roles and (request.requester_role or '').strip().lower() not in profile.allowed_roles:
            return False
        return True

    def _profile_specificity(self, profile: PolicyProfile, request: TaskRequest) -> int:
        """计算 profile 相对请求的作用域特异性分数。"""

        score = 0
        if profile.organization_id and profile.organization_id == request.organization_id:
            score += 4
        if profile.tenant_id and profile.tenant_id == request.tenant_id:
            score += 3
        if profile.allowed_roles and (request.requester_role or '').strip().lower() in profile.allowed_roles:
            score += 2
        if profile.match_keywords:
            score += 1
        return score
