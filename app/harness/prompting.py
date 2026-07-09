"""Prompt Runtime / PromptBuilder 实现。

负责收口核心 prompt，建立统一渲染入口和版本管理能力，支持与 regression eval 打通。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.harness.prompt_registry import PromptVersionRegistry
from app.harness.grounding import GroundingBundle
from app.harness.models import ContextBundle
from app.harness.policy import PolicyProfile
from app.models.artifact import EvidencePack, ReportArtifactContent


class PromptTemplate(BaseModel):
    """定义一个可复用的提示词模板。"""

    template_id: str
    version: str = 'v1'
    step_type: str
    output_schema: str = ''
    content: str
    experimental: bool = False
    tags: list[str] = Field(default_factory=list)


class PromptRenderResult(BaseModel):
    """提示词渲染结果。"""

    prompt: str
    template_id: str
    version: str
    step_type: str
    token_count: int = 0
    experimental: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptBuilder:
    """统一的提示词构建器。

    主要职责：
    - 模板管理
    - prompt 渲染
    - 版本号管理
    - 输出 schema 约束注入
    - 与 policy / context / grounding 统一拼装
    """

    def __init__(
        self,
        version_registry: PromptVersionRegistry | None = None,
    ) -> None:
        """初始化模板仓库并注册默认模板。

        Args:
            version_registry: 可选的 prompt 版本注册表；
                提供时将同步注册默认模板到版本管理。
        """

        self.templates: dict[str, dict[str, PromptTemplate]] = {}
        self.version_registry = version_registry or PromptVersionRegistry()
        self._register_default_templates()

    def _register_default_templates(self) -> None:
        """注册默认提示词模板。"""
        templates = [
            PromptTemplate(
                template_id='extract_key_points',
                version='v1',
                step_type='analyze',
                output_schema='{"summary": str, "key_findings": [{"title": str, "summary": str, "citation_ids": [str], "tags": [str]}], "open_questions": [str], "confidence": float}',
                content='你是 Document Analysis Agent 的分析模块。请只输出严格 JSON，不要输出额外解释、Markdown 或代码块。\n'
                        '输出 schema: {schema}\n'
                        '规则:\n'
                        '1. 只能输出有证据支撑的结论。\n'
                        '2. citation_ids 必须来自给定证据编号。\n'
                        '3. summary 需覆盖任务目标、主要发现和证据缺口。\n'
                        '4. confidence 必须在 0 到 1 之间。\n'
                        '用户任务: {objective}\n'
                        '文档上下文:\n{documents}\n'
                        '证据列表:\n{evidence}\n'
                        '证据缺口: {missing_aspects}',
            ),
            PromptTemplate(
                template_id='extract_risks',
                version='v1',
                step_type='analyze',
                output_schema='{"risks": [{"title": str, "description": str, "severity": "low|medium|high", "citation_ids": [str], "recommendation": str}]}',
                content='你是 Document Analysis Agent 的风险抽取模块。请只输出严格 JSON，不要输出额外解释。\n'
                        '输出 schema: {schema}\n'
                        '规则:\n'
                        '1. 只保留高价值、可行动的风险。\n'
                        '2. citation_ids 必须来自给定证据编号。\n'
                        '3. severity 只能是 low/medium/high。\n'
                        '审查目标: {objective}\n'
                        '证据:\n{evidence}\n'
                        '证据缺口: {missing_aspects}',
            ),
            PromptTemplate(
                template_id='draft_report',
                version='v1',
                step_type='draft_artifact',
                output_schema='{"summary": str, "key_findings": [dict], "risks": [dict], "open_questions": [str], "confidence": float, "report_markdown": str, "report_json": dict}',
                content='你是 Document Analysis Agent 的报告生成模块。请只输出严格 JSON，不要输出额外解释。\n'
                        '输出 schema: {schema}\n'
                        '规则:\n'
                        '1. 报告必须基于提供的分析结果和证据。\n'
                        '2. 所有结论必须有证据支撑。\n'
                        '3. report_markdown 必须包含完整的报告内容。\n'
                        '4. report_json 必须包含结构化数据。\n'
                        '分析摘要: {summary}\n'
                        '关键发现: {key_findings}\n'
                        '风险项: {risks}\n'
                        '证据: {evidence}\n'
                        '开放问题: {open_questions}\n'
                        '置信度: {confidence}',
            ),
            PromptTemplate(
                template_id='review_report',
                version='v1',
                step_type='review_artifact',
                output_schema='{"passed": bool, "unsupported_claims": [str], "missing_sections": [str], "review_notes": [str]}',
                content='你是 Document Analysis Agent 的质量审查模块。请只输出严格 JSON，不要输出额外解释。\n'
                        '输出 schema: {schema}\n'
                        '规则:\n'
                        '1. unsupported_claims 必须填写 finding_id 或 risk_id。\n'
                        '2. missing_sections 只能填写缺失字段名。\n'
                        '3. review_notes 只写高价值审查意见。\n'
                        '4. 检查所有结论是否有证据支撑。\n'
                        '报告摘要: {summary}\n'
                        '关键发现: {key_findings}\n'
                        '风险项: {risks}\n'
                        '证据编号: {evidence_ids}\n'
                        '开放问题: {open_questions}\n'
                        '对齐分数: {alignment_score}',
            ),
            PromptTemplate(
                template_id='finalize_report',
                version='v1',
                step_type='finalize',
                output_schema='{"summary": str, "content": str, "format": str}',
                content='你是 Document Analysis Agent 的最终交付模块。\n'
                        '请将草稿转换为最终格式输出。\n'
                        '输出格式: {output_format}\n'
                        '草稿内容: {content}\n'
                        '审查结果: {review}',
            ),
        ]

        for template in templates:
            key = template.template_id
            if key not in self.templates:
                self.templates[key] = {}
            self.templates[key][template.version] = template

    def register_template(self, template: PromptTemplate) -> None:
        """注册一个新的提示词模板。"""
        key = template.template_id
        if key not in self.templates:
            self.templates[key] = {}
        self.templates[key][template.version] = template

    def get_template(self, template_id: str, version: str = 'v1') -> PromptTemplate | None:
        """获取指定版本的模板。"""
        if template_id not in self.templates:
            return None
        return self.templates[template_id].get(version)

    def list_templates(self, step_type: str | None = None) -> list[PromptTemplate]:
        """列出所有模板，可按步骤类型过滤。"""
        all_templates = []
        for versions in self.templates.values():
            all_templates.extend(versions.values())
        if step_type:
            return [t for t in all_templates if t.step_type == step_type]
        return all_templates

    def render(
        self,
        step: str,
        context: ContextBundle | None = None,
        grounding: GroundingBundle | None = None,
        policy: PolicyProfile | None = None,
        **kwargs: Any,
    ) -> PromptRenderResult:
        """渲染提示词。

        Args:
            step: 步骤类型，如 'analyze', 'draft_artifact', 'review_artifact', 'finalize'
            context: 上下文 bundle
            grounding: grounding bundle
            policy: 策略配置
            **kwargs: 额外参数

        Returns:
            渲染后的提示词结果
        """
        template_id = self._step_to_template_id(step)
        template = self.get_template(template_id)
        
        if template is None:
            template = PromptTemplate(
                template_id=template_id,
                version='v1',
                step_type=step,
                output_schema='{}',
                content='{objective}\n{evidence}',
            )

        template_params = self._build_template_params(template, context, grounding, policy, kwargs)
        rendered_content = template.content.format(**template_params)
        
        token_count = self._estimate_tokens(rendered_content)

        return PromptRenderResult(
            prompt=rendered_content,
            template_id=template.template_id,
            version=template.version,
            step_type=template.step_type,
            token_count=token_count,
            experimental=template.experimental,
            metadata={
                'template_id': template.template_id,
                'version': template.version,
                'step_type': template.step_type,
                'alignment_score': kwargs.get('alignment_score', 0.0),
                'unsupported_claim_count': len(grounding.unsupported_claims) if grounding else 0,
                'coverage_gap_count': len(grounding.coverage_gaps) if grounding else 0,
            },
        )

    def _step_to_template_id(self, step: str) -> str:
        """将步骤类型映射到模板 ID。"""
        step_map = {
            'analyze': 'extract_key_points',
            'extract_risks': 'extract_risks',
            'draft_artifact': 'draft_report',
            'review_artifact': 'review_report',
            'finalize': 'finalize_report',
        }
        return step_map.get(step, step)

    def _build_template_params(
        self,
        template: PromptTemplate,
        context: ContextBundle | None = None,
        grounding: GroundingBundle | None = None,
        policy: PolicyProfile | None = None,
        extra_params: dict[str, Any] = {},
    ) -> dict[str, str]:
        """构建模板参数字典。"""
        params: dict[str, str] = {
            'schema': template.output_schema,
            'objective': context.objective if context else '',
            'step_id': context.step_id if context else '',
            'summary': '',
            'key_findings': '[]',
            'risks': '[]',
            'confidence': '0.0',
            'open_questions': '',
            'evidence_ids': '',
            'alignment_score': '0.0',
            'unsupported_claims': '',
            'coverage_gaps': '',
            'content': '',
            'review': '',
            'output_format': '',
        }

        if context:
            params['evidence'] = self._format_evidence(context.evidence_slice)
            params['documents'] = self._format_documents(context.state_slice.get('document_context_documents', []))
            params['missing_aspects'] = self._format_missing_aspects(context)
            
            memory_slice = context.memory_slice or {}
            params['open_questions'] = ', '.join(memory_slice.get('missing_aspects', []))
            
            if context.artifact_slice:
                params['summary'] = str(context.artifact_slice.get('summary') or '')
                params['key_findings'] = str(context.artifact_slice.get('key_findings') or [])
                params['risks'] = str(context.artifact_slice.get('risks') or [])
                params['confidence'] = str(context.artifact_slice.get('confidence') or 0.0)

        if grounding:
            params['alignment_score'] = str(grounding.alignment_score if hasattr(grounding, 'alignment_score') else 0.0)
            params['unsupported_claims'] = ', '.join(grounding.unsupported_claims)
            params['coverage_gaps'] = ', '.join(grounding.coverage_gaps)
            
            evidence_ids = set()
            for claim in getattr(grounding, 'claims', []):
                evidence_ids.update(claim.citation_ids)
            params['evidence_ids'] = ', '.join(sorted(evidence_ids))

        if policy:
            params['policy'] = str(policy)
            params['evidence_top_k'] = str(policy.evidence_top_k if hasattr(policy, 'evidence_top_k') else 5)

        params.update(extra_params)

        for key, value in params.items():
            if value is None:
                params[key] = ''
            elif not isinstance(value, str):
                params[key] = str(value)

        return params

    def _format_evidence(self, evidence_slice: list[dict]) -> str:
        """格式化证据列表。"""
        if not evidence_slice:
            return '- 无'
        return '\n'.join(
            f'- {item.get("citation_id", "unknown")}: {item.get("text", "")[:280]}'
            for item in evidence_slice[:10]
        )

    def _format_documents(self, documents: list[dict]) -> str:
        """格式化文档上下文。"""
        if not documents:
            return '- 无'
        return '\n'.join(
            f'- {item.get("title", "unknown")} | summary: {item.get("summary", "")[:100]}'
            for item in documents[:4]
        )

    def _format_missing_aspects(self, context: ContextBundle) -> str:
        """格式化缺失证据维度。"""
        missing_aspects = context.memory_slice.get('missing_aspects', []) if context.memory_slice else []
        return ', '.join(missing_aspects) if missing_aspects else '无'

    def _estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数量。"""
        return len(text) // 4

    def render_for_evidence_pack(self, step: str, evidence_pack: EvidencePack, **kwargs) -> PromptRenderResult:
        """为证据包渲染提示词。"""
        context = ContextBundle(
            step_id=step,
            objective=kwargs.get('instructions', ''),
            evidence_slice=[item.model_dump(mode='json') for item in evidence_pack.evidence_items],
            memory_slice={
                'missing_aspects': list(evidence_pack.missing_aspects),
                'coverage_score': float(evidence_pack.coverage_score),
            },
        )
        return self.render(step, context=context, **kwargs)

    def render_for_draft(self, step: str, draft_content: ReportArtifactContent, **kwargs) -> PromptRenderResult:
        """为草稿内容渲染提示词。"""
        context = ContextBundle(
            step_id=step,
            objective=kwargs.get('instructions', ''),
            artifact_slice={
                'summary': draft_content.summary,
                'key_findings': [item.model_dump(mode='json') for item in draft_content.key_findings],
                'risks': [item.model_dump(mode='json') for item in draft_content.risks],
                'open_questions': draft_content.open_questions,
                'confidence': draft_content.confidence,
            },
            evidence_slice=[item.model_dump(mode='json') for item in draft_content.evidence],
        )
        return self.render(step, context=context, **kwargs)
