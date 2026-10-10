"""Fault injection for the package entry; Docker/runtime evidence is host-only."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_deploy", ROOT / "deploy/ai-platform/deploy.py")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


def test_internal_test_bridge_preflight_binds_proxy_and_lifecycle(monkeypatch):
    api = {
        "DEPLOYMENT_ENVIRONMENT": "test",
        "SANDBOX_CONTAINER_PROVIDER": "opensandbox",
        "SANDBOX_SECURITY_PROFILE": "internal-test",
        "OPENSANDBOX_EXPECTED_NETWORK_MODE": "bridge",
        "SANDBOX_EGRESS_POLICY_ENABLED": "false",
        "SANDBOX_CALLBACK_TOKEN": "synthetic-callback-key-with-enough-entropy-2026",
        "SANDBOX_CALLBACK_BASE_URL": "http://172.17.0.1:8020",
        "OPENSANDBOX_USE_SERVER_PROXY": "true",
        "OPENSANDBOX_EGRESS_PROXY_URL": "http://172.17.0.1:18043",
        "OPENSANDBOX_BASE_URL": "http://172.18.0.1:8080",
        "OPENSANDBOX_DOMAIN": "",
        "OPENSANDBOX_PROTOCOL": "",
    }
    config = {"services": {
        "api": {"environment": api, "ports": [{"host_ip": "", "published": "8020", "target": 8020, "protocol": "tcp"}]},
        "worker": {"environment": dict(api)},
        "opensandbox-egress-proxy": {"ports": [{
            "host_ip": "172.17.0.1", "published": "18043", "target": 8080, "protocol": "tcp",
        }]},
    }}
    bridge = [{"Driver": "bridge", "Internal": False, "IPAM": {"Config": [{"Gateway": "172.17.0.1"}]}}]
    calls = []

    def inspect_bridge(command, stage, timeout=90):
        calls.append((command, stage))
        return json.dumps(bridge)

    monkeypatch.setattr(entry, "run", inspect_bridge)
    entry.validate_internal_test_bridge(config, ["docker"])
    assert calls == [(["docker", "network", "inspect", "bridge"], "Docker bridge inspection")]
    api["SANDBOX_CALLBACK_TOKEN"] = "short"
    with pytest.raises(entry.DeploymentError, match="callback credential must match"):
        entry.validate_internal_test_bridge(config, ["docker"])
    api["SANDBOX_CALLBACK_TOKEN"] = config["services"]["worker"]["environment"]["SANDBOX_CALLBACK_TOKEN"]
    api["SANDBOX_CALLBACK_BASE_URL"] = "http://api.sandbox.internal:8020"
    with pytest.raises(entry.DeploymentError, match="callback URLs must match"):
        entry.validate_internal_test_bridge(config, ["docker"])
    api["SANDBOX_CALLBACK_BASE_URL"] = "http://172.17.0.1:8020"
    config["services"]["api"]["ports"][0]["host_ip"] = "127.0.0.1"
    with pytest.raises(entry.DeploymentError, match="callback URLs must match"):
        entry.validate_internal_test_bridge(config, ["docker"])
    config["services"]["api"]["ports"][0]["host_ip"] = ""
    for forbidden_port in ("9527", "18043"):
        config["services"]["api"]["ports"][0]["published"] = forbidden_port
        api["SANDBOX_CALLBACK_BASE_URL"] = f"http://172.17.0.1:{forbidden_port}"
        config["services"]["worker"]["environment"] = dict(api)
        with pytest.raises(entry.DeploymentError, match="callback URLs must match"):
            entry.validate_internal_test_bridge(config, ["docker"])
    config["services"]["api"]["ports"][0]["published"] = "8020"
    api["SANDBOX_CALLBACK_BASE_URL"] = "http://172.17.0.1:8020"
    config["services"]["worker"]["environment"] = dict(api)
    api["OPENSANDBOX_BASE_URL"] = ""
    api["OPENSANDBOX_DOMAIN"] = "172.18.0.1:8080"
    api["OPENSANDBOX_PROTOCOL"] = "http"
    config["services"]["worker"]["environment"] = dict(api)
    entry.validate_internal_test_bridge(config, ["docker"])

    for field, value in (
        ("host_ip", "0.0.0.0"),
        ("host_ip", "172.17.0.2"),
        ("published", "18044"),
    ):
        port = config["services"]["opensandbox-egress-proxy"]["ports"][0]
        before = port[field]
        port[field] = value
        with pytest.raises(entry.DeploymentError, match="proxy must bind"):
            entry.validate_internal_test_bridge(config, ["docker"])
        port[field] = before
    api["OPENSANDBOX_EGRESS_PROXY_URL"] = "http://172.17.0.2:18043"
    with pytest.raises(entry.DeploymentError, match="API and Worker OpenSandbox endpoints must match"):
        entry.validate_internal_test_bridge(config, ["docker"])
    config["services"]["worker"]["environment"] = dict(api)
    with pytest.raises(entry.DeploymentError, match="proxy must bind"):
        entry.validate_internal_test_bridge(config, ["docker"])
    api["OPENSANDBOX_EGRESS_PROXY_URL"] = "http://172.17.0.1:18043"
    config["services"]["worker"]["environment"] = dict(api)
    bridge[0]["IPAM"]["Config"][0]["Gateway"] = "172.17.0.2"
    with pytest.raises(entry.DeploymentError, match="proxy must bind"):
        entry.validate_internal_test_bridge(config, ["docker"])
    bridge[0]["IPAM"]["Config"][0]["Gateway"] = "172.17.0.1"
    api["OPENSANDBOX_DOMAIN"] = ""
    config["services"]["worker"]["environment"] = dict(api)
    with pytest.raises(entry.DeploymentError, match="lifecycle endpoint is required"):
        entry.validate_internal_test_bridge(config, ["docker"])
    api["OPENSANDBOX_BASE_URL"] = "http://user:pass@172.18.0.1:8080"
    config["services"]["worker"]["environment"] = dict(api)
    with pytest.raises(entry.DeploymentError, match="private IPv4 URL"):
        entry.validate_internal_test_bridge(config, ["docker"])
    for host in ("127.0.0.1", "0.0.0.0", "169.254.169.254", "8.8.8.8", "host.docker.internal"):
        api["OPENSANDBOX_BASE_URL"] = f"http://{host}:8080"
        config["services"]["worker"]["environment"] = dict(api)
        with pytest.raises(entry.DeploymentError, match="private IPv4 URL"):
            entry.validate_internal_test_bridge(config, ["docker"])
    api["OPENSANDBOX_BASE_URL"] = ""
    api["OPENSANDBOX_DOMAIN"] = "8.8.8.8:8080"
    config["services"]["worker"]["environment"] = dict(api)
    with pytest.raises(entry.DeploymentError, match="private IPv4 URL"):
        entry.validate_internal_test_bridge(config, ["docker"])


@pytest.mark.skipif(sys.platform != "linux", reason="real Linux procfs descriptor paths required")
def test_environment_snapshot_survives_original_path_replacement(tmp_path):
    original = tmp_path / ".env"
    original.write_bytes(b"SYNTHETIC=original\n")
    original.chmod(0o600)
    with entry.protected_environment(original) as snapshot:
        assert snapshot.stat().st_mode & 0o777 == 0o600
        assert str(snapshot).startswith(f"/proc/{os.getpid()}/fd/")
        original.unlink()
        original.write_bytes(b"SYNTHETIC=replaced\n")
        assert snapshot.read_bytes() == b"SYNTHETIC=original\n"
    assert not snapshot.exists()


@pytest.mark.skipif(os.name != "posix", reason="real POSIX file ownership and O_NOFOLLOW required")
def test_protected_file_survives_original_path_replacement(tmp_path):
    original = tmp_path / ".env"
    original.write_bytes(b"SYNTHETIC=original\n")
    original.chmod(0o600)
    with entry.protected_file(original) as source:
        assert os.fstat(source.fileno()).st_mode & 0o777 == 0o600
        original.unlink()
        original.write_bytes(b"SYNTHETIC=replaced\n")
        assert source.read() == b"SYNTHETIC=original\n"
    assert source.closed
    assert original.read_bytes() == b"SYNTHETIC=replaced\n"


@pytest.mark.skipif(os.name != "posix", reason="real POSIX file ownership and O_NOFOLLOW required")
@pytest.mark.parametrize("reader_name", ["protected_file", "protected_environment"])
def test_protected_reader_rejects_unsafe_mode_and_symlink(tmp_path, reader_name):
    reader = getattr(entry, reader_name)
    original = tmp_path / ".env"
    original.write_bytes(b"SYNTHETIC=original\n")
    original.chmod(0o644)
    with pytest.raises(entry.DeploymentError):
        with reader(original):
            pytest.fail("unsafe config was accepted")
    original.chmod(0o600)
    link = tmp_path / "linked.env"
    link.symlink_to(original)
    with pytest.raises(OSError):
        with reader(link):
            pytest.fail("symlink was accepted")


@pytest.mark.skipif(os.name != "posix", reason="real POSIX file ownership required")
@pytest.mark.parametrize("reader_name", ["protected_file", "protected_environment"])
def test_protected_reader_rejects_foreign_owner_and_closes_the_descriptor(tmp_path, monkeypatch, reader_name):
    original = tmp_path / ".env"
    original.write_bytes(b"SYNTHETIC=original\n")
    original.chmod(0o600)
    inode = original.stat().st_ino
    fstat = os.fstat
    descriptors = []

    def foreign_owner(descriptor):
        metadata = fstat(descriptor)
        if metadata.st_ino != inode:
            return metadata
        descriptors.append(descriptor)
        values = list(metadata)
        values[4] = os.geteuid() + 1
        return os.stat_result(values)

    monkeypatch.setattr(entry.os, "fstat", foreign_owner)
    with pytest.raises(entry.DeploymentError, match="owner-held"):
        with getattr(entry, reader_name)(original):
            pytest.fail("foreign owner was accepted")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        fstat(descriptors[0])


def test_quiescence_sql_only_excludes_expired_terminal_unclaimed_quarantine():
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            create table runs (id text, tenant_id text, status text);
            create table run_attempts (status text);
            create table sandbox_leases (status text, expires_at text, executor_status text,
              executor_reconciliation_status text, executor_reconciliation_claim_token text,
              run_id text, tenant_id text, runtime_container_id text, runtime_container_name text);
            insert into runs values ('r', 't', 'failed');
            insert into sandbox_leases values ('quarantined','2000-01-01','failed','failed',null,'r','t','synthetic-id',null);
        """)
        assert db.execute(entry.QUIESCENCE_SQL).fetchone() == (0, 0, 0)
        for column, value in (("status", "active"), ("expires_at", None),
                              ("expires_at", "2999-01-01"), ("executor_status", "running"),
                              ("executor_reconciliation_status", "claimed"),
                              ("executor_reconciliation_claim_token", "synthetic"),
                              ("tenant_id", "other"), ("runtime_container_id", None),
                              ("runtime_container_id", "   ")):
            db.execute("savepoint counterexample")
            db.execute(f"update sandbox_leases set {column}=?", (value,))
            assert db.execute(entry.QUIESCENCE_SQL).fetchone()[2] == 1
            db.execute("rollback to counterexample")


