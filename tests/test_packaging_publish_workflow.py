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
    assert manifest["permissions"] == {"contents": "read", "packages": "read"}
    assert "attestations" not in manifest["permissions"]
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
    assert names.index("Generate SPDX SBOM") < names.index("Scan published digest")
    assert names.index("Scan published digest") < names.index("Attest SPDX SBOM")
    assert names.index("Scan published digest") < names.index("Attest build provenance")
    assert names.index("Attest build provenance") < names.index("Sign image by digest")
    assert names.index("Sign image by digest") < names.index("Create subject evidence record")

    build = next(step for step in steps if step.get("name") == "Build and push immutable image")
    build_inputs = build["with"]
    assert "secrets" not in build_inputs
    assert "secret-files" not in build_inputs
    assert set(build_inputs["build-args"].splitlines()) == {
        "AI_PLATFORM_BUILD_COMMIT=${{ github.sha }}",
        "AI_PLATFORM_BUILD_DIRTY=false",
        "AI_PLATFORM_BUILD_REPOSITORY=https://github.com/demonsxxxxxx/ai-platform.git",
    }
    assert build_inputs["provenance"] == "false"

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


def test_github_cli_is_fixed_checksum_verified_and_token_is_step_scoped():
    workflow = _workflow()
    steps = workflow["jobs"]["publish"]["steps"]
    install = next(step for step in steps if step.get("name") == "Install pinned GitHub CLI")
    verify = next(step for step in steps if step.get("name") == "Verify signature and attestations")

    assert install["env"] == {
        "GH_CLI_CHECKSUMS_SHA256": "61905c69ec8660f310814ec98395cdd0c2d07aabf024c597ec45813984a02334",
        "GH_CLI_TARBALL_SHA256": "a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112",
        "GH_CLI_VERSION": "2.97.0",
    }
    install_run = install["run"]
    assert 'release_url="https://github.com/cli/cli/releases/download/v${GH_CLI_VERSION}"' in install_run
    assert '"$release_url/gh_${GH_CLI_VERSION}_checksums.txt"' in install_run
    assert '"$release_url/$archive_name"' in install_run
    assert 'printf \'%s  %s\\n\' "$GH_CLI_CHECKSUMS_SHA256" "$checksums_path" | sha256sum -c -' in install_run
    assert 'printf \'%s  %s\\n\' "$GH_CLI_TARBALL_SHA256" "$archive_path" | sha256sum -c -' in install_run
    assert 'test "$actual_version" = "$GH_CLI_VERSION"' in install_run
    assert "latest" not in install_run.lower()

    assert verify["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert verify["env"]["GH_CLI_BIN"] == "${{ env.GH_CLI_BIN }}"
    assert '"$GH_CLI_BIN" attestation verify' in verify["run"]
    assert '--bundle "provenance-${{ matrix.role }}.bundle.json"' in verify["run"]
    assert "set -x" not in verify["run"]
    assert 'echo "$GH_TOKEN"' not in verify["run"]
    for step in steps:
        if step is verify:
            continue
        assert "GH_TOKEN" not in step.get("env", {})


def test_release_manifest_reverifies_exact_downloaded_bundles_with_pinned_gh():
    workflow = _workflow()
    steps = workflow["jobs"]["release-manifest"]["steps"]
    install = next(
        step for step in steps if step.get("name") == "Install pinned GitHub CLI for assembly"
    )
    verify = next(
        step for step in steps if step.get("name") == "Reverify downloaded provenance bundles"
    )

    assert install["env"] == {
        "GH_CLI_CHECKSUMS_SHA256": "61905c69ec8660f310814ec98395cdd0c2d07aabf024c597ec45813984a02334",
        "GH_CLI_TARBALL_SHA256": "a2c9b8497e1f85b1ad0dfcb78b5a622e098801b8e461e459e88e1ee12f018112",
        "GH_CLI_VERSION": "2.97.0",
    }
    assert 'test "$actual_version" = "$GH_CLI_VERSION"' in install["run"]
    assert verify["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert verify["env"]["GH_CLI_BIN"] == "${{ env.GH_CLI_BIN }}"
    assert '"$GH_CLI_BIN" attestation verify "$image_ref"' in verify["run"]
    assert '--bundle "provenance-$role.bundle.json"' in verify["run"]
    assert '> "provenance-$role.assembly-verified.json"' in verify["run"]
    assert "set -x" not in verify["run"]
    assert 'echo "$GH_TOKEN"' not in verify["run"]
    for step in steps:
        if step is verify:
            continue
        assert "GH_TOKEN" not in step.get("env", {})


def test_release_manifest_authenticates_private_ghcr_before_local_bundle_verification():
    workflow = _workflow()
    manifest = workflow["jobs"]["release-manifest"]
    steps = manifest["steps"]
    names = [step.get("name") for step in steps]
    login = next(step for step in steps if step.get("name") == "Log in to GHCR for assembly")
    verify = next(
        step for step in steps if step.get("name") == "Reverify downloaded provenance bundles"
    )

    assert manifest["permissions"] == {"contents": "read", "packages": "read"}
    assert "attestations" not in manifest["permissions"]
    assert names.index("Log in to GHCR for assembly") < names.index(
        "Reverify downloaded provenance bundles"
    )
    assert login["uses"] == (
        "docker/login-action@dbcb813823bdd20940b903addbd779551569679f"
    )
    assert login["with"] == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ github.token }}",
    }
    assert verify["env"] == {
        "CERTIFICATE_IDENTITY": (
            "https://github.com/demonsxxxxxx/ai-platform/.github/workflows/"
            "ai-platform-packaging-publish.yml@refs/heads/main"
        ),
        "GH_CLI_BIN": "${{ env.GH_CLI_BIN }}",
        "GH_TOKEN": "${{ github.token }}",
        "OIDC_ISSUER": "https://token.actions.githubusercontent.com",
    }
    assert "--bundle" in verify["run"]
    assert "attestations:" not in str(manifest["permissions"])
    assert "set -x" not in verify["run"]
    assert 'echo "$GH_TOKEN"' not in verify["run"]
    logout = next(
        step
        for step in steps
        if step.get("name") == "Log out of GHCR after assembly verification"
    )
    assert names.index("Reverify downloaded provenance bundles") < names.index(
        "Log out of GHCR after assembly verification"
    )
    assert names.index("Log out of GHCR after assembly verification") < names.index(
        "Assemble and verify ready manifest"
    )
    assert logout["if"] == "always()"
    assert logout["run"] == "docker logout ghcr.io"

    for step in steps:
        if step is verify:
            continue
        assert "GH_TOKEN" not in step.get("env", {})
    for step in steps:
        if step is login:
            continue
        assert "github.token" not in str(step.get("with", {}))
    artifact_steps = [step for step in steps if "artifact" in str(step.get("uses", ""))]
    for step in artifact_steps:
        assert "github.token" not in str(step)
        assert "GH_TOKEN" not in str(step)


