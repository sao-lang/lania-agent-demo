"""受控子代理模块。

负责定义文档分析任务可用的受控子 Agent、静态 schema、注册表和运行时。该模块位于任务
工作流与工具层之间，用统一白名单约束子 Agent 的能力边界，并补充可追踪的执行摘要。
"""

from __future__ import annotations

from typing import Any, Callable, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from app.agents.memory import TaskMemory
from app.capabilities.api_contract import ApiContractDocument, ApiContractOperationMatch, ApiContractReadResult
from app.models.artifact import EvidenceItem, EvidencePack, FindingItem, ReportArtifactContent, ReviewResult, RiskItem
from app.rag.observability import TraceRecorder


class EvidenceCollectionInput(BaseModel):
    """Evidence Agent 首次收集证据的输入。"""

    task_id: str
    query: str
    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    focus_aspects: list[str] = Field(default_factory=list)


class EvidenceCollectionResult(BaseModel):
    """Evidence Agent 首次收集证据的输出。"""

    evidence_pack: EvidencePack
    selected_tools: list[str] = Field(default_factory=list)
    decision: Literal['continue', 'replan'] = 'continue'


class EvidenceSupplementInput(BaseModel):
    """Evidence Agent 补证据输入。"""

    task_id: str
    query: str
    collection_name: str
    doc_ids: list[str] = Field(default_factory=list)
    top_k: int = Field(default=6, ge=1, le=20)
    missing_aspects: list[str] = Field(default_factory=list)
    evidence_pack: EvidencePack


class EvidenceSupplementResult(BaseModel):
    """Evidence Agent 补证据输出。"""

    evidence_pack: EvidencePack
    selected_tools: list[str] = Field(default_factory=list)


class ReviewDraftInput(BaseModel):
    """Review Agent 审查报告输入。"""

    task_id: str
    content: ReportArtifactContent


class ReviewDraftResult(BaseModel):
    """Review Agent 审查报告输出。"""

    review: ReviewResult
    selected_tools: list[str] = Field(default_factory=list)
    decision: Literal['finalize', 'revise'] = 'finalize'


class ReviseDraftInput(BaseModel):
    """Review Agent 修订草稿输入。"""

    task_id: str
    draft_content: ReportArtifactContent
    review: ReviewResult


class ReviseDraftResult(BaseModel):
    """Review Agent 修订草稿输出。"""

    content: ReportArtifactContent
    selected_tools: list[str] = Field(default_factory=list)


