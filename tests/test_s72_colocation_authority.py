from __future__ import annotations

import contextlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from tools import release_authority
from tools import s72_colocation_authority as authority


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / authority.COLOCATION_COMPOSE
BROKER_TEMPLATE = ROOT / "deploy/ai-platform/s72-broker-nginx.conf.template"


def _platform_environment() -> dict[str, str]:
    digest = "sha256:" + "1" * 64
    return {
        "AI_PLATFORM_MODEL_UPSTREAM": "http://host.docker.internal:3002",
        "AI_PLATFORM_FRONTEND_PORT": "18001",
        "OPENSANDBOX_API_KEY": "secret-value",
        "OPENSANDBOX_DOMAIN": "10.56.0.72:8443",
        "OPENSANDBOX_PROTOCOL": "https",
        "OPENSANDBOX_EXECUTOR_IMAGE": f"registry.example/executor@{digest}",
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST": digest,
        "OPENSANDBOX_ATTESTATION_PATH": "/v1/sandboxes/{sandbox_id}/attestation",
        "OPENSANDBOX_ATTESTATION_CONTRACT_VERSION": "ai-platform.opensandbox.topology-attestation.v1",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_URL": "https://10.56.0.72:8443/v1/capabilities/governed-egress",
        "OPENSANDBOX_EXTERNAL_EGRESS_CAPABILITY_TOKEN": "secret-value",
        "OPENSANDBOX_EXTERNAL_EGRESS_GATEWAY_POLICY_SUBJECT": "s72/policy/v1",
        "OPENSANDBOX_EXTERNAL_EGRESS_CALLBACK_BOUNDARY_SUBJECT": "callbacks/v1",
        "SANDBOX_CALLBACK_TOKEN": "secret-value",
        "SANDBOX_EGRESS_PROOF_SIGNING_KEY": "secret-value",
        "SANDBOX_RUNTIME_SUBJECT": "s72/runsc/v1",
    }


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def test_colocation_overlay_owns_ports_volumes_and_broker_boundary() -> None:
    overlay = OVERLAY.read_text(encoding="utf-8")
    broker = BROKER_TEMPLATE.read_text(encoding="utf-8")
    assert overlay.count("ports: !reset []") == 4
    assert '"127.0.0.1:18043:8080"' in overlay
    assert "network_mode: host" not in overlay
    assert "/var/run/docker.sock" not in overlay
    assert "ai_platform_postgres" not in overlay
    assert "ai_platform_redis" not in overlay
    assert "ai_platform_minio" not in overlay
    assert "AI_PLATFORM_S72_BRIDGE" not in overlay
    assert "18443" not in overlay
    assert "ssl" not in broker.lower()
    assert "location / {\n        return 404;" in broker
    assert "limit_except POST" in broker
    assert "s72_callback:" in overlay
    assert "s72_model_host:" in overlay
    assert "internal: true" in overlay
    assert "healthcheck:" in overlay


def test_release_authority_accepts_only_the_new_exact_colocation_selection() -> None:
    selection = release_authority.resolve_compose_files(ROOT, authority.COMPOSE_FILES)
    assert selection.relative_paths == authority.COMPOSE_FILES
    assert authority.COMPOSE_FILES in release_authority.PROVIDER_OVERLAY_COMPOSE_SELECTIONS
    assert (authority.BASE_COMPOSE, "deploy/ai-platform/docker-compose.opensandbox.yml") in (
        release_authority.PROVIDER_OVERLAY_COMPOSE_SELECTIONS
    )
    with pytest.raises(release_authority.ReleaseAuthorityError, match="retired"):
        release_authority.assert_cli_deploy_compose_selection_is_active(
            (authority.BASE_COMPOSE, "deploy/ai-platform/docker-compose.opensandbox.yml")
        )
    release_authority.assert_cli_deploy_compose_selection_is_active(authority.COMPOSE_FILES)


