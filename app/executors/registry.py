from app.executors.base import ExecutorAdapter
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
from app.executors.ragflow import RagflowAdapter


class AdapterRegistry:
    def __init__(self, adapters: dict[str, ExecutorAdapter] | None = None) -> None:
        self._adapters = adapters if adapters is not None else self._default_adapters()

    @staticmethod
    def _default_adapters() -> dict[str, ExecutorAdapter]:
        return {
            "claude-agent-worker": ClaudeAgentWorkerAdapter(),
            "ragflow": RagflowAdapter(),
        }

    def get(self, executor_type: str) -> ExecutorAdapter:
        adapter = self._adapters.get(executor_type)
        if adapter is None:
            raise KeyError(f"Unknown executor_type: {executor_type}")
        return adapter
