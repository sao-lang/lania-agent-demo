#!/usr/bin/env python3
"""定制化原语文件模板生成工具。

用法::

    # 生成 Agent 定义
    python scripts/generate_primitive.py agent my-reviewer \\
        --display-name "Code Reviewer" \\
        --model gpt-4o --temperature 0.3 \\
        --allowed-tools read_file search_repository \\
        --skills ai-coding-rules

    # 生成文件指令
    python scripts/generate_primitive.py instructions python-rules \\
        --apply-to "**/*.py"

    # 生成 Prompt 模板
    python scripts/generate_primitive.py prompt code-review \\
        --variables files

    # 生成 Hook
    python scripts/generate_primitive.py hook dangerous-tool-guard \\
        --events before_tool --condition "tool_names: [shell_command]"

    # 生成 MCP Server 配置
    python scripts/generate_primitive.py mcp my-server \\
        --server-type url --url http://localhost:8080/sse
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_agent(args: argparse.Namespace) -> str:
    """生成 .agent.md 文件内容。"""
    lines = ["---"]
    lines.append(f"name: {args.name}")
    if args.display_name:
        lines.append(f"display_name: {args.display_name}")
    if args.description:
        lines.append(f"description: {args.description}")
    if args.model:
        lines.append(f"model: {args.model}")
    if args.temperature is not None:
        lines.append(f"temperature: {args.temperature}")
    if args.max_turns:
        lines.append(f"max_turns: {args.max_turns}")
    if args.allowed_tools:
        tools = ", ".join(args.allowed_tools)
        lines.append(f"allowed_tools:\n  - " + "\n  - ".join(args.allowed_tools))
    if args.skills:
        lines.append(f"skills:\n  - " + "\n  - ".join(args.skills))
    lines.append("---")
    lines.append("")
    lines.append(f"# {args.display_name or args.name} Instructions")
    lines.append("")
    lines.append("在这里编写 Agent 指令...")
    return "\n".join(lines)


def build_instructions(args: argparse.Namespace) -> str:
    """生成 .instructions.md 文件内容。"""
    lines = ["---"]
    lines.append(f"applyTo: \"{args.apply_to}\"")
    lines.append(f"name: {args.name}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {args.name}")
    lines.append("")
    lines.append("在这里编写指令内容...")
    return "\n".join(lines)


def build_prompt(args: argparse.Namespace) -> str:
    """生成 .prompt.md 文件内容。"""
    lines = ["---"]
    lines.append(f"name: {args.name}")
    if args.description:
        lines.append(f"description: {args.description}")
    if args.variables:
        lines.append("variables:")
        for v in args.variables:
            lines.append(f"  - {v}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {args.name}")
    lines.append("")
    lines.append("在这里编写提示词模板，使用 {variable} 作为占位符。")
    return "\n".join(lines)


def build_hook(args: argparse.Namespace) -> str:
    """生成 .json Hook 文件内容。"""
    data = {
        "name": args.name,
        "description": args.description or "",
        "events": args.events or [],
        "conditions": {},
        "actions": [
            {
                "type": "log",
                "params": {
                    "level": "info",
                    "message": f"Hook '{args.name}' triggered"
                }
            }
        ],
    }
    if args.condition:
        for cond in args.condition:
            if ":" in cond:
                key, value = cond.split(":", 1)
                data["conditions"][key.strip()] = json.loads(value.strip())
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_mcp(args: argparse.Namespace) -> str:
    """生成 MCP Server JSON 片段。"""
    entry = {args.name: {}}
    if args.server_type == "url":
        entry[args.name] = {
            "type": "url",
            "url": args.url or "http://localhost:8080/sse",
            "description": args.description or "",
        }
    else:
        entry[args.name] = {
            "type": "stdio",
            "command": args.command or "python",
            "args": args.args or [],
            "description": args.description or "",
        }
    return json.dumps({"mcpServers": entry}, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成定制化原语文件模板")
    sub = parser.add_subparsers(dest="type", required=True, help="原语类型")

    # Agent
    p_agent = sub.add_parser("agent", help="生成 .agent.md")
    p_agent.add_argument("name", help="Agent 名称")
    p_agent.add_argument("--display-name", default="", help="显示名称")
    p_agent.add_argument("--description", default="", help="描述")
    p_agent.add_argument("--model", default="", help="LLM 模型")
    p_agent.add_argument("--temperature", type=float, default=None, help="温度参数")
    p_agent.add_argument("--max-turns", type=int, default=0, help="最大轮次")
    p_agent.add_argument("--allowed-tools", nargs="*", default=[], help="工具白名单")
    p_agent.add_argument("--skills", nargs="*", default=[], help="绑定的 Skill")

    # Instructions (File)
    p_inst = sub.add_parser("instructions", help="生成 .instructions.md")
    p_inst.add_argument("name", help="指令名称")
    p_inst.add_argument("--apply-to", default="**/*", help="文件匹配 glob")
    p_inst.add_argument("--description", default="", help="描述")

    # Prompt
    p_prompt = sub.add_parser("prompt", help="生成 .prompt.md")
    p_prompt.add_argument("name", help="Prompt 名称")
    p_prompt.add_argument("--description", default="", help="描述")
    p_prompt.add_argument("--variables", nargs="*", default=[], help="模板变量列表")

    # Hook
    p_hook = sub.add_parser("hook", help="生成 .json Hook")
    p_hook.add_argument("name", help="Hook 名称")
    p_hook.add_argument("--description", default="", help="描述")
    p_hook.add_argument("--events", nargs="*", default=["before_tool"], help="监听事件")
    p_hook.add_argument("--condition", action="append", default=[], help="条件 (key: value)")

    # MCP
    p_mcp = sub.add_parser("mcp", help="生成 MCP Server 配置")
    p_mcp.add_argument("name", help="Server 名称")
    p_mcp.add_argument("--server-type", choices=["url", "stdio"], default="url")
    p_mcp.add_argument("--url", default="", help="URL 地址")
    p_mcp.add_argument("--command", default="", help="启动命令")
    p_mcp.add_argument("--args", nargs="*", default=[], help="启动参数")
    p_mcp.add_argument("--description", default="", help="描述")

    args = parser.parse_args()

    builders = {
        "agent": build_agent,
        "instructions": build_instructions,
        "prompt": build_prompt,
        "hook": build_hook,
        "mcp": build_mcp,
    }

    builder = builders[args.type]
    content = builder(args)

    # 确定输出路径
    output_dir = {
        "agent": ".lania/agents",
        "instructions": ".lania/instructions",
        "prompt": ".lania/prompts",
        "hook": ".lania/hooks",
        "mcp": ".lania",
    }[args.type]

    ext_map = {
        "agent": ".agent.md",
        "instructions": ".instructions.md",
        "prompt": ".prompt.md",
        "hook": ".json",
        "mcp": ".json",
    }
    ext = ext_map[args.type]
    if args.type == "mcp":
        output_path = Path(output_dir) / "mcp-servers.json"
    else:
        output_path = Path(output_dir) / f"{args.name}{ext}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    print(f"Generated: {output_path.resolve()}")


if __name__ == "__main__":
    main()
