from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_backend_dockerfile_overwrites_all_runtime_source_markers():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    marker_block = dockerfile.split("RUN if [ -n \"$APT_MIRROR\" ]", 1)[0]

    for marker_path in (
        "/app/.ai-platform-source-revision",
        "/app/.codex-source-revision",
        "/app/.source-commit",
    ):
        assert marker_path in marker_block

    assert marker_block.count('printf \'%s\\n\' "$AI_PLATFORM_BUILD_COMMIT"') >= 3
    assert "source_tree_commit_sha=commit" in marker_block
    assert "runtime_subject_commit_sha=commit" in marker_block
