"""Policy Harness 实现。

负责把任务约束从隐式经验规则抽成显式策略判断，并支持从外部 yaml 配置加载策略画像。
在 phase3 中，PolicyEngine 保留兼容 facade，内部拆分 profile store、resolver、evaluator。
"""

from __future__ import annotations
from typing import Any

from app.core.config import Settings
from app.harness.components.policy_checks import PolicyEvaluator
from app.harness.components.policy_profiles import PolicyProfile, PolicyProfileResolver, PolicyProfileStore
from app.harness.models import PolicyDecision
from app.models.artifact import ReportArtifactContent, ReviewResult
from app.models.task import TaskPlan, TaskRequest
from app.services.sqlite_store import SQLiteStateStore


class PolicyEngine:
    """面向文档分析任务的最小策略引擎。"""

    def __init__(
        self,
        settings: Settings | None = None,
        config_path=None,
        persistence: SQLiteStateStore | None = None,
    ) -> None:
        """初始化策略引擎及其 profile 存储、解析和评估组件。"""

        self.settings = settings
        self.persistence = persistence
        self.profile_store = PolicyProfileStore(settings=settings, config_path=config_path, persistence=persistence)
        self.profile_resolver = PolicyProfileResolver()
        self.policy_evaluator = PolicyEvaluator()
        self.config_path = self.profile_store.config_path
        self._config_mtime_ns: int | None = None
        self._db_signature: tuple[int, tuple[str, ...]] | None = None
        self.default_profile_name, self.profiles = self.profile_store.load_profiles()
        self._config_mtime_ns = self.profile_store.read_config_mtime_ns()
        self._db_signature = self.profile_store.read_db_signature()

    def resolve_profile(self, request: TaskRequest) -> PolicyProfile:
        """为当前请求解析最匹配的策略 profile。"""

        self.reload_if_needed()
        return self.profile_resolver.resolve_profile(
            request,
            default_profile_name=self.default_profile_name,
            profiles=self.profiles,
        )

    def get_profile(self, name: str) -> PolicyProfile | None:
        """按名称读取指定策略 profile。"""

        self.reload_if_needed()
        return self.profiles.get(name)

    def list_profiles(self) -> list[PolicyProfile]:
        """返回当前已加载的全部策略 profile。"""

        self.reload_if_needed()
        return list(self.profiles.values())

    def reload_if_needed(self) -> bool:
        """当配置文件或数据库签名变化时重新加载 profile。"""

        current_mtime_ns = self.profile_store.read_config_mtime_ns()
        current_db_signature = self.profile_store.read_db_signature()
        if current_mtime_ns == self._config_mtime_ns and current_db_signature == self._db_signature:
            return False
        self.default_profile_name, self.profiles = self.profile_store.load_profiles()
        self._config_mtime_ns = current_mtime_ns
        self._db_signature = current_db_signature
        return True

    def check_task(self, request: TaskRequest) -> PolicyDecision:
        """校验任务请求是否符合命中策略。"""

        profile = self.resolve_profile(request)
        return self.policy_evaluator.check_task(request, profile)

    def check_plan(self, request: TaskRequest, plan: TaskPlan) -> PolicyDecision:
        """校验任务计划是否符合命中策略。"""

        profile = self.resolve_profile(request)
        return self.policy_evaluator.check_plan(request, plan, profile)

    def check_tool(self, request: TaskRequest, tool_name: str, payload: dict[str, Any]) -> PolicyDecision:
        """校验单次工具调用是否符合命中策略。"""

        profile = self.resolve_profile(request)
        return self.policy_evaluator.check_tool(request, tool_name, payload, profile)

    def check_artifact(
        self,
        request: TaskRequest,
        artifact: ReportArtifactContent,
        *,
        coverage_score: float = 0.0,
        review: ReviewResult | None = None,
    ) -> PolicyDecision:
        """校验产物是否符合命中策略。"""

        profile = self.resolve_profile(request)
        return self.policy_evaluator.check_artifact(
            request,
            artifact,
            profile,
            coverage_score=coverage_score,
            review=review,
        )

    def check_output(
        self,
        request: TaskRequest,
        result: ReportArtifactContent,
        *,
        coverage_score: float = 0.0,
        review: ReviewResult | None = None,
    ) -> PolicyDecision:
        """兼容输出校验入口，当前复用产物校验规则。"""

        return self.check_artifact(request, result, coverage_score=coverage_score, review=review)
