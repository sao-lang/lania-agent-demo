"""Document Analysis 趋势报告脚本。

负责聚合多次 benchmark 报告，提取门禁、指标和工具调用的时间窗口趋势，
并输出 JSON 与 Markdown 结果。脚本本身只做结果整合与格式化。
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

# 统一切换到仓库根目录，保证导入与相对路径解析行为一致。
os.chdir(PROJECT_ROOT)

from app.core.config import get_settings
from app.core.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='聚合多次 Document Analysis benchmark 结果，生成趋势报告')
    parser.add_argument('--input-dir', default=None)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--report-name', default=None)
    parser.add_argument('--collection-name', default=None)
    parser.add_argument('--limit', type=int, default=10)
    return parser


def load_benchmark_reports(input_dir: Path, limit: int, collection_name: str | None = None) -> list[dict[str, Any]]:
    """读取并筛选 Document Analysis benchmark 报告。

    Args:
        input_dir: 报告目录。
        limit: 最多保留的报告数量。
        collection_name: 可选的知识库过滤条件。

    Returns:
        按完成时间排序后的报告列表。
    """

    reports: list[dict[str, Any]] = []
    normalized_collection_name = str(collection_name or '').strip()
    for path in sorted(input_dir.glob('*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(payload, dict) or payload.get('report_mode') != 'document_analysis_benchmark':
            continue
        result = payload.get('result')
        dashboard_summary = payload.get('dashboard_summary')
        if not isinstance(result, dict) or not isinstance(dashboard_summary, dict):
            continue
        if normalized_collection_name and str(result.get('collection_name') or '').strip() != normalized_collection_name:
            continue
        reports.append(
            {
                'path': str(path),
                'payload': payload,
                'result': result,
                'dashboard_summary': dashboard_summary,
                'gate': payload.get('gate') or result.get('gate') or {},
                'completed_at': str(result.get('completed_at') or ''),
            }
        )
    reports.sort(key=lambda item: item['completed_at'])
    if limit > 0:
        reports = reports[-limit:]
    return reports


def build_trend_payload(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """把多次 benchmark 结果聚合为趋势载荷。

    Args:
        reports: 已筛选的 benchmark 报告列表。

    Returns:
        包含门禁、指标和工具趋势的聚合结果。
    """

    if not reports:
        raise ValueError('未找到可用的 document analysis benchmark 报告')
    latest = reports[-1]
    gate_counts = Counter(str(item.get('gate', {}).get('status', 'unknown')) for item in reports)
    payload = {
        'generated_at': datetime.now().isoformat(),
        'report_count': len(reports),
        'latest_report_path': latest['path'],
        'latest_completed_at': latest['completed_at'],
        'latest_benchmark_id': latest['result'].get('benchmark_id', '-'),
        'latest_collection_name': latest['result'].get('collection_name', '-'),
        'gate_counts': dict(sorted(gate_counts.items())),
        'gate_history': _build_gate_history(reports),
        'metric_trends': _build_metric_trends(reports),
        'tool_trends': _build_tool_trends(reports),
        'latest_bucket_breakdown': (latest.get('dashboard_summary') or {}).get('bucket_breakdown') or {},
        'latest_worst_samples': (latest.get('dashboard_summary') or {}).get('worst_samples') or [],
    }
    payload['insights'] = _build_insights(payload)
    return payload


def build_markdown_report(payload: dict[str, Any]) -> str:
    """把趋势载荷渲染为 Markdown 报告。

    Args:
        payload: 趋势聚合结果。

    Returns:
        可直接写入文件的 Markdown 文本。
    """

    lines = ['# Document Analysis Trend Report', '']
    lines.append(f"- 报告数量: `{payload.get('report_count', 0)}`")
    lines.append(f"- 最新报告: `{payload.get('latest_report_path', '-')}`")
    lines.append(f"- 最新完成时间: `{payload.get('latest_completed_at', '-')}`")
    lines.append(f"- 最新 Benchmark: `{payload.get('latest_benchmark_id', '-')}`")
    lines.append(f"- 最新知识库: `{payload.get('latest_collection_name', '-')}`")
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
        lines.extend(_build_simple_table(['Gate', 'Count'], [[k, v] for k, v in sorted(gate_counts.items())]))
        lines.append('')

    gate_history = payload.get('gate_history') or []
    if gate_history:
        lines.append('## Gate History')
        lines.extend(
            _build_simple_table(
                ['Completed At', 'Benchmark', 'Gate', 'Recommendation'],
                [
                    [
                        item.get('completed_at', '-'),
                        item.get('benchmark_id', '-'),
                        item.get('gate_status', '-'),
                        item.get('recommendation', '-'),
                    ]
                    for item in gate_history
                ],
            )
        )
        lines.append('')

    metric_trends = payload.get('metric_trends') or []
    if metric_trends:
        lines.append('## Metric Trends')
        lines.extend(
            _build_simple_table(
                ['Metric', 'First', 'Latest', 'Delta'],
                [
                    [
                        item.get('metric', '-'),
                        _format_metric_value(item.get('first_value')),
                        _format_metric_value(item.get('latest_value')),
                        _format_delta(item.get('delta')),
                    ]
                    for item in metric_trends
                ],
            )
        )
        lines.append('')

    tool_trends = payload.get('tool_trends') or []
    if tool_trends:
        lines.append('## Tool Trends')
        lines.extend(
            _build_simple_table(
                ['Tool', 'Latest Error Rate', 'Latest Avg Duration'],
                [
                    [
                        item.get('tool_name', '-'),
                        _format_metric_value(item.get('latest_error_rate')),
                        _format_metric_value(item.get('latest_avg_duration_ms')),
                    ]
                    for item in tool_trends
                ],
            )
        )
        lines.append('')

    latest_bucket_breakdown = payload.get('latest_bucket_breakdown') or {}
    if latest_bucket_breakdown:
        lines.append('## Latest Bucket Breakdown')
        lines.extend(
            _build_simple_table(
                ['Bucket', 'Samples', 'Success Rate', 'Avg Score', 'Avg Coverage', 'Avg Usability'],
                [
                    [
                        label,
                        item.get('sample_count', 0),
                        _format_metric_value(item.get('success_rate')),
                        _format_metric_value(item.get('avg_score')),
                        _format_metric_value(item.get('avg_evidence_coverage')),
                        _format_metric_value(item.get('avg_evidence_usability_score')),
                    ]
                    for label, item in sorted(latest_bucket_breakdown.items())
                ],
            )
        )
        lines.append('')

    latest_worst_samples = payload.get('latest_worst_samples') or []
    if latest_worst_samples:
        lines.append('## Latest Worst Samples')
        lines.extend(
            _build_simple_table(
                ['Index', 'Bucket', 'Status', 'Score', 'Coverage', 'Usability'],
                [
                    [
                        item.get('index', '-'),
                        item.get('bucket', 'default'),
                        item.get('status', '-'),
                        _format_metric_value(item.get('score')),
                        _format_metric_value(item.get('evidence_coverage')),
                        _format_metric_value(item.get('evidence_usability_score')),
                    ]
                    for item in latest_worst_samples
                ],
            )
        )
        lines.append('')

    return '\n'.join(lines).strip() + '\n'


def generate_document_analysis_trend_report(
    *,
    input_dir: str | None = None,
    output_dir: str | None = None,
    report_name: str | None = None,
    collection_name: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """执行 Document Analysis 趋势报告生成流程。

    Args:
        input_dir: 输入目录。
        output_dir: 输出目录。
        report_name: 报告文件名前缀。
        collection_name: 可选的知识库过滤条件。
        limit: 趋势窗口大小。

    Returns:
        包含趋势载荷和产物路径的结果字典。
    """

    settings = get_settings()
    configure_logging(settings.log_level)
    resolved_input_dir = Path(input_dir).expanduser().resolve() if input_dir else settings.eval_dir
    reports = load_benchmark_reports(resolved_input_dir, limit=limit, collection_name=collection_name)
    trend_payload = build_trend_payload(reports)
    resolved_output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.eval_dir
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_report_name = report_name or f"document-analysis-trend-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
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


def _build_gate_history(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取门禁历史，供趋势报告展示。"""

    rows: list[dict[str, Any]] = []
    for item in reports:
        gate = item.get('gate') or {}
        result = item.get('result') or {}
        rows.append(
            {
                'completed_at': item.get('completed_at', ''),
                'benchmark_id': result.get('benchmark_id', '-'),
                'gate_status': gate.get('status', 'unknown'),
                'recommendation': gate.get('recommendation', '-'),
            }
        )
    return rows


