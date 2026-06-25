"""统一运行时契约模型模块。

负责定义 query workflow、task workflow、prompt 构建链路与结果交付之间共享的中间契约，
用于把 memory、prompt、grounding、graph 和结果结构以稳定强类型形式串起来。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class MemoryRecord(BaseModel):
    """统一承载 runtime memory 元数据。

    该模型是运行时“可回看记忆”的最小通用单元，既可记录工具观察，也可记录分析结论、用户偏好、
    错误信息和最终产物摘要。
    """

    memory_id: str
    scope: Literal['working', 'session', 'run', 'semantic', 'profile']
    namespace: dict[str, str] = Field(default_factory=dict)
    kind: Literal['observation', 'evidence', 'analysis', 'reflection', 'artifact', 'preference', 'error']
    trust_level: Literal['unverified', 'provisional', 'verified', 'final'] = 'provisional'
    source: Literal['tool', 'subagent', 'reflection', 'system', 'user'] = 'system'
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False
    stale: bool = False
    conflict_refs: list[str] = Field(default_factory=list)
    checkpoint_ref: str | None = None
    related_task_run_id: str | None = None
    related_step_id: str | None = None
    created_at: datetime


class PromptSpec(BaseModel):
    """统一 prompt 模板契约。

    用来描述某类 prompt 模板本身，而不是某一次具体渲染结果。它偏静态，适合做版本管理与审计。
    """

    prompt_id: str
    prompt_version: str
    scope: Literal['platform', 'skill', 'step'] = 'step'
    purpose: str
    target_model_family: str = 'generic'
    expected_output_schema: str | None = None
    template_parts: dict[str, str] = Field(default_factory=dict)
    guardrails: list[str] = Field(default_factory=list)
    change_log: list[str] = Field(default_factory=list)


class PromptBuildRequest(BaseModel):
    """统一 prompt 构建输入。

    该模型记录一次 prompt 构建所依赖的规格引用、步骤引用和上下文引用，便于还原“这个 prompt
    是在什么上下文下构出来的”。
    """

    prompt_spec_ref: str
    task_spec_ref: str
    step_spec_ref: str | None = None
    context_bundle_ref: str
    tool_specs_ref: list[str] = Field(default_factory=list)
    policy_profile_ref: str | None = None
    prompt_profile_ref: str | None = None


class PromptBuildResult(BaseModel):
    """统一 prompt 构建输出。

    与 `PromptSpec` 相比，它属于一次具体构建的产物，保存系统提示、用户提示、工具说明和构建备注。
    """

    prompt_build_id: str
    resolved_prompt_version: str
    system_prompt: str
    developer_prompt: str | None = None
    user_prompt: str
    tool_instructions: list[str] = Field(default_factory=list)
    output_contract: dict[str, Any] | None = None
    build_notes: list[str] = Field(default_factory=list)


class GroundedContext(BaseModel):
    """统一 grounded context 契约。

    用于把某次结果所依赖的证据事实和未解决缺口以结构化形式暴露出来，供结果解释、回放与审计复用。
    """

    objective: str
    evidence_pack_ref: str
    grounded_facts: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_gaps: list[str] = Field(default_factory=list)


class GraphSubgraph(BaseModel):
    """统一 graph 子图契约。

    主要用于 GraphRAG 或图结构检索场景，把命中的实体、节点与边裁剪成结果侧可消费的子图快照。
    """

    root_entities: list[str] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class ResultContract(BaseModel):
    """统一结果契约。

    当前先把 query/task 两侧已经稳定出现的公共字段收口为强类型模型，同时保留 `extra`
    以兼容存量流程中尚未完全收敛的扩展键。
    """

    model_config = ConfigDict(extra='allow')

    kind: str
    exit_reason: str | None = None
    degraded: bool = False
    fallback_action_applied: str | None = None
    skill_name: str | None = None
    final_artifact_id: str | None = None
    artifact_type: str | None = None
    artifact_status: str | None = None
    artifact_version: int | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    result_artifact_id: str | None = None
    result_artifact_type: str | None = None
    replayed_from_checkpoint_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        """返回适合持久化和跨层传递的 JSON dict。"""
        return self.model_dump(mode='json', exclude_none=True)


def load_result_contract(payload: ResultContract | dict[str, Any] | None) -> ResultContract | None:
    """把存量 dict 或已构造的模型统一转为 `ResultContract`。

    该 helper 主要服务于迁移期兼容，让旧持久化记录和新运行态模型都能通过同一入口收口。
    """
    if payload is None:
        return None
    if isinstance(payload, ResultContract):
        return payload
    return ResultContract.model_validate(payload)


def dump_result_contract(payload: ResultContract | dict[str, Any] | None) -> dict[str, Any] | None:
    """把结果契约统一转为可持久化 dict。"""
    contract = load_result_contract(payload)
    if contract is None:
        return None
    return contract.to_record()