def test_generated_spdx_is_bound_before_scan_and_attestation():
    steps = _workflow()["jobs"]["publish"]["steps"]
    names = [step.get("name") for step in steps]
    source = next(
        step
        for step in steps
        if step.get("name") == "Capture generated SPDX source identity"
    )
    bind = next(step for step in steps if step.get("name") == "Bind SPDX SBOM to immutable subject")

    assert names.index("Generate SPDX SBOM") < names.index(
        "Capture generated SPDX source identity"
    )
    assert names.index("Capture generated SPDX source identity") < names.index(
        "Bind SPDX SBOM to immutable subject"
    )
    assert names.index("Bind SPDX SBOM to immutable subject") < names.index("Scan published digest")
    assert names.index("Bind SPDX SBOM to immutable subject") < names.index("Attest SPDX SBOM")
    assert source["id"] == "spdx-source"
    assert "python tools/release_image_manifest.py spdx-source-hash" in source["run"]
    assert 'printf \'sha256=%s\\n\' "$source_hash" >> "$GITHUB_OUTPUT"' in source["run"]
    assert "python tools/release_image_manifest.py bind-spdx" in bind["run"]
    assert '--source-commit "$SOURCE_COMMIT"' in bind["run"]
    assert '--manifest-digest "$MANIFEST_DIGEST"' in bind["run"]
    assert '--image-ref "$IMAGE_REF"' in bind["run"]
    assert '--workflow-run-id "$GITHUB_RUN_ID"' in bind["run"]
    assert '--workflow-run-attempt "$GITHUB_RUN_ATTEMPT"' in bind["run"]
    assert (
        '--unbound-content-sha256 "${{ steps.spdx-source.outputs.sha256 }}"'
        in bind["run"]
    )


