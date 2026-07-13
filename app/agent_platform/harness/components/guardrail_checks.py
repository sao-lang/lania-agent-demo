"""Guardrail 决策评估模块�?
集中实现输入、计划、工具调用、产物与输出五个阶段的安全与约束检查，向上�?统一返回 ``GuardrailDecision``，避免各阶段各自维护零散校验逻辑�?"""

from __future__ import annotations

from typing import Any

from app.agent_platform.agents.tools.registry import ToolRegistry
from app.agent_platform.harness.models import GuardrailDecision
from app.models.artifact import ReportArtifactContent, ReviewResult
from app.models.task import TaskPlan, TaskRequest
from app.rag_system.guardrails.input import inspect_prompt_injection
from app.rag_system.guardrails.output import redact_text
from app.services.state import InMemoryState


class GuardrailEvaluator:
    """执行任务链路各阶段的 guardrail 校验�?""

    def __init__(self, registry: ToolRegistry) -> None:
        """初始�?guardrail 评估器�?""

        self.registry = registry

    def validate_input(self, request: TaskRequest, state: InMemoryState) -> GuardrailDecision:
        """校验任务输入的集合、文档数、提示注入与敏感内容�?""

        if request.collection_name not in state.collections:
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='collection_not_found',
                reason='collection not found',
                details={'collection_name': request.collection_name},
            )
        if not request.doc_ids:
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='task_documents_required',
                reason='task requires at least one document',
                details={'collection_name': request.collection_name},
            )
        if len(request.doc_ids) > 64:
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='task_document_limit_exceeded',
                reason='task exceeds maximum supported document count',
                details={'doc_count': len(request.doc_ids), 'max_doc_count': 64},
            )
        if len(request.instructions.strip()) > 4000:
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='task_instructions_too_long',
                reason='task instructions exceed guardrail limit',
                details={'instruction_length': len(request.instructions.strip()), 'max_length': 4000},
            )
        injection = inspect_prompt_injection(request.instructions)
        if injection.get('blocked'):
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='task_prompt_injection_detected',
                reason='task instructions contain prompt injection or hidden instruction exfiltration patterns',
                details=injection,
            )
        input_safety = self._classify_input_safety(request.instructions)
        if input_safety['category'] == 'unsafe':
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='task_input_safety_rejected',
                reason='task instructions are rejected by input safety classification',
                details=input_safety,
            )
        sensitive_scan = self._scan_sensitive_content(request.instructions)
        if sensitive_scan.get('applied') and (
            'secret_key' in sensitive_scan.get('matched_types', []) or sensitive_scan.get('replacement_count', 0) >= 3
        ):
            return GuardrailDecision(
                allowed=False,
                stage='input',
                code='task_sensitive_input_detected',
                reason='task instructions contain sensitive content that must be removed before execution',
                details=sensitive_scan,
            )
        return GuardrailDecision(allowed=True, stage='input', code='ok', reason='input passed')

    def validate_plan(self, plan: TaskPlan) -> GuardrailDecision:
        """校验任务计划结构、工具注册情况与退出条件数量�?""

        if not plan.steps:
            return GuardrailDecision(
                allowed=False,
                stage='plan',
                code='plan_steps_required',
                reason='plan must contain at least one step',
            )
        if len(plan.steps) > plan.max_steps:
            return GuardrailDecision(
                allowed=False,
                stage='plan',
                code='plan_step_limit_exceeded',
                reason='plan step count exceeds max_steps',
                details={'step_count': len(plan.steps), 'max_steps': plan.max_steps},
            )
        step_ids: list[str] = []
        unknown_tools: list[str] = []
        for step in plan.steps:
            if step.step_id in step_ids:
                return GuardrailDecision(
                    allowed=False,
                    stage='plan',
                    code='plan_step_id_duplicated',
                    reason='plan contains duplicated step ids',
                    details={'step_id': step.step_id},
                )
            step_ids.append(step.step_id)
            for tool_name in step.candidate_tools:
                try:
                    self.registry.get(tool_name)
                except KeyError:
                    unknown_tools.append(tool_name)
        if unknown_tools:
            return GuardrailDecision(
                allowed=False,
                stage='plan',
                code='plan_unknown_tools',
                reason='plan references tools that are not registered',
                details={'unknown_tools': sorted(set(unknown_tools))},
            )
        if len(plan.exit_criteria) > 12:
            return GuardrailDecision(
                allowed=False,
                stage='plan',
                code='plan_exit_criteria_too_many',
                reason='plan exit criteria exceed guardrail limit',
                details={'exit_criteria_count': len(plan.exit_criteria), 'max_exit_criteria': 12},
            )
        return GuardrailDecision(allowed=True, stage='plan', code='ok', reason='plan passed')

    def validate_tool_call(
        self,
        tool_name: str,
        payload: dict[str, Any],
        allowed_tools: list[str] | None = None,
    ) -> GuardrailDecision:
        """校验单次工具调用是否超出注册范围与步骤白名单�?""

        try:
            self.registry.get(tool_name)
        except KeyError:
            return GuardrailDecision(
                allowed=False,
                stage='tool',
                code='tool_not_registered',
                reason='tool is not registered',
                details={'tool_name': tool_name},
            )
        if allowed_tools and tool_name not in allowed_tools:
            return GuardrailDecision(
                allowed=False,
                stage='tool',
                code='tool_not_allowed_for_step',
                reason='tool is not allowed in current step context',
                details={'tool_name': tool_name, 'allowed_tools': allowed_tools},
            )
        if len(payload) > 32:
            return GuardrailDecision(
                allowed=False,
                stage='tool',
                code='tool_payload_too_large',
                reason='tool payload contains too many top-level fields',
                details={'tool_name': tool_name, 'field_count': len(payload), 'max_field_count': 32},
            )
        return GuardrailDecision(allowed=True, stage='tool', code='ok', reason='tool call passed')

    def validate_artifact(self, artifact: ReportArtifactContent, *, stage: str = 'artifact') -> GuardrailDecision:
        """校验中间产物的摘要、证据绑定和脱敏状态�?""

        if not artifact.summary.strip():
            return GuardrailDecision(
                allowed=False,
                stage=stage,
                code='artifact_summary_required',
                reason='artifact summary is required',
            )
        evidence_ids = {item.citation_id for item in artifact.evidence}
        unsupported: list[str] = []
        for item in artifact.key_findings:
            if item.citation_ids and not set(item.citation_ids).issubset(evidence_ids):
                unsupported.append(item.finding_id)
        for risk in artifact.risks:
            if risk.citation_ids and not set(risk.citation_ids).issubset(evidence_ids):
                unsupported.append(risk.risk_id)
        if unsupported:
            return GuardrailDecision(
                allowed=False,
                stage=stage,
                code='artifact_unsupported_claims',
                reason='artifact contains claims without evidence binding',
                details={'unsupported_claims': unsupported},
            )
        if artifact.report_markdown is None or artifact.report_json is None:
            return GuardrailDecision(
                allowed=False,
                stage=stage,
                code='artifact_format_incomplete',
                reason='artifact is missing markdown or json rendering',
            )
        sensitive_scan = self._scan_sensitive_content(self._artifact_text(artifact))
        if sensitive_scan.get('applied'):
            return GuardrailDecision(
                allowed=False,
                stage=stage,
                code=f'{stage}_sensitive_content_detected',
                reason='artifact contains sensitive content that should be redacted before output',
                details=sensitive_scan,
            )
        return GuardrailDecision(allowed=True, stage=stage, code='ok', reason='artifact passed')

    def validate_output(
        self,
        result: ReportArtifactContent,
        *,
        review: ReviewResult | None,
        output_format: str,
    ) -> GuardrailDecision:
        """校验最终输出是否满足发布格式与审查要求�?""

        artifact_decision = self.validate_artifact(result, stage='output')
        if not artifact_decision.allowed:
            return artifact_decision
        report_json = result.report_json or {}
        fallback_disclosed = bool(report_json.get('fallback_reason') if isinstance(report_json, dict) else None) or any(
            keyword in f'{result.summary}\n{result.report_markdown or ""}'
            for keyword in ['降级', '证据不足', '待确�?]
        )
        if review is not None and review.unsupported_claims:
            if fallback_disclosed:
                return GuardrailDecision(
                    allowed=True,
                    stage='output',
                    code='output_fallback_disclosed',
                    reason='final output keeps unsupported claims disclosed under fallback mode',
                    details={'unsupported_claims': review.unsupported_claims},
                )
            return GuardrailDecision(
                allowed=False,
                stage='output',
                code='output_contains_unsupported_claims',
                reason='final output still contains unsupported claims',
                details={'unsupported_claims': review.unsupported_claims},
            )
        if review is not None and review.missing_sections:
            if fallback_disclosed:
                return GuardrailDecision(
                    allowed=True,
                    stage='output',
                    code='output_fallback_disclosed',
                    reason='final output keeps missing sections disclosed under fallback mode',
                    details={'missing_sections': review.missing_sections},
                )
            return GuardrailDecision(
                allowed=False,
                stage='output',
                code='output_missing_sections',
                reason='final output still misses reviewed sections',
                details={'missing_sections': review.missing_sections},
            )
        if 'markdown' in output_format and result.report_markdown is None:
            return GuardrailDecision(
                allowed=False,
                stage='output',
                code='output_markdown_required',
                reason='final output requires markdown',
            )
        if 'json' in output_format and result.report_json is None:
            return GuardrailDecision(
                allowed=False,
                stage='output',
                code='output_json_required',
                reason='final output requires json',
            )
        sensitive_scan = self._scan_sensitive_content(self._artifact_text(result))
        if sensitive_scan.get('applied'):
            return GuardrailDecision(
                allowed=False,
                stage='output',
                code='output_sensitive_content_detected',
                reason='final output contains sensitive content that should be redacted before delivery',
                details=sensitive_scan,
            )
        return GuardrailDecision(allowed=True, stage='output', code='ok', reason='output passed')

    def _artifact_text(self, artifact: ReportArtifactContent) -> str:
        """把产物聚合为单段文本，便于统一做敏感内容扫描�?""

        units = [
            artifact.summary,
            artifact.report_markdown or '',
            *[item.title for item in artifact.key_findings],
            *[item.summary for item in artifact.key_findings],
            *[item.title for item in artifact.risks],
            *[item.description for item in artifact.risks],
            *[item.text for item in artifact.evidence],
            *artifact.open_questions,
        ]
        if artifact.report_json is not None:
            units.append(str(artifact.report_json))
        return '\n'.join(unit for unit in units if str(unit).strip())

    def _scan_sensitive_content(self, text: str) -> dict[str, Any]:
        """调用脱敏工具扫描文本中的敏感信息�?""

        _, summary = redact_text(text or '')
        return summary

    def _classify_input_safety(self, text: str) -> dict[str, Any]:
        """基于规则把输入粗分为 safe/sensitive/unsafe�?""

        normalized = (text or '').strip().lower()
        unsafe_patterns = {
            'permission_bypass': ('绕过权限', 'bypass permission', 'ignore permission'),
            'destructive_request': ('删除全部', 'drop table', 'delete all documents'),
            'secret_export': ('导出密钥', 'export secret', 'print api key'),
        }
        for label, patterns in unsafe_patterns.items():
            if any(pattern in normalized for pattern in patterns):
                return {'category': 'unsafe', 'label': label}
        if any(pattern in normalized for pattern in ('身份�?, '手机�?, 'email', 'phone', 'secret', 'token')):
            return {'category': 'sensitive', 'label': 'pii_or_secret'}
        return {'category': 'safe', 'label': 'clean'}