def test_opensandbox_server_contract_is_runsc_none_and_immutable(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    digest = "sha256:" + "2" * 64
    (root / "server.env").write_text(
        "\n".join(
            (
                f"OPENSANDBOX_SERVER_IMAGE=registry.example/opensandbox@{digest}",
                f"OPENSANDBOX_SERVER_IMAGE_DIGEST={digest}",
                "OPENSANDBOX_SERVER_UID=2001",
                "OPENSANDBOX_SERVER_GID=2001",
                "OPENSANDBOX_DOCKER_SOCKET_GID=999",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "server.toml").write_text(
        """
[server]
host = "0.0.0.0"
port = 8080
api_key = "abcdefghijklmnopqrstuvwxyz-1234567890"
[runtime]
type = "docker"
execd_image = "registry.example/execd@sha256:3333333333333333333333333333333333333333333333333333333333333333"
[storage]
allowed_host_paths = ["/data/opensandbox/workspaces"]
[store]
type = "sqlite"
path = "/var/lib/ai-platform-opensandbox/opensandbox.db"
[docker]
network_mode = "none"
drop_capabilities = ["AUDIT_WRITE", "MKNOD", "NET_ADMIN", "NET_RAW", "SYS_ADMIN", "SYS_MODULE", "SYS_PTRACE", "SYS_TIME", "SYS_TTY_CONFIG"]
no_new_privileges = true
pids_limit = 4096
[ingress]
mode = "direct"
[secure_runtime]
type = "gvisor"
docker_runtime = "runsc"
""".lstrip(),
        encoding="utf-8",
    )
    (root / "server.env").chmod(0o600)
    (root / "server.toml").chmod(0o440)
    projection = authority.validate_opensandbox_server_configuration(
        root,
        require_root_ownership=False,
    )
    assert projection["runtime"] == "runsc"
    assert projection["sandbox_network_mode"] == "none"
    config = (root / "server.toml").read_text(encoding="utf-8")
    (root / "server.toml").chmod(0o600)
    (root / "server.toml").write_text(config.replace('network_mode = "none"', 'network_mode = "bridge"'), encoding="utf-8")
    (root / "server.toml").chmod(0o440)
    with pytest.raises(authority.S72ColocationError, match="security contract"):
        authority.validate_opensandbox_server_configuration(root, require_root_ownership=False)


def test_smoke_accounts_file_rejects_non_list(tmp_path: Path) -> None:
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps({"account": "secret"}), encoding="utf-8")
    with pytest.raises(authority.S72ColocationError, match="invalid"):
        authority._load_smoke_accounts(path)


def test_smoke_inputs_require_two_tenants_and_a_real_docx(tmp_path: Path) -> None:
    accounts = tmp_path / "accounts.json"
    accounts.write_text(
        json.dumps(
            [
                "tenant-a/user-a=user-a:secret-a",
                "tenant-b/user-b=user-b:secret-b",
            ]
        ),
        encoding="utf-8",
    )
    document = tmp_path / "input.docx"
    with zipfile.ZipFile(document, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
    projection = authority.validate_smoke_inputs(accounts, document, "issue-843-review")
    assert projection["tenant_count"] == 2
    accounts.write_text(
        json.dumps(
            [
                "tenant-a/user-a=user-a:secret-a",
                "tenant-a/user-b=user-b:secret-b",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(authority.S72ColocationError, match="two tenants"):
        authority.validate_smoke_inputs(accounts, document, "issue-843-review")


def test_broker_runtime_rejects_default_control_plane_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    working_dir = f"/opt/ai-platform/releases/{commit}/deploy/ai-platform"
    record = {
        "Config": {
            "User": "101:101",
            "Labels": {
                "ai-platform.source-commit": commit,
                "ai-platform.release-owner": "repo-local-compose",
                "ai-platform.release-role": "s72-broker-entry",
                "ai-platform.security-domain": "control-plane-broker",
                "com.docker.compose.project.working_dir": working_dir,
            },
        },
        "HostConfig": {
            "NetworkMode": f"{release_authority.COMPOSE_PROJECT}_s72_callback",
            "Privileged": False,
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PortBindings": {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18043"}]},
            "Tmpfs": {
                "/etc/nginx/conf.d": "",
                "/var/cache/nginx": "",
                "/var/run": "",
            },
        },
        "NetworkSettings": {
            "Networks": {
                f"{release_authority.COMPOSE_PROJECT}_s72_callback": {},
                f"{release_authority.COMPOSE_PROJECT}_s72_model_host": {},
                f"{release_authority.COMPOSE_PROJECT}_default": {},
            }
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": f"{working_dir}/s72-broker-nginx.conf.template",
                "Destination": "/etc/nginx/templates-s72-colocation/default.conf.template",
                "RW": False,
            }
        ],
    }
    monkeypatch.setattr(
        authority,
        "collect_opensandbox_runtime_parity",
        lambda *_args: {"verified": True},
    )
    monkeypatch.setattr(
        authority,
        "_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, json.dumps([record]), ""),
    )
    with pytest.raises(authority.S72ColocationError, match="broker runtime boundary"):
        authority.collect_colocation_parity("docker", commit)


def test_existing_legacy_platform_selection_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "b" * 40
    working_dir = f"/opt/ai-platform/releases/{commit}/deploy/ai-platform"
    legacy_config = ",".join(
        (
            f"{working_dir}/docker-compose.yml",
            f"{working_dir}/docker-compose.opensandbox.yml",
        )
    )

    def fake_command(argv, **_kwargs):
        role = str(argv[-1]).removeprefix("ai-platform-")
        record = {
            "Config": {
                "Labels": {
                    "ai-platform.source-commit": commit,
                    "ai-platform.release-owner": "repo-local-compose",
                    "ai-platform.release-role": role,
                    "com.docker.compose.project": release_authority.COMPOSE_PROJECT,
                    "com.docker.compose.project.working_dir": working_dir,
                    "com.docker.compose.project.config_files": legacy_config,
                }
            }
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps([record]), "")

    monkeypatch.setattr(authority, "_command", fake_command)
    with pytest.raises(authority.S72ColocationError, match="not the s72 authority"):
        authority._current_platform_commit("docker")


def test_managed_environment_rejects_retired_bridge_projection(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    values = _platform_environment()
    values["AI_PLATFORM_S72_BRIDGE_SERVER_NAME"] = "legacy.invalid"
    _write_env(env_file, values)
    with pytest.raises(authority.S72ColocationError, match="retired cross-host"):
        authority.validate_platform_environment(env_file)


@pytest.mark.parametrize(
    "upstream",
    [
        "http://postgres:5432",
        "http://redis:6379",
        "http://minio:9000",
        "http://127.0.0.1:5432",
        "http://host.docker.internal:5432",
        "https://host.docker.internal:3002",
    ],
)
def test_managed_environment_rejects_noncanonical_model_upstream(
    tmp_path: Path,
    upstream: str,
) -> None:
    env_file = tmp_path / ".env"
    values = _platform_environment()
    values["AI_PLATFORM_MODEL_UPSTREAM"] = upstream
    _write_env(env_file, values)
    with pytest.raises(authority.S72ColocationError, match="model upstream"):
        authority.validate_platform_environment(env_file)


def test_deploy_holds_one_outer_lease_and_rolls_back_platform_then_gateway(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def fake_lease(_path: Path):
        events.append("lease-acquired")
        try:
            yield
        finally:
            events.append("lease-released")

    checkout = tmp_path / "releases" / ("a" * 40)
    env_file = tmp_path / ".env"
    env_file.write_text("SAFE=1\n", encoding="utf-8")
    monkeypatch.setattr(authority, "mutation_lease", fake_lease)
    monkeypatch.setattr(authority, "collect_read_only_preflight", lambda *args, **kwargs: {"verified": True})
    monkeypatch.setattr(authority.release_authority, "resolve_managed_env_file", lambda *args: env_file)
    monkeypatch.setattr(authority, "_current_platform_commit", lambda _docker: None)
    monkeypatch.setattr(authority.release_authority, "materialize_main_checkout", lambda *_args: checkout)
    monkeypatch.setattr(
        authority,
        "_install_opensandbox_runtime",
        lambda *_args, **_kwargs: events.append("runtime-installed") or {"snapshot": {"unit_bytes": None}},
    )
    monkeypatch.setattr(authority, "_gateway_install", lambda *_args: events.append("gateway-installed"))
    monkeypatch.setattr(
        authority.release_authority,
        "deploy_main_commit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("compose failed")),
    )
    monkeypatch.setattr(
        authority,
        "_restore_platform",
        lambda *_args: events.append("platform-rollback") or {"mode": "remove-new-runtime"},
    )
    monkeypatch.setattr(
        authority,
        "_command",
        lambda argv, **_kwargs: events.append("gateway-rollback") or None,
    )
    monkeypatch.setattr(
        authority,
        "_restore_opensandbox_runtime",
        lambda *_args, **_kwargs: events.append("runtime-rollback") or {"restored": True},
    )

    with pytest.raises(authority.S72ColocationError, match="rollback completed") as exc_info:
        authority.deploy_main_commit(
            tmp_path,
            "a" * 40,
            tmp_path / "releases",
            authority_evidence_id="issue-843-review",
            smoke_accounts_file=tmp_path / "accounts.json",
            smoke_sample_docx=tmp_path / "sample.docx",
            lease_path=tmp_path / "lease",
        )
    assert events == [
        "lease-acquired",
        "runtime-installed",
        "gateway-installed",
        "platform-rollback",
        "gateway-rollback",
        "runtime-rollback",
        "lease-released",
    ]
    assert exc_info.value.rollback["gateway"] == {"restored": True}


def test_gateway_install_failure_relies_on_its_snapshot_trap_without_second_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    @contextlib.contextmanager
    def fake_lease(_path: Path):
        yield

    checkout = tmp_path / "releases" / ("a" * 40)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(authority, "mutation_lease", fake_lease)
    monkeypatch.setattr(authority, "collect_read_only_preflight", lambda *args, **kwargs: {"verified": True})
    monkeypatch.setattr(authority.release_authority, "resolve_managed_env_file", lambda *args: env_file)
    monkeypatch.setattr(authority, "_current_platform_commit", lambda _docker: None)
    monkeypatch.setattr(authority.release_authority, "materialize_main_checkout", lambda *_args: checkout)
    monkeypatch.setattr(
        authority,
        "_install_opensandbox_runtime",
        lambda *_args, **_kwargs: events.append("runtime-installed") or {"snapshot": {"unit_bytes": None}},
    )
    monkeypatch.setattr(
        authority,
        "_gateway_install",
        lambda *_args: (_ for _ in ()).throw(authority.S72ColocationError("installer failed")),
    )
    monkeypatch.setattr(authority, "_restore_platform", lambda *_args: events.append("platform"))
    monkeypatch.setattr(authority, "_command", lambda *_args, **_kwargs: events.append("gateway"))
    monkeypatch.setattr(
        authority,
        "_restore_opensandbox_runtime",
        lambda *_args, **_kwargs: events.append("runtime-rollback") or {"restored": True},
    )

    with pytest.raises(authority.S72ColocationError, match="rollback completed"):
        authority.deploy_main_commit(
            tmp_path,
            "a" * 40,
            tmp_path / "releases",
            authority_evidence_id="issue-843-review",
            smoke_accounts_file=tmp_path / "accounts.json",
            smoke_sample_docx=tmp_path / "sample.docx",
            lease_path=tmp_path / "lease",
        )
    assert events == ["runtime-installed", "runtime-rollback"]
