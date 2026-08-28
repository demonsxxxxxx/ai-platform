import json
import os
import uuid

import pytest

from app.runtime.sandbox.container_provider import OpenSandboxContainerProvider
from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.settings import get_settings


_LIVE_FLAG = "AI_PLATFORM_RUN_LIVE_OPENSANDBOX_CREDENTIAL_ISOLATION"


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get(_LIVE_FLAG) != "1" or os.name == "nt",
    reason="requires an explicit Linux OpenSandbox live-acceptance environment",
)
async def test_real_opensandbox_bash_cannot_read_raw_model_credentials(
    monkeypatch,
    tmp_path,
):
    """Exercise real Bash and report booleans only; never print credential values."""

    settings = get_settings()
    if settings.sandbox_security_profile != "internal-test":
        pytest.skip("requires the explicit OpenSandbox internal-test risk profile")

    suffix = uuid.uuid4().hex
    credential_canaries = [
        f"live-openai-canary-{suffix}",
        f"live-anthropic-canary-{suffix}",
        f"live-catalog-canary-{suffix}",
    ]
    acceptance_settings = settings.model_copy(
        update={
            "openai_api_key": credential_canaries[0],
            "anthropic_auth_token": credential_canaries[1],
            "model_catalog_json": json.dumps({"canary": credential_canaries[2]}),
        }
    )
    monkeypatch.setattr(
        "app.runtime.sandbox.container_provider.get_settings",
        lambda: acceptance_settings,
    )
    request = SandboxRuntimeRequest(
        tenant_id="default",
        workspace_id=f"live-{suffix}",
        user_id="live-opensandbox-acceptance",
        session_id=f"ses_{suffix}",
        run_id=f"run_{suffix}",
        attempt_id=f"attempt_{suffix}",
        agent_id="credential-isolation-acceptance",
        skill_ids=[],
        input_message="credential isolation acceptance",
        sandbox_mode="ephemeral",
        browser_enabled=False,
        model=settings.claude_agent_model,
        permissions=["sandbox.execute"],
        callback_url=(
            f"{settings.sandbox_callback_base_url.rstrip('/')}"
            "/api/ai/runtime/callbacks/executor"
        ),
        callback_token_id=f"cbt_{suffix}",
    )
    workspace = SandboxWorkspaceManager(root=tmp_path).prepare(request)
    provider = OpenSandboxContainerProvider()
    lease = None
    try:
        lease = await provider.create_or_reuse(request, workspace)
        assert lease.provider == "opensandbox"
        assert lease.labels["ai-platform.security_profile"] == "internal-test"
        assert lease.labels["ai-platform.internal_test.network_mode"] == "bridge"

        sandbox = provider._sandboxes[lease.container_id]
        canary_json = json.dumps(credential_canaries)
        execution = await sandbox.commands.run(
            "python - <<'PY'\n"
            "import glob, json, os\n"
            "from urllib.parse import urlsplit\n"
            "keys = {b'OPENAI_API_KEY', b'ANTHROPIC_AUTH_TOKEN', b'ANTHROPIC_API_KEY'}\n"
            "sentinel = b'opensandbox-sdk-sentinel'\n"
            f"canaries = {{item.encode() for item in json.loads({canary_json!r})}}\n"
            "def raw(entries):\n"
            "    for entry in entries:\n"
            "        key, sep, value = entry.partition(b'=')\n"
            "        if sep and ((key in keys and value and value != sentinel) or any(canary in value for canary in canaries)):\n"
            "            return True\n"
            "    return False\n"
            "env_raw = raw([f'{key}={value}'.encode() for key, value in os.environ.items()])\n"
            "def base_has_secret(name):\n"
            "    parsed = urlsplit(os.environ.get(name, ''))\n"
            "    return bool(parsed.username or parsed.password or parsed.query or parsed.fragment)\n"
            "proc_raw = False\n"
            "readable_proc_environ = 0\n"
            "for path in glob.glob('/proc/[0-9]*/environ'):\n"
            "    try:\n"
            "        entries = open(path, 'rb').read().split(b'\\0')\n"
            "    except (OSError, PermissionError):\n"
            "        continue\n"
            "    readable_proc_environ += 1\n"
            "    proc_raw = proc_raw or raw(entries)\n"
            "print(json.dumps({'env_raw_model_credential': env_raw, 'proc_raw_model_credential': proc_raw, 'model_base_embeds_credential': base_has_secret('OPENAI_BASE_URL') or base_has_secret('ANTHROPIC_BASE_URL'), 'model_catalog_present': bool(os.environ.get('MODEL_CATALOG_JSON')), 'readable_proc_environ': readable_proc_environ}, sort_keys=True))\n"
            "PY"
        )
        assert execution.exit_code == 0
        observation = json.loads(execution.text.strip().splitlines()[-1])
        assert observation["env_raw_model_credential"] is False
        assert observation["proc_raw_model_credential"] is False
        assert observation["model_base_embeds_credential"] is False
        assert observation["model_catalog_present"] is False
        assert observation["readable_proc_environ"] >= 1
    finally:
        if lease is not None:
            await provider.stop(lease, reason="credential_isolation_acceptance")