class DraftArtifactInput(BaseModel):
    """Reporting Agent 生成草稿输入。"""

    task_id: str
    summary: str
    key_findings: list[FindingItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class DraftArtifactResult(BaseModel):
    """Reporting Agent 生成草稿输出。"""

    content: ReportArtifactContent
    selected_tools: list[str] = Field(default_factory=list)


class ContractDiscoverInput(BaseModel):
    """Contract Agent 发现 API contract 输入。"""

    task_id: str
    query: str | None = None
    path_prefix: str = '.'
    max_results: int = Field(default=20, ge=1, le=200)


class ContractDiscoverResult(BaseModel):
    """Contract Agent 发现 API contract 输出。"""

    contracts: list[ApiContractDocument] = Field(default_factory=list)
    operation_matches: list[ApiContractOperationMatch] = Field(default_factory=list)
    selected_tools: list[str] = Field(default_factory=list)
    decision: Literal['inspect_contract', 'no_match'] = 'inspect_contract'


class ContractInspectInput(BaseModel):
    """Contract Agent 读取 API contract 输入。"""

    task_id: str
    path: str
    method: str | None = None
    endpoint_path: str | None = None


class ContractInspectResult(BaseModel):
    """Contract Agent 读取 API contract 输出。"""

    contract: ApiContractReadResult
    selected_tools: list[str] = Field(default_factory=list)


ToolRunner = Callable[[str, dict[str, Any]], Any]
EvidenceMerger = Callable[[EvidencePack, EvidencePack], EvidencePack]


class SubAgentActionSchema(BaseModel):
    """描述子代理允许执行的单个动作。"""

    action: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_tools: list[str] = Field(default_factory=list)


class SubAgentSchema(BaseModel):
    """描述受控子代理的静态能力边界。"""

    name: str
    version: str = 'v1'
    description: str
    allowed_tools: list[str] = Field(default_factory=list)
    actions: list[SubAgentActionSchema] = Field(default_factory=list)
    trace_fields: list[str] = Field(default_factory=list)


class SubAgentHandoff(BaseModel):
    """描述一次受控子代理 handoff 契约。"""

    handoff_id: str = Field(default_factory=lambda: f'handoff-{uuid4().hex[:12]}')
    source_step_id: str
    context_keys: list[str] = Field(default_factory=list)
    step_limit: int = Field(default=1, ge=1, le=8)
    budget_limit: int = Field(default=1, ge=1, le=32)
    sandbox_profile: Literal['inline', 'thread_isolated', 'restricted'] = 'inline'


class RegisteredSubAgent(Protocol):
    """子代理注册表依赖的最小协议。"""

    name: str

    def describe(self) -> SubAgentSchema:
        """返回子代理的静态能力描述。"""
        ...

    def execute(self, action: str, payload: BaseModel, **kwargs: Any) -> BaseModel:
        """按动作名称执行一次子代理能力。"""
        ...


class ControlledSubAgent:
    """受控子代理基类，约束可用工具并记录执行摘要。"""

    name = 'controlled_sub_agent'
    version = 'v1'
    description = '受控子代理'
    allowed_tools: tuple[str, ...] = ()
    trace_fields = ['task_id', 'agent_name', 'action', 'allowed_tools', 'selected_tools']

    def __init__(self, memory: TaskMemory, trace: TraceRecorder) -> None:
        """初始化受控子代理基类。

        Args:
            memory: 任务记忆服务，用于记录子 Agent 运行摘要。
            trace: 链路追踪记录器。
        """
        self.memory = memory
        self.trace = trace

    def _ensure_allowed(self, tool_name: str) -> None:
        """校验工具是否在当前子代理白名单内。

        Args:
            tool_name: 待调用工具名称。

        Raises:
            RuntimeError: 当工具不在白名单中时抛出。
        """
        if tool_name not in self.allowed_tools:
            raise RuntimeError(f'sub agent {self.name} is not allowed to use tool {tool_name}')

    def _run_tool(self, tool_name: str, payload: dict[str, Any], runner: ToolRunner) -> Any:
        """在白名单校验后执行工具。

        Args:
            tool_name: 工具名称。
            payload: 工具输入载荷。
            runner: 实际工具执行器。

        Returns:
            工具返回结果。
        """
        self._ensure_allowed(tool_name)
        return runner(tool_name, payload)

    def _record_run(
        self,
        task_id: str,
        action: str,
        selected_tools: list[str],
        input_summary: dict[str, Any],
        output_summary: dict[str, Any],
        handoff: SubAgentHandoff | None = None,
    ) -> None:
        """记录子代理执行摘要到任务记忆和 trace。"""

        self.memory.record_sub_agent_run(
            task_id,
            self.name,
            action,
            handoff_id=handoff.handoff_id if handoff is not None else None,
            source_step_id=handoff.source_step_id if handoff is not None else None,
            context_keys=list(handoff.context_keys) if handoff is not None else [],
            step_limit=handoff.step_limit if handoff is not None else None,
            budget_limit=handoff.budget_limit if handoff is not None else None,
            sandbox_profile=handoff.sandbox_profile if handoff is not None else None,
            allowed_tools=list(self.allowed_tools),
            selected_tools=selected_tools,
            input_summary=input_summary,
            output_summary=output_summary,
        )
        self.trace.record(
            'task_sub_agent_completed',
            {
                'task_id': task_id,
                'agent_name': self.name,
                'action': action,
                'handoff_id': handoff.handoff_id if handoff is not None else None,
                'source_step_id': handoff.source_step_id if handoff is not None else None,
                'context_keys': list(handoff.context_keys) if handoff is not None else [],
                'step_limit': handoff.step_limit if handoff is not None else None,
                'budget_limit': handoff.budget_limit if handoff is not None else None,
                'sandbox_profile': handoff.sandbox_profile if handoff is not None else None,
                'allowed_tools': list(self.allowed_tools),
                'selected_tools': selected_tools,
                'input_summary': input_summary,
                'output_summary': output_summary,
            },
        )

    def describe(self) -> SubAgentSchema:
        """返回子代理静态 schema。"""

        return SubAgentSchema(
            name=self.name,
            version=self.version,
            description=self.description,
            allowed_tools=list(self.allowed_tools),
            actions=self._build_action_schemas(),
            trace_fields=list(self.trace_fields),
        )

    def _build_action_schemas(self) -> list[SubAgentActionSchema]:
        """返回当前子代理支持的动作 schema 列表。"""
        return []


class EvidenceAgent(ControlledSubAgent):
    """负责收集与补充 EvidencePack 的受控子代理。"""

    name = 'evidence_agent'
    description = '在白名单检索工具内收集证据、评估覆盖度并执行补证据。'
    allowed_tools = ('rag_retrieve_evidence', 'rag_retrieve_graph_evidence')

    def collect(
        self,
        payload: EvidenceCollectionInput,
        *,
        runner: ToolRunner,
        merge_packs: EvidenceMerger,
        handoff: SubAgentHandoff | None = None,
    ) -> EvidenceCollectionResult:
        """执行首轮证据收集，并在必要时补做图谱证据检索。

        Args:
            payload: 首轮证据收集输入。
            runner: 工具执行器。
            merge_packs: 证据包合并函数。

        Returns:
            收集完成后的证据结果。
        """
        selected_tools = ['rag_retrieve_evidence']
        primary_pack = self._run_tool(
            'rag_retrieve_evidence',
            {
                'query': payload.query,
                'collection_name': payload.collection_name,
                'doc_ids': payload.doc_ids,
                'top_k': payload.top_k,
                'focus_aspects': payload.focus_aspects,
            },
            runner,
        )
        merged_pack = primary_pack
        # 覆盖度不足时自动补一次图谱证据，避免主工作流在首次检索后立即中断。
        if primary_pack.coverage_score < 0.5 and primary_pack.missing_aspects:
            selected_tools.append('rag_retrieve_graph_evidence')
            graph_pack = self._run_tool(
                'rag_retrieve_graph_evidence',
                {
                    'query': payload.query,
                    'collection_name': payload.collection_name,
                    'doc_ids': payload.doc_ids,
                    'top_k': payload.top_k,
                    'focus_aspects': payload.focus_aspects,
                },
                runner,
            )
            merged_pack = merge_packs(primary_pack, graph_pack)
        decision: Literal['continue', 'replan'] = 'replan' if merged_pack.missing_aspects else 'continue'
        self._record_run(
            payload.task_id,
            'collect_evidence',
            selected_tools,
            input_summary={
                'doc_count': len(payload.doc_ids),
                'top_k': payload.top_k,
                'focus_aspects': payload.focus_aspects,
            },
            output_summary={
                'coverage_score': merged_pack.coverage_score,
                'missing_aspects': merged_pack.missing_aspects,
                'evidence_count': len(merged_pack.evidence_items),
                'decision': decision,
            },
            handoff=handoff,
        )
        return EvidenceCollectionResult(
            evidence_pack=merged_pack,
            selected_tools=selected_tools,
            decision=decision,
        )

    def supplement(
        self,
        payload: EvidenceSupplementInput,
        *,
        runner: ToolRunner,
        merge_packs: EvidenceMerger,
        handoff: SubAgentHandoff | None = None,
    ) -> EvidenceSupplementResult:
        """针对缺失维度执行一次补证据。

        Args:
            payload: 补证据输入。
            runner: 工具执行器。
            merge_packs: 证据包合并函数。

        Returns:
            合并补证据结果后的输出对象。
        """
        selected_tools = ['rag_retrieve_graph_evidence']
        supplement_pack = self._run_tool(
            'rag_retrieve_graph_evidence',
            {
                'query': payload.query,
                'collection_name': payload.collection_name,
                'doc_ids': payload.doc_ids,
                'top_k': payload.top_k,
                'focus_aspects': payload.missing_aspects,
            },
            runner,
        )
        merged_pack = merge_packs(payload.evidence_pack, supplement_pack)
        self._record_run(
            payload.task_id,
            'supplement_evidence',
            selected_tools,
            input_summary={
                'doc_count': len(payload.doc_ids),
                'missing_aspects': payload.missing_aspects,
                'top_k': payload.top_k,
            },
            output_summary={
                'coverage_score': merged_pack.coverage_score,
                'remaining_missing_aspects': merged_pack.missing_aspects,
                'evidence_count': len(merged_pack.evidence_items),
            },
            handoff=handoff,
        )
        return EvidenceSupplementResult(evidence_pack=merged_pack, selected_tools=selected_tools)

    def execute(self, action: str, payload: BaseModel, **kwargs: Any) -> BaseModel:
        """按动作名称分发 Evidence Agent 能力。

        Args:
            action: 待执行动作名称。
            payload: 动作输入对象。
            **kwargs: 动作执行依赖。

        Returns:
            对应动作的结果对象。

        Raises:
            KeyError: 当动作名称不受支持时抛出。
        """
        if action == 'collect':
            return self.collect(payload=EvidenceCollectionInput.model_validate(payload), **kwargs)
        if action == 'supplement':
            return self.supplement(payload=EvidenceSupplementInput.model_validate(payload), **kwargs)
        raise KeyError(f'unsupported evidence agent action: {action}')

    def _build_action_schemas(self) -> list[SubAgentActionSchema]:
        """返回 Evidence Agent 支持的动作 schema。"""
        return [
            SubAgentActionSchema(
                action='collect',
                description='收集主证据并在低覆盖度时触发图谱补证据。',
                input_schema=EvidenceCollectionInput.model_json_schema(),
                output_schema=EvidenceCollectionResult.model_json_schema(),
                allowed_tools=list(self.allowed_tools),
            ),
            SubAgentActionSchema(
                action='supplement',
                description='针对缺失维度执行一次受控补证据。',
                input_schema=EvidenceSupplementInput.model_json_schema(),
                output_schema=EvidenceSupplementResult.model_json_schema(),
                allowed_tools=['rag_retrieve_graph_evidence'],
            ),
        ]


class ReportingAgent(ControlledSubAgent):
    """负责生成首版报告草稿的受控子代理。"""

    name = 'reporting_agent'
    description = '在白名单产物工具内生成首版报告草稿。'
    allowed_tools = ('draft_report',)

    def draft(
        self,
        payload: DraftArtifactInput,
        *,
        runner: ToolRunner,
        handoff: SubAgentHandoff | None = None,
    ) -> DraftArtifactResult:
        """生成首版报告草稿。"""

        selected_tools = ['draft_report']
        drafted = self._run_tool(
            'draft_report',
            {
                'summary': payload.summary,
                'key_findings': [item.model_dump(mode='json') for item in payload.key_findings],
                'risks': [item.model_dump(mode='json') for item in payload.risks],
                'evidence': [item.model_dump(mode='json') for item in payload.evidence],
                'open_questions': list(payload.open_questions),
                'confidence': payload.confidence,
            },
            runner,
        )
        self._record_run(
            payload.task_id,
            'draft_artifact',
            selected_tools,
            input_summary={
                'finding_count': len(payload.key_findings),
                'risk_count': len(payload.risks),
                'evidence_count': len(payload.evidence),
                'open_question_count': len(payload.open_questions),
            },
            output_summary={
                'finding_count': len(drafted.content.key_findings),
                'risk_count': len(drafted.content.risks),
                'evidence_count': len(drafted.content.evidence),
                'open_question_count': len(drafted.content.open_questions),
            },
            handoff=handoff,
        )
        return DraftArtifactResult(content=drafted.content, selected_tools=selected_tools)

    def execute(self, action: str, payload: BaseModel, **kwargs: Any) -> BaseModel:
        """按动作名称分发 Reporting Agent 能力。"""

        if action == 'draft':
            return self.draft(payload=DraftArtifactInput.model_validate(payload), **kwargs)
        raise KeyError(f'unsupported reporting agent action: {action}')

    def _build_action_schemas(self) -> list[SubAgentActionSchema]:
        """返回 Reporting Agent 支持的动作 schema。"""

        return [
            SubAgentActionSchema(
                action='draft',
                description='根据当前分析结果和证据生成首版报告草稿。',
                input_schema=DraftArtifactInput.model_json_schema(),
                output_schema=DraftArtifactResult.model_json_schema(),
                allowed_tools=['draft_report'],
            )
        ]


class ReviewAgent(ControlledSubAgent):
    """负责审查与修订报告草稿的受控子代理。"""

    name = 'review_agent'
    description = '在白名单产物工具内完成报告审查和一次受控修订。'
    allowed_tools = ('review_report', 'draft_report')

    def review(
        self,
        payload: ReviewDraftInput,
        *,
        runner: ToolRunner,
        handoff: SubAgentHandoff | None = None,
    ) -> ReviewDraftResult:
        """审查报告草稿并决定是否进入修订回路。

        Args:
            payload: 草稿审查输入。
            runner: 工具执行器。

        Returns:
            审查结果与下一步决策。
        """
        selected_tools = ['review_report']
        review = self._run_tool('review_report', {'content': payload.content.model_dump(mode='json')}, runner)
        decision: Literal['finalize', 'revise'] = 'finalize' if review.passed else 'revise'
        self._record_run(
            payload.task_id,
            'review_artifact',
            selected_tools,
            input_summary={
                'finding_count': len(payload.content.key_findings),
                'risk_count': len(payload.content.risks),
                'evidence_count': len(payload.content.evidence),
            },
            output_summary={
                'passed': review.passed,
                'missing_sections': review.missing_sections,
                'unsupported_claims': review.unsupported_claims,
                'decision': decision,
            },
            handoff=handoff,
        )
        return ReviewDraftResult(review=review, selected_tools=selected_tools, decision=decision)

    def revise(
        self,
        payload: ReviseDraftInput,
        *,
        runner: ToolRunner,
        handoff: SubAgentHandoff | None = None,
    ) -> ReviseDraftResult:
        """根据审查意见生成一次受控修订草稿。

        Args:
            payload: 草稿修订输入。
            runner: 工具执行器。

        Returns:
            修订后的草稿结果。
        """
        evidence_ids = {item.citation_id for item in payload.draft_content.evidence}
        key_findings = [
            item
            for item in payload.draft_content.key_findings
            if not item.citation_ids or set(item.citation_ids).issubset(evidence_ids)
        ]
        risks = [
            item for item in payload.draft_content.risks if not item.citation_ids or set(item.citation_ids).issubset(evidence_ids)
        ]
        open_questions = list(payload.draft_content.open_questions)
        # 把缺失字段转成 open question，保证后续草稿能显式暴露尚未补齐的部分。
        for section in payload.review.missing_sections:
            placeholder = f'待补充：{section}'
            if placeholder not in open_questions:
                open_questions.append(placeholder)
        selected_tools = ['draft_report']
        revised = self._run_tool(
            'draft_report',
            {
                'summary': payload.draft_content.summary,
                'key_findings': [item.model_dump(mode='json') for item in key_findings],
                'risks': [item.model_dump(mode='json') for item in risks],
                'evidence': [item.model_dump(mode='json') for item in payload.draft_content.evidence],
                'open_questions': open_questions,
                'confidence': max(0.1, payload.draft_content.confidence - 0.05),
            },
            runner,
        )
        self._record_run(
            payload.task_id,
            'revise_artifact',
            selected_tools,
            input_summary={
                'missing_sections': payload.review.missing_sections,
                'unsupported_claim_count': len(payload.review.unsupported_claims),
            },
            output_summary={
                'finding_count': len(revised.content.key_findings),
                'risk_count': len(revised.content.risks),
                'open_question_count': len(revised.content.open_questions),
            },
            handoff=handoff,
        )
        return ReviseDraftResult(content=revised.content, selected_tools=selected_tools)

    def execute(self, action: str, payload: BaseModel, **kwargs: Any) -> BaseModel:
        """按动作名称分发 Review Agent 能力。

        Args:
            action: 待执行动作名称。
            payload: 动作输入对象。
            **kwargs: 动作执行依赖。

        Returns:
            对应动作的结果对象。

        Raises:
            KeyError: 当动作名称不受支持时抛出。
        """
        if action == 'review':
            return self.review(payload=ReviewDraftInput.model_validate(payload), **kwargs)
        if action == 'revise':
            return self.revise(payload=ReviseDraftInput.model_validate(payload), **kwargs)
        raise KeyError(f'unsupported review agent action: {action}')

    def _build_action_schemas(self) -> list[SubAgentActionSchema]:
        """返回 Review Agent 支持的动作 schema。"""
        return [
            SubAgentActionSchema(
                action='review',
                description='审查草稿结构完整性、unsupported claims 与待确认项。',
                input_schema=ReviewDraftInput.model_json_schema(),
                output_schema=ReviewDraftResult.model_json_schema(),
                allowed_tools=['review_report'],
            ),
            SubAgentActionSchema(
                action='revise',
                description='根据审查结果生成一次受控修订草稿。',
                input_schema=ReviseDraftInput.model_json_schema(),
                output_schema=ReviseDraftResult.model_json_schema(),
                allowed_tools=['draft_report'],
            ),
        ]


class ContractAgent(ControlledSubAgent):
    """负责发现并读取 API contract 的受控子代理。"""

    name = 'contract_agent'
    description = '在白名单 API contract 工具内发现、筛选并读取接口契约。'
    allowed_tools = ('list_api_contracts', 'search_api_contract_operations', 'read_api_contract')

    def discover(
        self,
        payload: ContractDiscoverInput,
        *,
        runner: ToolRunner,
        handoff: SubAgentHandoff | None = None,
    ) -> ContractDiscoverResult:
        """发现可用的 API contract 文档或 operation。"""

        query = (payload.query or '').strip()
        if query:
            selected_tools = ['search_api_contract_operations']
            matches = self._run_tool(
                'search_api_contract_operations',
                {
                    'query': query,
                    'path_prefix': payload.path_prefix,
                    'max_results': payload.max_results,
                },
                runner,
            )
            decision: Literal['inspect_contract', 'no_match'] = 'inspect_contract' if matches.matches else 'no_match'
            self._record_run(
                payload.task_id,
                'discover_contract',
                selected_tools,
                input_summary={'query': query, 'path_prefix': payload.path_prefix, 'max_results': payload.max_results},
                output_summary={'match_count': len(matches.matches), 'decision': decision},
                handoff=handoff,
            )
            return ContractDiscoverResult(
                operation_matches=list(matches.matches),
                selected_tools=selected_tools,
                decision=decision,
            )

        selected_tools = ['list_api_contracts']
        contracts = self._run_tool(
            'list_api_contracts',
            {'path_prefix': payload.path_prefix, 'max_entries': payload.max_results},
            runner,
        )
        decision = 'inspect_contract' if contracts.contracts else 'no_match'
        self._record_run(
            payload.task_id,
            'discover_contract',
            selected_tools,
            input_summary={'path_prefix': payload.path_prefix, 'max_results': payload.max_results},
            output_summary={'contract_count': len(contracts.contracts), 'decision': decision},
            handoff=handoff,
        )
        return ContractDiscoverResult(
            contracts=list(contracts.contracts),
            selected_tools=selected_tools,
            decision=decision,
        )

    def inspect(
        self,
        payload: ContractInspectInput,
        *,
        runner: ToolRunner,
        handoff: SubAgentHandoff | None = None,
    ) -> ContractInspectResult:
        """读取并返回指定 API contract 的结构化内容。"""

        selected_tools = ['read_api_contract']
        contract = self._run_tool(
            'read_api_contract',
            {
                'path': payload.path,
                'method': payload.method,
                'endpoint_path': payload.endpoint_path,
            },
            runner,
        )
        selected_operation = contract.selected_operation
        self._record_run(
            payload.task_id,
            'inspect_contract',
            selected_tools,
            input_summary={'path': payload.path, 'method': payload.method, 'endpoint_path': payload.endpoint_path},
            output_summary={
                'operation_count': len(contract.operations),
                'selected_operation': (
                    f'{selected_operation.method.upper()} {selected_operation.path}'
                    if selected_operation is not None
                    else None
                ),
            },
            handoff=handoff,
        )
        return ContractInspectResult(contract=contract, selected_tools=selected_tools)

    def execute(self, action: str, payload: BaseModel, **kwargs: Any) -> BaseModel:
        """按动作名称分发 Contract Agent 能力。"""

        if action == 'discover':
            return self.discover(payload=ContractDiscoverInput.model_validate(payload), **kwargs)
        if action == 'inspect':
            return self.inspect(payload=ContractInspectInput.model_validate(payload), **kwargs)
        raise KeyError(f'unsupported contract agent action: {action}')

    def _build_action_schemas(self) -> list[SubAgentActionSchema]:
        """返回 Contract Agent 支持的动作 schema。"""

        return [
            SubAgentActionSchema(
                action='discover',
                description='列出或搜索 API contract 文档与 operations。',
                input_schema=ContractDiscoverInput.model_json_schema(),
                output_schema=ContractDiscoverResult.model_json_schema(),
                allowed_tools=['list_api_contracts', 'search_api_contract_operations'],
            ),
            SubAgentActionSchema(
                action='inspect',
                description='读取指定 API contract，并可聚焦单个 operation。',
                input_schema=ContractInspectInput.model_json_schema(),
                output_schema=ContractInspectResult.model_json_schema(),
                allowed_tools=['read_api_contract'],
            ),
        ]


class SubAgentRegistry:
    """统一注册与枚举子代理 schema。"""

    def __init__(self) -> None:
        """初始化子代理注册表。"""
        self._agents: dict[str, RegisteredSubAgent] = {}

    def register(self, agent: RegisteredSubAgent) -> None:
        """注册一个子代理实现。

        Args:
            agent: 待注册的子代理对象。
        """
        self._agents[agent.name] = agent

    def get(self, name: str) -> RegisteredSubAgent:
        """按名称读取已注册子代理。

        Args:
            name: 子代理名称。

        Returns:
            对应的子代理实现。
        """
        return self._agents[name]

    def describe(self, name: str) -> SubAgentSchema:
        """返回指定子代理的静态 schema。"""
        return self.get(name).describe()

    def list_descriptions(self) -> list[SubAgentSchema]:
        """按名称顺序返回全部子代理 schema。"""
        return [self.describe(name) for name in sorted(self._agents)]


class SubAgentRuntime:
    """执行受控子代理动作并记录统一 trace。"""

    def __init__(self, registry: SubAgentRegistry, trace: TraceRecorder) -> None:
        """初始化子代理运行时。

        Args:
            registry: 子代理注册表。
            trace: 链路追踪记录器。
        """
        self.registry = registry
        self.trace = trace

    def execute(
        self,
        agent_name: str,
        action: str,
        payload: BaseModel,
        *,
        handoff: SubAgentHandoff | None = None,
        **kwargs: Any,
    ) -> BaseModel:
        """执行一次子代理动作，并补齐 trace 与失败摘要。

        Args:
            agent_name: 子代理名称。
            action: 动作名称。
            payload: 动作输入对象。
            **kwargs: 透传给子代理动作的附加依赖。

        Returns:
            子代理动作返回结果。
        """
        agent = self.registry.get(agent_name)
        self.trace.record(
            'task_sub_agent_started',
            {
                'task_id': getattr(payload, 'task_id', None),
                'agent_name': agent_name,
                'action': action,
                'handoff_id': handoff.handoff_id if handoff is not None else None,
                'source_step_id': handoff.source_step_id if handoff is not None else None,
                'context_keys': list(handoff.context_keys) if handoff is not None else [],
                'step_limit': handoff.step_limit if handoff is not None else None,
                'budget_limit': handoff.budget_limit if handoff is not None else None,
                'sandbox_profile': handoff.sandbox_profile if handoff is not None else None,
            },
        )
        try:
            result = agent.execute(action, payload, handoff=handoff, **kwargs)
        except Exception as exc:
            if isinstance(agent, ControlledSubAgent):
                # 即便动作失败，也补写一次 memory，避免任务排障时只在 trace 中看到异常而没有摘要。
                agent.memory.record_sub_agent_run(
                    getattr(payload, 'task_id', ''),
                    agent_name,
                    action,
                    status='failed',
                    handoff_id=handoff.handoff_id if handoff is not None else None,
                    source_step_id=handoff.source_step_id if handoff is not None else None,
                    context_keys=list(handoff.context_keys) if handoff is not None else [],
                    step_limit=handoff.step_limit if handoff is not None else None,
                    budget_limit=handoff.budget_limit if handoff is not None else None,
                    sandbox_profile=handoff.sandbox_profile if handoff is not None else None,
                    allowed_tools=list(agent.allowed_tools),
                    selected_tools=[],
                    input_summary={'payload_type': payload.__class__.__name__},
                    output_summary={'error': str(exc)},
                )
            self.trace.record(
                'task_sub_agent_failed',
                {
                    'task_id': getattr(payload, 'task_id', None),
                    'agent_name': agent_name,
                    'action': action,
                    'handoff_id': handoff.handoff_id if handoff is not None else None,
                    'error': str(exc),
                },
            )
            raise
        self.trace.record(
            'task_sub_agent_dispatched',
            {
                'task_id': getattr(payload, 'task_id', None),
                'agent_name': agent_name,
                'action': action,
                'handoff_id': handoff.handoff_id if handoff is not None else None,
                'result_type': result.__class__.__name__,
            },
        )
        return result