def test_quarantine_with_existing_runtime_blocks_even_without_owner_label(monkeypatch):
    def run(command, stage, timeout=90):
        return {"activity check": "0|0|0", "quarantined runtime check": "synthetic-id|synthetic-name",
                "sandbox inventory": "synthetic-id|synthetic-name"}.get(stage, "")
    monkeypatch.setattr(entry, "run", run)
    with pytest.raises(entry.DeploymentError, match="quarantined sandbox still exists"):
        entry.quiescent(["docker"])


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(entry, "COMMIT", "a" * 40)
    monkeypatch.setattr(entry, "BACKEND", "ghcr.io/example/backend@sha256:" + "b" * 64)
    monkeypatch.setattr(entry, "FRONTEND", "ghcr.io/example/frontend@sha256:" + "c" * 64)
    # Permission metadata is POSIX-only; do not fake subprocess or runner verdicts.
    env = tmp_path / ".env"
    env.write_text("SYNTHETIC=true\n")
    state = {"calls": [], "activity_checks": 0, "race": False, "fail": None, "ca_host": ""}
    workspace_root = str((tmp_path / "workspaces").resolve())
    migration_source = str((tmp_path / "legacy-workspaces").resolve())
    config = {"services": {
        service: {"image": entry.FRONTEND if service == "frontend" else entry.BACKEND}
        for service in (
            *entry.DATA,
            *entry.APPS,
            "migrate",
            "workspace-migrate",
            "workspace-init",
        )
    }}
    config["volumes"] = {}
    for service in entry.DATA:
        target = "/var/lib/postgresql/data" if service == "postgres" else "/data"
        source = f"ai_platform_{service}"
        config["volumes"][source] = {"name": f"{entry.PROJECT}_{source}"}
        config["services"][service]["volumes"] = [{"type": "volume", "source": source, "target": target}]
    def data_record(service, running=False):
        wanted = config["services"][service]["volumes"][0]
        return {"Image": config["services"][service]["image"], "State": {"Running": running},
                "Mounts": [{"Type": "volume", "Name": config["volumes"][wanted["source"]]["name"],
                            "Destination": wanted["target"], "RW": True}]}
    state["data_record"] = data_record
    for service in ("api", "worker"):
        config["services"][service]["environment"] = {
            "SANDBOX_WORKSPACE_ROOT": workspace_root,
            "TRUSTED_PRINCIPAL_SECRET": "synthetic-gateway-secret-" + "a" * 32,
            "AI_SESSION_SECRET": "synthetic-session-secret-" + "b" * 32,
            "CORS_ALLOW_ORIGINS": "https://platform.example.test",
            "AI_SESSION_COOKIE_SECURE": "true",
            "AUTH_CONTEXT_COOKIE_SECURE": "true",
        }
        config["services"][service]["volumes"] = [
            {
                "type": "bind",
                "source": workspace_root,
                "target": workspace_root,
                "read_only": False,
            }
        ]
    config["services"]["workspace-init"]["volumes"] = [
        {
            "type": "bind",
            "source": workspace_root,
            "target": "/runtime-workspaces",
            "read_only": False,
        }
    ]
    config["services"]["workspace-migrate"]["volumes"] = [
        {
            "type": "bind",
            "source": migration_source,
            "target": "/source-workspaces",
            "read_only": True,
        },
        {
            "type": "bind",
            "source": workspace_root,
            "target": "/target-workspaces",
            "read_only": False,
        },
    ]
    config["services"]["opensandbox-egress-proxy"] = {"image": entry.FRONTEND}

    def run(command, stage, timeout=90):
        state["calls"].append((stage, command))
        if state["fail"] == stage:
            raise entry.DeploymentError(stage + ": injected failure")
        if stage == "configuration identity":
            return json.dumps(config)
        if stage == "configuration inputs":
            return "PROFILE_DRIVE_TRANSFER_CA_CERT_HOST_FILE=" + state["ca_host"]
        if stage == "Docker data-root inspection":
            return str((tmp_path / "docker-data").resolve())
        if stage == "workspace volume inspection":
            return json.dumps([state["workspace_volume"]])
        if stage == "workspace migration marker inspection":
            root = Path(config["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"])
            return "incomplete" if (root / ".ai-platform-workspace-migration-v1.incomplete").exists() else "clear"
        if stage == "local image verification":
            return json.dumps([{"Id": command[-1], "RepoDigests": [command[-1]]}])
        return ""

    def quiescent(docker):
        state["activity_checks"] += 1
        state["calls"].append(("quiescent", []))
        if state["race"] and state["activity_checks"] == 2:
            raise entry.DeploymentError("activity appeared after first check")

    monkeypatch.setattr(entry, "run", run)
    def existing_snapshot(docker, **kwargs):
        records = {service: {} for service in (*entry.DATA, *entry.APPS)}
        for service in ("api", "worker"):
            root = config["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"]
            records[service] = {
                "Config": {"Env": [f"SANDBOX_WORKSPACE_ROOT={root}"]},
                "Mounts": [{"Type": "bind", "Source": root, "Destination": root}],
            }
        return records
    monkeypatch.setattr(entry, "snapshot", existing_snapshot)
    monkeypatch.setattr(entry, "quiescent", quiescent)
    monkeypatch.setattr(entry, "verify_runtime", lambda *args: state["calls"].append(("runtime verified", [])))
    state["config"] = config
    state["env"] = env
    state["state_path"] = tmp_path / ".ai-platform-install-state.json"
    state["deploy"] = lambda offline=False, check_only=False, **kwargs: entry.deploy(tmp_path, env, ["docker"], offline, check_only, **kwargs)
    return state


