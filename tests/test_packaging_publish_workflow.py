import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-packaging-publish.yml"
WORKFLOW_ROOT = ROOT / ".github" / "workflows"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _workflow() -> dict[str, object]:
    return yaml.load(_workflow_text(), Loader=yaml.BaseLoader)


def test_publish_is_reachable_only_from_trusted_main_events():
    workflow = _workflow()
    triggers = workflow["on"]

    assert set(triggers) == {"push", "workflow_dispatch"}
    assert triggers["push"] == {"branches": ["main"]}
    assert set(triggers["workflow_dispatch"]["inputs"]) == {"confirm_release"}
    confirm = triggers["workflow_dispatch"]["inputs"]["confirm_release"]
    assert confirm["required"] == "true"
    assert confirm["type"] == "choice"
    assert confirm["options"] == ["PUBLISH_MAIN"]

    text = _workflow_text()
    assert "pull_request:" not in text
    assert "pull_request_target:" not in text
    assert "github.head_ref" not in text
    assert "github.event.inputs.ref" not in text
    assert "github.event.pull_request" not in text


def test_publish_permissions_are_job_scoped_and_environment_protected():
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}

    publish = workflow["jobs"]["publish"]
    assert publish["permissions"] == {
        "attestations": "write",
        "contents": "read",
        "id-token": "write",
        "packages": "write",
    }
    assert publish["environment"] == "packaging-publish"
    assert publish["if"] == (
        "github.ref == 'refs/heads/main' && (github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.confirm_release == 'PUBLISH_MAIN'))"
    )

    manifest = workflow["jobs"]["release-manifest"]
    assert manifest["permissions"] == {"contents": "read"}
    assert "packages" not in manifest["permissions"]
    assert "id-token" not in manifest["permissions"]


def test_every_job_uses_an_unprivileged_github_hosted_runner_and_pinned_actions():
    workflow = _workflow()
    for job in workflow["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04"
        assert "self-hosted" not in str(job["runs-on"])
        for step in job.get("steps", []):
            action = step.get("uses")
            if action is not None:
                assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action), action


def test_all_repository_actions_are_commit_pinned_without_self_hosted_publishers():
    for workflow_path in sorted(WORKFLOW_ROOT.glob("*.yml")):
        text = workflow_path.read_text(encoding="utf-8")
        assert "runs-on: self-hosted" not in text
        for action in re.findall(r"^\s*uses:\s*([^\s#]+)", text, flags=re.MULTILINE):
            assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action), (
                workflow_path.name,
                action,
            )


def test_publish_checks_out_and_proves_the_exact_event_commit():
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    checkout = next(step for step in publish["steps"] if step.get("name") == "Checkout exact source")
    assert checkout["with"] == {
        "fetch-depth": "1",
        "persist-credentials": "false",
        "ref": "${{ github.sha }}",
    }

    text = _workflow_text()
    assert 'SOURCE_COMMIT: ${{ github.sha }}' in text
    assert 'test "$(git rev-parse HEAD)" = "$SOURCE_COMMIT"' in text
    assert 'test "$GITHUB_REPOSITORY" = "demonsxxxxxx/ai-platform"' in text
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in text
    assert '[[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]' in text


def test_publish_matrix_is_exactly_backend_and_frontend_on_linux_amd64():
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    matrix = publish["strategy"]["matrix"]["include"]

    assert matrix == [
        {
            "context": ".",
            "dockerfile": "Dockerfile",
            "role": "backend",
            "subject": "ghcr.io/demonsxxxxxx/ai-platform-backend",
        },
        {
            "context": ".",
            "dockerfile": "frontend/web/Dockerfile",
            "role": "frontend",
            "subject": "ghcr.io/demonsxxxxxx/ai-platform-frontend",
        },
    ]

    text = _workflow_text()
    assert "platforms: linux/amd64" in text
    assert "linux/arm64" not in text
    assert "latest" not in text.lower()
    assert "${{ matrix.subject }}:${{ github.sha }}" in text
    assert "${{ matrix.subject }}@${{ steps.build.outputs.digest }}" in text
    assert "docker image inspect" not in text


def test_publish_build_has_no_secret_inputs_and_all_evidence_precedes_ready_manifest():
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    steps = publish["steps"]
    names = [step.get("name") for step in steps]

    assert names.index("Build and push immutable image") < names.index("Generate SPDX SBOM")
    assert names.index("Generate SPDX SBOM") < names.index("Attest SPDX SBOM")
    assert names.index("Attest build provenance") < names.index("Sign image by digest")
    assert names.index("Sign image by digest") < names.index("Scan published digest")
    assert names.index("Scan published digest") < names.index("Create subject evidence record")

    build = next(step for step in steps if step.get("name") == "Build and push immutable image")
    build_inputs = build["with"]
    assert "secrets" not in build_inputs
    assert "secret-files" not in build_inputs
    assert set(build_inputs["build-args"].splitlines()) == {
        "AI_PLATFORM_BUILD_COMMIT=${{ github.sha }}",
        "AI_PLATFORM_BUILD_DIRTY=false",
        "AI_PLATFORM_BUILD_REPOSITORY=https://github.com/demonsxxxxxx/ai-platform.git",
    }

    text = _workflow_text()
    assert "secrets." not in build_inputs["build-args"]
    assert "github.token" not in build_inputs["build-args"]
    assert "password: ${{ github.token }}" in text
    assert "TRIVY_SEVERITY: HIGH,CRITICAL" in text
    assert "exit-code: '1'" in text
    assert "ignore-unfixed: false" in text
    assert "version: v0.70.0" in text
    assert "trivy-version:" not in text
    assert "syft-version: v1.50.0" in text
    assert "cosign-release: v3.1.3" in text
    assert "version: v0.36.1" in text
    assert "cosign verify" in text
    assert "cosign verify-attestation" in text
    assert '--cert-identity "$CERTIFICATE_IDENTITY"' in text
    assert '--source-digest "$SOURCE_COMMIT"' in text
    assert "--source-ref refs/heads/main" in text
    assert "--deny-self-hosted-runners" in text


def test_ready_manifest_requires_both_subject_records_and_is_uploaded_as_run_evidence():
    workflow = _workflow()
    manifest = workflow["jobs"]["release-manifest"]
    assert manifest["needs"] == ["publish"]

    text = _workflow_text()
    assert "python tools/release_image_manifest.py assemble" in text
    assert "python tools/release_image_manifest.py verify" in text
    assert "--expected-role backend" in text
    assert "--expected-role frontend" in text
    assert "release-image-manifest.json" in text
    assert "release-image-evidence-${{ github.sha }}" in text
