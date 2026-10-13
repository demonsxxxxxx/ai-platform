"""Receipt composition is importable without starting a queue worker."""

import subprocess
import sys


def test_receipt_entrypoint_import_and_explicit_bootstrap_do_not_start_queue_worker():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from app.bootstrap.files import configure_file_upload_services
from app.bootstrap.model_services import configure_model_services
from app.bootstrap.skills import configure_skill_markdown
from app.bootstrap.context import configure_context_services
from app.bootstrap.mcp import configure_mcp_runtime
from app.bootstrap.agent_profiles import configure_agent_profile_runtime
for configure in (
    configure_file_upload_services, configure_model_services, configure_skill_markdown,
    configure_context_services, configure_mcp_runtime, configure_agent_profile_runtime,
):
    configure()
from app.bootstrap.executor_terminal_reconciliation import reconcile_executor_terminal_result
assert callable(reconcile_executor_terminal_result)
assert 'app.worker' not in sys.modules
assert 'app.worker_main' not in sys.modules
"""],
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stderr