def test_profile_drive_ca_bind_is_optional_and_preflight_requires_readable_file(harness, tmp_path, monkeypatch):
    import certifi

    monkeypatch.setattr(entry, "validate_workspace_storage", lambda *args, **kwargs: False)
    harness["deploy"](check_only=True)
    assert not any("compose.profile-drive-ca.yaml" in str(command) for _, command in harness["calls"])

    ca_target = "/etc/ssl/certs/profile-drive-ca.pem"
    ca_source = tmp_path / "public-ca.pem"
    api = harness["config"]["services"]["api"]
    ca_mount = {"type": "bind", "source": str(ca_source), "target": ca_target, "read_only": True}
    api["volumes"].append(ca_mount)

    harness["ca_host"] = str(ca_source)
    with pytest.raises(entry.DeploymentError, match="requires both host and container paths"):
        harness["deploy"](check_only=True)
    harness["ca_host"] = ""
    api["environment"]["PROFILE_DRIVE_TRANSFER_CA_CERT_FILE"] = ca_target
    with pytest.raises(entry.DeploymentError, match="requires both host and container paths"):
        harness["deploy"](check_only=True)
    harness["ca_host"] = str(ca_source)
    with pytest.raises(entry.DeploymentError, match="must be a readable"):
        harness["deploy"](check_only=True)
    ca_source.write_text("not a certificate")
    with pytest.raises(entry.DeploymentError, match="public certificate material only"):
        harness["deploy"](check_only=True)
    public_bundle = Path(certifi.where()).read_bytes()
    ca_source.write_bytes(public_bundle + b"\n-----BEGIN PRIVATE KEY-----\nsynthetic-only\n")
    with pytest.raises(entry.DeploymentError, match="public certificate material only"):
        harness["deploy"](check_only=True)
    ca_source.write_bytes(public_bundle)
    if os.name == "posix":
        ca_source.chmod(0o644)
    harness["deploy"](check_only=True)
    assert any("compose.profile-drive-ca.yaml" in str(command) for _, command in harness["calls"])
    probe_calls = [command for stage, command in harness["calls"] if stage == "ProfileDrive CA runtime verification"]
    assert len(probe_calls) == 1
    assert all(flag in probe_calls[0] for flag in ("--network", "none", "--read-only", "--user", "10001:10001"))
    harness["fail"] = "ProfileDrive CA runtime verification"
    with pytest.raises(entry.DeploymentError, match="runtime verification"):
        harness["deploy"](check_only=True)
    harness["fail"] = None
    ca_mount["read_only"] = False
    with pytest.raises(entry.DeploymentError, match="must be a readable"):
        harness["deploy"](check_only=True)
    ca_mount["read_only"] = True
    if os.name == "posix":
        ca_link = tmp_path / "linked-ca.pem"
        ca_link.symlink_to(ca_source)
        ca_mount["source"] = str(ca_link)
        with pytest.raises(entry.DeploymentError, match="must be a readable"):
            harness["deploy"](check_only=True)


