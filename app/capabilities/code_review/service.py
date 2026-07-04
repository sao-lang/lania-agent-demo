"""代码审查 Capability 实现。

分析仓库中的代码，识别潜在问题，生成结构化审查报告。
使用 repository 工具读取代码 + LLM 分析，不需要沙盒执行。
"""

from __future__ import annotations

from typing import Any

from app.models.agent import AgentEvent


class CodeReviewCapability:
    """代码审查能力。

    分析指定路径下的代码质量、安全性和最佳实践。
    """

    name = "code_review"

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def execute(
        self,
        message: str,
        context: dict[str, Any],
    ) -> list[AgentEvent]:
        """执行代码审查。

        Args:
            message: 审查要求（含目标路径）。
            context: 执行上下文（含 repository, llm 等）。

        Returns:
            Agent 事件列表。
        """
        events: list[AgentEvent] = []
        repository = context.get("repository")
        llm = context.get("llm") or self._llm

        # 从消息中提取审查目标和路径
        target_path = self._extract_target_path(message)

        events.append(AgentEvent.tool_call(
            "list_repository_files",
            {"path": target_path, "recursive": False},
        ))

        # 1. 列出目录内容
        files = self._list_files(repository, target_path)
        if not files:
            events.append(AgentEvent.delta(
                f"路径 '{target_path}' 未找到文件，尝试递归搜索..."
            ))
            files = self._list_files(repository, target_path, recursive=True)

        events.append(AgentEvent.tool_result(
            "list_repository_files", "success",
        ))
        events.append(AgentEvent.delta(
            f"找到 {len(files)} 个文件\n\n"
        ))

        # 2. 读取关键文件（代码文件）
        code_files = self._filter_code_files(files[:10])
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
            f"已读取 {len(file_contents)} 个代码文件，开始分析...\n\n"
        ))

        # 3. LLM 分析
        if llm and file_contents:
            analysis = await self._analyze_with_llm(
                llm, message, file_contents,
            )
            events.append(AgentEvent.delta(analysis))
        elif not file_contents:
            events.append(AgentEvent.delta("未找到可审查的代码文件。"))
        else:
            events.append(AgentEvent.delta(
                "未配置 LLM，无法进行分析。已读取以下文件：\n"
                + "\n".join(f"- {f['path']}" for f in file_contents)
            ))

        events.append(AgentEvent.completed())
        return events

    def _extract_target_path(self, message: str) -> str:
        """从消息中提取目标路径。"""
        # 尝试匹配路径模式
        import re
        path_match = re.search(r'[\w/\\]+\.\w+', message)
        if path_match:
            return path_match.group()
        # 尝试匹配目录引用
        for kw in ["目录", "路径", "文件夹", "folder", "dir", "目录下"]:
            idx = message.find(kw)
            if idx >= 0:
                rest = message[idx + len(kw):].strip().split()[0] if \
                    message[idx + len(kw):].strip() else ""
                if rest and rest not in [".", "/"]:
                    return rest
        return "."

    def _list_files(
        self, repository: Any, path: str, recursive: bool = False,
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
    ) -> str:
        """使用 LLM 分析代码。"""
        file_list = "\n\n".join(
            f"### {f['path']}\n```\n{f['content'][:2000]}\n```"
            for f in files
        )

        prompt = (
            f"你是一个资深代码审查专家。请根据以下要求审查代码：\n\n"
            f"审查要求：{instructions}\n\n"
            f"代码文件：\n{file_list}\n\n"
            f"请从以下维度进行分析：\n"
            f"1. **架构设计** - 模块划分、依赖关系是否合理\n"
            f"2. **代码质量** - 命名、注释、复杂度\n"
            f"3. **安全性** - 潜在的安全风险\n"
            f"4. **错误处理** - 异常捕获和错误传播\n"
            f"5. **改进建议** - 具体的优化建议\n\n"
            f"对每个问题标注严重级别：🔴 严重 / 🟡 主要 / 🟢 建议\n"
            f"输出格式为 Markdown。"
        )

        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            return response.choices[0].message.content if \
                hasattr(response, "choices") else str(response)
        except Exception as e:
            return f"LLM 分析失败：{e}"
