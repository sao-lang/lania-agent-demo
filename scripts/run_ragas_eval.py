"""本地 RAGAS 评测脚本。

负责把命令行参数映射为 `RagasEvalRequest`，触发评测任务并输出结果摘要。
脚本只做入口编排，不改动评测任务的业务逻辑。
"""

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 统一切换到仓库根目录，保证配置解析和模块导入一致。
os.chdir(PROJECT_ROOT)

from app.container import build_container
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.eval import RagasEvalRequest


def build_parser() -> argparse.ArgumentParser:
    """构造 RAGAS 本地评测命令行参数解析器。

    Returns:
        当前脚本使用的参数解析器。
    """

    parser = argparse.ArgumentParser(description='本地执行 RAGAS 评测')
    parser.add_argument('--dataset-path', required=True, help='评测集 JSON 文件路径')
    parser.add_argument('--collection-name', required=True, help='知识库名称')
    parser.add_argument('--top-k', type=int, default=5, help='检索 top_k，默认 5')
    parser.add_argument(
        '--use-query-rewrite',
        dest='use_query_rewrite',
        action='store_true',
        help='开启查询改写，默认开启',
    )
    parser.add_argument(
        '--no-query-rewrite',
        dest='use_query_rewrite',
        action='store_false',
        help='关闭查询改写',
    )
    parser.add_argument(
        '--use-multi-query',
        dest='use_multi_query',
        action='store_true',
        help='开启 Multi-Query，默认关闭',
    )
    parser.add_argument(
        '--no-multi-query',
        dest='use_multi_query',
        action='store_false',
        help='关闭 Multi-Query',
    )
    parser.add_argument(
        '--multi-query-count',
        type=int,
        default=3,
        help='Multi-Query 的查询条数（2-6），默认 3',
    )
    parser.add_argument(
        '--use-multi-rewrite',
        dest='use_multi_rewrite',
        action='store_true',
        help='开启多路改写（非 LLM 依赖），默认关闭',
    )
    parser.add_argument(
        '--no-multi-rewrite',
        dest='use_multi_rewrite',
        action='store_false',
        help='关闭多路改写',
    )
    parser.add_argument(
        '--multi-rewrite-count',
        type=int,
        default=3,
        help='多路改写的查询条数（2-6），默认 3',
    )
    parser.add_argument(
        '--use-hybrid-retrieval',
        dest='use_hybrid_retrieval',
        action='store_true',
        help='开启混合检索，默认关闭',
    )
    parser.add_argument(
        '--no-hybrid-retrieval',
        dest='use_hybrid_retrieval',
        action='store_false',
        help='关闭混合检索',
    )
    parser.add_argument(
        '--use-rerank',
        dest='use_rerank',
        action='store_true',
        help='开启重排标记，默认开启',
    )
    parser.add_argument(
        '--no-rerank',
        dest='use_rerank',
        action='store_false',
        help='关闭重排标记',
    )
    parser.add_argument(
        '--use-hyde',
        dest='use_hyde',
        action='store_true',
        help='开启 HyDE 检索，默认关闭',
    )
    parser.add_argument(
        '--no-hyde',
        dest='use_hyde',
        action='store_false',
        help='关闭 HyDE 检索',
    )
    parser.add_argument(
        '--use-long-context-reorder',
        dest='use_long_context_reorder',
        action='store_true',
        help='开启 long context reorder，默认关闭',
    )
    parser.add_argument(
        '--no-long-context-reorder',
        dest='use_long_context_reorder',
        action='store_false',
        help='关闭 long context reorder',
    )
    parser.add_argument(
        '--use-parent-chunk-retrieval',
        dest='use_parent_chunk_retrieval',
        action='store_true',
        help='开启父子块检索 / small-to-big，默认关闭',
    )
    parser.add_argument(
        '--no-parent-chunk-retrieval',
        dest='use_parent_chunk_retrieval',
        action='store_false',
        help='关闭父子块检索 / small-to-big',
    )
    parser.add_argument(
        '--use-question-oriented-index',
        dest='use_question_oriented_index',
        action='store_true',
        help='开启问题导向索引，默认关闭',
    )
    parser.add_argument(
        '--no-question-oriented-index',
        dest='use_question_oriented_index',
        action='store_false',
        help='关闭问题导向索引',
    )
    parser.add_argument(
        '--use-corrective-rag',
        dest='use_corrective_rag',
        action='store_true',
        help='开启 Corrective RAG / Self-RAG 二次校验，默认关闭',
    )
    parser.add_argument(
        '--no-corrective-rag',
        dest='use_corrective_rag',
        action='store_false',
        help='关闭 Corrective RAG / Self-RAG 二次校验',
    )
    parser.add_argument(
        '--json',
        dest='json_output',
        action='store_true',
        help='仅输出 JSON 结果',
    )
    parser.set_defaults(
        use_query_rewrite=True,
        use_multi_query=False,
        use_multi_rewrite=False,
        use_hybrid_retrieval=False,
        use_rerank=True,
        use_hyde=False,
        use_long_context_reorder=False,
        use_parent_chunk_retrieval=False,
        use_question_oriented_index=False,
        use_corrective_rag=False,
        json_output=False,
    )
    return parser


def main() -> int:
    """执行本地 RAGAS 评测。

    Returns:
        进程退出码。任务完成返回 `0`，否则返回 `1`。
    """

    args = build_parser().parse_args()

    settings = get_settings()
    configure_logging(settings.log_level)
    container = build_container(settings)

    # 仅负责组装请求对象，真实评测逻辑由 eval_service 执行。
    payload = RagasEvalRequest(
        dataset_path=str(Path(args.dataset_path).expanduser().resolve()),
        collection_name=args.collection_name,
        top_k=args.top_k,
        use_query_rewrite=args.use_query_rewrite,
        use_multi_query=args.use_multi_query,
        multi_query_count=args.multi_query_count,
        use_multi_rewrite=args.use_multi_rewrite,
        multi_rewrite_count=args.multi_rewrite_count,
        use_hybrid_retrieval=args.use_hybrid_retrieval,
        use_rerank=args.use_rerank,
        use_hyde=args.use_hyde,
        use_long_context_reorder=args.use_long_context_reorder,
        use_parent_chunk_retrieval=args.use_parent_chunk_retrieval,
        use_question_oriented_index=args.use_question_oriented_index,
        use_corrective_rag=args.use_corrective_rag,
    )
    task = container.eval_service.create_task(payload)
    result = task.model_dump(mode='json')

    if args.json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if task.status == 'completed' else 1

    print(f"task_id: {task.task_id}")
    print(f"status: {task.status}")
    if task.summary:
        print(f"summary: {task.summary}")
    if task.metrics:
        print('metrics:')
        for key, value in task.metrics.items():
            print(f'  {key}: {value}')
    if task.result_path:
        print(f"result_path: {task.result_path}")
    if task.error:
        print(f"error: {task.error}")

    return 0 if task.status == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
