"""Assemble runtime-only Compose files from a qualified image manifest."""
from __future__ import annotations

import argparse
import copy
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import time

if __package__:
    from .release_image_manifest import validate_manifest
else:
    from release_image_manifest import validate_manifest


EVIDENCE_FILES = (
    "subject-{role}.json",
    "sbom-{role}.spdx.json",
    "trivy-{role}.json",
    "trivy-inventory-{role}.json",
    "cosign-signature-{role}.json",
    "cosign-sbom-{role}.json",
    "provenance-{role}.bundle.json",
    "provenance-{role}.verified.json",
    "provenance-{role}.assembly-verified.json",
)

# Assemble the internal-test package from the same qualified image manifest.
PACKAGE_OMITTED_ENV_KEYS = {
    "SANDBOX_CONTAINER_PROVIDER", "SANDBOX_SECURITY_PROFILE",
    "SANDBOX_EGRESS_POLICY_ENABLED", "SANDBOX_EGRESS_PROOF_SIGNING_KEY",
    "SANDBOX_EGRESS_PROOF_KEY_ID", "SANDBOX_EGRESS_PROOF_PREVIOUS_KEYS_JSON",
    "OPENSANDBOX_USE_SERVER_PROXY", "OPENSANDBOX_EXPECTED_NETWORK_MODE",
    "OPENSANDBOX_EGRESS_BRIDGE", "OPENSANDBOX_EGRESS_SUBNET", "OPENSANDBOX_EGRESS_PROXY_IPV4",
    "OPENAI_BASE_URL", "OPENAI_API_KEY",
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
    "DOCKER_SOCKET_GID",
}

DATA_IMAGES = {
    "postgres": "postgres:16-alpine",
    "redis": "redis:7-alpine",
    "minio": "bitnamilegacy/minio:2025.4.22-debian-12-r1",
}
DATA_IMAGE_PULL_ATTEMPTS = 3
# Keep retries within the former single-pull timeout for each image.
DATA_IMAGE_PULL_BUDGET_SECONDS = 600


def _validate_inventory_report(payload: bytes, subject: dict) -> None:
    """Bind the informational HIGH/CRITICAL inventory without clearing its findings."""
    report = json.loads(payload)
    immutable_ref = subject["image"]["immutable_ref"]
    if not isinstance(report, dict) or report.get("ArtifactName") != immutable_ref:
        raise ValueError("inventory subject does not match manifest")
    if report.get("SchemaVersion") != 2 or report.get("ArtifactType") != "container_image":
        raise ValueError("invalid inventory report metadata")
    metadata = report.get("Metadata")
    if not isinstance(metadata, dict):
        raise ValueError("invalid inventory image metadata")
    if "RepoDigests" in metadata:
        digests = metadata["RepoDigests"]
        if (
            not isinstance(digests, list)
            or not all(isinstance(digest, str) for digest in digests)
            or immutable_ref not in digests
        ):
            raise ValueError("inventory repository digest does not match manifest")
    if "ImageConfig" in metadata:
        config = metadata["ImageConfig"]
        if not isinstance(config, dict):
            raise ValueError("invalid inventory image configuration")
        expected_os, expected_architecture = subject["platform"].split("/")
        for key, expected in (("os", expected_os), ("architecture", expected_architecture)):
            if key in config and config[key] != expected:
                raise ValueError("inventory platform does not match manifest")
    results = report.get("Results")
    if not isinstance(results, list):
        raise ValueError("invalid inventory results")
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("invalid inventory result")
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            raise ValueError("invalid inventory vulnerabilities")
        for vulnerability in vulnerabilities:
            if not isinstance(vulnerability, dict) or vulnerability.get("Severity") not in {"HIGH", "CRITICAL"}:
                raise ValueError("invalid inventory vulnerability severity")
    # Findings, including unfixed vulnerabilities, are retained verbatim. Only
    # the separately validated trivy-{role}.json determines the fixable gate.


