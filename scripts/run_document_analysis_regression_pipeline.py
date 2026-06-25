"""Document Analysis 回归流水线脚本。

负责串联单次 benchmark 报告与趋势报告，并输出便于发布判断的流水线摘要。
脚本只承担参数解析、调用编排和产物落盘职责，不调整 benchmark 内部逻辑。
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

# 统一切换到仓库根目录，保证相对路径与导入行为一致。
os.chdir(PROJECT_ROOT)

from app.core.config import get_settings
from app.core.logging import configure_logging
from scripts.run_document_analysis_benchmark_report import generate_document_analysis_benchmark_report
from scripts.run_document_analysis_trend_report import generate_document_analysis_trend_report


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='执行 Document Analysis benchmark 流水线，串联单次报告与趋势报告')
    parser.add_argument('--dataset-path', required=True)
    parser.add_argument('--collection-name', default=None)
    parser.add_argument('--output-dir', default=None)
    parser.add_argument('--report-name', default=None)
    parser.add_argument('--benchmark-report-name', default=None)
    parser.add_argument('--trend-report-name', default=None)
    parser.add_argument('--trend-limit', type=int, default=10)
    parser.add_argument('--min-success-rate', type=float, default=0.9)
    parser.add_argument('--min-avg-score', type=float, default=0.7)
    parser.add_argument('--max-unsupported-claim-rate', type=float, default=0.0)
    parser.add_argument('--max-review-replan-rate', type=float, default=0.5)
    parser.add_argument('--max-p95-latency-ms', type=float, default=30000.0)
    parser.add_argument('--fail-on-gate-fail', action='store_true')
    return parser


def build_pipeline_markdown_report(payload: dict[str, Any]) -> str:
    """根据流水线执行结果生成 Markdown 摘要。

    Args:
        payload: 流水线聚合结果。

    Returns:
        可直接写入文件的 Markdown 文本。
    """

    benchmark = payload.get('benchmark_report') or {}
    trend = payload.get('trend_report') or {}
    gate = payload.get('gate') or {}
    lines = ['# Document Analysis Regression Pipeline', '']
    lines.append(f"- 生成时间: `{payload.get('generated_at', '-')}`")
    lines.append(f"- 数据集: `{payload.get('dataset_path', '-')}`")
    lines.append(f"- 知识库: `{payload.get('collection_name', '-')}`")
    lines.append(f"- 流水线状态: `{payload.get('pipeline_status', '-')}`")
    lines.append('')
    lines.append('## Pipeline Summary')
    lines.append(f"- 单次报告: `{benchmark.get('json_path', '-')}`")
    lines.append(f"- 单次 Markdown: `{benchmark.get('markdown_path', '-')}`")
    lines.append(f"- 趋势报告: `{trend.get('json_path', '-')}`")
    lines.append(f"- 趋势 Markdown: `{trend.get('markdown_path', '-')}`")
    lines.append('')
    lines.append('## Gate')
    lines.append(f"- 判定: `{gate.get('status', '-')}`")
    lines.append(f"- 推荐动作: {gate.get('recommendation', '-')}")
    reasons = gate.get('reasons') or []
    if reasons:
        lines.append(f"- 依据: {'；'.join(str(item) for item in reasons)}")
    lines.append('')
    insights = (trend.get('trend_payload') or {}).get('insights') or []
    if insights:
        lines.append('## Trend Highlights')
        for item in insights:
            lines.append(f'- {item}')
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


def generate_document_analysis_regression_pipeline(
    *,
    dataset_path: str,
    collection_name: str | None = None,
    output_dir: str | None = None,
    report_name: str | None = None,
    benchmark_report_name: str | None = None,
    trend_report_name: str | None = None,
    trend_limit: int = 10,
    min_success_rate: float = 0.9,
    min_avg_score: float = 0.7,
    max_unsupported_claim_rate: float = 0.0,
    max_review_replan_rate: float = 0.5,
    max_p95_latency_ms: float = 30000.0,
) -> dict[str, Any]:
    """执行 Document Analysis 回归流水线。

    Args:
        dataset_path: benchmark 数据集路径。
        collection_name: 知识库名称。
        output_dir: 输出目录。
        report_name: 流水线摘要名称。
        benchmark_report_name: 单次 benchmark 报告名称。
        trend_report_name: 趋势报告名称。
        trend_limit: 趋势窗口大小。
        min_success_rate: 最低成功率阈值。
        min_avg_score: 最低平均得分阈值。
        max_unsupported_claim_rate: 最多不受支持结论占比。
        max_review_replan_rate: 最多 review/replan 占比。
        max_p95_latency_ms: 最大 P95 延迟阈值。

    Returns:
        包含 benchmark 报告、趋势报告和流水线摘要路径的结果字典。
    """

    settings = get_settings()
    configure_logging(settings.log_level)
    resolved_output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.eval_dir
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_output = generate_document_analysis_benchmark_report(
        dataset_path=dataset_path,
        collection_name=collection_name,
        output_dir=str(resolved_output_dir),
        report_name=benchmark_report_name,
        min_success_rate=min_success_rate,
        min_avg_score=min_avg_score,
        max_unsupported_claim_rate=max_unsupported_claim_rate,
        max_review_replan_rate=max_review_replan_rate,
        max_p95_latency_ms=max_p95_latency_ms,
    )
    trend_output = generate_document_analysis_trend_report(
        input_dir=str(resolved_output_dir),
        output_dir=str(resolved_output_dir),
        report_name=trend_report_name,
        collection_name=collection_name,
        limit=trend_limit,
    )
    gate = benchmark_output.get('gate') or {}
    pipeline_status = str(gate.get('status') or 'unknown')
    resolved_report_name = report_name or f"document-analysis-pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path = resolved_output_dir / f'{resolved_report_name}.json'
    markdown_path = resolved_output_dir / f'{resolved_report_name}.md'
    payload = {
        'generated_at': datetime.now().isoformat(),
        'dataset_path': str(Path(dataset_path).expanduser().resolve()),
        'collection_name': collection_name,
        'pipeline_status': pipeline_status,
        'gate': gate,
        'benchmark_report': benchmark_output,
        'trend_report': trend_output,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    markdown_path.write_text(build_pipeline_markdown_report(payload), encoding='utf-8')
    payload['json_path'] = str(json_path)
    payload['markdown_path'] = str(markdown_path)
    return payload


def main() -> int:
    """执行命令行入口。

    Returns:
        进程退出码。门禁失败且启用严格模式时返回 `2`。
    """

    args = build_parser().parse_args()
    output = generate_document_analysis_regression_pipeline(
        dataset_path=args.dataset_path,
        collection_name=args.collection_name,
        output_dir=args.output_dir,
        report_name=args.report_name,
        benchmark_report_name=args.benchmark_report_name,
        trend_report_name=args.trend_report_name,
        trend_limit=args.trend_limit,
        min_success_rate=args.min_success_rate,
        min_avg_score=args.min_avg_score,
        max_unsupported_claim_rate=args.max_unsupported_claim_rate,
        max_review_replan_rate=args.max_review_replan_rate,
        max_p95_latency_ms=args.max_p95_latency_ms,
    )
    print(f"pipeline_status: {output.get('pipeline_status')}")
    print(f"pipeline_json: {output.get('json_path')}")
    print(f"pipeline_markdown: {output.get('markdown_path')}")
    if args.fail_on_gate_fail and output.get('pipeline_status') == 'fail':
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
