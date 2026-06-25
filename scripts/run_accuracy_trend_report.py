"""准确率趋势报告脚本。

负责聚合多次 accuracy report，提取门禁状态、核心指标和 bucket 波动趋势，
并输出 JSON 与 Markdown 两类报告。脚本本身只做聚合与格式化，不参与评测执行。
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 统一切换到仓库根目录，保证导入、配置和路径解析一致。
os.chdir(PROJECT_ROOT)

from app.core.config import get_settings
from app.core.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='聚合多次准确率回归结果，生成历史趋势报告（JSON + Markdown）')
    parser.add_argument('--input-dir', default=None, help='accuracy report 所在目录，默认读取 settings.eval_dir')
    parser.add_argument('--output-dir', default=None, help='趋势报告输出目录，默认写入 settings.eval_dir')
    parser.add_argument('--report-name', default=None, help='趋势报告文件名前缀，默认自动生成时间戳名称')
    parser.add_argument('--limit', type=int, default=10, help='最多读取最近多少份 accuracy report，默认 10')
    parser.add_argument('--prefix', default='accuracy-report-', help='accuracy report 文件名前缀，默认 accuracy-report-')
    return parser


def load_accuracy_reports(input_dir: Path, prefix: str, limit: int) -> list[dict[str, Any]]:
    """读取 accuracy report 目录并提取可用结果。

    Args:
        input_dir: 报告目录。
        prefix: 文件名前缀过滤条件。
        limit: 最多保留的报告数量。

    Returns:
        按完成时间排序后的报告记录列表。
    """

    reports: list[dict[str, Any]] = []
    for path in sorted(input_dir.glob(f'{prefix}*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            continue
        result = payload.get('result')
        if not isinstance(result, dict):
            continue
        record = {
            'path': str(path),
            'payload': payload,
            'result': result,
            'gate': payload.get('gate') or result.get('gate') or {},
            'completed_at': _extract_completed_at(payload, result),
            'report_mode': payload.get('report_mode', '-'),
        }
        reports.append(record)

    reports.sort(key=lambda item: item['completed_at'])
    if limit > 0:
        reports = reports[-limit:]
    return reports


def build_trend_payload(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """把多次准确率报告聚合为趋势载荷。

    Args:
        reports: 已筛选的 accuracy report 列表。

    Returns:
        包含门禁、指标和 bucket 趋势的聚合结果。
    """

    if not reports:
        raise ValueError('未找到可用的 accuracy report JSON 文件')

    latest = reports[-1]
    gate_counts = Counter(str(item.get('gate', {}).get('status', 'unknown')) for item in reports)
    latest_result = latest['result']

    payload = {
        'generated_at': datetime.now().isoformat(),
        'report_count': len(reports),
        'latest_report_path': latest['path'],
        'latest_completed_at': latest['completed_at'],
        'latest_baseline_name': latest_result.get('baseline_name', '-'),
        'latest_candidate_strategy': latest.get('gate', {}).get('candidate_strategy', '-'),
        'gate_counts': dict(sorted(gate_counts.items())),
        'gate_history': _build_gate_history(reports),
        'metric_trends': _build_metric_trends(reports),
        'volatile_buckets': _build_bucket_volatility(reports),
    }
    payload['insights'] = _build_trend_insights(payload)
    return payload


def build_markdown_report(payload: dict[str, Any]) -> str:
    """把趋势载荷渲染为 Markdown 文本。

    Args:
        payload: 趋势聚合结果。

    Returns:
        可直接写入文件的 Markdown 报告内容。
    """

    lines = ['# Accuracy Trend Report', '']
    lines.append(f"- 报告数量: `{payload.get('report_count', 0)}`")
    lines.append(f"- 最新报告: `{payload.get('latest_report_path', '-')}`")
    lines.append(f"- 最新完成时间: `{payload.get('latest_completed_at', '-')}`")
    lines.append(f"- 最新基线策略: `{payload.get('latest_baseline_name', '-')}`")
    lines.append(f"- 最新候选策略: `{payload.get('latest_candidate_strategy', '-')}`")
    lines.append('')

    insights = payload.get('insights') or []
    if insights:
        lines.append('## Trend Insights')
        for item in insights:
            lines.append(f'- {item}')
        lines.append('')

    gate_counts = payload.get('gate_counts') or {}
    if gate_counts:
        lines.append('## Gate Summary')
        lines.extend(_build_gate_count_table(gate_counts))
        lines.append('')

    gate_history = payload.get('gate_history') or []
    if gate_history:
        lines.append('## Gate History')
        lines.extend(_build_gate_history_table(gate_history))
        lines.append('')

    metric_trends = payload.get('metric_trends') or []
    if metric_trends:
        lines.append('## Metric Trends')
        lines.extend(_build_metric_trend_table(metric_trends))
        lines.append('')

    volatile_buckets = payload.get('volatile_buckets') or []
    if volatile_buckets:
        lines.append('## Bucket Volatility')
        lines.extend(_build_bucket_volatility_table(volatile_buckets))
        lines.append('')

    return '\n'.join(lines).strip() + '\n'


def _build_gate_history(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取门禁历史记录。"""

    history: list[dict[str, Any]] = []
    for item in reports:
        result = item['result']
        gate = item.get('gate') or {}
        history.append(
            {
                'completed_at': item['completed_at'],
                'report_mode': item.get('report_mode', '-'),
                'baseline_name': result.get('baseline_name', '-'),
                'candidate_strategy': gate.get('candidate_strategy', '-'),
                'gate_status': gate.get('status', 'unknown'),
                'recommendation': gate.get('recommendation', '-'),
            }
        )
    return history


