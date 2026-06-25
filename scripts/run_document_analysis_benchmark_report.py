"""Document Analysis benchmark 报告脚本。

负责触发单次 Document Analysis benchmark，生成 JSON 与 Markdown 两类报告产物，
并补充便于发布判断的门禁摘要。脚本仅承担命令行编排与格式化职责。
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

# 统一切换到仓库根目录，保证配置、导入和输出路径解析行为一致。
os.chdir(PROJECT_ROOT)

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.eval import DocumentAnalysisBenchmarkRequest


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='生成 Document Analysis benchmark 报告（JSON + Markdown）')
    parser.add_argument('--dataset-path', required=True, help='benchmark 数据集 JSON 路径')
    parser.add_argument('--collection-name', default=None, help='默认知识库名称')
    parser.add_argument('--output-dir', default=None, help='报告输出目录，默认写入 settings.eval_dir')
    parser.add_argument('--report-name', default=None, help='输出文件名前缀')
    parser.add_argument('--min-success-rate', type=float, default=0.9)
    parser.add_argument('--min-avg-score', type=float, default=0.7)
    parser.add_argument('--max-unsupported-claim-rate', type=float, default=0.0)
    parser.add_argument('--max-review-replan-rate', type=float, default=0.5)
    parser.add_argument('--max-p95-latency-ms', type=float, default=30000.0)
    parser.add_argument('--fail-on-gate-fail', action='store_true')
    return parser


def build_markdown_report(result: dict[str, Any]) -> str:
    """把 benchmark 结果渲染为 Markdown 报告。

    Args:
        result: benchmark 结果字典。

    Returns:
        可直接写入文件的 Markdown 文本。
    """

    dashboard = result.get('dashboard_summary') or {}
    gate = result.get('gate') or {}
    lines = ['# Document Analysis Benchmark Report', '']
    lines.append(f"- Benchmark ID: `{result.get('benchmark_id', '-')}`")
    lines.append(f"- 数据集: `{result.get('dataset_path', '-')}`")
    lines.append(f"- 知识库: `{result.get('collection_name', '-')}`")
    lines.append(f"- 完成时间: `{result.get('completed_at', '-')}`")
    lines.append(f"- 汇总: {result.get('summary', '-')}")
    lines.append('')

    lines.append('## Gate')
    lines.append(f"- 判定: `{gate.get('status', '-')}`")
    lines.append(f"- 推荐动作: {gate.get('recommendation', '-')}")
    reasons = gate.get('reasons') or []
    if reasons:
        lines.append(f"- 依据: {'；'.join(str(item) for item in reasons)}")
    lines.append('')

    lines.append('## Dashboard Summary')
    lines.append(f"- success_rate: `{_format_metric_value(dashboard.get('success_rate'))}`")
    lines.append(f"- avg_score: `{_format_metric_value(dashboard.get('avg_score'))}`")
    lines.append(f"- avg_evidence_coverage: `{_format_metric_value(dashboard.get('avg_evidence_coverage'))}`")
    lines.append(f"- avg_evidence_usability_score: `{_format_metric_value(dashboard.get('avg_evidence_usability_score'))}`")
    lines.append(f"- avg_focus_dimension_hit_rate: `{_format_metric_value(dashboard.get('avg_focus_dimension_hit_rate'))}`")
    lines.append(f"- unsupported_claim_rate: `{_format_metric_value(dashboard.get('unsupported_claim_rate'))}`")
    lines.append(f"- review_replan_rate: `{_format_metric_value(dashboard.get('review_replan_rate'))}`")
    lines.append(f"- avg_latency_ms: `{_format_metric_value(dashboard.get('avg_latency_ms'))}`")
    lines.append(f"- avg_estimated_cost_units: `{_format_metric_value(dashboard.get('avg_estimated_cost_units'))}`")
    lines.append('')

    step_breakdown = dashboard.get('step_breakdown') or {}
    if step_breakdown:
        lines.append('## Step Eval')
        for key, value in step_breakdown.items():
            lines.append(f"- {key}: `{_format_metric_value(value)}`")
        lines.append('')

    tool_breakdown = dashboard.get('tool_breakdown') or {}
    if tool_breakdown:
        lines.append('## Tool Eval')
        lines.extend(_build_tool_breakdown_table(tool_breakdown))
        lines.append('')

    retrieval_mode_breakdown = dashboard.get('retrieval_mode_breakdown') or {}
    if retrieval_mode_breakdown:
        lines.append('## Retrieval Modes')
        for key, value in retrieval_mode_breakdown.items():
            lines.append(f"- {key}: `{_format_metric_value(value)}`")
        lines.append('')

    bucket_breakdown = dashboard.get('bucket_breakdown') or {}
    if bucket_breakdown:
        lines.append('## Bucket Breakdown')
        lines.extend(
            _build_slice_breakdown_table(bucket_breakdown)
        )
        lines.append('')

    collection_breakdown = dashboard.get('collection_breakdown') or {}
    if collection_breakdown:
        lines.append('## Collection Breakdown')
        lines.extend(
            _build_slice_breakdown_table(collection_breakdown)
        )
        lines.append('')

    worst_samples = dashboard.get('worst_samples') or []
    if worst_samples:
        lines.append('## Worst Samples')
        lines.extend(_build_worst_sample_table(worst_samples))
        lines.append('')

    samples = result.get('samples') or []
    if samples:
        lines.append('## Sample Summary')
        lines.extend(_build_sample_table(samples))
        lines.append('')

    return '\n'.join(lines).strip() + '\n'


def generate_document_analysis_benchmark_report(
    *,
    dataset_path: str,
    collection_name: str | None = None,
    output_dir: str | None = None,
    report_name: str | None = None,
    min_success_rate: float = 0.9,
    min_avg_score: float = 0.7,
    max_unsupported_claim_rate: float = 0.0,
    max_review_replan_rate: float = 0.5,
    max_p95_latency_ms: float = 30000.0,
) -> dict[str, Any]:
    """执行单次 Document Analysis benchmark 并落盘报告。

    Args:
        dataset_path: benchmark 数据集路径。
        collection_name: 知识库名称。
        output_dir: 报告输出目录。
        report_name: 报告文件名前缀。
        min_success_rate: 最低成功率阈值。
        min_avg_score: 最低平均得分阈值。
        max_unsupported_claim_rate: 允许的最高手工结论不受支持占比。
        max_review_replan_rate: 允许的最高 review/replan 占比。
        max_p95_latency_ms: 允许的最大 P95 延迟阈值。

    Returns:
        包含门禁、摘要和产物路径的结果字典。
    """

    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)
    response = container.eval_service.benchmark_document_analysis(
        DocumentAnalysisBenchmarkRequest(
            dataset_path=str(Path(dataset_path).expanduser().resolve()),
            collection_name=collection_name,
            min_success_rate=min_success_rate,
            min_avg_score=min_avg_score,
            max_unsupported_claim_rate=max_unsupported_claim_rate,
            max_review_replan_rate=max_review_replan_rate,
            max_p95_latency_ms=max_p95_latency_ms,
        )
    )
    result = response.model_dump(mode='json')
    resolved_output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.eval_dir
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_report_name = report_name or f"document-analysis-benchmark-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path = resolved_output_dir / f'{resolved_report_name}.json'
    markdown_path = resolved_output_dir / f'{resolved_report_name}.md'
    payload = {
        'report_mode': 'document_analysis_benchmark',
        'dashboard_summary': result.get('dashboard_summary'),
        'gate': result.get('gate'),
        'result': result,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    markdown_path.write_text(build_markdown_report(result), encoding='utf-8')
    return {
        'report_mode': 'document_analysis_benchmark',
        'gate': result.get('gate'),
        'dashboard_summary': result.get('dashboard_summary'),
        'result': result,
        'json_path': str(json_path),
        'markdown_path': str(markdown_path),
    }


def _build_tool_breakdown_table(tool_breakdown: dict[str, Any]) -> list[str]:
    """构造工具维度拆解表。"""

    headers = ['Tool', 'Call Count', 'Avg Calls/Task', 'Error Rate', 'Avg Duration']
    rows = [_markdown_table_header(headers)]
    for tool_name, item in sorted(tool_breakdown.items()):
        rows.append(
            _markdown_table_row(
                [
                    tool_name,
                    _format_metric_value(item.get('call_count')),
                    _format_metric_value(item.get('avg_calls_per_task')),
                    _format_metric_value(item.get('error_rate')),
                    _format_metric_value(item.get('avg_duration_ms')),
                ]
            )
        )
    return rows


def _build_sample_table(samples: list[dict[str, Any]]) -> list[str]:
    """构造样本级摘要表。"""

    headers = ['Index', 'Bucket', 'Status', 'Score', 'Coverage', 'Latency', 'Cost']
    rows = [_markdown_table_header(headers)]
    for item in samples:
        rows.append(
            _markdown_table_row(
                [
                    item.get('index', '-'),
                    item.get('bucket', 'default'),
                    item.get('status', '-'),
                    _format_metric_value(item.get('score')),
                    _format_metric_value(item.get('evidence_coverage')),
                    _format_metric_value(item.get('latency_ms')),
                    _format_metric_value(item.get('estimated_cost_units')),
                ]
            )
        )
    return rows


def _build_slice_breakdown_table(items: dict[str, Any]) -> list[str]:
    """构造 bucket 或 collection 维度的切片统计表。"""

    headers = ['Label', 'Samples', 'Success Rate', 'Avg Score', 'Avg Coverage', 'Avg Usability', 'Avg Tool Error']
    rows = [_markdown_table_header(headers)]
    for label, item in sorted(items.items()):
        rows.append(
            _markdown_table_row(
                [
                    label,
                    _format_metric_value(item.get('sample_count')),
                    _format_metric_value(item.get('success_rate')),
                    _format_metric_value(item.get('avg_score')),
                    _format_metric_value(item.get('avg_evidence_coverage')),
                    _format_metric_value(item.get('avg_evidence_usability_score')),
                    _format_metric_value(item.get('avg_tool_error_count')),
                ]
            )
        )
    return rows


def _build_worst_sample_table(items: list[dict[str, Any]]) -> list[str]:
    """构造最差样本表，便于快速定位问题案例。"""

    headers = ['Index', 'Bucket', 'Collection', 'Status', 'Score', 'Coverage', 'Usability', 'Error']
    rows = [_markdown_table_header(headers)]
    for item in items:
        rows.append(
            _markdown_table_row(
                [
                    item.get('index', '-'),
                    item.get('bucket', 'default'),
                    item.get('collection_name', '-'),
                    item.get('status', '-'),
                    _format_metric_value(item.get('score')),
                    _format_metric_value(item.get('evidence_coverage')),
                    _format_metric_value(item.get('evidence_usability_score')),
                    item.get('error', '-') or '-',
                ]
            )
        )
    return rows


def _markdown_table_header(headers: list[str]) -> str:
    """构造 Markdown 表头与分隔行。"""

    return _markdown_table_row(headers) + '\n' + _markdown_table_row(['---'] * len(headers))


def _markdown_table_row(values: list[Any]) -> str:
    """把单行值渲染为 Markdown 表格行。"""

    return '| ' + ' | '.join(str(value) for value in values) + ' |'


def _format_metric_value(value: Any) -> str:
    """格式化指标值，统一空值与浮点展示风格。"""

    if value is None:
        return '-'
    if isinstance(value, float):
        return f'{value:.4f}'
    return str(value)


def main() -> int:
    """执行命令行入口。

    Returns:
        进程退出码。门禁失败且启用严格模式时返回 `2`。
    """

    args = build_parser().parse_args()
    output = generate_document_analysis_benchmark_report(
        dataset_path=args.dataset_path,
        collection_name=args.collection_name,
        output_dir=args.output_dir,
        report_name=args.report_name,
        min_success_rate=args.min_success_rate,
        min_avg_score=args.min_avg_score,
        max_unsupported_claim_rate=args.max_unsupported_claim_rate,
        max_review_replan_rate=args.max_review_replan_rate,
        max_p95_latency_ms=args.max_p95_latency_ms,
    )
    print(f"gate_status: {output.get('gate', {}).get('status')}")
    print(f"json_report: {output.get('json_path')}")
    print(f"markdown_report: {output.get('markdown_path')}")
    if args.fail_on_gate_fail and output.get('gate', {}).get('status') == 'fail':
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
