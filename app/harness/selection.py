"""Selection Strategy 实现。

基于 step intent 选择最相关的 state/memory/evidence/artifact，实现精准的上下文切片。
"""

from __future__ import annotations

import re
from typing import Any

from app.harness.context_policy import ContextPolicy, ContextSourceType
from app.models.task import TaskMemoryEntry


class SelectionStrategy:
    """上下文选择策略基类。"""

    def select(self, items: list[dict | Any], policy: ContextPolicy, source_type: ContextSourceType) -> list[dict | Any]:
        """根据策略选择合适的上下文项。"""
        raise NotImplementedError


class RelevanceBasedSelection(SelectionStrategy):
    """基于相关性的选择策略。"""

    def __init__(self, intent: str | None = None):
        """初始化相关性选择器，可选注入当前步骤意图。"""

        self.intent = intent

    def select(self, items: list[dict | Any], policy: ContextPolicy, source_type: ContextSourceType) -> list[dict | Any]:
        """按相关性分数与阈值筛选条目。"""

        if not items:
            return []

        rule = policy.get_selection_rule(source_type)
        top_k = rule.top_k

        scored_items = []
        for item in items:
            score = self._calculate_relevance(item, self.intent)
            if score >= rule.relevance_threshold:
                scored_items.append((item, score))

        scored_items.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored_items[:top_k]]

    def _calculate_relevance(self, item: dict | Any, intent: str | None) -> float:
        """计算项与意图的相关性分数。"""
        if intent is None:
            return 0.5

        text_content = self._extract_text(item)
        if not text_content:
            return 0.3

        score = 0.0
        intent_tokens = set(intent.lower().split())

        text_lower = text_content.lower()
        matched_tokens = sum(1 for token in intent_tokens if token in text_lower)
        if intent_tokens:
            score += matched_tokens / len(intent_tokens) * 0.5

        score += self._calculate_semantic_overlap(text_content, intent) * 0.5

        return min(1.0, score)

    def _extract_text(self, item: dict | Any) -> str:
        """从项中提取文本内容。"""
        if isinstance(item, dict):
            text_fields = ['summary', 'text', 'content', 'description', 'title', 'intent']
            for field in text_fields:
                if field in item and isinstance(item[field], str):
                    return str(item[field])
            return str(item)
        if hasattr(item, 'summary') and isinstance(item.summary, str):
            return item.summary
        if hasattr(item, 'text') and isinstance(item.text, str):
            return item.text
        return str(item)

    def _calculate_semantic_overlap(self, text: str, intent: str) -> float:
        """计算文本与意图的语义重叠度。"""
        text_lower = text.lower()
        intent_lower = intent.lower()

        text_tokens = set(self._tokenize(text_lower))
        intent_tokens = set(self._tokenize(intent_lower))

        if not text_tokens or not intent_tokens:
            return 0.0

        intersection = text_tokens.intersection(intent_tokens)
        return len(intersection) / len(intent_tokens)

    def _tokenize(self, text: str) -> list[str]:
        """分词函数，支持中英文混合。"""
        tokens = []

        chinese_pattern = re.compile(r'[\u4e00-\u9fff]+')
        english_pattern = re.compile(r'[a-zA-Z]+')

        for match in chinese_pattern.finditer(text):
            for char in match.group():
                tokens.append(char)

        for match in english_pattern.finditer(text):
            tokens.append(match.group())

        return tokens


class RecencyBasedSelection(SelectionStrategy):
    """基于时间顺序的选择策略。"""

    def __init__(self, timestamp_key: str = 'created_at'):
        """初始化按时间排序的选择器。"""

        self.timestamp_key = timestamp_key

    def select(self, items: list[dict | Any], policy: ContextPolicy, source_type: ContextSourceType) -> list[dict | Any]:
        """按时间倒序选择最近的若干条目。"""

        if not items:
            return []

        rule = policy.get_selection_rule(source_type)
        top_k = rule.top_k

        items_copy = list(items)
        items_copy.sort(key=self._get_timestamp, reverse=True)

        return items_copy[:top_k]

    def _get_timestamp(self, item: dict | Any) -> float:
        """获取项的时间戳。"""
        if isinstance(item, dict):
            if self.timestamp_key in item:
                val = item[self.timestamp_key]
                if hasattr(val, 'timestamp'):
                    return val.timestamp()
                return float(val) if isinstance(val, (int, float)) else 0.0
        if hasattr(item, self.timestamp_key):
            val = getattr(item, self.timestamp_key)
            if hasattr(val, 'timestamp'):
                return val.timestamp()
            return float(val) if isinstance(val, (int, float)) else 0.0
        return 0.0