def _build_metric_trends(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取核心指标在窗口内的变化情况。"""

    metrics_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in reports:
        metrics = item['result'].get('metrics') or {}
        for metric_name, metric_payload in metrics.items():
            if not isinstance(metric_payload, dict):
                continue
            metrics_by_name.setdefault(metric_name, []).append(
                {
                    'completed_at': item['completed_at'],
                    'best_strategy': metric_payload.get('best_strategy', '-'),
                    'best_value': metric_payload.get('best_value'),
                    'baseline': metric_payload.get('baseline'),
                }
            )

    trend_rows: list[dict[str, Any]] = []
    for metric_name, history in metrics_by_name.items():
        history.sort(key=lambda item: item['completed_at'])
        first_item = history[0]
        latest_item = history[-1]
        delta = _safe_delta(first_item.get('best_value'), latest_item.get('best_value'))
        trend_rows.append(
            {
                'metric': metric_name,
                'first_best_value': first_item.get('best_value'),
                'latest_best_value': latest_item.get('best_value'),
                'delta': delta,
                'latest_best_strategy': latest_item.get('best_strategy', '-'),
                'baseline_latest': latest_item.get('baseline'),
            }
        )
    trend_rows.sort(key=lambda item: item['metric'])
    return trend_rows


def _build_bucket_volatility(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """计算 bucket 维度最优策略的切换频次。"""

    bucket_metric_history: dict[tuple[str, str], list[str]] = {}
    for item in reports:
        bucket_metrics = item['result'].get('bucket_metrics') or {}
        for bucket, metric_map in bucket_metrics.items():
            if not isinstance(metric_map, dict):
                continue
            for metric_name, payload in metric_map.items():
                if not isinstance(payload, dict):
                    continue
                bucket_metric_history.setdefault((str(bucket), str(metric_name)), []).append(str(payload.get('best_strategy', '-')))

    rows: list[dict[str, Any]] = []
    for (bucket, metric_name), history in bucket_metric_history.items():
        change_count = _count_strategy_changes(history)
        if change_count <= 0:
            continue
        rows.append(
            {
                'bucket': bucket,
                'metric': metric_name,
                'change_count': change_count,
                'latest_best_strategy': history[-1] if history else '-',
            }
        )
    rows.sort(key=lambda item: (-int(item['change_count']), str(item['bucket']), str(item['metric'])))
    return rows[:10]


def _build_trend_insights(payload: dict[str, Any]) -> list[str]:
    """基于聚合结果生成简短趋势洞察。"""

    insights: list[str] = []
    gate_counts = payload.get('gate_counts') or {}
    gate_history = payload.get('gate_history') or []
    metric_trends = payload.get('metric_trends') or []
    volatile_buckets = payload.get('volatile_buckets') or []

    latest_gate = gate_history[-1]['gate_status'] if gate_history else 'unknown'
    if latest_gate == 'fail':
        insights.append('最近一次回归门禁为 `fail`，当前不建议直接放量。')
    elif latest_gate == 'warn':
        insights.append('最近一次回归门禁为 `warn`，建议先灰度并继续观察延迟与 bucket 表现。')
    elif latest_gate == 'pass':
        insights.append('最近一次回归门禁为 `pass`，当前回归结果适合作为默认候选。')

    fail_count = int(gate_counts.get('fail', 0))
    warn_count = int(gate_counts.get('warn', 0))
    if fail_count or warn_count:
        insights.append(f'最近窗口内共有 `{fail_count}` 次 `fail`、`{warn_count}` 次 `warn`，建议优先排查不稳定波动。')

    strongest_metric = _pick_strongest_metric(metric_trends)
    if strongest_metric is not None:
        insights.append(
            f"窗口内提升最明显的指标是 `{strongest_metric['metric']}`，累计变化 `{_format_delta(strongest_metric['delta'])}`。"
        )

    if volatile_buckets:
        top_bucket = volatile_buckets[0]
        insights.append(
            f"波动最大的 bucket 是 `{top_bucket['bucket']}` / `{top_bucket['metric']}`，最优策略切换了 `{top_bucket['change_count']}` 次。"
        )

    if not insights:
        insights.append('当前趋势窗口未发现明显异常，建议继续累积更多回归结果后再观察长期变化。')
    return insights


def _pick_strongest_metric(metric_trends: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选出窗口内波动幅度最大的指标。"""

    best_item: dict[str, Any] | None = None
    for item in metric_trends:
        delta = item.get('delta')
        if delta is None:
            continue
        try:
            numeric_delta = float(delta)
        except (TypeError, ValueError):
            continue
        if best_item is None or abs(numeric_delta) > abs(float(best_item['delta'])):
            best_item = item
    return best_item


def _count_strategy_changes(history: list[str]) -> int:
    """统计同一 bucket 指标的最优策略切换次数。"""

    if len(history) < 2:
        return 0
    change_count = 0
    previous = history[0]
    for item in history[1:]:
        if item != previous:
            change_count += 1
        previous = item
    return change_count


def _safe_delta(first: Any, latest: Any) -> float | None:
    """安全计算两个值的差值。"""

    if first is None or latest is None:
        return None
    try:
        return float(latest) - float(first)
    except (TypeError, ValueError):
        return None


def _extract_completed_at(payload: dict[str, Any], result: dict[str, Any]) -> str:
    """从结果或外层载荷中提取完成时间。"""

    completed_at = result.get('completed_at') or payload.get('generated_at')
    if completed_at:
        return str(completed_at)
    return ''


def _build_gate_count_table(gate_counts: dict[str, Any]) -> list[str]:
    """构造门禁状态分布表。"""

    headers = ['Gate Status', 'Count']
    rows = [_markdown_table_header(headers)]
    for status, count in sorted(gate_counts.items()):
        rows.append(_markdown_table_row([status, count]))
    return rows


def _build_gate_history_table(gate_history: list[dict[str, Any]]) -> list[str]:
    """构造门禁历史表。"""

    headers = ['Completed At', 'Mode', 'Baseline', 'Candidate', 'Gate', 'Recommendation']
    rows = [_markdown_table_header(headers)]
    for item in gate_history:
        rows.append(
            _markdown_table_row(
                [
                    item.get('completed_at', '-'),
                    item.get('report_mode', '-'),
                    item.get('baseline_name', '-'),
                    item.get('candidate_strategy', '-'),
                    item.get('gate_status', '-'),
                    item.get('recommendation', '-'),
                ]
            )
        )
    return rows


def _build_metric_trend_table(metric_trends: list[dict[str, Any]]) -> list[str]:
    """构造指标趋势表。"""

    headers = ['Metric', 'First Best', 'Latest Best', 'Delta', 'Latest Best Strategy', 'Latest Baseline']
    rows = [_markdown_table_header(headers)]
    for item in metric_trends:
        rows.append(
            _markdown_table_row(
                [
                    item.get('metric', '-'),
                    _format_metric_value(item.get('first_best_value')),
                    _format_metric_value(item.get('latest_best_value')),
                    _format_delta(item.get('delta')),
                    item.get('latest_best_strategy', '-'),
                    _format_metric_value(item.get('baseline_latest')),
                ]
            )
        )
    return rows


def _build_bucket_volatility_table(volatile_buckets: list[dict[str, Any]]) -> list[str]:
    """构造 bucket 波动表。"""

    headers = ['Bucket', 'Metric', 'Strategy Changes', 'Latest Best Strategy']
    rows = [_markdown_table_header(headers)]
    for item in volatile_buckets:
        rows.append(
            _markdown_table_row(
                [
                    item.get('bucket', '-'),
                    item.get('metric', '-'),
                    item.get('change_count', 0),
                    item.get('latest_best_strategy', '-'),
                ]
            )
        )
    return rows


def _markdown_table_header(headers: list[str]) -> str:
    """构造 Markdown 表头与分隔行。"""

    return _markdown_table_row(headers) + '\n' + _markdown_table_row(['---'] * len(headers))


def _markdown_table_row(values: list[Any]) -> str:
    """把一行值渲染为 Markdown 表格行。"""

    return '| ' + ' | '.join(str(value) for value in values) + ' |'


def _format_metric_value(value: Any) -> str:
    """格式化指标值，统一空值和浮点展示。"""

    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.4f}'
    return str(value)


