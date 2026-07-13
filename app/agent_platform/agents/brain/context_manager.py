"""Brain 路径的上下文管理器�?

职责�?
1. �?CustomizationEngine 组装 system_prompt
2. �?MemoryCommitGate / UserProfileService 注入记忆
3. 对话历史�?token 截断
4. token 计数与预算检�?
5. 可扩�?context_hooks：注入格式由调用方定制，不硬编码在平台中
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent_platform.agents.brain.models import IntentDecision


@dataclass
class BrainContext:
    """一�?LLM 调用所需的完整上下文�?""
    system_prompt: str
    messages: list[dict]       # 当前轮的消息
    history: list[dict]        # 截断后的历史
    token_count: int
    budget: Any | None = None


class BrainContextManager:
    """Brain 路径的上下文管理器�?""

    def __init__(
        self,
        customization_engine: Any | None = None,
        memory_commit_gate: Any | None = None,
        user_profile_service: Any | None = None,
        llm: Any | None = None,
        max_context_tokens: int = 32000,
        max_history_rounds: int = 20,
        summary_after_rounds: int = 8,
        max_message_chars: int = 4000,
        context_hooks: dict[str, Callable] | None = None,
    ) -> None:
        self._customization = customization_engine
        self._memory_gate = memory_commit_gate
        self._profile = user_profile_service
        self._llm = llm
        self._max_context_tokens = max_context_tokens
        self._max_history_rounds = max_history_rounds  # 滑窗保留最近 N 轮
        self._summary_after_rounds = summary_after_rounds  # 超过 N 轮时触发摘要
        self._max_message_chars = max_message_chars  # 单条消息截断阈值

        # 可扩展 hook：平台不关心注入格式，由调用方决定
        self._context_hooks = context_hooks or {}

    async def build(
        self,
        session: Any,
        message: str,
        decision: IntentDecision,
        available_tools: list[dict],
    ) -> BrainContext:
        """构建完整�?LLM 上下文�?""
        # 1. 基础 system_prompt
        if self._customization:
            try:
                customization_ctx = await self._customization.build_session_context(
                    agent_name=getattr(session, 'agent_name', None),
                )
                base_prompt = customization_ctx.system_prompt
            except Exception:
                base_prompt = "你是一�?AI 助手，可以使用工具来帮助用户�?
        else:
            base_prompt = "你是一�?AI 助手，可以使用工具来帮助用户�?
        system_parts = [base_prompt]

        # 2. 注入记忆上下文（格式�?hook 决定�?
        memories = await self._load_memories(session, message)
        if memories:
            default_fmt = lambda mems: "\n## 相关记忆\n" + "\n".join(
                f"- [{m.scope}] {m.content}" for m in mems
            )
            formatter = self._context_hooks.get("format_memories", default_fmt)
            system_parts.append(formatter(memories))

        # 3. 注入用户画像（格式由 hook 决定�?
        profile = await self._load_profile(session)
        if profile:
            default_fmt = lambda prefs: "\n## 用户偏好\n" + "\n".join(
                f"- {k}: {v}" for k, v in prefs.items()
            )
            formatter = self._context_hooks.get("format_profile", default_fmt)
            system_parts.append(formatter(profile))

        system_prompt = "\n\n".join(system_parts)

        # 4. 压缩历史（三层：超长截断 → 滑窗 → 递归摘要）
        history = self._truncate_long_messages(
            getattr(session, 'history', []),
            max_chars=self._max_message_chars,
        )
        history = await self._compress_history(
            history,
            max_tokens=self._max_context_tokens - self._count_tokens(system_prompt),
            max_rounds=self._max_history_rounds,
            summary_threshold=self._summary_after_rounds,
        )

        # 5. 历史消息转 dict 列表
        history_dicts = self._history_to_dicts(history)

        return BrainContext(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": message}],
            history=history_dicts,
            token_count=self._count_tokens(system_prompt, history_dicts, message),
            budget=getattr(session, 'budget', None),
        )

    async def _load_memories(self, session: Any, message: str) -> list | None:
        """返回原始记忆对象列表，格式化�?context_hooks 决定�?""
        if not self._memory_gate:
            return None
        try:
            memories = await self._memory_gate.retrieve(
                user_id=getattr(session, 'user_id', ''),
                query=message,
            )
            return memories or None
        except Exception:
            return None

    async def _load_profile(self, session: Any) -> dict | None:
        """返回原始偏好字典，格式化�?context_hooks 决定�?""
        if not self._profile:
            return None
        try:
            profile = await self._profile.get(
                user_id=getattr(session, 'user_id', ''),
            )
            if not profile or not profile.preferences:
                return None
            return profile.preferences
        except Exception:
            return None

    def _history_to_dicts(self, history: list) -> list[dict]:
        """�?history 消息对象转为 dict 列表�?""
        result = []
        for msg in history:
            if hasattr(msg, 'model_dump'):
                dumped = msg.model_dump()
                result.append({
                    "role": dumped.get("role", "user"),
                    "content": dumped.get("content", ""),
                })
            elif isinstance(msg, dict):
                result.append({
                    "role": msg.get("role", "user"),
                    "content": msg.get("content", ""),
                })
        return result

    def _truncate_long_messages(
        self, history: list, max_chars: int = 4000,
    ) -> list:
        """第一层：超长消息截断。

        对单条 tool_result 等超长消息，截断头部保留尾部。
        按 content 字段截断，保留最后 max_chars 字符。
        """
        truncated = []
        for msg in history:
            content = self._get_content(msg)
            if content and len(content) > max_chars:
                remaining = content[-max_chars:]
                truncated.append(self._set_content(msg, remaining))
            else:
                truncated.append(msg)
        return truncated

    async def _compress_history(
        self,
        history: list,
        max_tokens: int,
        max_rounds: int = 20,
        summary_threshold: int = 8,
    ) -> list:
        """第二、三层：滑窗 + 递归摘要。

        策略：
        1. 先按 round（user+assistant 对）计算轮数
        2. 如果总 token 不超限且轮数低于 max_rounds → 不做任何事
        3. 如果轮数超限 → 丢弃最早轮次直到满足 max_rounds
        4. 如果 token 仍超限 → 对最早轮次做 LLM 摘要后替换
        """
        # 快速路径：不超限且轮数适当
        if not history:
            return history
        if (self._count_tokens(history) <= max_tokens
                and self._count_rounds(history) <= max_rounds):
            return history

        # 第二层：滑窗 — 丢弃最早轮次直到满足 max_rounds
        compressed = list(history)
        while self._count_rounds(compressed) > max_rounds:
            # 找到最早一个完整 round 的结束位置
            end = self._find_first_round_end(compressed)
            compressed = compressed[end:] if end else compressed[1:]

        # 如果 token 不超限了 → 返回
        if self._count_tokens(compressed) <= max_tokens:
            return compressed

        # 第三层：递归摘要 — 对最早轮次做 LLM 摘要
        if self._llm is not None:
            compressed = await self._recursive_summarize(
                compressed, max_tokens, summary_threshold,
            )

        # 最终兜底：仍超限则 FIFO 丢弃
        while compressed and self._count_tokens(compressed) > max_tokens:
            compressed.pop(0)

        return compressed

    async def _recursive_summarize(
        self, history: list, max_tokens: int, threshold: int,
    ) -> list:
        """第三层：递归摘要。

        取最早 threshold 轮做 LLM 摘要，替换为一条 summary 消息。
        如果 token 仍超限则递归执行。
        """
        result = list(history)

        while result and self._count_tokens(result) > max_tokens:
            # 找到最早 threshold 轮的结束位置
            end = self._find_nth_round_end(result, threshold)
            if end is None or end <= 1:
                break

            early_rounds = result[:end]
            rest = result[end:]

            summary = await self._summarize_rounds(early_rounds)
            if summary is None:
                # LLM 摘要失败，退化为直接丢弃最早的消息
                result.pop(0)
                continue

            # 替换为一条约 100 字的 summary 消息
            summary_msg = {
                "role": "system",
                "content": f"[对话摘要] {summary[:500]}",
            }
            result = [summary_msg] + rest

        return result

    async def _summarize_rounds(self, rounds: list) -> str | None:
        """用 LLM 对一批历史轮次做摘要。"""
        if self._llm is None:
            return None
        if not rounds:
            return None

        prompt = self._build_summary_prompt(rounds)
        try:
            response = await self._llm.chat([{"role": "user", "content": prompt}])
            return response.content.strip()
        except Exception:
            return None

    def _build_summary_prompt(self, rounds: list) -> str:
        """构建摘要 prompt。"""
        texts = []
        for r in rounds:
            role = self._get_content_field(r, "role", "unknown")
            content = self._get_content(r, max_len=300)
            texts.append(f"[{role}] {content}")

        return (
            "请用中文将以下历史对话压缩为一条约 100 字的摘要，"
            "保留关键事实、决策和用户的意图。只输出摘要内容，不要多余格式。\n\n"
            + "\n---\n".join(texts)
        )

    # ── 辅助方法 ────────────────────────────────────

    def _get_content(self, msg, max_len: int = 0) -> str:
        """从消息对象中提取 content 字段。"""
        if hasattr(msg, 'model_dump'):
            dumped = msg.model_dump()
            content = dumped.get("content", "")
        elif isinstance(msg, dict):
            content = msg.get("content", "")
        elif hasattr(msg, 'content'):
            content = msg.content
        else:
            content = ""
        return content[:max_len] if max_len else content

    def _get_content_field(self, msg, field: str, default: str = "") -> str:
        """从消息对象中提取指定字段。"""
        if hasattr(msg, 'model_dump'):
            return msg.model_dump().get(field, default)
        elif isinstance(msg, dict):
            return msg.get(field, default)
        elif hasattr(msg, field):
            return getattr(msg, field, default)
        return default

    def _set_content(self, msg, content: str):
        """设置消息对象的 content 字段，返回新对象。"""
        if hasattr(msg, 'model_dump'):
            d = msg.model_dump()
            d["content"] = content
            return d
        elif isinstance(msg, dict):
            return {**msg, "content": content}
        elif hasattr(msg, 'content'):
            msg.content = content
            return msg
        return msg

    def _count_rounds(self, history: list) -> int:
        """估算历史中的轮数（按 user/assistant 对计数）。"""
        role_count = sum(
            1 for msg in history
            if self._get_content_field(msg, "role") in ("user", "assistant")
        )
        return (role_count + 1) // 2

    def _find_first_round_end(self, history: list) -> int:
        """找到最早一个完整 round 的结束索引。"""
        for i, msg in enumerate(history):
            role = self._get_content_field(msg, "role")
            if role in ("user", "assistant"):
                # 找到第二个 user 或结尾
                for j in range(i + 1, len(history)):
                    r2 = self._get_content_field(history[j], "role")
                    if r2 == "user":
                        return j
                return len(history)
        return len(history) // 2

    def _find_nth_round_end(self, history: list, n: int) -> int | None:
        """找到第 n 个 round 的结束位置。"""
        round_count = 0
        for i, msg in enumerate(history):
            role = self._get_content_field(msg, "role")
            if role in ("user", "assistant"):
                round_count += 1
            if round_count >= n * 2:  # n 个 round = n 对 user+assistant
                # 继续走到下一个 user 或结尾
                for j in range(i + 1, len(history)):
                    r2 = self._get_content_field(history[j], "role")
                    if r2 == "user":
                        return j
                return len(history)
        return None

    def _count_tokens(self, *args) -> int:
        """粗略 token 计数�? 字符 �?1 token）�?""
        total = 0
        for arg in args:
            if isinstance(arg, str):
                total += len(arg) // 4
            elif isinstance(arg, list):
                for item in arg:
                    if isinstance(item, dict):
                        total += len(item.get("content", "")) // 4
                        total += len(item.get("role", "")) // 4
                    elif hasattr(item, 'content'):
                        total += len(item.content) // 4
        return total
