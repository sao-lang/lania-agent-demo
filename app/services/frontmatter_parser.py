"""Frontmatter 解析与校验服务。

统一解析所有原语文件的 YAML frontmatter + body，
支持 ``PrimitiveFrontmatter`` Pydantic 校验。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.models.customization import PrimitiveFrontmatter


class FrontmatterParseResult:
    """Frontmatter 解析结果。

    Args:
        frontmatter: 解析后的结构化 frontmatter 数据。
        body: frontmatter 之后的正文内容。
        raw: 原始 frontmatter 字典（未校验）。
        errors: 校验错误信息列表（如有）。
    """

    def __init__(
        self,
        frontmatter: PrimitiveFrontmatter | None = None,
        body: str = "",
        raw: dict[str, Any] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self.frontmatter = frontmatter
        self.body = body
        self.raw = raw or {}
        self.errors = errors or []

    @property
    def is_valid(self) -> bool:
        """是否通过 Pydantic 校验。"""
        return self.frontmatter is not None and not self.errors

    @property
    def name(self) -> str | None:
        """快捷访问 name 字段。"""
        return self.frontmatter.name if self.frontmatter else self.raw.get("name")


class FrontmatterParser:
    """Frontmatter 解析器。

    用法::

        result = FrontmatterParser.parse_file("path/to/file.md")
        if result.is_valid:
            print(result.frontmatter.name)
            print(result.body)
    """

    @staticmethod
    def parse(content: str, *, validate: bool = True) -> FrontmatterParseResult:
        """解析带 frontmatter 的文本内容。

        Args:
            content: 原始文本。
            validate: 是否对 frontmatter 进行 Pydantic 校验。

        Returns:
            FrontmatterParseResult，包含解析后的 frontmatter 和 body。
        """
        if not content.startswith("---"):
            return FrontmatterParseResult(body=content.strip())

        parts = content.split("---", 2)
        if len(parts) < 3:
            return FrontmatterParseResult(body=content.strip())

        raw_yaml = parts[1]
        body = parts[2].strip()

        try:
            raw: dict[str, Any] = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError as e:
            return FrontmatterParseResult(
                body=body,
                raw={},
                errors=[f"YAML parse error: {e}"],
            )

        if not validate:
            return FrontmatterParseResult(body=body, raw=raw)

        try:
            fm = PrimitiveFrontmatter(**raw)
            return FrontmatterParseResult(frontmatter=fm, body=body, raw=raw)
        except ValidationError as e:
            return FrontmatterParseResult(
                body=body,
                raw=raw,
                errors=[str(err) for err in e.errors()],
            )

    @staticmethod
    def parse_file(file_path: str | Path, *, validate: bool = True) -> FrontmatterParseResult:
        """解析文件的 frontmatter。

        Args:
            file_path: 文件路径。
            validate: 是否进行 Pydantic 校验。

        Returns:
            FrontmatterParseResult。
        """
        path = Path(file_path)
        if not path.exists():
            return FrontmatterParseResult(errors=[f"File not found: {file_path}"])
        content = path.read_text(encoding="utf-8")
        return FrontmatterParser.parse(content, validate=validate)

    @staticmethod
    def build(
        body: str,
        **frontmatter_fields: Any,
    ) -> str:
        """构建带 frontmatter 的文件内容。

        Args:
            body: 正文内容。
            **frontmatter_fields: frontmatter 字段（接受别名如 applyTo）。

        Returns:
            完整的文件内容（含 frontmatter）。
        """
        # 展开别名
        cleaned: dict[str, Any] = {}
        for key, value in frontmatter_fields.items():
            if value is None:
                continue
            # 将 Python 风格名称转回 YAML 风格（如 apply_to → applyTo）
            yaml_key = _to_yaml_key(key)
            cleaned[yaml_key] = value

        if not cleaned:
            return body

        yaml_str = yaml.dump(cleaned, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{yaml_str}\n---\n\n{body}"


def _to_yaml_key(key: str) -> str:
    """将 Python 蛇形命名转为 YAML 驼峰命名。"""
    parts = key.split("_")
    if len(parts) <= 1:
        return key
    return parts[0] + "".join(p.capitalize() for p in parts[1:])
