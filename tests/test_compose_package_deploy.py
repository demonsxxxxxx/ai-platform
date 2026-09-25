"""Fault injection for the package entry; Docker/runtime evidence is host-only."""
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("package_deploy", ROOT / "deploy/ai-platform/deploy.py")
entry = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(entry)


@pytest.mark.skipif(os.name != "posix", reason="real POSIX file ownership and O_NOFOLLOW required")
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
    original.chmod(0o644)
    with pytest.raises(entry.DeploymentError):
        with entry.protected_environment(original):
            pytest.fail("unsafe config was accepted")
    link = tmp_path / "linked.env"
    link.symlink_to(original)
    with pytest.raises(OSError):
        with entry.protected_environment(link):
            pytest.fail("symlink was accepted")


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


def test_internal_test_model_proxy_bind_requires_private_docker_bridge_ipv4():
    base = {
        "services": {
            "opensandbox-egress-proxy": {
                "ports": [{
                    "host_ip": "172.17.0.1", "published": "18043",
                    "target": 8080, "protocol": "tcp",
                }]
            }
        }
    }
    assert entry.validate_model_proxy_bind(base) == "172.17.0.1"
    assert entry.validate_model_proxy_bind({
        "services": {"opensandbox-egress-proxy": {}}
    }) is None
    for host_ip in ("", "0.0.0.0", "127.0.0.1", "169.254.1.1", "8.8.8.8", "::1"):
        invalid = json.loads(json.dumps(base))
        invalid["services"]["opensandbox-egress-proxy"]["ports"][0]["host_ip"] = host_ip
        with pytest.raises(entry.DeploymentError, match="model proxy bind is invalid"):
            entry.validate_model_proxy_bind(invalid)
    with pytest.raises(entry.DeploymentError, match="model proxy is missing"):
        entry.validate_model_proxy_bind({"services": {}})


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
    state = {"calls": [], "activity_checks": 0, "race": False, "fail": None}
    workspace_root = str((tmp_path / "workspaces").resolve())
    monkeypatch.setattr(entry, "INTERNAL_TEST_WORKSPACE_ROOT", Path(workspace_root))
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
    config["volumes"] = {
        "ai_platform_sandbox_workspaces": {
            "name": "ai-platform-internal_ai_platform_sandbox_workspaces"
        }
    }
    for service in ("api", "worker"):
        config["services"][service]["environment"] = {
            "SANDBOX_WORKSPACE_ROOT": workspace_root,
            "SANDBOX_SECURITY_PROFILE": "internal-test",
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
            "type": "volume",
            "source": "ai_platform_sandbox_workspaces",
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
        if stage == "Docker data-root inspection":
            return str((tmp_path / "docker-data").resolve())
        if stage == "Docker bridge inspection":
            return state.get("bridge_gateway", "")
        if stage == "local image verification":
            return json.dumps([{"Id": command[-1], "RepoDigests": [command[-1]]}])
        return ""

    def quiescent(docker):
        state["activity_checks"] += 1
        state["calls"].append(("quiescent", []))
        if state["race"] and state["activity_checks"] == 2:
            raise entry.DeploymentError("activity appeared after first check")

    monkeypatch.setattr(entry, "run", run)
    monkeypatch.setattr(entry, "snapshot", lambda docker: {service: {} for service in (*entry.DATA, *entry.APPS)})
    monkeypatch.setattr(entry, "quiescent", quiescent)
    monkeypatch.setattr(entry, "verify_runtime", lambda *args: state["calls"].append(("runtime verified", [])))
    state["config"] = config
    state["bridge_gateway"] = ""
    state["deploy"] = lambda offline=False, check_only=False: entry.deploy(tmp_path, env, ["docker"], offline, check_only)
    return state


def test_internal_test_workspace_volume_identity_is_exact(harness):
    source = harness["config"]["services"]["workspace-migrate"]["volumes"][0]
    source["source"] = "other_workspace_volume"
    with pytest.raises(entry.DeploymentError, match="mount topology is invalid"):
        harness["deploy"](check_only=True)

    source["source"] = "ai_platform_sandbox_workspaces"
    harness["config"]["volumes"]["ai_platform_sandbox_workspaces"]["name"] = (
        "other_physical_volume"
    )
    with pytest.raises(entry.DeploymentError, match="mount topology is invalid"):
        harness["deploy"](check_only=True)


def test_workspace_root_must_match_the_reviewed_profile_allowlist(harness):
    harness["config"]["services"]["api"]["environment"]["SANDBOX_WORKSPACE_ROOT"] += "-other"

    with pytest.raises(entry.DeploymentError, match="not approved"):
        harness["deploy"](check_only=True)


@pytest.mark.parametrize("source_contains_target", [False, True])
def test_workspace_migration_rejects_host_source_target_nesting(
    harness,
    monkeypatch,
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
    monkeypatch.setattr(entry, "PRODUCTION_WORKSPACE_ROOT", target)
    monkeypatch.setattr(entry, "PRODUCTION_WORKSPACE_MIGRATION_SOURCE", source)
    for service in ("api", "worker"):
        harness["config"]["services"][service]["environment"].update(
            {
                "SANDBOX_SECURITY_PROFILE": "governed",
                "SANDBOX_WORKSPACE_ROOT": str(target),
            }
        )
        harness["config"]["services"][service]["volumes"][0].update(
            {"source": str(target), "target": str(target)}
        )
    harness["config"]["services"]["workspace-init"]["volumes"][0]["source"] = str(
        target
    )
    migration_volumes = harness["config"]["services"]["workspace-migrate"]["volumes"]
    migration_volumes[0] = {
        "type": "bind",
        "source": str(source),
        "target": "/source-workspaces",
        "read_only": True,
    }
    migration_volumes[1]["source"] = str(target)

    with pytest.raises(entry.DeploymentError, match="mount topology"):
        harness["deploy"](check_only=True)


def test_internal_test_proxy_bind_must_match_actual_docker_bridge_gateway(harness):
    proxy_url = "http://172.17.0.1:18043"
    harness["config"]["services"]["opensandbox-egress-proxy"]["ports"] = [{
        "host_ip": "172.17.0.1", "published": "18043", "target": 8080,
        "protocol": "tcp",
    }]
    with pytest.raises(entry.DeploymentError, match="not the Docker bridge gateway"):
        harness["deploy"](check_only=True)

    harness["bridge_gateway"] = "172.17.0.1"
    for service in ("api", "worker"):
        harness["config"]["services"][service]["environment"].update({
            "OPENSANDBOX_EGRESS_PROXY_URL": proxy_url,
        })
    harness["config"]["services"]["worker"]["environment"][
        "OPENSANDBOX_EGRESS_PROXY_URL"
    ] = "http://172.19.0.9:18043"
    with pytest.raises(entry.DeploymentError, match="URL does not match"):
        harness["deploy"](check_only=True)

    harness["config"]["services"]["worker"]["environment"][
        "OPENSANDBOX_EGRESS_PROXY_URL"
    ] = proxy_url
    harness["deploy"](check_only=True)
    stages = [stage for stage, _ in harness["calls"]]
    assert stages.count("Docker bridge inspection") == 3
    assert "local image verification" in stages


def test_package_upgrade_fences_twice_and_only_preserves_data_services(harness):
    harness["deploy"]()
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
    harness["fail"] = "workspace storage migration"
    with pytest.raises(entry.DeploymentError):
        harness["deploy"]()
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