def test_profile_drive_ca_runtime_probe_rejects_non_certificates_and_private_keys(tmp_path):
    import certifi

    ca_source = tmp_path / "public-ca.pem"
    def probe():
        return subprocess.run([sys.executable, "-B", "-c", entry.PROFILE_DRIVE_CA_PROBE, str(ca_source)],
                              capture_output=True, timeout=10).returncode

    ca_source.write_text("not a certificate")
    assert probe() != 0
    public_bundle = Path(certifi.where()).read_bytes()
    ca_source.write_bytes(public_bundle)
    assert probe() == 0
    ca_source.write_bytes(public_bundle + b"\n-----BEGIN PRIVATE KEY-----\nsynthetic-only\n")
    assert probe() != 0


def test_workspace_migration_source_must_be_a_readonly_host_bind(harness):
    source = harness["config"]["services"]["workspace-migrate"]["volumes"][0]
    source["source"] = "relative/path"
    with pytest.raises(entry.DeploymentError, match="mount topology is invalid"):
        harness["deploy"](check_only=True)

    source["source"] = str((Path(harness["config"]["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"]).parent / "legacy").resolve())
    source["type"] = "volume"
    with pytest.raises(entry.DeploymentError, match="mount topology is invalid"):
        harness["deploy"](check_only=True)

    source["type"] = "bind"
    source["read_only"] = False
    with pytest.raises(entry.DeploymentError, match="mount topology is invalid"):
        harness["deploy"](check_only=True)


def _set_workspace_paths(config, root: Path, source: Path) -> None:
    root_value = str(root)
    source_value = str(source)
    for service in ("api", "worker"):
        config["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"] = root_value
        config["services"][service]["volumes"][0].update(
            {"source": root_value, "target": root_value}
        )
    config["services"]["workspace-init"]["volumes"][0]["source"] = root_value
    target_mount = config["services"]["workspace-migrate"]["volumes"][1]
    target_mount.update({"source": root_value, "target": "/target-workspaces"})
    config["services"]["workspace-migrate"]["volumes"][0]["source"] = source_value


def test_workspace_paths_accept_custom_absolute_configuration(harness, tmp_path):
    root = tmp_path / "custom" / "runtime-workspaces"
    source = tmp_path / "previous" / "runtime-workspaces"
    _set_workspace_paths(harness["config"], root, source)

    harness["deploy"](check_only=True)

    source.mkdir(parents=True)
    with pytest.raises(entry.DeploymentError, match="migrate-legacy-workspaces"):
        harness["deploy"](check_only=True)
    harness["deploy"](check_only=True, migrate_legacy=True)