def _format_delta(value: Any) -> str:
    """格式化增减量。"""

    if value is None:
        return '-'
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f'{numeric:+.4f}'


def generate_trend_report(
    *,
    input_dir: str | None = None,
    output_dir: str | None = None,
    report_name: str | None = None,
    limit: int = 10,
    prefix: str = 'accuracy-report-',
) -> dict[str, Any]:
    """执行准确率趋势报告生成流程。

    Args:
        input_dir: 输入目录。
        output_dir: 输出目录。
        report_name: 报告文件名前缀。
        limit: 趋势窗口大小。
        prefix: 单次报告文件名前缀。

    Returns:
        包含趋势载荷和产物路径的结果字典。
    """

    settings = get_settings()
    configure_logging(settings.log_level)

    resolved_input_dir = Path(input_dir).expanduser().resolve() if input_dir else settings.eval_dir
    if not resolved_input_dir.exists():
        raise FileNotFoundError(f'报告目录不存在: {resolved_input_dir}')

    reports = load_accuracy_reports(resolved_input_dir, prefix=prefix, limit=limit)
    trend_payload = build_trend_payload(reports)

    resolved_output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.eval_dir
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_report_name = report_name or f"accuracy-trend-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path = resolved_output_dir / f'{resolved_report_name}.json'
    markdown_path = resolved_output_dir / f'{resolved_report_name}.md'

    json_path.write_text(json.dumps(trend_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    markdown_path.write_text(build_markdown_report(trend_payload), encoding='utf-8')
    return {
        'report_count': trend_payload.get('report_count', 0),
        'trend_payload': trend_payload,
        'json_path': str(json_path),
        'markdown_path': str(markdown_path),
    }


def main() -> int:
    """执行命令行入口。

    Returns:
        进程退出码。正常完成时返回 `0`。
    """

    args = build_parser().parse_args()
    output = generate_trend_report(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        report_name=args.report_name,
        limit=args.limit,
        prefix=args.prefix,
    )

    print(f'report_count: {output.get("report_count")}')
    print(f'json_report: {output.get("json_path")}')
    print(f'markdown_report: {output.get("markdown_path")}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
