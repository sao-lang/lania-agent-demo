"""统一意图识别模块。

双层识别策略：
  Layer 1: QuickHeuristicClassifier — 规则引擎，< 1ms
  Layer 2: LLMIntentClassifier — LLM 分类，~200ms

输出结构化的 IntentDecision，替代旧的 IntentMatcher。
"""

from __future__ import annotations

import re
from typing import Any

from app.harness.brain.models import (
    Complexity,
    IntentDecision,
    KnowledgeSource,
    RiskLevel,
    SuggestedMode,
)


class QuickHeuristicClassifier:
    """第一层：快速启发式分类器。

    通过关键词和正则表达式在 <1ms 内匹配常见意图。
    覆盖约 80% 的常见场景，余下交给 LLM 层。
    """

    # ── 数学表达式 ──
    MATH_PATTERN = re.compile(
        r'^[\d\s+\-*/%().,^sqrt|abs|ceil|floor|log|ln|sin|cos|tan]+$',
    )
    MATH_KEYWORDS = [
        '等于', '计算', '多少', '加减乘除', '平方', '开方', '公式',
        '等于多少', '结果', '算式', '运算', '解方程',
    ]

    # ── 翻译请求 ──
    TRANSLATE_PATTERNS = [
        re.compile(r'(翻译|译成|convert).{0,20}(英文|中文|日文|法文|德文|韩文)'),
        re.compile(r'(translate|convert).{0,20}(to|into)'),
    ]

    # ── 问候 ──
    GREETING_KEYWORDS = ['你好', '您好', '嗨', 'hello', 'hi', 'hey', '早上好', '晚上好', '下午好']

    # ── 搜索关键词 ──
    SEARCH_KEYWORDS = [
        '搜索', '查一下', '网上搜索', '互联网', '网页', '查找资料',
        'search', 'google', 'baidu', 'bing',
        '最新', '今天', '新闻', '天气', '股价', '汇率',
        'what is', 'who is', 'tell me about',
    ]

    # ── 代码审查 ──
    CODE_REVIEW_KEYWORDS = [
        '代码审查', 'review代码', '审查代码', '代码质量', '代码问题',
        '代码安全', '安全漏洞', '代码异味', '重构代码',
        'code review', 'code quality', 'security issue',
    ]

    # ── 类型报错/修复 ──
    TYPE_ERROR_KEYWORDS = [
        '类型报错', '类型错误', '编译错误', '编译报错',
        'type error', 'typecheck', 'tsc', 'mypy',
        'lint', 'eslint', 'flake8', 'pylint',
        '修复', 'fix', '类型', '报错',
    ]

    # ── 数据库操作 ──
    DATABASE_KEYWORDS = [
        '查询数据库', '数据库', 'sql', '数据表',
        'query', 'select', 'insert', 'update', 'delete',
        'drop table', 'alter table', 'create table',
    ]

    # ── 代码仓库 ──
    CODE_REPO_KEYWORDS = [
        '读取文件', '查看代码', '项目结构', '代码仓库',
        'read file', 'repository', 'repo', 'source code',
        'git log', 'git diff', 'git status',
    ]

    # ── 沙箱执行 ──
    SANDBOX_KEYWORDS = [
        '运行代码', '执行脚本', '运行python', '运行js',
        'run code', 'execute', 'sandbox',
    ]

    # ── 数据分析 ──
    DATA_ANALYSIS_KEYWORDS = [
        '数据分析', '统计', '图表', '趋势', '可视化',
        'data analysis', 'statistics', 'chart', 'plot',
    ]

    async def classify(self, message: str) -> IntentDecision | None:
        """快速分类，匹配成功时返回 IntentDecision，否则返回 None。

        Args:
            message: 用户输入。

        Returns:
            匹配成功时返回 IntentDecision，否则 None（交给 LLM 层）。
        """
        msg = message.strip()
        msg_lower = msg.lower()

        # ── 1. 数学表达式 ──
        if self.MATH_PATTERN.match(msg) or any(kw in msg for kw in self.MATH_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.SIMPLE,
                suggested_sources=[KnowledgeSource.CALCULATOR],
                suggested_mode=SuggestedMode.CHAT,
                risk_level=RiskLevel.LOW,
                confidence=0.9,
                reasoning="规则匹配: 数学表达式/计算请求",
                matched_capabilities=["calculator"],
            )

        # ── 2. 翻译请求 ──
        for pattern in self.TRANSLATE_PATTERNS:
            if pattern.search(msg):
                return IntentDecision(
                    complexity=Complexity.SIMPLE,
                    suggested_sources=[KnowledgeSource.INTERNAL_LLM],
                    suggested_mode=SuggestedMode.CHAT,
                    risk_level=RiskLevel.LOW,
                    confidence=0.85,
                    reasoning="规则匹配: 翻译请求",
                )

        # ── 3. 简单问候 ──
        if any(kw in msg_lower for kw in self.GREETING_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.SIMPLE,
                suggested_sources=[KnowledgeSource.INTERNAL_LLM],
                suggested_mode=SuggestedMode.CHAT,
                risk_level=RiskLevel.LOW,
                confidence=0.95,
                reasoning="规则匹配: 问候",
            )

        # ── 4. 代码审查/安全漏洞分析 ──
        if any(kw in msg for kw in self.CODE_REVIEW_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.COMPLEX,
                suggested_sources=[
                    KnowledgeSource.CODE_REPO,
                    KnowledgeSource.INTERNAL_LLM,
                ],
                suggested_mode=SuggestedMode.PLAN,
                needs_planning=True,
                risk_level=RiskLevel.HIGH,
                confidence=0.85,
                reasoning="规则匹配: 代码审查/安全分析，需要读取代码仓库",
                matched_capabilities=["code_review"],
            )

        # ── 5. 类型报错/修复 → 高风险，需要规划 ──
        if any(kw in msg for kw in self.TYPE_ERROR_KEYWORDS):
            sources = [KnowledgeSource.CODE_REPO, KnowledgeSource.SHELL_CMD]
            return IntentDecision(
                complexity=Complexity.COMPLEX,
                suggested_sources=sources,
                suggested_mode=SuggestedMode.PLAN,
                needs_planning=True,
                risk_level=RiskLevel.HIGH,
                confidence=0.8,
                reasoning="规则匹配: 类型报错/代码修复，需要运行命令和读取文件",
                matched_capabilities=["coding"],
            )

        # ── 6. 数据库操作 → 高风险 ──
        if any(kw in msg_lower for kw in self.DATABASE_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.MODERATE,
                suggested_sources=[KnowledgeSource.DATABASE],
                suggested_mode=SuggestedMode.PLAN_CONFIRM,
                needs_planning=True,
                risk_level=RiskLevel.HIGH,
                confidence=0.75,
                reasoning="规则匹配: 数据库操作，需要确认",
            )

        # ── 7. 搜索请求 ──
        if any(kw in msg for kw in self.SEARCH_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.SIMPLE,
                suggested_sources=[KnowledgeSource.WEB_SEARCH],
                suggested_mode=SuggestedMode.AUTOPILOT,
                risk_level=RiskLevel.MEDIUM,
                confidence=0.8,
                reasoning="规则匹配: 搜索请求",
                matched_capabilities=["web_search"],
            )

        # ── 8. 代码仓库操作 ──
        if any(kw in msg for kw in self.CODE_REPO_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.MODERATE,
                suggested_sources=[KnowledgeSource.CODE_REPO],
                suggested_mode=SuggestedMode.AUTOPILOT,
                risk_level=RiskLevel.MEDIUM,
                confidence=0.8,
                reasoning="规则匹配: 代码仓库操作",
            )

        # ── 9. 数据分析 ──
        if any(kw in msg for kw in self.DATA_ANALYSIS_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.MODERATE,
                suggested_sources=[KnowledgeSource.SANDBOX_EXEC],
                suggested_mode=SuggestedMode.AUTOPILOT,
                risk_level=RiskLevel.MEDIUM,
                confidence=0.75,
                reasoning="规则匹配: 数据分析请求",
                matched_capabilities=["data_analysis"],
            )

        # ── 10. 沙箱执行 ──
        if any(kw in msg for kw in self.SANDBOX_KEYWORDS):
            return IntentDecision(
                complexity=Complexity.COMPLEX,
                suggested_sources=[KnowledgeSource.SANDBOX_EXEC],
                suggested_mode=SuggestedMode.PLAN,
                needs_planning=True,
                risk_level=RiskLevel.HIGH,
                confidence=0.8,
                reasoning="规则匹配: 代码执行请求",
            )

        # 未匹配 → 交给 LLM 层
        return None