def test_spdx_binding_uses_an_authenticated_immutable_linux_amd64_producer_digest():
    steps = _workflow()["jobs"]["publish"]["steps"]
    names = [step.get("name") for step in steps]
    resolver = next(
        step
        for step in steps
        if step.get("name") == "Resolve authenticated linux/amd64 producer digest"
    )
    source = next(
        step
        for step in steps
        if step.get("name") == "Capture generated SPDX source identity"
    )
    bind = next(step for step in steps if step.get("name") == "Bind SPDX SBOM to immutable subject")

    assert names.index("Require registry manifest digest") < names.index(
        "Resolve authenticated linux/amd64 producer digest"
    ) < names.index("Generate SPDX SBOM")
    assert 'docker buildx imagetools inspect --raw "$IMAGE_REF"' in resolver["run"]
    assert "resolve-producer-digest" in resolver["run"]
    assert '--manifest-digest "$MANIFEST_DIGEST"' in resolver["run"]
    assert '--image-ref "$IMAGE_REF"' in resolver["run"]
    assert 'printf \'PRODUCER_DIGEST=%s\\n\' "$producer_digest" >> "$GITHUB_ENV"' in resolver[
        "run"
    ]
    assert "GH_TOKEN" not in resolver.get("env", {})
    assert "github.token" not in str(resolver)
    assert "set -x" not in resolver["run"]
    assert '--producer-digest "$PRODUCER_DIGEST"' in source["run"]
    assert '--producer-digest "$PRODUCER_DIGEST"' in bind["run"]


def test_spdx_binding_failure_uploads_only_untrusted_run_bound_diagnostics():
    workflow = _workflow()
    publish = workflow["jobs"]["publish"]
    steps = publish["steps"]
    names = [step.get("name") for step in steps]
    source = next(
        step for step in steps if step.get("name") == "Capture generated SPDX source identity"
    )
    diagnostic = next(
        step
        for step in steps
        if step.get("name") == "Upload untrusted SPDX binding diagnostic"
    )

    assert source["id"] == "spdx-source"
    assert "continue-on-error" not in source
    assert (
        '--failure-evidence-file "spdx-binding-diagnostic-${{ matrix.role }}.json"'
        in source["run"]
    )
    assert names.index("Capture generated SPDX source identity") < names.index(
        "Upload untrusted SPDX binding diagnostic"
    ) < names.index("Bind SPDX SBOM to immutable subject")
    assert diagnostic["if"] == (
        "${{ failure() && steps.spdx-source.outcome == 'failure' && "
        "hashFiles(format('sbom-{0}.spdx.json', matrix.role)) != '' && "
        "hashFiles(format('spdx-binding-diagnostic-{0}.json', matrix.role)) != '' }}"
    )
    assert diagnostic["uses"] == (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    )
    assert diagnostic["with"] == {
        "name": (
            "release-image-spdx-diagnostic-${{ github.sha }}-${{ github.run_id }}-"
            "${{ github.run_attempt }}-${{ matrix.role }}"
        ),
        "if-no-files-found": "error",
        "retention-days": "1",
        "path": (
            "sbom-${{ matrix.role }}.spdx.json\n"
            "spdx-binding-diagnostic-${{ matrix.role }}.json\n"
        ),
    }
    assert "github.token" not in str(diagnostic)
    assert "GH_TOKEN" not in str(diagnostic)
    assert "release-image-spdx-diagnostic" not in str(workflow["jobs"]["release-manifest"])
    assert workflow["jobs"]["release-manifest"]["needs"] == ["publish"]


def test_artifact_and_evidence_names_bind_run_attempt():
    text = _workflow_text()

    assert (
        "release-image-subject-${{ github.sha }}-${{ github.run_id }}-"
        "${{ github.run_attempt }}-${{ matrix.role }}"
    ) in text
    assert (
        "release-image-evidence-${{ github.sha }}-${{ github.run_id }}-"
        "${{ github.run_attempt }}"
    ) in text
    assert (
        "github-artifact://release-image-subject-$SOURCE_COMMIT-$GITHUB_RUN_ID-"
        "$GITHUB_RUN_ATTEMPT-${{ matrix.role }}/trivy-${{ matrix.role }}.json"
    ) in text


def test_ready_manifest_requires_both_subject_records_and_is_uploaded_as_run_evidence():
    workflow = _workflow()
    manifest = workflow["jobs"]["release-manifest"]
    assert manifest["needs"] == ["publish"]

    text = _workflow_text()
    assert "python tools/release_image_manifest.py assemble" in text
    assert "python tools/release_image_manifest.py verify" in text
    assert "--expected-role backend" in text
    assert "--expected-role frontend" in text
    assert text.count("--evidence-root .") == 2
    assert "--provenance-bundle \"provenance-${{ matrix.role }}.bundle.json\"" in text
    assert "--provenance-verification \"provenance-${{ matrix.role }}.verified.json\"" in text
    assert "release-image-manifest.json" in text
    assert (
        "release-image-evidence-${{ github.sha }}-${{ github.run_id }}-"
        "${{ github.run_attempt }}"
    ) in text