def _build_metric_trends(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取核心 benchmark 指标的时间序列变化。"""

    tracked_metrics = [
        'success_rate',
        'avg_score',
        'avg_evidence_coverage',
        'avg_evidence_usability_score',
        'unsupported_claim_rate',
        'review_replan_rate',
        'avg_estimated_cost_units',
    ]
    rows: list[dict[str, Any]] = []
    for metric_name in tracked_metrics:
        values = [float((item.get('dashboard_summary') or {}).get(metric_name, 0.0) or 0.0) for item in reports]
        rows.append(
            {
                'metric': metric_name,
                'first_value': values[0],
                'latest_value': values[-1],
                'delta': round(values[-1] - values[0], 4),
            }
        )
    return rows


def _build_tool_trends(reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取最近一次报告中的工具调用表现摘要。"""

    latest_tools = (reports[-1].get('dashboard_summary') or {}).get('tool_breakdown') or {}
    rows: list[dict[str, Any]] = []
    for tool_name, payload in sorted(latest_tools.items()):
        rows.append(
            {
                'tool_name': tool_name,
                'latest_error_rate': payload.get('error_rate', 0.0),
                'latest_avg_duration_ms': payload.get('avg_duration_ms', 0.0),
            }
        )
    return rows


def _build_insights(payload: dict[str, Any]) -> list[str]:
    """基于聚合结果生成简短中文洞察。"""

    insights: list[str] = []
    gate_history = payload.get('gate_history') or []
    metric_trends = payload.get('metric_trends') or []
    latest_gate = gate_history[-1]['gate_status'] if gate_history else 'unknown'
    if latest_gate == 'fail':
        insights.append('最近一次任务 benchmark 门禁为 `fail`，当前不建议继续放量。')
    elif latest_gate == 'warn':
        insights.append('最近一次任务 benchmark 门禁为 `warn`，建议继续灰度观察。')
    elif latest_gate == 'pass':
        insights.append('最近一次任务 benchmark 门禁为 `pass`，当前结果可继续作为候选。')
    strongest = max(metric_trends, key=lambda item: abs(float(item.get('delta', 0.0))), default=None)
    if strongest is not None:
        insights.append(
            f"波动最大的任务指标是 `{strongest['metric']}`，窗口变化 `{_format_delta(strongest['delta'])}`。"
        )
    if not insights:
        insights.append('当前趋势窗口没有发现明显异常。')
    return insights


def _build_simple_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    """构造简单二维 Markdown 表。"""

    values = [_markdown_table_header(headers)]
    for row in rows:
        values.append(_markdown_table_row(row))
    return values


def _markdown_table_header(headers: list[str]) -> str:
    """构造 Markdown 表头与分隔行。"""

    return _markdown_table_row(headers) + '\n' + _markdown_table_row(['---'] * len(headers))


def _markdown_table_row(values: list[Any]) -> str:
    """把一行值渲染为 Markdown 表格行。"""

    return '| ' + ' | '.join(str(value) for value in values) + ' |'


def _format_metric_value(value: Any) -> str:
    """格式化指标值，统一浮点与空值展示。"""

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


def main() -> int:
    """执行命令行入口。

    Returns:
        进程退出码。正常完成时返回 `0`。
    """

    args = build_parser().parse_args()
    output = generate_document_analysis_trend_report(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        report_name=args.report_name,
        collection_name=args.collection_name,
        limit=args.limit,
    )
    print(f"report_count: {output.get('report_count')}")
    print(f"json_report: {output.get('json_path')}")
    print(f"markdown_report: {output.get('markdown_path')}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
