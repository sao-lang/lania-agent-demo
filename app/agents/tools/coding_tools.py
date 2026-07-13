"""Coding Agent 工具模块。

提供代码问题提取和静态分析工具执行两个工具，
供 CodingCapability 在代码审查工作流中使用。
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolExecutionError, ToolRetryPolicy


# ── 数据模型 ──────────────────────────────────

class CodeFile(BaseModel):
    """代码文件信息。"""

    path: str
    content: str
    language: str = ""


class LintResult(BaseModel):
    """lint 工具的单个结果。"""

    file_path: str
    line: int
    column: int = 0
    message: str
    rule_id: str = ""
    severity: Literal["error", "warning", "info"] = "warning"


class CodeIssue(BaseModel):
    """代码问题。"""

    issue_id: str
    file_path: str
    line_start: int
    line_end: int = 0
    severity: Literal["critical", "major", "minor", "info"]
    category: Literal[
        "architecture", "security", "performance",
        "style", "correctness", "maintainability",
    ]
    title: str
    description: str
    suggestion: str | None = None
    source: Literal["linter", "llm", "test"] = "llm"


# ── 工具输入 / 输出 ──────────────────────────

class ExtractCodeIssuesInput(BaseModel):
    """代码问题提取输入。"""

    instructions: str
    files: list[CodeFile] = Field(default_factory=list)
    lint_results: list[LintResult] = Field(default_factory=list)
    review_dimensions: list[str] = Field(
        default_factory=lambda: [
            "correctness", "security", "performance",
            "style", "maintainability",
        ],
    )


class ExtractCodeIssuesOutput(BaseModel):
    """代码问题提取输出。"""

    summary: str = ""
    issues: list[CodeIssue] = Field(default_factory=list)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)


class RunCodeAnalysisInput(BaseModel):
    """代码分析执行输入。"""

    target_path: str = "."
    analysis_types: list[str] = Field(
        default_factory=lambda: ["lint", "type_check"],
    )
    timeout_seconds: int = Field(default=60, ge=1, le=300)


class RunCodeAnalysisOutput(BaseModel):
    """代码分析执行输出。"""

    lint_results: list[LintResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    success: bool = False


# ── 工具类 ──────────────────────────────────

class ExtractCodeIssuesTool:
    """从代码文件和分析结果中提取结构化问题列表。

    基于 LLM 分析代码文件，结合 lint 工具输出，提取结构化问题。
    """

    name = "extract_code_issues"
    version = "v1"
    timeout_ms = 30000
    retry_policy = ToolRetryPolicy(max_attempts=1, backoff_ms=300)
    trace_fields = [
        "tool_call_id", "task_id", "step_name", "tool_name",
        "duration_ms", "status",
    ]
    input_model = ExtractCodeIssuesInput
    output_model = ExtractCodeIssuesOutput

    def run(
        self, payload: ExtractCodeIssuesInput, context,
    ) -> ExtractCodeIssuesOutput:
        """提取结构化代码问题。"""
        llm = getattr(context, "llm", None)
        if llm is None:
            return ExtractCodeIssuesOutput(
                summary="LLM 不可用，无法进行代码分析。",
                issues=[],
                coverage=0.0,
            )

        # 构建分析提示
        prompt = self._build_analysis_prompt(
            payload.files,
            payload.lint_results,
            payload.review_dimensions,
        )

        try:
            response = llm.complete(prompt)
            raw_text = str(response)
            issues = self._parse_issues(raw_text, payload.files, payload.lint_results)
            return ExtractCodeIssuesOutput(
                summary=self._extract_summary(raw_text),
                issues=issues,
                coverage=min(1.0, len(payload.files) / 10.0),
            )
        except Exception:
            return ExtractCodeIssuesOutput(
                summary="LLM 分析失败，请检查 LLM 配置。",
                issues=[],
                coverage=0.0,
            )

    def _build_analysis_prompt(
        self,
        files: list[CodeFile],
        lint_results: list[LintResult],
        dimensions: list[str],
    ) -> str:
        dims = "、".join(dimensions)
        files_text = "\n\n".join(
            f"### {f.path}\n```{f.language or 'auto'}\n{f.content}\n```"
            for f in files[:10]
        )

        lint_text = ""
        if lint_results:
            lint_text = "\n\n## Lint 工具输出\n"
            for r in lint_results[:20]:
                lint_text += (
                    f"- [{r.severity}] {r.file_path}:{r.line}:{r.column}"
                    f" — {r.message}\n"
                )

        return f"""你是一位资深代码审查专家。请对以下代码进行多维度分析。