def test_api_and_worker_workspace_roots_must_match(harness):
    harness["config"]["services"]["worker"]["environment"]["SANDBOX_WORKSPACE_ROOT"] += "-other"

    with pytest.raises(entry.DeploymentError, match="API and Worker workspace roots do not match"):
        harness["deploy"](check_only=True)


@pytest.mark.parametrize(
    ("service", "volume_index", "field", "value"),
    [
        ("workspace-init", 0, "source", "/other/workspaces"),
        ("workspace-init", 0, "read_only", True),
        ("workspace-migrate", 1, "source", "/other/workspaces"),
        ("workspace-migrate", 1, "read_only", True),
    ],
)
def test_workspace_initializer_and_migration_target_must_bind_the_same_writable_root(
    harness, service, volume_index, field, value
):
    harness["config"]["services"][service]["volumes"][volume_index][field] = value

    with pytest.raises(entry.DeploymentError, match="workspace migration mount topology is invalid"):
        harness["deploy"](check_only=True)


@pytest.mark.parametrize("root_kind", ["relative", "unnormalized", "docker-data-root"])
def test_workspace_root_rejects_relative_unnormalized_and_docker_data_paths(
    harness, tmp_path, root_kind
):
    if root_kind == "relative":
        root = "relative/workspaces"
    elif root_kind == "unnormalized":
        root = str(tmp_path / "unused" / ".." / "workspaces")
    else:
        root = str(tmp_path / "docker-data" / "workspaces")
    for service in ("api", "worker"):
        harness["config"]["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"] = root
        harness["config"]["services"][service]["volumes"][0].update(
            {"source": root, "target": root}
        )
    harness["config"]["services"]["workspace-init"]["volumes"][0]["source"] = root
    harness["config"]["services"]["workspace-migrate"]["volumes"][1]["source"] = root

    with pytest.raises(entry.DeploymentError, match="absolute normalized|outside Docker data-root"):
        harness["deploy"](check_only=True)


def test_workspace_root_and_migration_source_reject_symlinked_parents(harness, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    root = link / "workspaces"
    _set_workspace_paths(
        harness["config"], root, tmp_path / "migration-source"
    )

    with pytest.raises(entry.DeploymentError, match="symlinked parents"):
        harness["deploy"](check_only=True)

    safe_root = tmp_path / "safe-root" / "workspaces"
    source = link / "migration-source"
    _set_workspace_paths(harness["config"], safe_root, source)
    with pytest.raises(entry.DeploymentError, match="symlinked parents"):
        harness["deploy"](check_only=True)


@pytest.mark.parametrize("source_contains_target", [False, True])
def test_workspace_migration_rejects_host_source_target_nesting(
    harness,
    source_contains_target,
):
    original_root = Path(
        harness["config"]["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"]
    )
    base = original_root.parent / f"nested-{source_contains_target}"
    if source_contains_target:
        source = base
        target = base / "target"
    else:
        target = base
        source = base / "source"
    source.mkdir(parents=True)
    target.mkdir(parents=True, exist_ok=True)
    _set_workspace_paths(harness["config"], target, source)

    with pytest.raises(entry.DeploymentError, match="mount topology"):
        harness["deploy"](check_only=True)


def test_package_upgrade_fences_twice_and_only_preserves_data_services(harness):
    source = harness["config"]["services"]["workspace-migrate"]["volumes"][0]["source"]
    Path(source).mkdir()
    harness["deploy"](migrate_legacy=True)
    stages = [stage for stage, _ in harness["calls"]]
    assert stages.index("image download") < stages.index("admission stop")
    assert stages.count("quiescent") == 2
    assert max(i for i, stage in enumerate(stages) if stage == "quiescent") < stages.index(
        "workspace storage migration"
    )
    assert (
        stages.index("workspace storage migration")
        < stages.index("schema migration")
        < stages.index("workspace initialization")
        < stages.index("application startup")
    )
    assert stages[-1] == "runtime verified"
    for stage, command in harness["calls"]:
        if "--no-recreate" in command:
            assert stage == "persistent services"
            assert command[-3:] == list(entry.DATA)
        assert "down" not in command and "--volumes" not in command


def test_check_only_never_pulls_or_mutates_services(harness):
    harness["deploy"](check_only=True)
    stages = [stage for stage, _ in harness["calls"]]
    assert "local image verification" in stages
    assert not set(stages) & {
        "image download",
        "admission stop",
        "persistent services",
        "workspace storage migration",
        "schema migration",
        "application startup",
    }


def test_package_offline_does_not_pull_but_verifies_local_images(harness):
    harness["deploy"](offline=True)
    stages = [stage for stage, _ in harness["calls"]]
    assert "image download" not in stages
    assert "local image verification" in stages
    assert "runtime verified" in stages


def test_admission_race_restores_old_services_without_starting_migration(harness):
    harness["race"] = True
    with pytest.raises(entry.DeploymentError, match="activity appeared"):
        harness["deploy"]()
    stages = [stage for stage, _ in harness["calls"]]
    assert stages.count("restore pre-migration admission") == 3
    assert "schema migration" not in stages
    assert "application startup" not in stages


@pytest.mark.parametrize("stage", ["configuration", "image download", "local image verification"])
def test_preflight_failure_does_not_stop_services(harness, stage):
    harness["fail"] = stage
    with pytest.raises(entry.DeploymentError):
        harness["deploy"]()
    assert not any(name in {"admission stop", "schema migration", "application startup"} for name, _ in harness["calls"])


def test_persistent_service_failure_restores_old_services_without_migration(harness):
    harness["fail"] = "persistent services"
    with pytest.raises(entry.DeploymentError):
        harness["deploy"]()
    stages = [name for name, _ in harness["calls"]]
    assert stages.count("restore pre-migration admission") == 3
    assert "schema migration" not in stages
    assert "failed-deployment admission stop" not in stages


def test_workspace_storage_migration_failure_keeps_admission_stopped(harness):
    source = harness["config"]["services"]["workspace-migrate"]["volumes"][0]["source"]
    Path(source).mkdir()
    harness["fail"] = "workspace storage migration"
    with pytest.raises(entry.DeploymentError):
        harness["deploy"](migrate_legacy=True)
    stages = [name for name, _ in harness["calls"]]
    assert "restore pre-migration admission" not in stages
    assert stages.count("failed-deployment admission stop") == 3
    assert "schema migration" not in stages


@pytest.mark.parametrize("stage", ["schema migration", "workspace initialization", "application startup"])
def test_post_migration_failure_fences_without_unsafe_image_rollback(harness, stage):
    harness["fail"] = stage
    with pytest.raises(entry.DeploymentError):
        harness["deploy"]()
    stages = [name for name, _ in harness["calls"]]
    assert stages.count("failed-deployment admission stop") == 3
    assert "restore pre-migration admission" not in stages
    assert "runtime verified" not in stages


def production_workspace(harness, monkeypatch):
    config = harness["config"]
    root = Path(config["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"])
    legacy = root.parent / "legacy"
    config["services"]["workspace-migrate"]["volumes"][0] = {
        "type": "bind", "source": str(legacy), "target": "/source-workspaces", "read_only": True,
    }
    return legacy


def test_clean_production_install_does_not_require_or_create_legacy_source(harness, monkeypatch):
    legacy = production_workspace(harness, monkeypatch)
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {})
    harness["deploy"]()
    assert not legacy.exists()
    assert not harness["state_path"].exists()
    stages = [name for name, _ in harness["calls"]]
    assert "workspace storage migration" not in stages
    assert "workspace initialization" in stages
    assert "runtime verified" in stages


