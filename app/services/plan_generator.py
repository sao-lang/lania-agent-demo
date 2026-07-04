"""计划生成模块。

根据用户输入和 Capability 生成结构化的执行计划。
支持预定义模板和 LLM 动态生成两种模式。
"""

from __future__ import annotations

from typing import Any

from app.capabilities.registry import CapabilityRegistry
from app.models.agent import Plan, PlanStep


class PlanGenerator:
    """执行计划生成器。

    为 plan 模式生成结构化的多步骤执行计划。
    """

    # 各 Capability 的预定义计划模板
    PLAN_TEMPLATES: dict[str, list[dict]] = {
        "document_analysis": [
            {"name": "收集文档上下文", "description": "加载目标文档的基本信息",
             "tool": "rag_load_document_context"},
            {"name": "检索证据", "description": "检索与问题相关的证据片段",
             "tool": "rag_retrieve_evidence"},
            {"name": "补充图谱检索", "description": "检查覆盖率，必要时补充图谱检索",
             "tool": "rag_retrieve_graph_evidence"},
            {"name": "提取关键发现", "description": "基于证据提取结构化发现",
             "tool": "extract_key_points"},
            {"name": "识别风险", "description": "提取潜在风险和问题",
             "tool": "extract_risks"},
            {"name": "生成分析报告", "description": "将分析结果整理为最终报告",
             "tool": "draft_report"},
        ],
        "document_summary": [
            {"name": "加载文档", "description": "读取待摘要的文档内容",
             "tool": "rag_load_document_context"},
            {"name": "生成摘要", "description": "基于文档内容生成简洁摘要",
             "tool": None},
        ],
        "code_review": [
            {"name": "列出目录结构", "description": "了解项目的文件组织",
             "tool": "list_repository_files"},
            {"name": "读取核心模块", "description": "读取关键源代码文件",
             "tool": "read_repository_file"},
            {"name": "运行静态分析", "description": "使用 linter 检查代码质量",
             "tool": "shell_command"},
            {"name": "审查安全漏洞", "description": "检查常见安全风险",
             "tool": None},
            {"name": "生成审查报告", "description": "汇总发现并生成报告",
             "tool": "draft_report"},
        ],
        "data_analysis": [
            {"name": "浏览数据表", "description": "了解数据源的结构",
             "tool": "list_database_tables"},
            {"name": "查询数据", "description": "执行 SQL 查询获取数据",
             "tool": "query_database"},
            {"name": "运行分析", "description": "使用 Python 进行统计分析",
             "tool": "shell_command"},
            {"name": "生成可视化", "description": "根据分析结果生成图表",
             "tool": None},
            {"name": "解读结果", "description": "对分析结果进行文字解读",
             "tool": None},
        ],
        "chat": [
            {"name": "理解问题", "description": "分析用户问题的意图",
             "tool": None},
            {"name": "生成回答", "description": "基于 LLM 生成回答",
             "tool": None},
        ],
    }

    def __init__(
        self, registry: CapabilityRegistry, llm: Any | None = None,
    ) -> None:
        self._registry = registry
        self._llm = llm

    async def generate(
        self,
        message: str,
        capability: str,
        context: dict[str, Any] | None = None,
    ) -> Plan:
        """生成执行计划。

        优先使用预定义模板，回退到 LLM 动态生成。

        Args:
            message: 用户输入。
            capability: 目标 Capability。
            context: 额外上下文（可选）。

        Returns:
            生成的执行计划。
        """
        cap_def = self._registry.get(capability)

        # 尝试使用 LLM 动态生成（如果有 LLM）
        if self._llm is not None:
            try:
                return await self._generate_with_llm(
                    message, capability, cap_def,
                )
            except Exception:
                pass

        # 回退到预定义模板
        return self._generate_from_template(message, capability, cap_def)

    def _generate_from_template(
        self,
        message: str,
        capability: str,
        cap_def: Any | None,
    ) -> Plan:
        """从预定义模板生成计划。"""
        template = self.PLAN_TEMPLATES.get(
            capability, self.PLAN_TEMPLATES["chat"],
        )
        steps: list[PlanStep] = []

        for i, step_def in enumerate(template, start=1):
            steps.append(PlanStep(
                step_id=i,
                name=step_def["name"],
                description=step_def["description"],
                tool=step_def["tool"],
            ))

        display_name = cap_def.display_name if cap_def else capability
        summary = (
            f"将使用 {display_name} 能力处理您的请求，"
            f"共 {len(steps)} 个步骤"
        )

        return Plan(steps=steps, summary=summary)

    async def _generate_with_llm(
        self,
        message: str,
        capability: str,
        cap_def: Any | None,
    ) -> Plan:
        """使用 LLM 动态生成计划。"""
        display_name = cap_def.display_name if cap_def else capability
        available_tools = cap_def.tools if cap_def else []

        prompt = (
            f"你是一个任务规划专家。用户请求使用 '{display_name}' 能力处理以下任务：\n\n"
            f"{message}\n\n"
            f"可用的工具：{', '.join(available_tools) if available_tools else '无'}"
            f"\n\n"
            f"请生成一个分步骤的执行计划，每个步骤包含：步骤名、描述、使用的工具。\n"
            f"以 JSON 格式返回：{{'steps': [{{'name', 'description', 'tool'}}]}}\n"
            f"步骤数控制在 3-8 步之间。"
        )

        try:
            response = self._llm.chat([{"role": "user", "content": prompt}])
            import json
            content = response.choices[0].message.content

            # 尝试从 LLM 响应中提取 JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            data = json.loads(content.strip())
            steps = [
                PlanStep(step_id=i + 1, **s)
                for i, s in enumerate(data.get("steps", []))
            ]

            if not steps:
                raise ValueError("LLM 返回了空的步骤列表")

            summary = (
                f"基于您的请求 '{message[:50]}...'，"
                f"规划了 {len(steps)} 个执行步骤"
            )
            return Plan(steps=steps, summary=summary)

        except Exception:
            # LLM 生成失败，回退到模板
            return self._generate_from_template(message, capability, cap_def)
