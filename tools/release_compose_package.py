"""Assemble runtime-only Compose files from a qualified image manifest."""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

if __package__:
    from .release_image_manifest import validate_manifest
else:
    from release_image_manifest import validate_manifest


PROFILES = {
    "internal-test": "docker-compose.opensandbox-internal-test.yml",
    "production": "docker-compose.opensandbox.yml",
}

# Fixed by the selected Compose profile, or used only by source/legacy tools.
PACKAGE_OMITTED_ENV_KEYS = {
    "SANDBOX_CONTAINER_PROVIDER",
    "SANDBOX_EGRESS_POLICY_ENABLED", "OPENSANDBOX_USE_SERVER_PROXY",
    "DOCKER_SOCKET_GID",
}

DATA_IMAGES = {
    "postgres": "postgres:16-alpine",
    "redis": "redis:7-alpine",
    "minio": "quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z",
}


def pin_data_images() -> dict[str, str]:
    """CI resolves the approved base tags once, before either archive is made."""
    result = {}
    for service, tag in DATA_IMAGES.items():
        subprocess.run(["docker", "pull", "--platform", "linux/amd64", tag], check=True, timeout=600)
        record = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True, timeout=30))[0]
        result[service] = record["RepoDigests"][0]
    return result


def build_package(source: Path, manifest: dict, profile: str, output: Path, data_images: dict[str, str]) -> None:
    if set(data_images) != set(DATA_IMAGES):
        raise ValueError("three data-service digests are required")
    for service, ref in data_images.items():
        repository = ref.split("@", 1)[0].removeprefix("docker.io/").removeprefix("library/")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", ref) or repository != DATA_IMAGES[service].rsplit(":", 1)[0]:
            raise ValueError("data-service image must bind the approved repository and a digest")
    manifest = validate_manifest(manifest, expected_roles=("backend", "frontend"))
    overlay = PROFILES[profile]
    images = {subject["role"]: subject["image"] for subject in manifest["subjects"]}
    bindings = {
        "AI_PLATFORM_IMAGE": images["backend"]["immutable_ref"],
        "AI_PLATFORM_FRONTEND_IMAGE": images["frontend"]["immutable_ref"],
        "AI_PLATFORM_SOURCE_COMMIT": manifest["source_commit"],
        "OPENSANDBOX_EXECUTOR_IMAGE": images["backend"]["immutable_ref"],
        "OPENSANDBOX_EXECUTOR_IMAGE_DIGEST": images["backend"]["manifest_digest"],
    }
    deployment = source / "deploy" / "ai-platform"
    files = {
        "compose.yaml": "docker-compose.yml",
        "compose.override.yaml": overlay,
        ".env.example": ".env.example",
        "deploy.py": "deploy.py",
        "README.md": "README.md",
    }
    if profile == "production":
        files["opensandbox-egress-nginx.conf.template"] = "opensandbox-egress-nginx.conf.template"
    payloads = {}
    for name, original in files.items():
        path = deployment / original
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"package source is not a regular file: {original}")
        text = path.read_text(encoding="utf-8")
        if name == ".env.example":
            omitted = PACKAGE_OMITTED_ENV_KEYS | bindings.keys()
            if profile == "production":
                omitted = omitted | {"OPENSANDBOX_EGRESS_PROXY_URL"}
            # Remove each assignment and its directly attached explanation.
            for key in sorted(omitted):
                text = re.sub(rf"(?m)(?:^#[^\n]*\n)*^{key}=[^\n]*(?:\n|$)", "", text)
        if name.endswith(".yaml"):
            for key, value in bindings.items():
                text = text.replace("${" + key + ":?set " + key + "}", value)
                text = text.replace("${" + key + ":-}", value)
            if any("${" + key in text for key in bindings):
                raise ValueError("unresolved release image binding")
        if name == "deploy.py":
            for marker, value in {
                "@@SOURCE_COMMIT@@": manifest["source_commit"],
                "@@BACKEND_IMAGE@@": images["backend"]["immutable_ref"],
                "@@FRONTEND_IMAGE@@": images["frontend"]["immutable_ref"],
            }.items():
                text = text.replace(marker, value)
        if name == "compose.yaml":
            # Preserve the existing project and its named volumes on upgrades.
            text = "name: ai-platform-internal\n" + text
            for service, tag in DATA_IMAGES.items():
                original = f"    image: {tag}\n"
                if text.count(original) != 1:
                    raise ValueError("data-service source image changed; update its release input")
                text = text.replace(original, f"    image: {data_images[service]}\n")
        payloads[name] = text.encode("utf-8")
    payloads["release-image-manifest.json"] = (
        json.dumps(manifest, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    # Assemble completely before creating the output; never overwrite an artifact.
    with output.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, payload in payloads.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(payload)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(payload))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-images", type=Path, required=True)
    args = parser.parse_args()
    build_package(args.source, json.loads(args.manifest.read_text(encoding="utf-8")), args.profile, args.output,
                  json.loads(args.data_images.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