class CombinedSelection(SelectionStrategy):
    """组合选择策略 - 综合考虑相关性和时间因素。"""

    def __init__(self, relevance_weight: float = 0.6, recency_weight: float = 0.4):
        """初始化组合策略及相关性/时序权重。"""

        self.relevance_weight = relevance_weight
        self.recency_weight = recency_weight
        self.relevance_selector = RelevanceBasedSelection()
        self.recency_selector = RecencyBasedSelection()

    def select(self, items: list[dict | Any], policy: ContextPolicy, source_type: ContextSourceType, intent: str | None = None) -> list[dict | Any]:
        """综合相关性和新近性筛选上下文项。"""

        if not items:
            return []

        self.relevance_selector.intent = intent

        rule = policy.get_selection_rule(source_type)
        top_k = rule.top_k

        relevance_scores = {}
        for item in items:
            relevance = self.relevance_selector._calculate_relevance(item, intent)
            relevance_scores[id(item)] = relevance

        recency_scores = {}
        timestamps = [(item, self.recency_selector._get_timestamp(item)) for item in items]
        if timestamps:
            max_ts = max(ts for _, ts in timestamps)
            min_ts = min(ts for _, ts in timestamps)
            range_ts = max_ts - min_ts if max_ts != min_ts else 1.0
            for item, ts in timestamps:
                recency_scores[id(item)] = (ts - min_ts) / range_ts

        combined_scores = []
        for item in items:
            item_id = id(item)
            relevance = relevance_scores.get(item_id, 0.5)
            recency = recency_scores.get(item_id, 0.5)
            combined = self.relevance_weight * relevance + self.recency_weight * recency
            combined_scores.append((item, combined))

        combined_scores.sort(key=lambda x: x[1], reverse=True)

        selected = [item for item, _ in combined_scores[:top_k]]

        filtered = []
        for item in selected:
            relevance = relevance_scores.get(id(item), 0.0)
            if relevance >= rule.relevance_threshold:
                filtered.append(item)

        return filtered