def test_legacy_data_requires_explicit_migration_and_is_retained(harness, monkeypatch):
    legacy = production_workspace(harness, monkeypatch)
    legacy.mkdir()
    sentinel = legacy / "existing-data"
    sentinel.write_text("preserved")
    with pytest.raises(entry.DeploymentError, match="migrate-legacy-workspaces") as exc:
        harness["deploy"]()
    assert "back it up" not in str(exc.value)
    assert not any(name == "admission stop" for name, _ in harness["calls"])
    harness["deploy"](migrate_legacy=True)
    assert sentinel.read_text() == "preserved"
    assert any(name == "workspace storage migration" for name, _ in harness["calls"])


def test_requested_missing_legacy_source_is_rejected(harness, monkeypatch):
    production_workspace(harness, monkeypatch)
    with pytest.raises(entry.DeploymentError, match="source is unavailable"):
        harness["deploy"](migrate_legacy=True)


@pytest.mark.parametrize("key", ["TRUSTED_PRINCIPAL_SECRET", "AI_SESSION_SECRET"])
@pytest.mark.parametrize("value", ["", "short", "change_me_gateway_secret", "change_me_" + "x" * 40])
def test_production_secret_preflight_precedes_all_mutation(harness, monkeypatch, key, value):
    production_workspace(harness, monkeypatch)
    for service in ("api", "worker"):
        harness["config"]["services"][service]["environment"][key] = value
    with pytest.raises(entry.DeploymentError, match=key):
        harness["deploy"]()
    assert not set(name for name, _ in harness["calls"]) & {
        "image download", "admission stop", "persistent services", "schema migration",
    }


def test_production_http_requires_explicit_acknowledgement(harness, monkeypatch, capsys):
    production_workspace(harness, monkeypatch)
    for service in ("api", "worker"):
        harness["config"]["services"][service]["environment"].update({
            "CORS_ALLOW_ORIGINS": "http://platform.internal", "AI_SESSION_COOKIE_SECURE": "false",
            "AUTH_CONTEXT_COOKIE_SECURE": "false",
        })
    with pytest.raises(entry.DeploymentError, match="allow-insecure-http"):
        harness["deploy"]()
    harness["deploy"](allow_insecure_http=True, check_only=True)
    assert "trusted isolated intranet" in capsys.readouterr().err
    assert not any(name == "admission stop" for name, _ in harness["calls"])


def test_partial_first_install_retains_state_and_same_package_can_resume(harness, monkeypatch):
    production_workspace(harness, monkeypatch)
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {})
    harness["fail"] = "schema migration"
    with pytest.raises(entry.DeploymentError, match="injected failure"):
        harness["deploy"]()
    assert harness["state_path"].stat().st_mode & 0o777 == 0o600
    assert "secret" not in harness["state_path"].read_text()
    harness["fail"] = None
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {"postgres": harness["data_record"]("postgres")})
    monkeypatch.setattr(entry, "resume_quiescent", lambda docker: None)
    harness["deploy"](resume_install=True)
    assert not harness["state_path"].exists()
    assert not any(name == "restore pre-migration admission" for name, _ in harness["calls"])


def test_resume_rejects_missing_or_changed_install_identity(harness, monkeypatch):
    production_workspace(harness, monkeypatch)
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {"postgres": harness["data_record"]("postgres")})
    with pytest.raises(entry.DeploymentError, match="state file"):
        harness["deploy"](resume_install=True)
    entry.install_state(harness["state_path"], harness["config"], False, create=True)
    harness["config"]["services"]["api"]["environment"]["CORS_ALLOW_ORIGINS"] = "https://changed.internal"
    with pytest.raises(entry.DeploymentError, match="same release and configuration"):
        harness["deploy"](resume_install=True)
    assert not any(name == "persistent services" for name, _ in harness["calls"])


