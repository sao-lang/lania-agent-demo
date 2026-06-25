"""报告产物格式化模块。

负责把结构化报告内容渲染成 markdown 和 JSON 两种产物，供任务结果展示、下载和存档复用。
"""

from __future__ import annotations

from app.models.artifact import ReportArtifactContent


class ArtifactFormatter:
    """把结构化内容渲染成 markdown/json 产物。"""

    @staticmethod
    def render_markdown(content: ReportArtifactContent) -> str:
        """渲染 Markdown 报告。"""

        finding_lines = '\n'.join(
            f"- {item.title}：{item.summary}（证据：{', '.join(item.citation_ids) or '无'}）"
            for item in content.key_findings
        ) or '- 暂无关键发现'
        risk_lines = '\n'.join(
            f"- [{item.severity.upper()}] {item.title}：{item.description}"
            for item in content.risks
        ) or '- 暂无显著风险'
        evidence_lines = '\n'.join(
            f"- {item.citation_id} | {item.source} | {item.text[:120]}"
            for item in content.evidence
        ) or '- 暂无证据'
        question_lines = '\n'.join(f'- {item}' for item in content.open_questions) or '- 无'
        return (
            f"# {content.title or '文档分析报告'}\n\n"
            '## 摘要\n'
            f'{content.summary}\n\n'
            '## 关键发现\n'
            f'{finding_lines}\n\n'
            '## 风险清单\n'
            f'{risk_lines}\n\n'
            '## 证据包\n'
            f'{evidence_lines}\n\n'
            '## 待确认问题\n'
            f'{question_lines}\n\n'
            '## 置信度\n'
            f'- {content.confidence:.2f}\n'
        )

    @staticmethod
    def render_json(content: ReportArtifactContent) -> dict:
        """生成适合 API 返回和落盘的 JSON 结构。"""

        return content.model_dump(mode='json', exclude={'report_markdown', 'report_json'})
