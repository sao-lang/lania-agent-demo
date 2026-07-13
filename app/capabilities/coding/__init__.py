"""Coding Agent Capability 模块。

提供代码助手能力，实际执行 lint/静态分析工具 + LLM 多维度代码审查。
"""

from app.capabilities.coding.service import CodingCapability

__all__ = ["CodingCapability"]
