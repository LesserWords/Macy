"""Headless Strands Agents MCP client for Macy's external editorial compliance loop (M5-08)."""

from __future__ import annotations

import argparse
import importlib
import sys
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser for the external editorial compliance agent runner."""
    parser = argparse.ArgumentParser(
        prog="compliance-agent",
        description=(
            "Run a Strands Agents MCP client against Macy as an editorial compliance pipeline."
        ),
    )
    parser.add_argument("--workspace-id", required=True, help="Target Macy workspace UUID")
    parser.add_argument("--topic", required=True, help="Article topic to research and draft")
    parser.add_argument(
        "--format",
        default="blog",
        choices=["blog-short", "blog", "blog-long"],
        help="Target Macy format pack slug (default: blog)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Connect to Macy MCP server in --mock mode",
    )
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        default=True,
        help="Use deterministic_only=True in macy_evaluate_draft (default: True)",
    )
    return parser


def build_compliance_prompt(
    *,
    workspace_id: str,
    topic: str,
    format_slug: str,
    deterministic_only: bool = True,
) -> str:
    """Construct the editorial compliance workflow prompt for the external Strands Agent."""
    return (
        f"You are an external editorial agent using Macy as a compliance and Gate 2 pipeline "
        f"for workspace {workspace_id}.\n"
        f"Topic: {topic}\n"
        f"Target format_slug: {format_slug}\n\n"
        "Workflow:\n"
        "1. Gather grounded sources and call `macy_ingest_research_documents`.\n"
        "2. Draft the article in Markdown with YAML frontmatter and call `macy_import_draft` "
        f"with `format_slug='{format_slug}'` and `deterministic_only={deterministic_only}`.\n"
        "3. If `verdict` is `REVISION`, inspect `findings`, revise the draft, and call "
        "`macy_import_draft` with the returned `brief_id` (up to revision 2) and "
        "`macy_evaluate_draft` until `verdict == 'PASS'`.\n"
        "4. Stop at Gate 2 (`status == 'pass'`) for human approval."
    )


def run_compliance_agent(args: argparse.Namespace) -> int:
    """Execute the Strands Agents MCPClient compliance loop against `content-engine mcp`."""
    try:
        from mcp.client.stdio import StdioServerParameters, stdio_client

        strands_mod = importlib.import_module("strands")
        strands_mcp_mod = importlib.import_module("strands.tools.mcp")
        agent_cls: Any = strands_mod.Agent
        mcp_client_cls: Any = strands_mcp_mod.MCPClient
    except ImportError as exc:
        print(
            f"Missing optional agent dependency ({exc}). Install with: uv sync --extra agent",
            file=sys.stderr,
        )
        return 1

    mcp_args = ["mcp", "--mock"] if args.mock else ["mcp"]
    mcp_client = mcp_client_cls(
        lambda: stdio_client(StdioServerParameters(command="content-engine", args=mcp_args))
    )
    prompt = build_compliance_prompt(
        workspace_id=args.workspace_id,
        topic=args.topic,
        format_slug=args.format,
        deterministic_only=bool(args.deterministic_only),
    )
    with mcp_client:
        tools: list[Any] = mcp_client.list_tools_sync()
        agent = agent_cls(tools=tools)
        result = agent(prompt)
        print(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for the external editorial compliance agent runner."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_compliance_agent(args)


if __name__ == "__main__":
    raise SystemExit(main())