class LLMIntentClassifier:
    """第二层：LLM 驱动意图分类器。

    当规则引擎无法匹配时，由 LLM 分析用户意图并输出结构化 IntentDecision。
    """

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def classify(
        self,
        message: str,
        history: list[dict] | None = None,
        available_capabilities: list[str] | None = None,
        **kwargs: Any,
    ) -> IntentDecision:
        """使用 LLM 进行意图分类。

        Args:
            message: 用户输入。
            history: 对话历史。
            available_capabilities: 可用能力列表。

        Returns:
            IntentDecision 结构化结果。
        """
        capabilities = available_capabilities or []

        system_prompt = (
            "你是一个意图识别专家。分析用户消息，输出 JSON 格式的意图判断。\n"
            "只输出 JSON，不要输出额外解释。\n\n"
            "JSON schema:\n"
            "{\n"
            '  "complexity": "simple" | "moderate" | "complex",\n'
            '  "suggested_sources": ["internal_llm" | "rag" | "web_search" | "web_fetch" | '
            '"calculator" | "code_repo" | "database" | "sandbox_exec" | "shell_cmd"],\n'
            '  "suggested_mode": "chat" | "autopilot" | "plan" | "plan_confirm",\n'
            '  "needs_planning": bool,\n'
            '  "risk_level": "low" | "medium" | "high" | "critical",\n'
            '  "reasoning": "判断理由（中文）",\n'
            '  "matched_capabilities": ["能力名称"]\n'
            "}\n\n"
            "知识来源说明:\n"
            "- internal_llm: LLM 训练数据可覆盖（翻译、概念解释、常识问答）\n"
            "- rag: 需要检索内部文档\n"
            "- web_search: 需要互联网搜索实时信息\n"
            "- web_fetch: 需要抓取指定 URL\n"
            "- calculator: 需要精确数学计算\n"
            "- code_repo: 需要读取/分析代码\n"
            "- database: 需要查询数据库\n"
            "- sandbox_exec: 需要沙箱执行代码\n"
            "- shell_cmd: 需要执行系统命令\n\n"
            "复杂度说明:\n"
            "- simple: 单步可解答\n"
            "- moderate: 需要 1-2 个工具辅助\n"
            "- complex: 需要多步规划、多工具编排\n\n"
            "风险等级说明:\n"
            "- low: 纯计算/只读/无副作用\n"
            "- medium: HTTP 读取/文件读取\n"
            "- high: 代码执行/数据写入/批量操作\n"
            "- critical: 系统命令/删除/敏感数据\n\n"
            "模式说明:\n"
            "- chat: 直接回答，无需交互\n"
            "- autopilot: 自动执行 + 披露\n"
            "- plan: 展示计划后执行\n"
            "- plan_confirm: 展示计划 + 二次确认"
        )

        if capabilities:
            system_prompt += "\n\n可用能力:\n" + "\n".join(f"- {c}" for c in capabilities)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history[-4:])  # 只取最近 4 轮
        messages.append({"role": "user", "content": message})

        try:
            response = await self._llm.chat(messages)
            content = response.choices[0].message.content.strip()
            return self._parse_llm_response(content)
        except Exception:
            return self._default_decision()

    def _parse_llm_response(self, content: str) -> IntentDecision:
        """解析 LLM 返回的 JSON 字符串。"""
        import json

        # 尝试提取 JSON
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return IntentDecision(
                    complexity=self._safe_enum(data.get("complexity"), Complexity, Complexity.MODERATE),
                    suggested_sources=self._safe_sources(data.get("suggested_sources", [])),
                    suggested_mode=self._safe_enum(data.get("suggested_mode"), SuggestedMode, SuggestedMode.CHAT),
                    needs_planning=bool(data.get("needs_planning", False)),
                    risk_level=self._safe_enum(data.get("risk_level"), RiskLevel, RiskLevel.MEDIUM),
                    confidence=0.6,
                    reasoning=data.get("reasoning", "LLM 分类"),
                    matched_capabilities=data.get("matched_capabilities", []),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        return self._default_decision()

    def _default_decision(self) -> IntentDecision:
        """LLM 调用失败时的兜底决策。"""
        return IntentDecision(
            complexity=Complexity.MODERATE,
            suggested_sources=[KnowledgeSource.INTERNAL_LLM],
            suggested_mode=SuggestedMode.CHAT,
            risk_level=RiskLevel.LOW,
            confidence=0.4,
            reasoning="LLM 分类失败，兜底为 chat",
        )

    @staticmethod
    def _safe_enum(value: Any, enum_class: type, default: Any) -> Any:
        """安全地将值转换为枚举成员。"""
        if value is None:
            return default
        try:
            return enum_class(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _safe_sources(sources: list) -> list[KnowledgeSource]:
        """安全地将字符串列表转换为 KnowledgeSource 列表。"""
        result: list[KnowledgeSource] = []
        for s in sources:
            try:
                result.append(KnowledgeSource(s))
            except (ValueError, TypeError):
                pass
        if not result:
            result.append(KnowledgeSource.INTERNAL_LLM)
        return result


class IntentRecognizer:
    """统一意图识别器。

    双层策略：
    Layer 1: QuickHeuristicClassifier — 规则引擎快速匹配
    Layer 2: LLMIntentClassifier — LLM 兜底分类

    用法:
        recognizer = IntentRecognizer(llm=llm_instance)
        decision = await recognizer.recognize(
            message="Rust 核心特性 vs C",
            history=history,
            available_capabilities=["rag", "web_search", ...],
        )
    """

    def __init__(
        self,
        llm: Any | None = None,
        enable_llm_fallback: bool = True,
    ) -> None:
        self._rule_classifier = QuickHeuristicClassifier()
        self._llm_classifier = LLMIntentClassifier(llm=llm)
        self._enable_llm_fallback = enable_llm_fallback

    async def recognize(
        self,
        message: str,
        history: list[dict] | None = None,
        available_capabilities: list[str] | None = None,
    ) -> IntentDecision:
        """识别用户意图。

        Args:
            message: 用户输入。
            history: 对话历史。
            available_capabilities: 可用能力列表（给 LLM 层参考）。

        Returns:
            结构化的意图识别结果。
        """
        # Layer 1: 规则引擎快速匹配
        decision = await self._rule_classifier.classify(message)
        if decision is not None:
            return decision

        # Layer 2: LLM 兜底
        if self._enable_llm_fallback and self._llm_classifier._llm is not None:
            return await self._llm_classifier.classify(
                message=message,
                history=history,
                available_capabilities=available_capabilities,
            )

        # 最终兜底
        return IntentDecision(
            complexity=Complexity.SIMPLE,
            suggested_sources=[KnowledgeSource.INTERNAL_LLM],
            suggested_mode=SuggestedMode.CHAT,
            risk_level=RiskLevel.LOW,
            confidence=0.5,
            reasoning="规则+LLM 均未匹配，兜底 chat",
        )