class SelectionEngine:
    """上下文选择引擎。"""

    def __init__(self):
        """初始化各来源类型的默认选择策略。"""

        self.strategies: dict[ContextSourceType, SelectionStrategy] = {
            ContextSourceType.EVIDENCE: CombinedSelection(relevance_weight=0.7, recency_weight=0.3),
            ContextSourceType.MEMORY: CombinedSelection(relevance_weight=0.5, recency_weight=0.5),
            ContextSourceType.ARTIFACT: CombinedSelection(relevance_weight=0.6, recency_weight=0.4),
            ContextSourceType.STATE: RecencyBasedSelection(),
        }

    def select_state(self, workflow_state: dict[str, Any], policy: ContextPolicy) -> dict[str, Any]:
        """选择状态切片。"""
        state_slice = {}

        if 'task' in workflow_state:
            task = workflow_state['task']
            request = task.get('request', {}) if isinstance(task, dict) else getattr(task, 'request', None)
            state_slice['task_id'] = task.get('task_id') if isinstance(task, dict) else getattr(task, 'task_id', None)
            if isinstance(task, dict):
                state_slice['collection_name'] = task.get('collection_name') or (
                    request.get('collection_name') if isinstance(request, dict) else None
                )
                state_slice['doc_ids'] = list(task.get('doc_ids', []) or (request.get('doc_ids', []) if isinstance(request, dict) else []))
            else:
                state_slice['collection_name'] = getattr(task, 'collection_name', None) or getattr(request, 'collection_name', None)
                state_slice['doc_ids'] = list(getattr(task, 'doc_ids', []) or getattr(request, 'doc_ids', []))
            state_slice['current_step'] = task.get('current_step') if isinstance(task, dict) else getattr(task, 'current_step', None)
            state_slice['completed_steps'] = list(task.get('completed_steps', [])) if isinstance(task, dict) else list(getattr(task, 'completed_steps', []))

        focus_aspects = workflow_state.get('focus_aspects', [])
        state_slice['focus_aspects'] = focus_aspects[:policy.document_limit]

        pending_plan_step_ids = workflow_state.get('pending_plan_step_ids', [])
        state_slice['pending_plan_step_ids'] = pending_plan_step_ids[:10]

        document_context = workflow_state.get('document_context', {})
        documents = document_context.get('documents', [])
        state_slice['document_context_documents'] = documents[:policy.document_limit]

        analysis = workflow_state.get('analysis', {})
        state_slice['analysis'] = self._truncate_dict(analysis, policy.compression_max_chars)

        risks = workflow_state.get('risks', [])
        state_slice['risks'] = risks[:policy.risk_limit]

        review = workflow_state.get('review')
        if review:
            state_slice['review'] = self._truncate_dict(review, policy.compression_max_chars)

        return state_slice

    def select_evidence(self, evidence_items: list[dict | Any], policy: ContextPolicy, intent: str | None = None) -> list[dict]:
        """选择证据切片。"""
        strategy = self.strategies[ContextSourceType.EVIDENCE]

        selected = []
        if isinstance(strategy, CombinedSelection):
            selected = strategy.select(evidence_items, policy, ContextSourceType.EVIDENCE, intent)
        else:
            selected = strategy.select(evidence_items, policy, ContextSourceType.EVIDENCE)

        if not selected and evidence_items:
            selected = list(evidence_items[: policy.evidence_top_k])

        return [self._item_to_dict(item) for item in selected]

    def select_memory(self, task_memory: list[TaskMemoryEntry | dict], policy: ContextPolicy, intent: str | None = None) -> dict[str, Any]:
        """选择记忆切片。"""
        strategy = self.strategies[ContextSourceType.MEMORY]

        task_memory_entries = task_memory[-policy.memory_limit:] if task_memory else []
        selected_memory = []
        if isinstance(strategy, CombinedSelection):
            selected_memory = strategy.select(task_memory_entries, policy, ContextSourceType.MEMORY, intent)
        else:
            selected_memory = strategy.select(task_memory_entries, policy, ContextSourceType.MEMORY)

        formatted_memory = []
        for item in selected_memory:
            if isinstance(item, dict):
                formatted_memory.append({
                    'step': item.get('step', ''),
                    'kind': item.get('kind', ''),
                    'summary': item.get('summary', '')[:500],
                })
            else:
                formatted_memory.append({
                    'step': item.step,
                    'kind': item.kind,
                    'summary': item.summary[:500],
                })

        return {'task_memory': formatted_memory}

    def select_artifact(self, draft_content: Any, policy: ContextPolicy) -> dict[str, Any] | None:
        """选择产物切片。"""
        if draft_content is None:
            return None

        artifact_slice: dict[str, Any] = {}

        if policy.artifact_scope == 'none':
            return None

        if isinstance(draft_content, dict):
            if policy.artifact_scope in ['full', 'summary']:
                artifact_slice['summary'] = draft_content.get('summary', '')[:1000]
            if policy.artifact_scope == 'full':
                artifact_slice['report_markdown'] = draft_content.get('report_markdown', '')[:5000]
                artifact_slice['report_json'] = draft_content.get('report_json')
            if policy.artifact_scope in ['full', 'questions']:
                artifact_slice['open_questions'] = list(draft_content.get('open_questions', []))[:5]
            artifact_slice['confidence'] = draft_content.get('confidence', 0.0)
        else:
            if policy.artifact_scope in ['full', 'summary']:
                artifact_slice['summary'] = (draft_content.summary[:1000] if hasattr(draft_content, 'summary') else '')
            if policy.artifact_scope == 'full':
                artifact_slice['report_markdown'] = (draft_content.report_markdown[:5000] if hasattr(draft_content, 'report_markdown') else '')
                artifact_slice['report_json'] = (draft_content.report_json if hasattr(draft_content, 'report_json') else None)
            if policy.artifact_scope in ['full', 'questions']:
                artifact_slice['open_questions'] = list(getattr(draft_content, 'open_questions', []))[:5]
            artifact_slice['confidence'] = getattr(draft_content, 'confidence', 0.0)

        return artifact_slice if artifact_slice else None

    def _item_to_dict(self, item: dict | Any) -> dict:
        """将项转换为字典形式。"""
        if isinstance(item, dict):
            return item
        if hasattr(item, 'model_dump'):
            return item.model_dump(mode='json')
        return {k: v for k, v in vars(item).items() if not k.startswith('_')}

    def _truncate_dict(self, data: dict | Any, max_chars: int) -> dict:
        """截断字典值以控制字符数。"""
        if not isinstance(data, dict):
            if hasattr(data, 'model_dump'):
                data = data.model_dump(mode='json')
            elif hasattr(data, '__dict__'):
                data = {k: v for k, v in vars(data).items() if not k.startswith('_')}
            else:
                return {'value': str(data)[:max_chars]}
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = value[:max_chars]
            elif isinstance(value, dict):
                result[key] = self._truncate_dict(value, max_chars // 2)
            else:
                result[key] = value
        return result
