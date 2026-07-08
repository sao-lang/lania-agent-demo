"""Coding Agent Capability 实现。

对仓库代码进行自动化审查，实际执行 lint/静态分析工具，
并结合 LLM 多维度分析，生成结构化审查报告。

与 code_review 的区别：
- code_review: 纯 LLM 审查，不执行工具
- coding: 实际执行 lint/静态分析 + LLM 分析
"""

from __future__ import annotations

import re
from typing import Any

from app.models.agent import AgentEvent


class CodingCapability:
    """代码助手能力。

    6 阶段工作流：
    1. Plan → 确定审查范围和维度
    2. CollectCodeContext → 读取目标代码文件
    3. RunAnalysis → 执行 lint/静态分析工具
    4. Analyze → LLM 多维度分析
    5. DraftReview → 生成结构化审查报告
    6. Finalize → 完成
    """

    name = "coding"

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def execute(
        self,
        message: str,
        context: dict[str, Any],
    ) -> list[AgentEvent]:
        """执行代码分析。

        Args:
            message: 分析要求（含目标路径和维度）。
            context: 执行上下文（含 repository, llm, services 等）。

        Returns:
            Agent 事件列表。
        """
        events: list[AgentEvent] = []
        repository = context.get("repository")
        llm = context.get("llm") or self._llm
        services = context.get("services") or {}

        target_path = self._extract_target_path(message)
        review_dimensions = self._extract_dimensions(message)

        # ── Phase 1: Plan ──────────────────────
        events.append(AgentEvent.step_start(1, "制定审查计划", "确定文件范围和审查维度"))
        events.append(AgentEvent.delta(
            f"目标路径: {target_path}\n"
            f"审查维度: {'、'.join(review_dimensions)}\n\n"
        ))

        files = self._list_files(repository, target_path)
        code_files = self._filter_code_files(files[:15])
        events.append(AgentEvent.delta(
            f"共发现 {len(code_files)} 个代码文件待审查\n\n"
        ))
        events.append(AgentEvent.step_end(1, "completed"))

        # ── Phase 2: CollectCodeContext ─────────
        events.append(AgentEvent.step_start(2, "收集代码上下文", "读取关键代码文件"))

        file_contents: list[dict] = []
        for f in code_files:
            events.append(AgentEvent.tool_call(
                "read_repository_file", {"path": f["path"]},
            ))
            content = self._read_file(repository, f["path"])
            if content:
                file_contents.append({
                    "path": f["path"],
                    "content": content,
                })
            events.append(AgentEvent.tool_result(
                "read_repository_file", "success",
            ))

        events.append(AgentEvent.delta(
            f"已读取 {len(file_contents)} 个文件\n\n"
        ))
        events.append(AgentEvent.step_end(2, "completed"))

        # ── Phase 3: RunAnalysis ───────────────
        events.append(AgentEvent.step_start(3, "运行代码分析", "执行 lint 和静态检查"))

        lint_results = []
        sandbox = services.get("sandbox_execute")
        if sandbox and file_contents:
            try:
                from app.agents.tools.coding_tools import (
                    RunCodeAnalysisInput,
                    RunCodeAnalysisTool,
                )
                analysis_tool = RunCodeAnalysisTool()
                analysis_result = analysis_tool.run(
                    RunCodeAnalysisInput(
                        target_path=target_path,
                        analysis_types=["lint", "type_check"],
                        timeout_seconds=60,
                    ),
                    context=_make_tool_context(
                        services=services,
                        llm=llm,
                    ),
                )
                lint_results = [
                    {
                        "file_path": r.file_path,
                        "line": r.line,
                        "column": r.column,
                        "message": r.message,
                        "rule_id": r.rule_id,
                        "severity": r.severity,
                    }
                    for r in analysis_result.lint_results
                ]
                events.append(AgentEvent.delta(
                    f"lint 工具发现 {len(lint_results)} 个问题\n"
                ))
                for w in analysis_result.warnings:
                    events.append(AgentEvent.delta(f"⚠️ {w}\n"))
            except Exception as e:
                events.append(AgentEvent.delta(
                    f"⚠️ 静态分析工具执行失败: {e}，将降级为纯 LLM 分析\n"
                ))
        else:
            events.append(AgentEvent.delta(
                "sandbox_execute 不可用，跳过静态分析工具执行\n"
            ))

        events.append(AgentEvent.step_end(3, "completed"))

        # ── Phase 4: Analyze ───────────────────
        events.append(AgentEvent.step_start(4, "综合分析", "LLM 多维度审查"))

        if llm and file_contents:
            analysis = await self._analyze_with_llm(
                llm, message, file_contents, lint_results, review_dimensions,
            )
            events.append(AgentEvent.delta(analysis))
        elif not file_contents:
            events.append(AgentEvent.delta("未找到可审查的代码文件。"))
        else:
            events.append(AgentEvent.delta(
                "未配置 LLM，无法进行代码分析。"
            ))

        events.append(AgentEvent.step_end(4, "completed"))

        # ── Phase 5: DraftReview ───────────────
        events.append(AgentEvent.step_start(5, "生成审查报告", "结构化输出"))

        report = await self._generate_report(
            llm, message, file_contents, lint_results, review_dimensions,
        )
        events.append(AgentEvent.delta(report))
        events.append(AgentEvent.step_end(5, "completed"))

        # ── Phase 6: Finalize ──────────────────
        events.append(AgentEvent.completed())
        return events

    # ── 辅助方法 ──────────────────────────────

    def _extract_target_path(self, message: str) -> str:
        """从消息中提取目标路径。"""
        path_match = re.search(r'[\w/\\]+\.\w+', message)
        if path_match:
            return path_match.group()
        for kw in ["目录", "路径", "文件夹", "folder", "dir", "目录下"]:
            idx = message.find(kw)
            if idx >= 0:
                rest = message[idx + len(kw):].strip().split()[0] if \
                    message[idx + len(kw):].strip() else ""
                if rest and rest not in [".", "/"]:
                    return rest
        return "."

    def _extract_dimensions(self, message: str) -> list[str]:
        """从消息中提取审查维度。"""
        dimensions = []
        dim_keywords = {
            "安全": "security",
            "性能": "performance",
            "架构": "architecture",
            "风格": "style",
            "正确性": "correctness",
            "可维护性": "maintainability",
            "维护": "maintainability",
        }
        for kw, dim in dim_keywords.items():
            if kw in message:
                if dim not in dimensions:
                    dimensions.append(dim)
        return dimensions or [
            "correctness", "security", "performance",
            "style", "maintainability",
        ]

    def _list_files(
        self, repository: Any, path: str, recursive: bool = True,
    ) -> list[dict]:
        """列出文件。"""
        if repository is None:
            return []
        try:
            from app.capabilities.repository import RepositoryListFilesRequest
            result = repository.list_files(RepositoryListFilesRequest(
                path_prefix=path,
                recursive=recursive,
                max_entries=100,
            ))
            return [
                {"path": e.path, "type": e.type, "size": e.size}
                for e in result.entries
            ]
        except Exception:
            return []

    def _filter_code_files(self, files: list[dict]) -> list[dict]:
        """过滤出代码文件。"""
        code_extensions = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go",
            ".rs", ".cpp", ".c", ".h", ".hpp", ".cs", ".rb", ".php",
            ".swift", ".kt", ".scala", ".sh", ".bash", ".zsh",
            ".yaml", ".yml", ".json", ".xml", ".toml", ".ini",
            ".md", ".rst", ".txt", ".cfg", ".conf",
        }
        return [
            f for f in files
            if any(f["path"].endswith(ext) for ext in code_extensions)
        ]

    def _read_file(self, repository: Any, path: str) -> str | None:
        """读取文件内容。"""
        if repository is None:
            return None
        try:
            from app.capabilities.repository import RepositoryReadFileRequest
            result = repository.read_file(RepositoryReadFileRequest(
                path=path,
                start_line=1,
                max_lines=200,
            ))
            return result.content
        except Exception:
            return None

    async def _analyze_with_llm(
        self,
        llm: Any,
        instructions: str,
        files: list[dict],
        lint_results: list[dict],
        dimensions: list[str],
    ) -> str:
        """使用 LLM 分析代码。"""
        file_list = "\n\n".join(
            f"### {f['path']}\n```\n{f['content'][:2000]}\n```"
            for f in files[:8]
        )

        lint_text = ""
        if lint_results:
            lint_text = "\n### Lint 工具输出\n"
            for r in lint_results[:20]:
                lint_text += (
                    f"- [{r.get('severity', '?')}] {r['file_path']}:"
                    f"{r['line']} — {r['message']}\n"
                )

        dims_text = "\n".join(f"- {d}" for d in dimensions)

        prompt = (
            f"你是一个资深代码审查专家。请根据以下要求审查代码：\n\n"
            f"审查要求：{instructions}\n\n"
            f"审查维度：\n{dims_text}\n\n"
            f"代码文件：\n{file_list}\n\n"
            f"{lint_text}\n"
            f"请从以下维度进行分析：\n"
            f"1. **正确性** - 逻辑错误、边界条件、异常处理\n"
            f"2. **安全性** - 注入风险、权限问题、敏感信息泄露\n"
            f"3. **性能** - 算法复杂度、资源使用、I/O 效率\n"
            f"4. **代码风格** - 命名规范、注释、代码结构\n"
            f"5. **可维护性** - 模块化、复用性、测试覆盖\n\n"
            f"对每个问题标注严重级别：🔴 严重 / 🟡 主要 / 🟢 建议\n"
            f"输出格式为 Markdown，包含\"问题汇总\"和\"改进建议\"两部分。"
        )

        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            return response.choices[0].message.content if \
                hasattr(response, "choices") else str(response)
        except Exception as e:
            return f"LLM 分析失败：{e}"

    async def _generate_report(
        self,
        llm: Any,
        instructions: str,
        files: list[dict],
        lint_results: list[dict],
        dimensions: list[str],
    ) -> str:
        """生成结构化审查报告。"""
        if not llm:
            return ""

        file_summary = "\n".join(
            f"| {f['path']} | {len(f['content'])} 字符 |"
            for f in files[:10]
        )

        lint_count = len(lint_results)
        lint_errors = sum(
            1 for r in lint_results if r.get("severity") == "error"
        )
        lint_warnings = lint_count - lint_errors

        dims_text = "、".join(dimensions)

        prompt = (
            f"请根据以下信息生成一份结构化的代码审查报告：\n\n"
            f"审查范围：{len(files)} 个文件\n"
            f"审查维度：{dims_text}\n\n"
            f"文件列表：\n"
            f"| 文件 | 大小 |\n|------|------|\n{file_summary}\n\n"
            f"Lint 结果：{lint_count} 个问题"
            f"（{lint_errors} 错误, {lint_warnings} 警告）\n\n"
            f"请生成 Markdown 格式的报告，包含：\n"
            f"1. 总体评分（1-10 分）\n"
            f"2. 各维度评分\n"
            f"3. 关键问题列表（Top 5）\n"
            f"4. 改进建议\n"
            f"5. 下一步行动建议"
        )

        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            content = response.choices[0].message.content if \
                hasattr(response, "choices") else str(response)
            return f"\n---\n## 📊 审查报告\n\n{content}\n"
        except Exception as e:
            return f"\n---\n报告生成失败：{e}\n"


def _make_tool_context(**kwargs: Any) -> Any:
    """构建一个简单的工具执行上下文。"""
    class ToolContext:
        pass
    ctx = ToolContext()
    for key, value in kwargs.items():
        setattr(ctx, key, value)
    return ctx