审查维度：{dims}

{files_text}

{lint_text}

请以 JSON 格式输出分析结果：
```json
{{
  "summary": "整体代码质量评估摘要（中文，100字以内）",
  "issues": [
    {{
      "file_path": "文件路径",
      "line_start": 行号,
      "severity": "critical|major|minor|info",
      "category": "correctness|security|performance|style|maintainability|architecture",
      "title": "问题标题",
      "description": "问题描述",
      "suggestion": "改进建议"
    }}
  ]
}}
```

请确保：
1. 只输出真正的代码问题，不要过度报告
2. severity 判断要合理：critical 为可能导致崩溃/安全漏洞的问题
3. 每条 issue 都要有具体可操作的改进建议
4. 最多输出 15 条 issues"""

    def _parse_issues(
        self,
        text: str,
        files: list[CodeFile],
        lint_results: list[LintResult],
    ) -> list[CodeIssue]:
        issues: list[CodeIssue] = []

        # 尝试解析 JSON
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                parsed = json.loads(match.group(0))
                raw_issues = parsed.get("issues", [])
                for idx, ri in enumerate(raw_issues, start=1):
                    issues.append(CodeIssue(
                        issue_id=f"issue-{idx}",
                        file_path=str(ri.get("file_path", "")),
                        line_start=int(ri.get("line_start", 0)),
                        line_end=int(ri.get("line_end", 0)),
                        severity=self._normalize_severity(ri.get("severity")),
                        category=self._normalize_category(ri.get("category")),
                        title=str(ri.get("title", f"问题 {idx}")),
                        description=str(ri.get("description", "")),
                        suggestion=str(ri.get("suggestion", "")) or None,
                        source="llm",
                    ))
                return issues[:15]
            except (json.JSONDecodeError, ValueError):
                pass

        # 回退：从 lint 结果生成 issues
        known_files = {f.path for f in files}
        for idx, lr in enumerate(lint_results[:15], start=1):
            if lr.file_path in known_files:
                issues.append(CodeIssue(
                    issue_id=f"lint-{idx}",
                    file_path=lr.file_path,
                    line_start=lr.line,
                    severity="major" if lr.severity == "error" else "minor",
                    category="style",
                    title=lr.message,
                    description=lr.message,
                    source="linter",
                ))
        return issues

    def _extract_summary(self, text: str) -> str:
        match = re.search(r"\"summary\"\s*:\s*\"([^\"]+)\"", text)
        if match:
            return match.group(1)
        return "代码分析完成（详见 issues 列表）。"

    @staticmethod
    def _normalize_severity(value: object) -> Literal["critical", "major", "minor", "info"]:
        valid = {"critical", "major", "minor", "info"}
        s = str(value or "minor").strip().lower()
        if s in valid:
            return s  # type: ignore[return-value]
        if s == "error":
            return "major"
        if s == "warning":
            return "minor"
        return "minor"

    @staticmethod
    def _normalize_category(
        value: object,
    ) -> Literal["architecture", "security", "performance", "style", "correctness", "maintainability"]:
        valid = {"architecture", "security", "performance", "style", "correctness", "maintainability"}
        s = str(value or "style").strip().lower()
        if s in valid:
            return s  # type: ignore[return-value]
        return "style"


class RunCodeAnalysisTool:
    """执行代码分析工具链（pyflakes, mypy, pytest 等）。

    通过 sandbox_execute 在沙盒中运行分析工具，解析输出为结构化结果。
    如果 sandbox_execute 不可用，返回空结果但不阻塞工作流。
    """

    name = "run_code_analysis"
    version = "v1"
    timeout_ms = 120000
    retry_policy = ToolRetryPolicy(max_attempts=0, backoff_ms=0)
    trace_fields = [
        "tool_call_id", "task_id", "step_name", "tool_name",
        "duration_ms", "status",
    ]
    input_model = RunCodeAnalysisInput
    output_model = RunCodeAnalysisOutput

    def run(
        self, payload: RunCodeAnalysisInput, context,
    ) -> RunCodeAnalysisOutput:
        """执行代码分析。"""
        services = getattr(context, "services", None) or {}
        sandbox = services.get("sandbox_execute")

        lint_results: list[LintResult] = []
        warnings: list[str] = []
        errors: list[str] = []

        if sandbox is None:
            warnings.append("sandbox_execute 不可用，跳过静态分析工具执行")
            return RunCodeAnalysisOutput(
                lint_results=lint_results,
                warnings=warnings,
                errors=errors,
                success=False,
            )

        from app.capabilities.sandbox_execute import (
            CommandExecutionRequest,
            build_sandboxed_policy,
        )
        policy = build_sandboxed_policy()

        # 1. pyflakes
        if "lint" in payload.analysis_types:
            try:
                result = sandbox.execute(
                    CommandExecutionRequest(
                        command="python",
                        args=["-m", "pyflakes", payload.target_path],
                        working_directory=".",
                        timeout_seconds=min(payload.timeout_seconds, 30),
                    ),
                    policy=policy,
                )
                lint_results.extend(self._parse_pyflakes(result.stdout, result.stderr))
            except ToolExecutionError as e:
                warnings.append(f"pyflakes 执行失败: {e}")
            except Exception as e:
                warnings.append(f"pyflakes 执行异常: {e}")

        # 2. mypy（类型检查）
        if "type_check" in payload.analysis_types:
            try:
                result = sandbox.execute(
                    CommandExecutionRequest(
                        command="python",
                        args=["-m", "mypy", payload.target_path, "--ignore-missing-imports"],
                        working_directory=".",
                        timeout_seconds=min(payload.timeout_seconds, 60),
                    ),
                    policy=policy,
                )
                lint_results.extend(self._parse_mypy(result.stdout, result.stderr))
            except ToolExecutionError as e:
                warnings.append(f"mypy 执行失败: {e}")
            except Exception as e:
                warnings.append(f"mypy 执行异常: {e}")

        success = len(errors) == 0
        return RunCodeAnalysisOutput(
            lint_results=lint_results,
            warnings=warnings,
            errors=errors,
            success=success,
        )

    @staticmethod
    def _parse_pyflakes(stdout: str, stderr: str) -> list[LintResult]:
        """解析 pyflakes 输出格式。

        pyflakes 格式: path:line:column message
        """
        results: list[LintResult] = []
        for line in (stdout + "\n" + stderr).splitlines():
            match = re.match(
                r"^(.+?):(\d+):(\d+)\s+(.+)$",
                line.strip(),
            )
            if match:
                results.append(LintResult(
                    file_path=match.group(1),
                    line=int(match.group(2)),
                    column=int(match.group(3)),
                    message=match.group(4),
                    rule_id="pyflakes",
                    severity="warning",
                ))
        return results

    @staticmethod
    def _parse_mypy(stdout: str, stderr: str) -> list[LintResult]:
        """解析 mypy 输出格式。

        mypy 格式: path:line: severity: message
        """
        results: list[LintResult] = []
        for line in (stdout + "\n" + stderr).splitlines():
            match = re.match(
                r"^(.+?):(\d+):\s*(error|warning|note):\s*(.+)$",
                line.strip(),
            )
            if match:
                severity = match.group(3)
                sev: Literal["error", "warning", "info"] = "warning"
                if severity == "error":
                    sev = "error"
                elif severity == "note":
                    sev = "info"
                results.append(LintResult(
                    file_path=match.group(1),
                    line=int(match.group(2)),
                    column=0,
                    message=match.group(4),
                    rule_id="mypy",
                    severity=sev,
                ))
        return results