@pytest.mark.parametrize("failure", ["malformed-json", "non-utf8", "permissions", "symlink"])
def test_resume_rejects_unsafe_install_state_before_mutation(harness, monkeypatch, failure):
    production_workspace(harness, monkeypatch)
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {"postgres": harness["data_record"]("postgres")})
    path = harness["state_path"]
    entry.install_state(path, harness["config"], False, create=True)
    if failure == "malformed-json":
        path.write_text("{invalid")
    elif failure == "non-utf8":
        path.write_bytes(b"\xff")
    elif failure == "permissions":
        path.chmod(0o644)
    else:
        source = path.with_suffix(".original.json")
        path.rename(source)
        path.symlink_to(source)
    message = "owner-held with mode 0600" if failure == "permissions" else "intact owner-held installation state file"
    with pytest.raises(entry.DeploymentError, match=message):
        harness["deploy"](resume_install=True)
    assert not set(name for name, _ in harness["calls"]) & {
        "image download", "persistent services", "schema migration", "application startup",
    }


def test_snapshot_requires_resume_for_data_only_and_rejects_application_resume(monkeypatch):
    names = ["postgres"]
    monkeypatch.setattr(entry, "run", lambda *args, **kwargs: "\n".join(f"ai-platform-{name}" for name in names))
    monkeypatch.setattr(entry, "inspect", lambda docker, name: {
        "Config": {"Labels": {"com.docker.compose.project": entry.PROJECT,
                               "com.docker.compose.service": name.removeprefix("ai-platform-")}}
    })
    with pytest.raises(entry.DeploymentError, match="partial existing stack"):
        entry.snapshot(["docker"])
    assert set(entry.snapshot(["docker"], resume_install=True)) == {"postgres"}
    names.append("api")
    with pytest.raises(entry.DeploymentError, match="data-only state"):
        entry.snapshot(["docker"], resume_install=True)


@pytest.mark.parametrize("tables,accepted", [("0", True), ("3", True), ("1", False), ("2", False)])
def test_resume_checks_existing_activity_or_rejects_partial_schema(monkeypatch, tables, accepted):
    calls = []
    monkeypatch.setattr(entry, "run", lambda command, stage, timeout=90: tables if stage == "install resume schema inspection" else "")
    monkeypatch.setattr(entry, "quiescent", lambda docker: calls.append("quiescent"))
    if accepted:
        entry.resume_quiescent(["docker"])
        assert calls == (["quiescent"] if tables == "3" else [])
    else:
        with pytest.raises(entry.DeploymentError, match="partially created activity schema"):
            entry.resume_quiescent(["docker"])


def test_resume_check_does_not_claim_activity_validation_without_running_database(harness, monkeypatch):
    production_workspace(harness, monkeypatch)
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {"postgres": harness["data_record"]("postgres")})
    entry.install_state(harness["state_path"], harness["config"], False, create=True)
    with pytest.raises(entry.DeploymentError, match="preflight needs.*running"):
        harness["deploy"](resume_install=True, check_only=True)
    assert not any(name == "persistent services" for name, _ in harness["calls"])


def test_resume_check_checks_activity_before_success_without_mutation(harness, monkeypatch):
    production_workspace(harness, monkeypatch)
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {"postgres": harness["data_record"]("postgres", running=True)})
    entry.install_state(harness["state_path"], harness["config"], False, create=True)
    calls = []
    monkeypatch.setattr(entry, "resume_quiescent", lambda docker: calls.append("checked"))
    harness["deploy"](resume_install=True, check_only=True)
    assert calls == ["checked"]
    assert harness["state_path"].exists()
    assert not any(name == "persistent services" for name, _ in harness["calls"])


def test_failed_legacy_copy_cannot_resume_with_missing_source_or_skip_mode(harness, monkeypatch):
    legacy = production_workspace(harness, monkeypatch)
    legacy.mkdir()
    (legacy / "data").write_text("preserve")
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {})
    harness["fail"] = "workspace storage migration"
    with pytest.raises(entry.DeploymentError, match="injected failure"):
        harness["deploy"](migrate_legacy=True)
    (legacy / "data").unlink()
    legacy.rmdir()
    harness["fail"] = None
    with pytest.raises(entry.DeploymentError, match="same release and configuration"):
        harness["deploy"](resume_install=True)
    with pytest.raises(entry.DeploymentError, match="source is unavailable"):
        harness["deploy"](resume_install=True, migrate_legacy=True)
    root = Path(harness["config"]["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"])
    root.mkdir()
    (root / ".ai-platform-workspace-migration-v1.incomplete").touch()
    harness["state_path"].unlink()
    with pytest.raises(entry.DeploymentError, match="incomplete workspace migration"):
        harness["deploy"]()


@pytest.mark.parametrize("mount_type", ["bind", "volume"])
def test_existing_workspace_storage_migrates_only_from_the_inspected_source(
    harness, monkeypatch, mount_type
):
    legacy = production_workspace(harness, monkeypatch)
    legacy.mkdir()
    source_identity = str(legacy)
    harness["workspace_volume"] = {
        "Name": "old-compose-project_runtime-data", "Driver": "local",
        "Mountpoint": source_identity, "Options": None,
    }
    records = {service: {} for service in (*entry.DATA, *entry.APPS)}
    for service in ("api", "worker"):
        root = harness["config"]["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"]
        records[service] = {
            "Config": {"Env": [f"SANDBOX_WORKSPACE_ROOT={root}"]},
            "Mounts": [{
                "Type": mount_type,
                **({"Name": "old-compose-project_runtime-data"} if mount_type == "volume" else {}),
                "Source": source_identity, "Destination": root, "RW": True,
            }],
        }
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: records)

    harness["deploy"](migrate_legacy=True, check_only=True)
    source_mount = harness["config"]["services"]["workspace-migrate"]["volumes"][0]
    assert source_mount == {
        "type": "bind", "source": source_identity,
        "target": "/source-workspaces", "read_only": True,
    }
    assert not any(name == "admission stop" for name, _ in harness["calls"])


