"""旧模型到统一 runtime contracts 的适配模块�?
负责把历史模型、查询事件和任务记录转换为新�?runtime contracts 结构，便�?query/task
runtime、评测链路和可观测能力复用同一套运行时协议�?"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.rag_system.knowledge.contracts import RetrievalQualityReport
from app.agent_platform.harness.prompting import PromptRenderResult, PromptTemplate
from app.models.artifact import EvidenceItem, EvidencePack
from app.models.query import CitationItem
from app.models.runtime_contracts import GraphSubgraph, GroundedContext, MemoryRecord, PromptBuildRequest, PromptBuildResult, PromptSpec
from app.models.task import ArtifactMemoryEntry, ReflectionEntry, TaskMemoryEntry


def task_memory_entry_to_memory_record(
    entry: TaskMemoryEntry,
    *,
    task_id: str,
    task_run_id: str | None,
    checkpoint_ref: str | None = None,
) -> MemoryRecord:
    """�?`TaskMemoryEntry` 映射为统一 `MemoryRecord`�?
    Args:
        entry: 旧任务记忆条目�?        task_id: 所属任�?ID�?        task_run_id: 所属任务运�?ID�?        checkpoint_ref: 可�?checkpoint 引用�?
    Returns:
        转换后的统一记忆记录对象�?    """
    kind_map = {
        'context': 'observation',
        'evidence': 'evidence',
        'analysis': 'analysis',
        'review': 'analysis',
        'replan': 'reflection',
        'state': 'observation',
    }
    return MemoryRecord(
        memory_id=entry.entry_id,
        scope='working',
        namespace={'task_id': task_id},
        kind=kind_map.get(entry.kind, 'observation'),  # type: ignore[arg-type]
        trust_level='verified' if entry.kind in {'evidence', 'analysis', 'review'} else 'provisional',
        source='tool' if entry.kind in {'evidence', 'analysis', 'review'} else 'system',
        summary=entry.summary,
        payload=dict(entry.payload),
        checkpoint_ref=checkpoint_ref,
        related_task_run_id=task_run_id,
        related_step_id=entry.step,
        created_at=entry.created_at,
    )


def artifact_memory_entry_to_memory_record(
    entry: ArtifactMemoryEntry,
    *,
    task_id: str,
    task_run_id: str | None,
) -> MemoryRecord:
    """�?`ArtifactMemoryEntry` 映射为统一 `MemoryRecord`�?
    Args:
        entry: 产物记忆条目�?        task_id: 所属任�?ID�?        task_run_id: 所属任务运�?ID�?
    Returns:
        转换后的统一记忆记录对象�?    """
    return MemoryRecord(
        memory_id=f'mr-artifact-{entry.artifact_id}',
        scope='run',
        namespace={'task_id': task_id, 'artifact_id': entry.artifact_id},
        kind='artifact',
        trust_level='final' if entry.status == 'final' else 'verified',
        source='system',
        summary=entry.summary,
        payload=entry.model_dump(mode='json'),
        degraded=False,
        stale=False,
        related_task_run_id=task_run_id,
        created_at=entry.created_at,
    )


def reflection_entry_to_memory_record(
    entry: ReflectionEntry,
    *,
    task_id: str,
    task_run_id: str | None,
) -> MemoryRecord:
    """�?`ReflectionEntry` 映射为统一 `MemoryRecord`�?
    Args:
        entry: 反思记录条目�?        task_id: 所属任�?ID�?        task_run_id: 所属任务运�?ID�?
    Returns:
        转换后的统一记忆记录对象�?    """
    return MemoryRecord(
        memory_id=entry.entry_id,
        scope='run',
        namespace={'task_id': task_id},
        kind='reflection',
        trust_level='verified',
        source='reflection',
        summary=entry.summary,
        payload=entry.model_dump(mode='json'),
        related_task_run_id=task_run_id,
        related_step_id=entry.step,
        created_at=entry.created_at,
    )


def query_run_event_to_memory_record(
    event: dict[str, Any],
    *,
    run_id: str,
    collection_name: str,
) -> MemoryRecord:
    """�?query run event 映射为统一 `MemoryRecord`�?
    Args:
        event: 原始 query runtime 事件字典�?        run_id: 查询运行 ID�?        collection_name: 所属集合名称�?
    Returns:
        转换后的统一记忆记录对象�?    """
    name = str(event.get('name') or 'run_event')
    payload = dict(event.get('payload') or {})
    kind = 'error' if 'failed' in name or payload.get('status') == 'error' else 'observation'
    if 'reflect' in name or 'decision' in name:
        kind = 'reflection'
    return MemoryRecord(
        memory_id=str(event.get('event_id') or f'mr-{uuid4().hex[:12]}'),
        scope='run',
        namespace={'run_id': run_id, 'collection_name': collection_name},
        kind=kind,  # type: ignore[arg-type]
        trust_level='final' if name in {'query_completed', 'workflow_completed'} else 'verified',
        source='system',
        summary=name,
        payload=payload,
        degraded=bool(payload.get('degraded')),
        related_task_run_id=run_id,
        related_step_id=payload.get('task_step_id'),
        created_at=_normalize_datetime(event.get('timestamp')),
    )


def prompt_template_to_spec(template: PromptTemplate) -> PromptSpec:
    """�?`PromptTemplate` 映射为统一 `PromptSpec`�?
    Args:
        template: 旧提示词模板对象�?
    Returns:
        统一协议下的提示词规格对象�?    """
    return PromptSpec(
        prompt_id=template.template_id,
        prompt_version=template.version,
        scope='step',
        purpose=template.step_type,
        target_model_family='generic',
        expected_output_schema=template.output_schema or None,
        template_parts={'content': template.content},
        guardrails=['strict_json_output'] if template.output_schema else [],
        change_log=[f'registered:{template.version}'],
    )


def build_prompt_build_request(
    *,
    prompt_spec: PromptSpec,
    task_spec_ref: str,
    step_spec_ref: str | None,
    context_bundle_ref: str,
    tool_specs_ref: list[str] | None = None,
    policy_profile_ref: str | None = None,
    prompt_profile_ref: str | None = None,
) -> PromptBuildRequest:
    """构造统一 `PromptBuildRequest`�?
    Args:
        prompt_spec: 提示词规格对象�?        task_spec_ref: 任务规格引用�?        step_spec_ref: 步骤规格引用�?        context_bundle_ref: 上下文包引用�?        tool_specs_ref: 可选工具规格引用列表�?        policy_profile_ref: 可选策略画像引用�?        prompt_profile_ref: 可选提示词画像引用�?
    Returns:
        统一协议下的提示词构建请求对象�?    """
    return PromptBuildRequest(
        prompt_spec_ref=f'{prompt_spec.prompt_id}:{prompt_spec.prompt_version}',
        task_spec_ref=task_spec_ref,
        step_spec_ref=step_spec_ref,
        context_bundle_ref=context_bundle_ref,
        tool_specs_ref=tool_specs_ref or [],
        policy_profile_ref=policy_profile_ref,
        prompt_profile_ref=prompt_profile_ref,
    )


def prompt_render_result_to_build_result(
    render_result: PromptRenderResult,
    *,
    output_contract: dict[str, Any] | None = None,
    build_notes: list[str] | None = None,
) -> PromptBuildResult:
    """�?`PromptRenderResult` 映射为统一 `PromptBuildResult`�?
    Args:
        render_result: 提示词渲染结果�?        output_contract: 可选输出契约�?        build_notes: 可选构建备注�?
    Returns:
        统一协议下的提示词构建结果对象�?    """
    return PromptBuildResult(
        prompt_build_id=f'pb-{uuid4().hex[:12]}',
        resolved_prompt_version=render_result.version,
        system_prompt='',
        developer_prompt=None,
        user_prompt=render_result.prompt,
        tool_instructions=[],
        output_contract=output_contract,
        build_notes=build_notes or [f'template:{render_result.template_id}', f'tokens:{render_result.token_count}'],
    )


def build_ad_hoc_prompt_spec(*, prompt_id: str, purpose: str, prompt_text: str, output_schema: str | None = None) -> PromptSpec:
    """为非 PromptBuilder 路径构造一个即�?`PromptSpec`�?
    Args:
        prompt_id: 提示词标识�?        purpose: 提示词用途�?        prompt_text: 原始提示词文本�?        output_schema: 可选输出结构描述�?
    Returns:
        即席构造的统一提示词规格对象�?    """
    return PromptSpec(
        prompt_id=prompt_id,
        prompt_version='v1',
        scope='step',
        purpose=purpose,
        target_model_family='generic',
        expected_output_schema=output_schema,
        template_parts={'content': prompt_text},
        guardrails=[],
        change_log=['adhoc'],
    )


def build_ad_hoc_prompt_build_result(
    *,
    prompt_text: str,
    output_contract: dict[str, Any] | None = None,
    build_notes: list[str] | None = None,
) -> PromptBuildResult:
    """为非 PromptBuilder 路径构�?`PromptBuildResult`�?
    Args:
        prompt_text: 最终提示词文本�?        output_contract: 可选输出契约�?        build_notes: 可选构建备注�?
    Returns:
        即席构造的提示词构建结果对象�?    """
    return PromptBuildResult(
        prompt_build_id=f'pb-{uuid4().hex[:12]}',
        resolved_prompt_version='v1',
        system_prompt='',
        developer_prompt=None,
        user_prompt=prompt_text,
        tool_instructions=[],
        output_contract=output_contract,
        build_notes=build_notes or ['adhoc_prompt'],
    )


def evidence_pack_to_grounded_context(
    *,
    objective: str,
    evidence_pack: EvidencePack,
    evidence_pack_ref: str,
    unresolved_gaps: list[str] | None = None,
) -> GroundedContext:
    """�?`EvidencePack` 映射为统一 `GroundedContext`�?
    Args:
        objective: 当前任务目标�?        evidence_pack: 证据包对象�?        evidence_pack_ref: 证据包引用标识�?        unresolved_gaps: 可选未解决缺口列表�?
    Returns:
        可直接挂�?runtime contracts 中的 grounded context�?    """
    return GroundedContext(
        objective=objective,
        evidence_pack_ref=evidence_pack_ref,
        grounded_facts=[_evidence_item_to_fact(item) for item in evidence_pack.evidence_items],
        unresolved_gaps=list(unresolved_gaps or evidence_pack.missing_aspects),
    )


def citations_to_graph_subgraph(citations: list[CitationItem]) -> GraphSubgraph:
    """�?query citations 提取可审计子图�?
    Args:
        citations: 查询结果中的引用列表�?
    Returns:
        基于引用关系构造出的最小图谱子图�?    """
    roots: list[str] = []
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for item in citations:
        start = str(item.graph_start_entity or '').strip()
        end = str(item.graph_end_entity or '').strip()
        relation = str(item.graph_relation or '').strip()
        if start:
            roots.append(start)
            nodes[start] = {'node_id': start, 'label': start, 'entity_type': 'graph_entity'}
        if end:
            nodes[end] = {'node_id': end, 'label': end, 'entity_type': 'graph_entity'}
        if start and end and relation:
            edges.append(
                {
                    'edge_id': f'gedge-{uuid4().hex[:8]}',
                    'source': start,
                    'target': end,
                    'relation': relation,
                    'chunk_id': item.chunk_id,
                }
            )
    return GraphSubgraph(
        root_entities=list(dict.fromkeys(roots)),
        nodes=list(nodes.values()),
        edges=edges,
    )


def evidence_pack_to_graph_subgraph(evidence_pack: EvidencePack) -> GraphSubgraph:
    """�?task evidence pack 构造最小子图视图�?
    Args:
        evidence_pack: 任务证据包�?
    Returns:
        以证据块为节点的最小子图视图�?    """
    nodes = [
        {
            'node_id': item.chunk_id,
            'label': item.source,
            'entity_type': 'evidence_chunk',
            'support_score': item.support_score,
        }
        for item in evidence_pack.evidence_items
    ]
    return GraphSubgraph(root_entities=[], nodes=nodes, edges=[])


def build_retrieval_quality_report(
    *,
    query: str,
    coverage_score: float,
    relevance_score: float,
    confidence_score: float,
    suggested_actions: list[str] | None = None,
) -> RetrievalQualityReport:
    """把统一分数映射�?`RetrievalQualityReport`�?
    Args:
        query: 原始查询文本�?        coverage_score: 覆盖度分数�?        relevance_score: 相关性分数�?        confidence_score: 置信度分数�?        suggested_actions: 可选建议动作列表�?
    Returns:
        统一质量分数折算后的检索质量报告对象�?    """
    overall_score = max(0.0, min(1.0, round((coverage_score + relevance_score + confidence_score) / 3, 3)))
    return RetrievalQualityReport(
        enabled=True,
        supported=overall_score >= 0.5,
        risk='low' if overall_score >= 0.75 else 'medium' if overall_score >= 0.5 else 'high',
        confidence=confidence_score,
        reason=query,
        rewrite_needed=overall_score < 0.5,
        applied=False,
        check_mode='adapter',
        final_mode=None,
        metadata={
            'query': query,
            'overall_score': overall_score,
            'coverage_score': coverage_score,
            'relevance_score': relevance_score,
            'confidence_score': confidence_score,
            'suggested_actions': list(suggested_actions or []),
        },
    )


def _evidence_item_to_fact(item: EvidenceItem) -> dict[str, Any]:
    """把单条证据项转换�?grounded fact 字典�?
    Args:
        item: 证据项对象�?
    Returns:
        �?grounded context 使用的扁平化事实字典�?    """
    return {
        'citation_id': item.citation_id,
        'chunk_id': item.chunk_id,
        'source': item.source,
        'text': item.text,
        'support_score': item.support_score,
        'page': item.page,
    }


def _normalize_datetime(value: Any) -> datetime:
    """把任意时间值归一化为 `datetime`�?
    Args:
        value: 待归一化的时间值�?
    Returns:
        如果输入已经�?`datetime` 则直接返回，否则返回当前 UTC 时间�?    """
    if isinstance(value, datetime):
        return value
    return datetime.now(timezone.utc)
