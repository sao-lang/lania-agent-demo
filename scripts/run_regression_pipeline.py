"""准确率回归流水线脚本。

负责串联单次准确率回归报告与趋势报告，并生成面向发布判断的流水线摘要。
脚本仅做 CLI 编排、文件落盘和结果汇总，不改变具体评测逻辑。
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

# 统一切换到仓库根目录，保证导入、配置和输出路径解析行为一致。
os.chdir(PROJECT_ROOT)

from app.core.config import get_settings
from app.core.logging import configure_logging
from scripts.run_accuracy_report import generate_accuracy_report
from scripts.run_accuracy_trend_report import generate_trend_report


def build_parser() -> argparse.ArgumentParser:
    """构造流水线命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='执行统一回归流水线，串联 accuracy report、gate 和 trend report')
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
    parser.add_argument('--output-dir', default=None, help='流水线产物输出目录，默认写入 settings.eval_dir')
    parser.add_argument('--report-name', default=None, help='流水线摘要文件名前缀，默认自动生成时间戳名称')
    parser.add_argument('--accuracy-report-name', default=None, help='单次回归报告文件名前缀')
    parser.add_argument('--trend-report-name', default=None, help='趋势报告文件名前缀')
    parser.add_argument('--trend-limit', type=int, default=10, help='趋势报告最多读取最近多少份 accuracy report，默认 10')
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


def build_pipeline_markdown_report(payload: dict[str, Any]) -> str:
    """生成流水线摘要 Markdown。

    Args:
        payload: 流水线执行后的聚合结果。

    Returns:
        可直接写入文件的 Markdown 文本。
    """

    accuracy = payload.get('accuracy_report') or {}
    trend = payload.get('trend_report') or {}
    gate = payload.get('gate') or {}

    lines = ['# Regression Pipeline Report', '']
    lines.append(f"- 生成时间: `{payload.get('generated_at', '-')}`")
    lines.append(f"- 数据集: `{payload.get('dataset_path', '-')}`")
    lines.append(f"- 知识库: `{payload.get('collection_name', '-')}`")
    lines.append(f"- 流水线状态: `{payload.get('pipeline_status', '-')}`")
    lines.append('')

    lines.append('## Pipeline Summary')
    lines.append(f"- 单次报告: `{accuracy.get('json_path', '-')}`")
    lines.append(f"- 单次 Markdown: `{accuracy.get('markdown_path', '-')}`")
    lines.append(f"- 趋势报告: `{trend.get('json_path', '-')}`")
    lines.append(f"- 趋势 Markdown: `{trend.get('markdown_path', '-')}`")
    lines.append('')

    lines.append('## Gate')
    lines.append(f"- 判定: `{gate.get('status', '-')}`")
    lines.append(f"- 候选策略: `{gate.get('candidate_strategy', '-')}`")
    lines.append(f"- 推荐动作: {gate.get('recommendation', '-')}")
    reasons = gate.get('reasons') or []
    if reasons:
        lines.append(f"- 依据: {'；'.join(str(item) for item in reasons)}")
    lines.append('')

    trend_payload = trend.get('trend_payload') or {}
    insights = trend_payload.get('insights') or []
    if insights:
        lines.append('## Trend Highlights')
        for item in insights:
            lines.append(f'- {item}')
        lines.append('')

    return '\n'.join(lines).strip() + '\n'


def generate_regression_pipeline(
    *,
    dataset_path: str,
    collection_name: str,
    baseline_name: str | None = None,
    strategies_path: str | None = None,
    strategy_preset: str = 'accuracy',
    output_dir: str | None = None,
    report_name: str | None = None,
    accuracy_report_name: str | None = None,
    trend_report_name: str | None = None,
    trend_limit: int = 10,
    max_latency_increase_ms: float = 80.0,
    max_quality_regression: float = 0.0,
) -> dict[str, Any]:
    """执行准确率回归流水线并输出摘要产物。

    Args:
        dataset_path: 评测集路径。
        collection_name: 知识库名称。
        baseline_name: 基线策略名称。
        strategies_path: 外部策略配置路径。
        strategy_preset: 默认策略预设名称。
        output_dir: 输出目录。
        report_name: 流水线摘要名称。
        accuracy_report_name: 单次准确率报告名称。
        trend_report_name: 趋势报告名称。
        trend_limit: 趋势窗口大小。
        max_latency_increase_ms: 允许的平均延迟增量阈值。
        max_quality_regression: 允许的质量回退阈值。

    Returns:
        包含单次报告、趋势报告和流水线摘要路径的结果字典。
    """

    settings = get_settings()
    configure_logging(settings.log_level)

    resolved_output_dir = Path(output_dir).expanduser().resolve() if output_dir else settings.eval_dir
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    accuracy_output = generate_accuracy_report(
        dataset_path=dataset_path,
        collection_name=collection_name,
        baseline_name=baseline_name,
        strategies_path=strategies_path,
        strategy_preset=strategy_preset,
        output_dir=str(resolved_output_dir),
        report_name=accuracy_report_name,
        max_latency_increase_ms=max_latency_increase_ms,
        max_quality_regression=max_quality_regression,
    )
    trend_output = generate_trend_report(
        input_dir=str(resolved_output_dir),
        output_dir=str(resolved_output_dir),
        report_name=trend_report_name,
        limit=trend_limit,
        prefix='accuracy-report-',
    )

    gate = accuracy_output.get('gate') or {}
    pipeline_status = str(gate.get('status') or 'unknown')
    resolved_report_name = report_name or f"regression-pipeline-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path = resolved_output_dir / f'{resolved_report_name}.json'
    markdown_path = resolved_output_dir / f'{resolved_report_name}.md'

    payload = {
        'generated_at': datetime.now().isoformat(),
        'dataset_path': str(Path(dataset_path).expanduser().resolve()),
        'collection_name': collection_name,
        'pipeline_status': pipeline_status,
        'gate': gate,
        'accuracy_report': accuracy_output,
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
    output = generate_regression_pipeline(
        dataset_path=args.dataset_path,
        collection_name=args.collection_name,
        baseline_name=args.baseline_name,
        strategies_path=args.strategies_path,
        strategy_preset=args.strategy_preset,
        output_dir=args.output_dir,
        report_name=args.report_name,
        accuracy_report_name=args.accuracy_report_name,
        trend_report_name=args.trend_report_name,
        trend_limit=args.trend_limit,
        max_latency_increase_ms=args.max_latency_increase_ms,
        max_quality_regression=args.max_quality_regression,
    )

    print(f"pipeline_status: {output.get('pipeline_status')}")
    print(f"pipeline_json: {output.get('json_path')}")
    print(f"pipeline_markdown: {output.get('markdown_path')}")
    print(f"accuracy_json: {output.get('accuracy_report', {}).get('json_path')}")
    print(f"trend_json: {output.get('trend_report', {}).get('json_path')}")
    if args.fail_on_gate_fail and output.get('pipeline_status') == 'fail':
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