@pytest.mark.parametrize("change", [
    {"Driver": "unsupported-plugin"},
    {"Options": {"type": "none", "o": "bind", "device": "/srv/workspaces"}},
    {"Options": {"type": "nfs", "o": "addr=10.0.0.2", "device": ":/workspaces"}},
    {"Mountpoint": "/wrong/source"},
    {"Name": "other-volume"},
])
def test_existing_workspace_volume_requires_stable_local_storage_before_admission(harness, monkeypatch, change):
    legacy = production_workspace(harness, monkeypatch)
    legacy.mkdir()
    name = "old-compose-project_runtime-data"
    harness["workspace_volume"] = {
        "Name": name, "Driver": "local", "Mountpoint": str(legacy),
        "Options": None, **change,
    }
    records = {service: {} for service in (*entry.DATA, *entry.APPS)}
    for service in ("api", "worker"):
        root = harness["config"]["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"]
        records[service] = {
            "Config": {"Env": [f"SANDBOX_WORKSPACE_ROOT={root}"]},
            "Mounts": [{"Type": "volume", "Name": name, "Driver": "local",
                        "Source": str(legacy), "Destination": root}],
        }
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: records)

    with pytest.raises(entry.DeploymentError, match="plain local volume"):
        harness["deploy"](migrate_legacy=True)
    assert not any(stage == "admission stop" for stage, _ in harness["calls"])


def test_existing_workspace_volume_rejects_a_different_selected_source(harness, monkeypatch):
    legacy = production_workspace(harness, monkeypatch)
    legacy.mkdir()
    records = {service: {} for service in (*entry.DATA, *entry.APPS)}
    for service in ("api", "worker"):
        root = harness["config"]["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"]
        records[service] = {
            "Config": {"Env": [f"SANDBOX_WORKSPACE_ROOT={root}"]},
            "Mounts": [{
                "Type": "volume", "Name": "legacy-stack_workspace-volume",
                "Source": str(legacy / "inspected-source"), "Destination": root, "RW": True,
            }],
        }
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: records)

    with pytest.raises(entry.DeploymentError, match="explicitly supported migration"):
        harness["deploy"](migrate_legacy=True, check_only=True)
    assert not any(name == "admission stop" for name, _ in harness["calls"])


def test_existing_workspace_api_and_worker_mount_identities_must_match(harness, monkeypatch):
    legacy = production_workspace(harness, monkeypatch)
    legacy.mkdir()
    records = {service: {} for service in (*entry.DATA, *entry.APPS)}
    for service in ("api", "worker"):
        root = harness["config"]["services"][service]["environment"]["SANDBOX_WORKSPACE_ROOT"]
        records[service] = {
            "Config": {"Env": [f"SANDBOX_WORKSPACE_ROOT={root}"]},
            "Mounts": [{
                "Type": "volume", "Name": f"old-stack_{service}-workspace",
                "Source": str(legacy), "Destination": root, "RW": True,
            }],
        }
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: records)

    with pytest.raises(entry.DeploymentError, match="identities do not match"):
        harness["deploy"](migrate_legacy=True, check_only=True)
    assert not any(name == "admission stop" for name, _ in harness["calls"])


@pytest.mark.parametrize("change", ["image", "mount_name", "mount_rw", "extra_mount"])
def test_resume_rejects_changed_persistent_resource_before_service_changes(harness, monkeypatch, change):
    production_workspace(harness, monkeypatch)
    record = harness["data_record"]("postgres")
    if change == "image":
        record["Image"] = "other-image-id"
    elif change == "mount_name":
        record["Mounts"][0]["Name"] = "unrelated_data"
    elif change == "mount_rw":
        record["Mounts"][0]["RW"] = False
    else:
        record["Mounts"].append({"Type": "bind", "Source": "/unexpected", "Destination": "/unexpected"})
    monkeypatch.setattr(entry, "snapshot", lambda docker, **kwargs: {"postgres": record})
    entry.install_state(harness["state_path"], harness["config"], False, create=True)
    with pytest.raises(entry.DeploymentError, match="persistent.*recorded package"):
        harness["deploy"](resume_install=True)
    assert not any(name == "persistent services" for name, _ in harness["calls"])


def test_private_workspace_marker_is_checked_by_readonly_docker_not_host_traversal(harness, monkeypatch):
    production_workspace(harness, monkeypatch)
    root = Path(harness["config"]["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"])
    root.mkdir(mode=0o700)
    calls = []
    def run(command, stage, timeout=90):
        calls.append(command)
        assert stage == "workspace migration marker inspection"
        assert command[:3] == ["sudo", "-n", "docker"]
        assert "--read-only" in command and "--network" in command and "none" in command
        assert f"type=bind,source={root},target=/workspaces,readonly" in command
        assert "DAC_READ_SEARCH" in command
        assert command[command.index("--pull") + 1] == "never"
        return "clear"
    original_lstat = Path.lstat
    def restricted_lstat(path, *args, **kwargs):
        if path.parent == root:
            raise PermissionError("private root")
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", restricted_lstat)
    monkeypatch.setattr(entry, "run", run)
    entry.verify_workspace_migration_complete(harness["config"], ["sudo", "-n", "docker"])
    assert len(calls) == 1
