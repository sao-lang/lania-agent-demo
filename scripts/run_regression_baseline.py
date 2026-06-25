"""回归基线生成脚本。

负责根据运行时能力选择 RAGAS 对比或回放对比模式，输出单次基线结果。
脚本本身只做命令行参数解析、环境初始化和结果分发，不改动评测逻辑。
"""

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 统一切换到仓库根目录，保证相对路径、配置加载和导入行为一致。
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

    parser = argparse.ArgumentParser(description='生成回归基线（有 RAGAS 则跑 RAGAS，否则跑回放统计）')
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
    return parser


def load_strategies(path: str | None) -> list[EvalStrategyConfig] | None:
    """从 JSON 文件加载策略配置列表。

    Args:
        path: 策略文件路径。未传时返回 `None`，交由调用方回退到默认策略。

    Returns:
        解析后的策略配置列表，或 `None`。
    """

    if not path:
        return None
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding='utf-8'))
    if not isinstance(payload, list):
        raise ValueError('strategies_path 必须是 JSON 数组')
    return [EvalStrategyConfig(**item) for item in payload]


def main() -> int:
    """执行回归基线生成流程。

    Returns:
        进程退出码。正常完成时返回 `0`。
    """

    args = build_parser().parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)

    default_strategies = container.eval_service.build_default_compare_strategies()
    if args.strategy_preset == 'accuracy':
        default_strategies = container.eval_service.build_default_compare_strategies()
    strategies = load_strategies(args.strategies_path) or default_strategies
    runtime = container.eval_service.get_runtime_status()

    dataset_path = str(Path(args.dataset_path).expanduser().resolve())
    # 运行时可用时优先走 RAGAS 对比，否则退回到回放结果对比。
    if runtime['ready']:
        payload = RagasCompareRequest(
            dataset_path=dataset_path,
            collection_name=args.collection_name,
            strategies=strategies,
            baseline_name=args.baseline_name,
        )
        result = container.eval_service.compare_tasks(payload).model_dump(mode='json')
    else:
        payload = ReplayCompareRequest(
            dataset_path=dataset_path,
            collection_name=args.collection_name,
            strategies=strategies,
            baseline_name=args.baseline_name,
        )
        result = container.eval_service.compare_replay(payload).model_dump(mode='json')

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
