import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DOCKERFILE = ROOT / "Dockerfile"
FRONTEND_DOCKERFILE = ROOT / "frontend" / "web" / "Dockerfile"
BACKEND_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-backend.yml"
FRONTEND_WORKFLOW = ROOT / ".github" / "workflows" / "ai-platform-frontend.yml"

PYTHON_VERSION = "3.13.14"
NODE_VERSION = "22.23.2"
PNPM_VERSION = "10.32.1"
UV_VERSION = "0.12.1"

EXPECTED_BASES = {
    "python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8",
    "ghcr.io/astral-sh/uv:0.12.1@sha256:cf4eedcaa81655197f625739489effcbe71b61ceb1506f332c3facae5deceded",
    "node:22.23.2-bookworm@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a",
    "nginx:1.30.4-alpine@sha256:97d490c12ba55b4946b01546d1c3ed324e8d41ab1c9fcb2a616aa470620e5b46",
}


def _from_references(dockerfile: Path) -> list[str]:
    return re.findall(
        r"^FROM\s+(\S+)", dockerfile.read_text(encoding="utf-8"), re.MULTILINE
    )


def test_every_external_base_is_an_expected_immutable_index_subject():
    backend_refs = _from_references(BACKEND_DOCKERFILE)
    frontend_refs = _from_references(FRONTEND_DOCKERFILE)
    references = backend_refs + frontend_refs

    assert len(references) == 5
    assert set(references) == EXPECTED_BASES
    assert (
        backend_refs.count(
            next(ref for ref in EXPECTED_BASES if ref.startswith("python:"))
        )
        == 2
    )
    for reference in references:
        assert re.search(r":[^@\s]+@sha256:[0-9a-f]{64}$", reference)
        assert ":latest" not in reference
        assert "${" not in reference


def test_python_lock_is_the_install_authority_for_ci_and_the_backend_image():
    backend = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    workflow = BACKEND_WORKFLOW.read_text(encoding="utf-8")
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))

    assert pyproject["tool"]["uv"]["required-version"] == f"=={UV_VERSION}"
    assert lock["requires-python"] == pyproject["project"]["requires-python"]
    assert any(
        package["name"] == "claude-agent-sdk" and package["version"] == "0.2.130"
        for package in lock["package"]
    )
    assert "COPY pyproject.toml uv.lock /app/" in backend
    assert "uv sync --locked --no-dev --no-install-project" in backend
    assert "pip install" not in backend
    assert "pip config" not in backend
    assert "tomllib" not in backend
    assert "uv lock --check" in workflow
    assert "uv sync --locked --extra test --no-install-project" in workflow
    assert workflow.index("uv lock --check") < workflow.index(
        "uv sync --locked --extra test --no-install-project"
    )


def test_python_node_and_package_manager_versions_cannot_drift():
    backend_dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
    frontend_dockerfile = FRONTEND_DOCKERFILE.read_text(encoding="utf-8")
    backend_workflow = BACKEND_WORKFLOW.read_text(encoding="utf-8")
    frontend_workflow = FRONTEND_WORKFLOW.read_text(encoding="utf-8")
    package = json.loads(
        (ROOT / "frontend" / "web" / "package.json").read_text(encoding="utf-8")
    )

    assert f"python:{PYTHON_VERSION}-slim-bookworm@sha256:" in backend_dockerfile
    assert f'BACKEND_PYTHON_VERSION: "{PYTHON_VERSION}"' in backend_workflow
    assert f'FRONTEND_PYTHON_VERSION: "{PYTHON_VERSION}"' in frontend_workflow
    assert f"node:{NODE_VERSION}-bookworm@sha256:" in frontend_dockerfile
    assert f'FRONTEND_NODE_VERSION: "{NODE_VERSION}"' in frontend_workflow
    assert package["engines"]["node"] == NODE_VERSION
    assert package["packageManager"] == f"pnpm@{PNPM_VERSION}"
    assert f"ghcr.io/astral-sh/uv:{UV_VERSION}@sha256:" in backend_dockerfile
    assert f'UV_VERSION: "{UV_VERSION}"' in backend_workflow


def test_packaged_image_jobs_have_no_publish_deploy_or_secret_authority():
    backend = BACKEND_WORKFLOW.read_text(encoding="utf-8")
    frontend = FRONTEND_WORKFLOW.read_text(encoding="utf-8")

    for workflow in (backend, frontend):
        lower = workflow.lower()
        assert "permissions:\n  contents: read" in workflow
        assert "packages: write" not in lower
        assert "id-token: write" not in lower
        assert "docker push" not in lower
        assert "docker compose" not in lower
        assert "secrets." not in lower
        assert "deploy/ai-platform/.env" not in lower
        assert "paths:" not in workflow.split("workflow_dispatch:", 1)[0]

    backend_image = backend.split("  backend-image:", 1)[1].split("  required:", 1)[0]
    frontend_image = frontend.split("  frontend-image:", 1)[1].split("  required:", 1)[
        0
    ]
    assert backend.count("persist-credentials: false") == 2
    assert frontend.count("persist-credentials: false") == 2
    assert (
        frontend.count("ref: ${{ github.event.pull_request.head.sha || github.sha }}")
        == 2
    )
    assert "if:" not in backend_image
    assert "if:" not in frontend_image
    assert "PIP_INDEX_URL" not in backend_image
    assert "PIP_TRUSTED_HOST" not in backend_image
    for image_job in (backend_image, frontend_image):
        assert "IMAGE_SOURCE_HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}" in image_job
        assert "- name: Resolve image source repository" in image_job
        assert 'if [ "$GITHUB_EVENT_NAME" = "pull_request" ]; then' in image_job
        assert '[[ "$IMAGE_SOURCE_HEAD_REPOSITORY" =~ ^[A-Za-z0-9]' in image_job
        assert 'image_source_repository="https://github.com/${IMAGE_SOURCE_HEAD_REPOSITORY}.git"' in image_job
        assert 'printf \'IMAGE_SOURCE_REPOSITORY=%s\\n\' "$image_source_repository" >> "$GITHUB_ENV"' in image_job
        assert "IMAGE_SOURCE_REPOSITORY: https://github.com/${{ github.repository }}.git" not in image_job
        assert "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25" in image_job
        assert "severity: HIGH,CRITICAL" in image_job
        assert "ignore-unfixed: true" in image_job
        assert "exit-code: '1'" in image_job
    assert "packaged_backend_image_id=%s" in backend_image
    assert "packaged_frontend_image_id=%s" in frontend_image
    assert "backend_container_state=" in backend_image
    assert "frontend_container_state=" in frontend_image
    assert "docker logs --tail 80" in backend_image
    assert "docker logs --tail 80" in frontend_image
    assert "backend_redacted_container_log_tail_lines=" in backend_image
    assert "frontend_redacted_container_log_tail_lines=" in frontend_image
    assert "container_log_signal=redacted" in backend_image
    assert "container_log_signal=redacted" in frontend_image
    assert "| sed -E" not in backend_image
    assert "| sed -E" not in frontend_image
    assert "--env AI_PLATFORM_API_UPSTREAM=http://127.0.0.1:8020" in frontend_image
    assert "AI_PLATFORM_API_UPSTREAM=http://api:8020" in (
        ROOT / "frontend" / "web" / "Dockerfile"
    ).read_text(encoding="utf-8")
    assert "http://127.0.0.1:18080/healthz" in frontend_image
    assert 'labels["ai-platform.source-commit"] == os.environ["IMAGE_SOURCE_COMMIT"]' in frontend_image