def pin_data_images() -> dict[str, str]:
    """CI resolves the approved base tags once before the deployment archive is made."""
    result = {}
    for service, tag in DATA_IMAGES.items():
        pull = ["docker", "pull", "--platform", "linux/amd64", tag]
        deadline = time.monotonic() + DATA_IMAGE_PULL_BUDGET_SECONDS
        for attempt in range(1, DATA_IMAGE_PULL_ATTEMPTS + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(pull, DATA_IMAGE_PULL_BUDGET_SECONDS)
            try:
                subprocess.run(pull, check=True, timeout=remaining)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == DATA_IMAGE_PULL_ATTEMPTS:
                    raise
                delay = 5 * attempt
                if deadline - time.monotonic() <= delay:
                    raise
                print(f"Retrying {service} image pull ({attempt + 1}/{DATA_IMAGE_PULL_ATTEMPTS})", flush=True)
                time.sleep(delay)
        record = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], text=True, timeout=30))[0]
        result[service] = record["RepoDigests"][0]
    return result


def build_package(
    source: Path, manifest: dict, output: Path,
    data_images: dict[str, str], *, evidence_root: Path,
) -> None:
    if set(data_images) != set(DATA_IMAGES):
        raise ValueError("three data-service digests are required")
    for service, ref in data_images.items():
        repository = ref.split("@", 1)[0].removeprefix("docker.io/").removeprefix("library/")
        if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", ref) or repository != DATA_IMAGES[service].rsplit(":", 1)[0]:
            raise ValueError("data-service image must bind the approved repository and a digest")
    manifest = validate_manifest(manifest, expected_roles=("backend", "frontend"))
    evidence_payloads = {}
    # Only explicit qualification outputs belong in a distributable package.
    # Never recursively include the working directory or operator configuration.
    for subject in manifest["subjects"]:
        role = subject["role"]
        for template in EVIDENCE_FILES:
            name = template.format(role=role)
            path = evidence_root / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"release evidence is not a regular file: {name}")
            payload = path.read_bytes()
            if not payload.strip():
                raise ValueError(f"release evidence is empty: {name}")
            evidence_payloads[f"release-evidence/{name}"] = payload
        # Assembly adds only the fresh provenance reverification to this record.
        published_subject = copy.deepcopy(subject)
        provenance = published_subject["evidence"]["provenance"]
        provenance.pop("reverification_ref")
        provenance.pop("reverification_sha256")
        if json.loads(evidence_payloads[f"release-evidence/subject-{role}.json"]) != published_subject:
            raise ValueError(f"release evidence subject does not match manifest: {role}")
        _validate_inventory_report(
            evidence_payloads[f"release-evidence/trivy-inventory-{role}.json"], subject,
        )
    validate_manifest(manifest, expected_roles=("backend", "frontend"), evidence_root=evidence_root)
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
        "compose.override.yaml": "docker-compose.opensandbox-internal-test.yml",
        "compose.profile-drive-ca.yaml": "docker-compose.profile-drive-ca.yml",
        ".env.example": ".env.example",
        "deploy.py": "deploy.py",
        "README.md": "README.internal-test.md",
        "BACKUP-RESTORE.md": "BACKUP-RESTORE.md",
        "opensandbox-egress-nginx.conf.template": "opensandbox-egress-nginx.conf.template",
    }
    payloads = {}
    for name, original in files.items():
        path = deployment / original
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"package source is not a regular file: {original}")
        text = path.read_text(encoding="utf-8")
        if name == ".env.example":
            text, count = re.subn(
                r"(?m)^SANDBOX_WORKSPACE_ROOT=.*$",
                "SANDBOX_WORKSPACE_ROOT=/data/opensandbox/workspaces/ai-platform-internal-test",
                text,
            )
            if count != 1:
                raise ValueError("package environment must contain one workspace root")
            for key in ("OPENSANDBOX_BASE_URL", "SANDBOX_CALLBACK_BASE_URL"):
                text, count = re.subn(rf"(?m)^{key}=.*$", f"{key}=", text)
                if count != 1:
                    raise ValueError(f"package environment must contain one {key}")
            omitted = PACKAGE_OMITTED_ENV_KEYS | bindings.keys()
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
    payloads.update(evidence_payloads)
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-images", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    args = parser.parse_args()
    build_package(args.source, json.loads(args.manifest.read_text(encoding="utf-8")), args.output,
                  json.loads(args.data_images.read_text(encoding="utf-8")), evidence_root=args.evidence_root)


if __name__ == "__main__":
    main()
