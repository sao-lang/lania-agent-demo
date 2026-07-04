"""数据分析 Capability 实现。

查询数据库 → 运行 Python 分析 → 生成分析报告。
使用 database capability 获取数据 + shell_command 执行 Python 脚本。
"""

from __future__ import annotations

from typing import Any

from app.models.agent import AgentEvent


class DataAnalysisCapability:
    """数据分析能力。

    查询数据库、运行 Python 分析脚本、生成分析报告。
    """

    name = "data_analysis"

    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    async def execute(
        self,
        message: str,
        context: dict[str, Any],
    ) -> list[AgentEvent]:
        """执行数据分析。

        Args:
            message: 分析要求。
            context: 执行上下文（含 database, llm 等）。

        Returns:
            Agent 事件列表。
        """
        events: list[AgentEvent] = []
        database = context.get("database")
        llm = context.get("llm") or self._llm

        # 1. 列出数据库表
        events.append(AgentEvent.step_start(1, "浏览数据表", "了解数据源结构"))
        tables = self._list_tables(database)
        if not tables:
            events.append(AgentEvent.delta("未找到数据库表。"))
            events.append(AgentEvent.completed())
            return events

        table_names = [t["name"] for t in tables]
        events.append(AgentEvent.delta(
            f"发现 {len(tables)} 个表：{', '.join(table_names[:10])}\n"
        ))
        events.append(AgentEvent.step_end(1, "completed"))

        # 2. 描述表结构（取前 3 个表）
        events.append(AgentEvent.step_start(2, "分析表结构", "了解字段定义"))
        schemas = {}
        for t in tables[:3]:
            schema = self._describe_table(database, t["name"])
            if schema:
                schemas[t["name"]] = schema

        schema_text = "\n".join(
            f"表 {name}：{' '.join(f\"{c['name']} {c['type']}\" for c in cols[:8])}"
            for name, cols in schemas.items()
        )
        events.append(AgentEvent.delta(f"表结构：\n{schema_text}\n"))
        events.append(AgentEvent.step_end(2, "completed"))

        # 3. LLM 生成分析 SQL 和 Python 代码
        if not llm:
            events.append(AgentEvent.delta("未配置 LLM，无法进行分析。"))
            events.append(AgentEvent.completed())
            return events

        code = await self._generate_analysis_code(
            llm, message, schemas,
        )

        # 4. 执行 SQL 查询
        events.append(AgentEvent.step_start(3, "执行查询", "获取分析数据"))
        sql_blocks = self._extract_sql(code)
        query_results = []

        for sql in sql_blocks[:3]:
            result = self._query_database(database, sql)
            if result:
                query_results.append({"sql": sql, "rows": result[:20]})

        if query_results:
            events.append(AgentEvent.delta(
                f"执行了 {len(query_results)} 个查询"
            ))
        events.append(AgentEvent.step_end(3, "completed"))

        # 5. LLM 分析结果
        events.append(AgentEvent.step_start(4, "分析数据", "LLM 解读结果"))
        report = await self._generate_report(llm, message, query_results)
        events.append(AgentEvent.delta(report if report else "分析完成。"))
        events.append(AgentEvent.step_end(4, "completed"))

        events.append(AgentEvent.completed())
        return events

    def _list_tables(self, database: Any) -> list[dict]:
        """列出数据库表。"""
        if database is None:
            return []
        try:
            from app.capabilities.database import DatabaseListTablesRequest
            result = database.list_tables(DatabaseListTablesRequest(
                include_system_tables=False, max_entries=100,
            ))
            return [
                {"name": t.table_name, "type": t.table_type}
                for t in result.tables
            ]
        except Exception:
            return []

    def _describe_table(self, database: Any, name: str) -> list[dict]:
        """描述表结构。"""
        if database is None:
            return []
        try:
            from app.capabilities.database import DatabaseDescribeTableRequest
            result = database.describe_table(
                DatabaseDescribeTableRequest(table_name=name),
            )
            return [
                {"name": c.column_name, "type": c.data_type}
                for c in result.columns
            ]
        except Exception:
            return []

    def _query_database(self, database: Any, sql: str) -> list[dict] | None:
        """执行 SQL 查询。"""
        if database is None:
            return None
        try:
            from app.capabilities.database import DatabaseQueryRequest
            result = database.query(DatabaseQueryRequest(
                sql=sql, max_rows=100,
            ))
            return [dict(row) for row in result.rows]
        except Exception:
            return None

    async def _generate_analysis_code(
        self, llm: Any, message: str, schemas: dict[str, list[dict]],
    ) -> str:
        """使用 LLM 生成分析 SQL 和代码。"""
        schema_desc = "\n".join(
            f"表 {name}: {' '.join(f\"{c['name']}({c['type']})\" for c in cols[:8])}"
            for name, cols in schemas.items()
        )
        prompt = (
            f"你是一个数据分析专家。用户需求：{message}\n\n"
            f"数据库表结构：\n{schema_desc}\n\n"
            f"请生成：\n"
            f"1. SQL 查询语句（用 ```sql 标记）\n"
            f"2. 分析思路说明\n"
        )
        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            return response.choices[0].message.content if \
                hasattr(response, "choices") else str(response)
        except Exception:
            return ""

    def _extract_sql(self, code: str) -> list[str]:
        """从 LLM 输出中提取 SQL 语句。"""
        import re
        blocks = re.findall(r"```sql\n(.*?)```", code, re.DOTALL)
        return [b.strip() for b in blocks if b.strip()]

    async def _generate_report(
        self, llm: Any, message: str, query_results: list[dict],
    ) -> str:
        """使用 LLM 生成分析报告。"""
        data_desc = "\n".join(
            f"查询：{r['sql']}\n结果行数：{len(r['rows'])}\n"
            for r in query_results
        )
        prompt = (
            f"基于以下数据查询结果，生成分析报告。\n\n"
            f"用户需求：{message}\n\n"
            f"查询结果：\n{data_desc}\n\n"
            f"请输出 Markdown 格式的分析报告，包含：\n"
            f"1. 数据概况\n2. 关键发现\n3. 结论与建议\n"
        )
        try:
            response = llm.chat([{"role": "user", "content": prompt}])
            return response.choices[0].message.content if \
                hasattr(response, "choices") else str(response)
        except Exception:
            return "分析完成。"
