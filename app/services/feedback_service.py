"""反馈服务模块。

负责沉淀用户反馈、把可用反馈转换为评测候选样本，并按筛选条件导出反馈评测数据集。该服务
位于反馈接口与评测服务之间，承担“在线反馈 -> 离线评测样本”的桥接职责。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.bucketing import infer_bucket, normalize_bucket
from app.core.errors import bad_request_error
from app.models.feedback import (
    EvalCandidateItem,
    EvalCandidateListResponse,
    FeedbackEvalDatasetResponse,
    FeedbackEvalRunRequest,
    FeedbackCreateRequest,
    FeedbackCreateResponse,
    FeedbackItem,
    FeedbackListResponse,
)
from app.rag.observability import TraceRecorder
from app.services.sqlite_store import SQLiteStateStore
from app.services.state import InMemoryState


class FeedbackService:
    """管理用户反馈，并把可用样本转为评测候选。"""

    def __init__(
        self,
        state: InMemoryState,
        settings: Settings,
        trace: TraceRecorder,
        persistence: SQLiteStateStore | None = None,
    ) -> None:
        """初始化反馈服务。

        Args:
            state: 内存态业务数据。
            settings: 全局配置对象，决定评测数据集输出目录。
            trace: 链路追踪记录器。
            persistence: 可选持久化存储。
        """
        self.state = state
        self.settings = settings
        self.trace = trace
        self.persistence = persistence

    def add_feedback(self, payload: FeedbackCreateRequest) -> FeedbackCreateResponse:
        """新增一条反馈，并在可行时同步生成评测候选样本。

        Args:
            payload: 反馈创建请求体。

        Returns:
            反馈创建结果对象。
        """
        created_at = datetime.now(timezone.utc)
        feedback_id = f'fb-{uuid4().hex[:12]}'
        item = FeedbackItem(
            feedback_id=feedback_id,
            feedback_type=payload.feedback_type,
            collection_name=payload.collection_name,
            question=payload.question.strip(),
            answer=payload.answer.strip(),
            session_id=payload.session_id,
            correction=payload.correction.strip() if payload.correction else None,
            note=payload.note.strip() if payload.note else None,
            citations=payload.citations,
            metadata=payload.metadata,
            created_at=created_at,
        )

        # 只有存在可用参考答案的反馈，才会进入后续自动评测链路。
        candidate = self._build_eval_candidate(item)
        if candidate is not None:
            candidate_payload = candidate.model_dump(mode='json')
            self.state.eval_candidates.append(candidate_payload)
            if self.persistence is not None:
                self.persistence.upsert_eval_candidate(candidate_payload)
            item.eval_candidate_created = True

        item_payload = item.model_dump(mode='json')
        self.state.feedback_items.append(item_payload)
        if self.persistence is not None:
            self.persistence.upsert_feedback_item(item_payload)
        return FeedbackCreateResponse(
            feedback_id=feedback_id,
            eval_candidate_created=item.eval_candidate_created,
            created_at=created_at,
        )

    def list_feedback(
        self,
        collection_name: str | None = None,
        feedback_type: str | None = None,
        session_id: str | None = None,
        eval_candidate_created: bool | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> FeedbackListResponse:
        """按筛选条件返回反馈列表。

        Args:
            collection_name: 可选集合名称过滤条件。
            feedback_type: 可选反馈类型过滤条件。
            session_id: 可选会话 ID 过滤条件。
            eval_candidate_created: 是否仅筛选已生成候选样本的反馈。
            limit: 返回数量上限。
            offset: 分页偏移量。

        Returns:
            反馈列表响应对象。
        """
        items = [FeedbackItem(**item) for item in self.state.feedback_items]
        if collection_name is not None:
            items = [item for item in items if item.collection_name == collection_name]
        if feedback_type is not None:
            items = [item for item in items if item.feedback_type == feedback_type]
        if session_id is not None:
            items = [item for item in items if item.session_id == session_id]
        if eval_candidate_created is not None:
            items = [item for item in items if item.eval_candidate_created == eval_candidate_created]

        items.sort(key=lambda item: item.created_at, reverse=True)
        total = len(items)
        paged = items[offset: offset + limit]
        return FeedbackListResponse(items=paged, total=total, limit=limit, offset=offset)

    def list_eval_candidates(
        self,
        collection_name: str | None = None,
        feedback_type: str | None = None,
        feedback_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> EvalCandidateListResponse:
        """按筛选条件返回评测候选列表。

        Args:
            collection_name: 可选集合名称过滤条件。
            feedback_type: 可选反馈类型过滤条件。
            feedback_id: 可选反馈 ID 过滤条件。
            limit: 返回数量上限。
            offset: 分页偏移量。

        Returns:
            评测候选列表响应对象。
        """
        items = [EvalCandidateItem(**item) for item in self.state.eval_candidates]
        if collection_name is not None:
            items = [item for item in items if item.collection_name == collection_name]
        if feedback_type is not None:
            items = [item for item in items if item.feedback_type == feedback_type]
        if feedback_id is not None:
            items = [item for item in items if item.feedback_id == feedback_id]

        items.sort(key=lambda item: item.created_at, reverse=True)
        total = len(items)
        paged = items[offset: offset + limit]
        return EvalCandidateListResponse(items=paged, total=total, limit=limit, offset=offset)

    def export_eval_dataset(self, payload: FeedbackEvalRunRequest) -> FeedbackEvalDatasetResponse:
        """根据筛选条件导出反馈评测数据集文件。

        Args:
            payload: 反馈评测运行请求体。

        Returns:
            数据集导出结果对象。
        """
        selected = self._select_eval_candidates(payload)
        collection_name = self._resolve_collection_name(payload, selected)
        generated_at = datetime.now(timezone.utc)
        target = self._build_dataset_path(payload.dataset_name, collection_name, generated_at)
        # 导出时把线上开关一并固化到数据集中，确保后续评测任务具备可复现输入。
        rows = [
            {
                'bucket': normalize_bucket(item.bucket)
                if item.bucket
                else infer_bucket(
                    item.question,
                    item.reference,
                    feedback_type=item.feedback_type,
                    note=item.note,
                ),
                'question': item.question,
                'ground_truth': item.reference,
                'collection_name': item.collection_name,
                'top_k': payload.top_k,
                'use_query_rewrite': payload.use_query_rewrite,
                'use_hybrid_retrieval': payload.use_hybrid_retrieval,
                'use_rerank': payload.use_rerank,
                'metadata': {
                    'candidate_id': item.candidate_id,
                    'feedback_id': item.feedback_id,
                    'feedback_type': item.feedback_type,
                    'note': item.note,
                },
            }
            for item in selected
        ]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
        self.trace.record(
            'feedback_eval_dataset_created',
            {
                'dataset_path': str(target),
                'candidate_count': len(selected),
                'collection_name': collection_name,
            },
        )
        return FeedbackEvalDatasetResponse(
            dataset_path=str(target),
            candidate_count=len(selected),
            collection_name=collection_name,
            candidate_ids=[item.candidate_id for item in selected],
            generated_at=generated_at,
        )

    def _build_eval_candidate(self, item: FeedbackItem) -> EvalCandidateItem | None:
        """把反馈记录转换为可用于评测的标准样本。

        Args:
            item: 单条反馈记录。

        Returns:
            可用时返回评测候选对象，否则返回 `None`。
        """
        reference = None
        if item.feedback_type == 'correction' and item.correction:
            reference = item.correction
        elif item.feedback_type == 'upvote':
            reference = item.answer
        elif item.feedback_type == 'downvote' and item.correction:
            reference = item.correction

        if not reference:
            return None

        bucket = None
        if isinstance(item.metadata, dict) and item.metadata.get('bucket') is not None:
            bucket = normalize_bucket(str(item.metadata.get('bucket')))

        return EvalCandidateItem(
            candidate_id=f'cand-{uuid4().hex[:12]}',
            feedback_id=item.feedback_id,
            collection_name=item.collection_name,
            bucket=bucket,
            question=item.question,
            reference=reference,
            answer=item.answer,
            feedback_type=item.feedback_type,
            citations=item.citations,
            note=item.note,
            created_at=item.created_at,
        )

    def _select_eval_candidates(self, payload: FeedbackEvalRunRequest) -> list[EvalCandidateItem]:
        """按集合、候选 ID 和反馈类型筛选评测样本。

        Args:
            payload: 反馈评测运行请求体。

        Returns:
            通过筛选后的评测候选列表。
        """
        items = [EvalCandidateItem(**item) for item in self.state.eval_candidates]
        if payload.collection_name:
            items = [item for item in items if item.collection_name == payload.collection_name]
        if payload.candidate_ids:
            expected_ids = set(payload.candidate_ids)
            items = [item for item in items if item.candidate_id in expected_ids]
        if payload.feedback_types:
            expected_types = set(payload.feedback_types)
            items = [item for item in items if item.feedback_type in expected_types]

        items = sorted(items, key=lambda item: item.created_at, reverse=True)
        selected = items[: payload.limit]
        if not selected:
            raise bad_request_error(
                code='feedback_eval_candidates_empty',
                message='没有可用于评测的反馈候选样本',
                details={
                    'collection_name': payload.collection_name,
                    'candidate_ids': payload.candidate_ids,
                    'feedback_types': payload.feedback_types,
                },
            )
        # 外部通常希望按“从旧到新”消费样本，因此这里把倒序筛出的窗口再反转回来。
        return list(reversed(selected))

    def _resolve_collection_name(self, payload: FeedbackEvalRunRequest, items: list[EvalCandidateItem]) -> str:
        """为导出的数据集确定唯一的集合名称。

        Args:
            payload: 反馈评测运行请求体。
            items: 已选中的评测候选列表。

        Returns:
            本次数据集对应的集合名称。
        """
        if payload.collection_name:
            return payload.collection_name

        collection_names = sorted({item.collection_name for item in items})
        if len(collection_names) != 1:
            raise bad_request_error(
                code='feedback_eval_collection_required',
                message='候选样本包含多个 collection_name，请显式指定 collection_name',
                details={'collection_names': collection_names},
            )
        return collection_names[0]

    def _build_dataset_path(self, dataset_name: str | None, collection_name: str, generated_at: datetime) -> Path:
        """生成反馈评测数据集的输出路径。

        Args:
            dataset_name: 可选自定义数据集名称。
            collection_name: 集合名称。
            generated_at: 数据集生成时间。

        Returns:
            数据集输出路径。
        """
        feedback_eval_dir = self.settings.eval_dir / 'feedback'
        safe_collection = self._slugify(collection_name)
        if dataset_name:
            filename = f'{self._slugify(dataset_name)}.json'
        else:
            filename = f'feedback-{safe_collection}-{generated_at.strftime("%Y%m%d-%H%M%S")}.json'
        return feedback_eval_dir / filename

    def _slugify(self, value: str) -> str:
        """把任意文本转换为适合文件名使用的安全片段。

        Args:
            value: 原始文本。

        Returns:
            适合拼接到文件名中的安全字符串。
        """
        normalized = re.sub(r'[^0-9A-Za-z._-]+', '-', value.strip())
        return normalized.strip('-._') or 'feedback-eval'
