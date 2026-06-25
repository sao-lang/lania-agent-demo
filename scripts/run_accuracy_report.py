"""准确率回归报告脚本。

负责根据运行时能力选择 RAGAS 对比或回放对比模式，生成单次准确率回归报告，
并附带 Markdown 摘要与发布门禁判断。脚本本身只承担参数解析、结果聚合和产物落盘职责。
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 统一切换到仓库根目录，保证导入、配置和相对路径解析行为一致。
os.chdir(PROJECT_ROOT)

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.eval import EvalStrategyConfig, RagasCompareRequest, ReplayCompareRequest


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='一键生成准确率回归报告（JSON + Markdown）')
    parser.add_argument('--dataset-path', required=True, help='评测集 JSON 文件路径')
    parser.add_argument('--collection-name', required=True, help='知识库名称')
    parser.add_argument('--baseline-name', default=None, help='基线策略名称，默认取第一条策略')
    parser.add_argument('--strategies-path', default=None, help='策略 JSON 文件路径（可选）')
    parser.add_argument(
        '--strategy-preset',
        choices=['accuracy'],
        default='accuracy',
        help='未传 strategies-path 时使用的内置策略预设，默认 accuracy',
    )
    parser.add_argument('--output-dir', default=None, help='报告输出目录，默认写入 settings.eval_dir')
    parser.add_argument('--report-name', default=None, help='报告文件名前缀，默认自动生成时间戳名称')
    parser.add_argument(
        '--max-latency-increase-ms',
        type=float,
        default=80.0,
        help='相对基线可接受的最大平均延迟增量，默认 80ms',
    )
    parser.add_argument(
        '--max-quality-regression',
        type=float,
        default=0.0,
        help='相对基线可接受的最大质量指标回退，默认不允许回退',
    )
    parser.add_argument(
        '--fail-on-gate-fail',
        action='store_true',
        help='当回归门禁失败时返回非 0 退出码',
    )
    return parser


def load_strategies(path: str | None) -> list[EvalStrategyConfig] | None:
    """从 JSON 文件加载策略配置列表。

    Args:
        path: 策略文件路径。未传时返回 `None`。

    Returns:
        解析后的策略配置列表，或 `None`。
    """

    if not path:
        return None
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise ValueError('strategies_path 必须是 JSON 数组')
    return [EvalStrategyConfig(**item) for item in payload]


def build_markdown_report(result: dict[str, Any]) -> str:
    """把单次准确率回归结果渲染为 Markdown 报告。

    Args:
        result: 单次回归结果字典。

    Returns:
        可直接写入文件的 Markdown 文本。
    """

    lines: list[str] = []
    title = f"# Accuracy Regression Report: {result.get('compare_id', 'unknown')}"
    lines.append(title)
    lines.append('')
    lines.append(f"- 数据集: `{result.get('dataset_path', '-')}`")
    lines.append(f"- 知识库: `{result.get('collection_name', '-')}`")
    lines.append(f"- 基线策略: `{result.get('baseline_name', '-')}`")
    lines.append(f"- 完成时间: `{result.get('completed_at', '-')}`")
    lines.append(f"- 汇总: {result.get('summary', '-')}")
    lines.append('')

    insights = _build_report_insights(result)
    if insights:
        lines.append('## Report Insights')
        for item in insights:
            lines.append(f'- {item}')
        lines.append('')

    gate = result.get('gate') or _build_release_gate(
        result,
        max_latency_increase_ms=80.0,
        max_quality_regression=0.0,
    )
    if gate:
        lines.append('## Release Gate')
        lines.extend(_build_gate_summary(gate))
        lines.append('')

    strategies = result.get('strategies') or []
    if strategies:
        lines.append('## Strategy Flags')
        lines.extend(_build_strategy_flag_table(strategies))
        lines.append('')
        lines.append('## Strategy Summary')
        lines.extend(_build_strategy_summary_table(strategies))
        lines.append('')

    metrics = result.get('metrics') or {}
    if metrics:
        lines.append('## Metric Summary')
        lines.extend(_build_metric_table(metrics))
        lines.append('')

    bucket_metrics = result.get('bucket_metrics') or {}
    if bucket_metrics:
        lines.append('## Bucket Metrics')
        lines.extend(_build_bucket_metric_table(bucket_metrics))
        lines.append('')

    return '\n'.join(lines).strip() + '\n'


def _build_report_insights(result: dict[str, Any]) -> list[str]:
    """根据回归结果生成简短中文洞察。"""

    insights: list[str] = []
    baseline_name = str(result.get('baseline_name') or '-')
    metrics = result.get('metrics') or {}
    bucket_metrics = result.get('bucket_metrics') or {}
    strategies = result.get('strategies') or []

    recommended_strategy, recommended_metric = _pick_recommended_strategy(metrics, baseline_name)
    if recommended_strategy is not None:
        if recommended_strategy == baseline_name:
            insights.append(f'当前默认基线仍建议保持 `{baseline_name}`，主要指标没有出现更优替代策略。')
        else:
            insights.append(
                f'默认建议优先考虑 `{recommended_strategy}`，它在核心指标 `{recommended_metric}` 上表现最好。'
            )
            flag_recommendation = _build_flag_recommendation(_find_strategy_item(strategies, recommended_strategy))
            if flag_recommendation:
                insights.append(flag_recommendation)

    key_gain = _pick_key_gain(metrics, baseline_name)
    if key_gain is not None:
        metric_name, strategy_name, delta = key_gain
        insights.append(
            f'当前最明显的增益点是 `{metric_name}`，`{strategy_name}` 相对基线提升 `{_format_delta(delta)}`。'
        )

    bucket_focus = _pick_bucket_focus(bucket_metrics, baseline_name)
    if bucket_focus:
        bucket_preview = '、'.join(f'`{bucket}`' for bucket in bucket_focus)
        insights.append(f'建议重点查看这些 bucket 的策略差异：{bucket_preview}。')

    if not insights:
        insights.append('本次报告未提取到显著差异，建议直接查看下方策略表和指标表。')
    return insights


def _build_gate_summary(gate: dict[str, Any]) -> list[str]:
    """把门禁结果渲染为简短列表。"""

    lines = [
        f"- 判定: `{gate.get('status', 'unknown')}`",
        f"- 候选策略: `{gate.get('candidate_strategy', '-')}`",
        f"- 推荐动作: {gate.get('recommendation', '-')}",
    ]
    reasons = gate.get('reasons') or []
    if reasons:
        lines.append(f"- 依据: {'；'.join(str(item) for item in reasons)}")
    return lines


def _build_strategy_flag_table(strategies: list[dict[str, Any]]) -> list[str]:
    """构造策略开关对照表。"""

    headers = [
        'Strategy',
        'Rewrite',
        'Hybrid',
        'Rerank',
        'Parent',
        'Q-Index',
        'Corrective',
        'Graph',
        'Hops',
    ]
    rows = [_markdown_table_header(headers)]
    for item in strategies:
        strategy = item.get('strategy', {})
        rows.append(
            _markdown_table_row(
                [
                    strategy.get('name', '-'),
                    _bool_cell(strategy.get('use_query_rewrite', False)),
                    _bool_cell(strategy.get('use_hybrid_retrieval', False)),
                    _bool_cell(strategy.get('use_rerank', False)),
                    _bool_cell(strategy.get('use_parent_chunk_retrieval', False)),
                    _bool_cell(strategy.get('use_question_oriented_index', False)),
                    _bool_cell(strategy.get('use_corrective_rag', False)),
                    _bool_cell(strategy.get('use_graph_rag', False)),
                    strategy.get('graph_max_hops', '-') if strategy.get('use_graph_rag', False) else '-',
                ]
            )
        )
    return rows


def _build_strategy_summary_table(strategies: list[dict[str, Any]]) -> list[str]:
    """构造策略摘要表。

    当存在任务级结果时展示质量指标，否则展示基础成功率与耗时摘要。
    """

    has_task_results = any(isinstance(item.get('task'), dict) for item in strategies)
    if has_task_results:
        headers = [
            'Strategy',
            'Status',
            'Samples',
            'Success',
            'Faithfulness',
            'Answer Rel',
            'Ctx Precision',
            'Ctx Recall',
        ]
        rows = [_markdown_table_header(headers)]
        for item in strategies:
            strategy = item.get('strategy', {})
            task = item.get('task', {})
            metric_map = task.get('metrics') or {}
            rows.append(
                _markdown_table_row(
                    [
                        strategy.get('name', '-'),
                        task.get('status', '-'),
                        task.get('sample_count', 0),
                        task.get('success_count', 0),
                        _format_metric_value(metric_map.get('faithfulness')),
                        _format_metric_value(metric_map.get('answer_relevancy') or metric_map.get('answer_relevance')),
                        _format_metric_value(metric_map.get('context_precision')),
                        _format_metric_value(metric_map.get('context_recall')),
                    ]
                )
            )
        return rows

    headers = ['Strategy', 'Samples', 'Success', 'Success Rate', 'Avg Retrieved', 'Avg Latency']
    rows = [_markdown_table_header(headers)]
    for item in strategies:
        strategy = item.get('strategy', {})
        sample_count = int(item.get('sample_count', 0) or 0)
        success_count = int(item.get('success_count', 0) or 0)
        success_rate = (success_count / sample_count) if sample_count else 0.0
        rows.append(
            _markdown_table_row(
                [
                    strategy.get('name', '-'),
                    sample_count,
                    success_count,
                    _format_metric_value(success_rate),
                    _format_metric_value(item.get('avg_retrieved_count')),
                    _format_metric_value(item.get('avg_latency_ms')),
                ]
            )
        )
    return rows


def _build_metric_table(metrics: dict[str, Any]) -> list[str]:
    """构造核心指标对比表。"""

    headers = ['Metric', 'Baseline', 'Best Strategy', 'Best Value', 'Deltas']
    rows = [_markdown_table_header(headers)]
    for metric_name, payload in metrics.items():
        delta_map = payload.get('deltas') or {}
        delta_preview = '; '.join(f'{name}={_format_delta(value)}' for name, value in delta_map.items()) or '-'
        rows.append(
            _markdown_table_row(
                [
                    metric_name,
                    _format_metric_value(payload.get('baseline')),
                    payload.get('best_strategy', '-'),
                    _format_metric_value(payload.get('best_value')),
                    delta_preview,
                ]
            )
        )
    return rows


def _build_bucket_metric_table(bucket_metrics: dict[str, Any]) -> list[str]:
    """构造 bucket 维度的指标摘要表。"""

    headers = ['Bucket', 'Metric', 'Baseline', 'Best Strategy', 'Best Value']
    rows = [_markdown_table_header(headers)]
    for bucket, metrics_payload in bucket_metrics.items():
        for metric_name, payload in metrics_payload.items():
            rows.append(
                _markdown_table_row(
                    [
                        bucket,
                        metric_name,
                        _format_metric_value(payload.get('baseline')),
                        payload.get('best_strategy', '-'),
                        _format_metric_value(payload.get('best_value')),
                    ]
                )
            )
    return rows


def _pick_recommended_strategy(metrics: dict[str, Any], baseline_name: str) -> tuple[str | None, str | None]:
    """根据指标优先级选出推荐策略与依据指标。"""

    priority_metrics = [
        'faithfulness',
        'answer_relevancy',
        'answer_relevance',
        'context_precision',
        'context_recall',
        'success_rate',
    ]
    for metric_name in priority_metrics:
        payload = metrics.get(metric_name)
        if not isinstance(payload, dict):
            continue
        best_strategy = payload.get('best_strategy')
        if best_strategy:
            return str(best_strategy), metric_name

    strategy_score: dict[str, int] = {}
    for payload in metrics.values():
        if not isinstance(payload, dict):
            continue
        best_strategy = payload.get('best_strategy')
        if not best_strategy:
            continue
        strategy_score[str(best_strategy)] = strategy_score.get(str(best_strategy), 0) + 1
    if not strategy_score:
        return (baseline_name if baseline_name != '-' else None), None
    best_name = max(strategy_score.items(), key=lambda item: item[1])[0]
    return best_name, 'overall'


def _pick_key_gain(metrics: dict[str, Any], baseline_name: str) -> tuple[str, str, float] | None:
    """找出相对基线增益最明显的指标项。"""

    best_item: tuple[str, str, float] | None = None
    for metric_name, payload in metrics.items():
        if not isinstance(payload, dict):
            continue
        best_strategy = payload.get('best_strategy')
        deltas = payload.get('deltas') or {}
        if not best_strategy or best_strategy == baseline_name:
            continue
        delta = deltas.get(best_strategy)
        if delta is None:
            baseline = payload.get('baseline')
            best_value = payload.get('best_value')
            if baseline is None or best_value is None:
                continue
            try:
                delta = float(best_value) - float(baseline)
            except (TypeError, ValueError):
                continue
        try:
            numeric_delta = float(delta)
        except (TypeError, ValueError):
            continue
        if best_item is None or abs(numeric_delta) > abs(best_item[2]):
            best_item = (metric_name, str(best_strategy), numeric_delta)
    return best_item


def _pick_bucket_focus(bucket_metrics: dict[str, Any], baseline_name: str) -> list[str]:
    """找出最值得重点关注的 bucket 列表。"""

    bucket_scores: dict[str, int] = {}
    for bucket, metrics_payload in bucket_metrics.items():
        if not isinstance(metrics_payload, dict):
            continue
        for payload in metrics_payload.values():
            if not isinstance(payload, dict):
                continue
            best_strategy = payload.get('best_strategy')
            if best_strategy and str(best_strategy) != baseline_name:
                bucket_scores[str(bucket)] = bucket_scores.get(str(bucket), 0) + 1
    ranked = sorted(bucket_scores.items(), key=lambda item: item[1], reverse=True)
    return [bucket for bucket, _ in ranked[:3]]


def _build_release_gate(
    result: dict[str, Any],
    max_latency_increase_ms: float,
    max_quality_regression: float,
) -> dict[str, Any]:
    """根据质量和延迟阈值构造发布门禁结果。

    Args:
        result: 单次回归结果。
        max_latency_increase_ms: 允许的平均延迟增量阈值。
        max_quality_regression: 允许的质量回退阈值。

    Returns:
        包含状态、推荐动作和原因的门禁结果字典。
    """

    baseline_name = str(result.get('baseline_name') or '-')
    metrics = result.get('metrics') or {}
    strategies = result.get('strategies') or []
    candidate_strategy, _ = _pick_recommended_strategy(metrics, baseline_name)
    candidate_strategy = candidate_strategy or baseline_name

    reasons: list[str] = []
    status = 'pass'
    recommendation = f'建议继续保持 `{baseline_name}`'

    if candidate_strategy and candidate_strategy != baseline_name:
        recommendation = f'建议优先灰度 `{candidate_strategy}`，并继续观察关键 bucket 表现'

    quality_issues = _collect_quality_regressions(metrics, baseline_name, candidate_strategy, max_quality_regression)
    if quality_issues:
        status = 'fail'
        reasons.extend(quality_issues)
        recommendation = '不建议直接切默认策略，优先处理质量回退项'

    latency_issue = _check_latency_tradeoff(strategies, baseline_name, candidate_strategy, max_latency_increase_ms)
    if latency_issue is not None:
        if status != 'fail':
            status = 'warn'
            recommendation = f'建议先灰度 `{candidate_strategy}`，关注延迟开销'
        reasons.append(latency_issue)

    if not reasons:
        if candidate_strategy == baseline_name:
            reasons.append('主要指标未发现比当前基线更稳妥的替代策略。')
        else:
            reasons.append(f'候选策略 `{candidate_strategy}` 在核心指标上优于或不劣于当前基线。')

    return {
        'status': status,
        'candidate_strategy': candidate_strategy,
        'baseline_name': baseline_name,
        'recommendation': recommendation,
        'reasons': reasons,
        'thresholds': {
            'max_latency_increase_ms': max_latency_increase_ms,
            'max_quality_regression': max_quality_regression,
        },
    }


def _collect_quality_regressions(
    metrics: dict[str, Any],
    baseline_name: str,
    candidate_strategy: str | None,
    max_quality_regression: float,
) -> list[str]:
    """收集候选策略相对基线的质量回退项。"""

    if not candidate_strategy or candidate_strategy == baseline_name:
        return []

    guarded_metrics = [
        'faithfulness',
        'answer_relevancy',
        'answer_relevance',
        'context_precision',
        'context_recall',
        'success_rate',
    ]
    issues: list[str] = []
    for metric_name in guarded_metrics:
        payload = metrics.get(metric_name)
        if not isinstance(payload, dict):
            continue
        delta_map = payload.get('deltas') or {}
        delta = delta_map.get(candidate_strategy)
        if delta is None:
            continue
        try:
            numeric_delta = float(delta)
        except (TypeError, ValueError):
            continue
        if numeric_delta < -abs(max_quality_regression):
            issues.append(f'`{metric_name}` 相对基线回退 `{_format_delta(numeric_delta)}`，超过允许阈值。')
    return issues


def _check_latency_tradeoff(
    strategies: list[dict[str, Any]],
    baseline_name: str,
    candidate_strategy: str | None,
    max_latency_increase_ms: float,
) -> str | None:
    """检查候选策略是否带来不可接受的延迟开销。"""

    if not candidate_strategy or candidate_strategy == baseline_name:
        return None

    baseline_item = _find_strategy_item(strategies, baseline_name)
    candidate_item = _find_strategy_item(strategies, candidate_strategy)
    if baseline_item is None or candidate_item is None:
        return None

    baseline_latency = baseline_item.get('avg_latency_ms')
    candidate_latency = candidate_item.get('avg_latency_ms')
    if baseline_latency is None or candidate_latency is None:
        return None

    try:
        delta = float(candidate_latency) - float(baseline_latency)
    except (TypeError, ValueError):
        return None
    if delta > max_latency_increase_ms:
        return (
            f'候选策略 `{candidate_strategy}` 平均延迟比基线高 `{_format_delta(delta)}ms`，'
            f'超过阈值 `{max_latency_increase_ms:.1f}ms`。'
        )
    return None


def _find_strategy_item(strategies: list[dict[str, Any]], strategy_name: str) -> dict[str, Any] | None:
    """按策略名称查找对应的结果项。"""

    for item in strategies:
        strategy = item.get('strategy') or {}
        if strategy.get('name') == strategy_name:
            return item
    return None


def _build_flag_recommendation(strategy_item: dict[str, Any] | None) -> str | None:
    """把推荐策略的开关组合整理为可读建议。"""

    if strategy_item is None:
        return None
    strategy = strategy_item.get('strategy') or {}
    enabled_flags = []
    if strategy.get('use_query_rewrite'):
        enabled_flags.append('query_rewrite')
    if strategy.get('use_hybrid_retrieval'):
        enabled_flags.append('hybrid_retrieval')
    if strategy.get('use_rerank'):
        enabled_flags.append('rerank')
    if strategy.get('use_parent_chunk_retrieval'):
        enabled_flags.append('parent_chunk_retrieval')
    if strategy.get('use_question_oriented_index'):
        enabled_flags.append('question_oriented_index')
    if strategy.get('use_corrective_rag'):
        enabled_flags.append('corrective_rag')
    if strategy.get('use_graph_rag'):
        hops = strategy.get('graph_max_hops')
        enabled_flags.append(f'graph_rag({hops}hop)' if hops else 'graph_rag')
    if not enabled_flags:
        return None
    return '建议优先采用这组开关组合：' + ' + '.join(f'`{item}`' for item in enabled_flags) + '。'


def _markdown_table_header(headers: list[str]) -> str:
    """构造 Markdown 表头与分隔行。"""

    return _markdown_table_row(headers) + '\n' + _markdown_table_row(['---'] * len(headers))


def _markdown_table_row(values: list[Any]) -> str:
    """把一行值渲染为 Markdown 表格行。"""

    return '| ' + ' | '.join(str(value) for value in values) + ' |'


def _format_metric_value(value: Any) -> str:
    """格式化指标值，统一空值与浮点展示风格。"""

    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.4f}'
    return str(value)


def _format_delta(value: Any) -> str:
    """格式化指标增减量。"""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f'{numeric:+.4f}'


def _bool_cell(value: bool) -> str:
    """把布尔值渲染为表格友好的标记。"""

    return 'Y' if bool(value) else '-'


def resolve_default_strategies(container: Any, preset: str) -> list[EvalStrategyConfig]:
    """根据预设名称获取默认策略集合。"""

    if preset == 'accuracy':
        return container.eval_service.build_default_compare_strategies()
    raise ValueError(f'不支持的策略预设: {preset}')


def generate_accuracy_report(
    *,
    dataset_path: str,
    collection_name: str,
    baseline_name: str | None = None,
    strategies_path: str | None = None,
    strategy_preset: str = 'accuracy',
    output_dir: str | None = None,
    report_name: str | None = None,
    max_latency_increase_ms: float = 80.0,
    max_quality_regression: float = 0.0,
) -> dict[str, Any]:
    """执行单次准确率回归并输出报告产物。

    Args:
        dataset_path: 评测集路径。
        collection_name: 知识库名称。
        baseline_name: 基线策略名称。
        strategies_path: 策略文件路径。
        strategy_preset: 默认策略预设名称。
        output_dir: 输出目录。
        report_name: 报告文件名前缀。
        max_latency_increase_ms: 允许的平均延迟增量阈值。
        max_quality_regression: 允许的质量回退阈值。

    Returns:
        包含单次结果、门禁和产物路径的结果字典。
    """

    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)

    strategies = load_strategies(strategies_path) or resolve_default_strategies(container, strategy_preset)
    resolved_dataset_path = str(Path(dataset_path).expanduser().resolve())
    runtime = container.eval_service.get_runtime_status()

    # 运行时准备就绪时优先走 RAGAS 对比，否则退回回放对比。
    if runtime['ready']:
        payload = RagasCompareRequest(
            dataset_path=resolved_dataset_path,
            collection_name=collection_name,
            strategies=strategies,
            baseline_name=baseline_name,
        )
        response = container.eval_service.compare_tasks(payload).model_dump(mode='json')
        report_mode = 'ragas_compare'
    else:
        payload = ReplayCompareRequest(
            dataset_path=resolved_dataset_path,
            collection_name=collection_name,
            strategies=strategies,
            baseline_name=baseline_name,
        )
        response = container.eval_service.compare_replay(payload).model_dump(mode='json')
        report_mode = 'replay_compare'

    gate = _build_release_gate(
        response,
        max_latency_increase_ms=max_latency_increase_ms,
        max_quality_regression=max_quality_regression,
    )
    response['gate'] = gate

    resolved_output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.eval_dir
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_report_name = report_name or f"accuracy-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path = resolved_output_dir / f'{resolved_report_name}.json'
    markdown_path = resolved_output_dir / f'{resolved_report_name}.md'

    report_payload = {
        'report_mode': report_mode,
        'runtime_ready': runtime.get('ready', False),
        'runtime_reason': runtime.get('reason'),
        'gate': gate,
        'result': response,
    }
    json_path.write_text(json.dumps(report_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    markdown_path.write_text(build_markdown_report(response), encoding='utf-8')
    return {
        'report_mode': report_mode,
        'runtime_ready': runtime.get('ready', False),
        'runtime_reason': runtime.get('reason'),
        'gate': gate,
        'result': response,
        'json_path': str(json_path),
        'markdown_path': str(markdown_path),
    }


def main() -> int:
    """执行命令行入口。

    Returns:
        进程退出码。门禁失败且启用严格模式时返回 `2`。
    """

    args = build_parser().parse_args()
    output = generate_accuracy_report(
        dataset_path=args.dataset_path,
        collection_name=args.collection_name,
        baseline_name=args.baseline_name,
        strategies_path=args.strategies_path,
        strategy_preset=args.strategy_preset,
        output_dir=args.output_dir,
        report_name=args.report_name,
        max_latency_increase_ms=args.max_latency_increase_ms,
        max_quality_regression=args.max_quality_regression,
    )

    print(f"report_mode: {output.get('report_mode')}")
    print(f"gate_status: {output.get('gate', {}).get('status')}")
    print(f"json_report: {output.get('json_path')}")
    print(f"markdown_report: {output.get('markdown_path')}")
    if args.fail_on_gate_fail and output.get('gate', {}).get('status') == 'fail':
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
