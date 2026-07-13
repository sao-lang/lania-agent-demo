"""策略决策评估模块�?
根据解析出的策略 profile，对任务请求、计划、工具调用和最终产物逐层执行
约束检查，并统一返回 ``PolicyDecision`` 结果�?"""

from __future__ import annotations

from typing import Any

from app.agent_platform.harness.components.policy_profiles import PolicyProfile
from app.agent_platform.harness.models import PolicyDecision
from app.models.artifact import ReportArtifactContent, ReviewResult
from app.models.task import TaskPlan, TaskRequest


class PolicyEvaluator:
    """对任务链路执行策略规则检查�?""

    def check_task(self, request: TaskRequest, profile: PolicyProfile) -> PolicyDecision:
        """校验任务请求是否满足角色、输出格式与步数限制�?""

        if profile.allowed_roles and (request.requester_role or '').strip().lower() not in profile.allowed_roles:
            return PolicyDecision(
                allowed=False,
                stage='task',
                policy_name=profile.name,
                reason='task requester role is not allowed by policy',
                details={'requester_role': request.requester_role, 'allowed_roles': list(profile.allowed_roles)},
            )
        if request.output_format not in profile.allowed_output_formats:
            return PolicyDecision(
                allowed=False,
                stage='task',
                policy_name=profile.name,
                reason='task output format is not allowed by policy',
                details={'output_format': request.output_format, 'allowed_output_formats': list(profile.allowed_output_formats)},
            )
        if request.constraints.max_steps > profile.max_plan_steps:
            return PolicyDecision(
                allowed=False,
                stage='task',
                policy_name=profile.name,
                reason='task max_steps exceeds policy limit',
                details={'max_steps': request.constraints.max_steps, 'policy_max_plan_steps': profile.max_plan_steps},
            )
        return PolicyDecision(allowed=True, stage='task', policy_name=profile.name, reason='task passed')

    def check_plan(self, request: TaskRequest, plan: TaskPlan, profile: PolicyProfile) -> PolicyDecision:
        """校验任务计划是否突破步数或工具限制�?""

        if plan.max_steps > profile.max_plan_steps:
            return PolicyDecision(
                allowed=False,
                stage='plan',
                policy_name=profile.name,
                reason='plan max_steps exceeds policy limit',
                details={'plan_max_steps': plan.max_steps, 'policy_max_plan_steps': profile.max_plan_steps},
            )
        for step in plan.steps:
            blocked = [tool_name for tool_name in step.candidate_tools if tool_name in profile.blocked_tools]
            if blocked:
                return PolicyDecision(
                    allowed=False,
                    stage='plan',
                    policy_name=profile.name,
                    reason='plan contains tools blocked by policy',
                    details={'step_id': step.step_id, 'blocked_tools': blocked},
                )
        return PolicyDecision(allowed=True, stage='plan', policy_name=profile.name, reason='plan passed')

    def check_tool(self, request: TaskRequest, tool_name: str, payload: dict[str, Any], profile: PolicyProfile) -> PolicyDecision:
        """校验单次工具调用是否触碰策略禁用项�?""

        if tool_name in profile.blocked_tools:
            return PolicyDecision(
                allowed=False,
                stage='tool',
                policy_name=profile.name,
                reason='tool is blocked by policy',
                details={'tool_name': tool_name},
            )
        denied_permissions = set(profile.denied_permissions)
        granted_permissions = set(request.allowed_permissions)
        if denied_permissions and granted_permissions.intersection(denied_permissions):
            return PolicyDecision(
                allowed=False,
                stage='tool',
                policy_name=profile.name,
                reason='task permissions conflict with current policy',
                details={
                    'tool_name': tool_name,
                    'denied_permissions': sorted(denied_permissions),
                    'granted_permissions': sorted(granted_permissions),
                },
            )
        if profile.require_evidence and tool_name == 'finalize_report':
            content = payload.get('content') or {}
            evidence = content.get('evidence') if isinstance(content, dict) else None
            if not evidence:
                return PolicyDecision(
                    allowed=False,
                    stage='tool',
                    policy_name=profile.name,
                    reason='finalize_report requires evidence under current policy',
                    details={'tool_name': tool_name},
                )
        return PolicyDecision(allowed=True, stage='tool', policy_name=profile.name, reason='tool passed')

    def check_artifact(
        self,
        request: TaskRequest,
        artifact: ReportArtifactContent,
        profile: PolicyProfile,
        *,
        coverage_score: float = 0.0,
        review: ReviewResult | None = None,
    ) -> PolicyDecision:
        """校验产物的证据覆盖、置信度与审查结果�?""

        if profile.require_evidence and artifact.key_findings and not artifact.evidence:
            return PolicyDecision(
                allowed=False,
                stage='artifact',
                policy_name=profile.name,
                reason='artifact findings require evidence under current policy',
            )
        if coverage_score < profile.min_coverage:
            return PolicyDecision(
                allowed=False,
                stage='artifact',
                policy_name=profile.name,
                reason='artifact evidence coverage is below policy threshold',
                details={'coverage_score': coverage_score, 'min_coverage': profile.min_coverage},
            )
        if artifact.confidence < profile.confidence_threshold:
            return PolicyDecision(
                allowed=False,
                stage='artifact',
                policy_name=profile.name,
                reason='artifact confidence is below policy threshold',
                details={'confidence': artifact.confidence, 'confidence_threshold': profile.confidence_threshold},
            )
        if len(artifact.open_questions) > profile.max_open_questions:
            return PolicyDecision(
                allowed=False,
                stage='artifact',
                policy_name=profile.name,
                reason='artifact open question count exceeds policy threshold',
                details={'open_question_count': len(artifact.open_questions), 'max_open_questions': profile.max_open_questions},
            )
        if profile.require_review_passed and review is not None and not review.passed:
            return PolicyDecision(
                allowed=False,
                stage='artifact',
                policy_name=profile.name,
                reason='artifact requires passed review under current policy',
            )
        return PolicyDecision(allowed=True, stage='artifact', policy_name=profile.name, reason='artifact passed')
