"""Synthetic remote MCP sessions for Runner unit tests; no network or credentials."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from mcp_types import CallToolResult, TextContent, Tool

from app.execution.infrastructure.claude_mcp import ClaudeMcpRegistration


def install_mcp_sessions(monkeypatch):
    def prepare(subjects, configs, callback_wrapper=None, *, failure_observer=None, credential_resolver=None):
        @asynccontextmanager
        async def session_factory(config, *, credential_resolver=None):
            connection = {
                key: value for key, value in config.items()
                if key not in {"max_response_bytes", "max_response_total_bytes"}
            }
            names = [
                subject["mcp_tool"] for subject in subjects.values()
                if subject.get("mcp_server") in configs
                and configs[subject["mcp_server"]] == connection
            ]

            async def call_tool(name, arguments):
                return CallToolResult(content=[TextContent(type="text", text="synthetic result")])

            yield SimpleNamespace(names=names, call_tool=call_tool)

        async def list_tools(session):
            return [Tool(name=name, input_schema={"type": "object"}) for name in session.names]

        return ClaudeMcpRegistration(
            subjects, configs, session_factory=session_factory, list_tools=list_tools,
            callback_wrapper=callback_wrapper, failure_observer=failure_observer,
            credential_resolver=credential_resolver,
        )

    monkeypatch.setattr("app.executors.claude_agent_sdk_runner.prepare_claude_mcp", prepare)
