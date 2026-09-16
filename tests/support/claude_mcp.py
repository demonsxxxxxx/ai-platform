"""Synthetic remote MCP sessions for Runner unit tests; no network or credentials."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent, Tool

from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration


def install_mcp_sessions(monkeypatch):
    def prepare(subjects, configs):
        @asynccontextmanager
        async def session_factory(config):
            names = [
                subject["mcp_tool"] for subject in subjects.values()
                if subject.get("mcp_server") in configs
                and configs[subject["mcp_server"]] == config
            ]

            async def call_tool(name, arguments):
                return CallToolResult(content=[TextContent(type="text", text="synthetic result")])

            yield SimpleNamespace(names=names, call_tool=call_tool)

        async def list_tools(session):
            return [Tool(name=name, inputSchema={"type": "object"}) for name in session.names]

        return ClaudeMcpRegistration(
            subjects, configs, session_factory=session_factory, list_tools=list_tools,
        )

    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.prepare_claude_mcp", prepare)
