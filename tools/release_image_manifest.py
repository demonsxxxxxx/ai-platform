from __future__ import annotations

import argparse
import base64
import binascii
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import quote, urlsplit


SCHEMA_VERSION = "ai-platform.release-image-manifest.v1"
PLATFORM = "linux/amd64"
REPOSITORY = "https://github.com/demonsxxxxxx/ai-platform.git"
WORKFLOW_REPOSITORY = "demonsxxxxxx/ai-platform"
SUBJECTS = {
    "backend": "ghcr.io/demonsxxxxxx/ai-platform-backend",
    "frontend": "ghcr.io/demonsxxxxxx/ai-platform-frontend",
}
DOCKERFILES = {
    "backend": "Dockerfile",
    "frontend": "frontend/web/Dockerfile",
}

_COMMIT = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}")
_WORKFLOW_REF = re.compile(
    r"demonsxxxxxx/ai-platform/\.github/workflows/"
    r"ai-platform-packaging-publish\.yml@refs/heads/main"
)
_SIGNATURE_IDENTITY = re.compile(
    r"https://github\.com/demonsxxxxxx/ai-platform/\.github/workflows/"
    r"ai-platform-packaging-publish\.yml@refs/heads/main"
)
_TOP_KEYS = {"schema_version", "source_commit", "repository", "workflow", "subjects"}
_WORKFLOW_KEYS = {"repository", "workflow_ref", "run_id", "run_attempt", "head_sha"}
_SUBJECT_KEYS = {"role", "platform", "build", "image", "evidence"}
_BUILD_KEYS = {"context", "dockerfile"}
_CONTEXT_KEYS = {"path", "source_commit"}
_DOCKERFILE_KEYS = {"path", "sha256"}
_IMAGE_KEYS = {"subject", "source_tag", "manifest_digest", "immutable_ref"}
_EVIDENCE_KEYS = {"sbom", "provenance", "signature", "scan"}
_SBOM_KEYS = {"format", "ref", "sha256", "unbound_content_sha256"}
_PREASSEMBLY_PROVENANCE_KEYS = {
    "attestation_id",
    "bundle_ref",
    "bundle_sha256",
    "predicate_type",
    "ref",
    "verification_ref",
    "verification_sha256",
}
_PROVENANCE_KEYS = _PREASSEMBLY_PROVENANCE_KEYS | {
    "reverification_ref",
    "reverification_sha256",
}
_SIGNATURE_KEYS = {"identity", "issuer", "ref"}
_SCAN_KEYS = {"blocking_severities", "ref", "result", "scanner", "sha256"}
_ATTESTATION_ID = re.compile(r"[A-Za-z0-9_-]+")
_TRIVY_SEVERITIES = {"UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
_SBOM_NAMESPACE_PLACEHOLDER = (
    f"https://github.com/{WORKFLOW_REPOSITORY}/sbom/content-pending"
)
_BOUND_SBOM_NAMESPACE_PREFIX = (
    f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/"
)
_BOUND_SBOM_NAMESPACE = re.compile(
    rf"{re.escape(_BOUND_SBOM_NAMESPACE_PREFIX)}(?P<run_id>[0-9]+)/"
    r"attempts/(?P<run_attempt>[1-9][0-9]*)/sbom/"
    r"(?P<role>backend|frontend)/(?P<source_commit>[0-9a-f]{40})/"
    r"sha256/(?P<digest>[0-9a-f]{64})/(?P<content_sha256>[0-9a-f]{64})"
)
_SYFT_IMAGE_NAMESPACE = re.compile(r"https://anchore\.com/syft/image/.+")
_SPDX_ID = re.compile(r"SPDXRef-[A-Za-z0-9.-]+")
_SPDX_23_RELATIONSHIP_TYPES = frozenset(
    {
        "AMENDS",
        "ANCESTOR_OF",
        "BUILD_DEPENDENCY_OF",
        "BUILD_TOOL_OF",
        "CONTAINED_BY",
        "CONTAINS",
        "COPY_OF",
        "DATA_FILE_OF",
        "DEPENDENCY_MANIFEST_OF",
        "DEPENDENCY_OF",
        "DEPENDS_ON",
        "DESCENDANT_OF",
        "DESCRIBED_BY",
        "DESCRIBES",
        "DEV_DEPENDENCY_OF",
        "DEV_TOOL_OF",
        "DISTRIBUTION_ARTIFACT",
        "DOCUMENTATION_OF",
        "DYNAMIC_LINK",
        "EXAMPLE_OF",
        "EXPANDED_FROM_ARCHIVE",
        "FILE_ADDED",
        "FILE_DELETED",
        "FILE_MODIFIED",
        "GENERATED_FROM",
        "GENERATES",
        "HAS_PREREQUISITE",
        "METAFILE_OF",
        "OPTIONAL_COMPONENT_OF",
        "OPTIONAL_DEPENDENCY_OF",
        "OTHER",
        "PACKAGE_OF",
        "PATCH_APPLIED",
        "PATCH_FOR",
        "PREREQUISITE_FOR",
        "PROVIDED_DEPENDENCY_OF",
        "REQUIREMENT_DESCRIPTION_FOR",
        "RUNTIME_DEPENDENCY_OF",
        "SPECIFICATION_FOR",
        "STATIC_LINK",
        "TEST_CASE_OF",
        "TEST_DEPENDENCY_OF",
        "TEST_OF",
        "TEST_TOOL_OF",
        "VARIANT_OF",
    }
)
# SPDX 2.3 Table 68 defines these as the reverse spelling of the same edge.
_SPDX_INVERSE_TO_CANONICAL = {
    "CONTAINED_BY": "CONTAINS",
    "DEPENDENCY_OF": "DEPENDS_ON",
    "DESCENDANT_OF": "ANCESTOR_OF",
    "DESCRIBED_BY": "DESCRIBES",
    "GENERATED_FROM": "GENERATES",
    "HAS_PREREQUISITE": "PREREQUISITE_FOR",
}
_SBOM_BINDING_ANNOTATOR = "Tool: ai-platform-release-image-manifest"
_SBOM_BINDING_COMMENT_PREFIX = "ai-platform.sbom-binding.v1:"
_SBOM_BINDING_KEYS = {
    "annotations_present",
    "original_namespace",
    "unbound_content_sha256",
}


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _loads_json(payload: str | bytes, name: str) -> Any:
    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        return json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except _DuplicateJsonKey as exc:
        raise ValueError("json_duplicate_key") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(name) from exc


def _load_json(path: Path, name: str) -> Any:
    return _loads_json(path.read_bytes(), name)


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name}_object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{name}_keys")


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name)
    return value


def _fullmatch(pattern: re.Pattern[str], value: Any, name: str) -> str:
    text = _nonempty_string(value, name)
    if pattern.fullmatch(text) is None:
        raise ValueError(name)
    return text


def _artifact_name(*, source_commit: str, workflow: dict[str, Any], role: str) -> str:
    return (
        f"release-image-subject-{source_commit}-{workflow['run_id']}-"
        f"{workflow['run_attempt']}-{role}"
    )


def _ready_artifact_name(*, source_commit: str, workflow: dict[str, Any]) -> str:
    return (
        f"release-image-evidence-{source_commit}-{workflow['run_id']}-"
        f"{workflow['run_attempt']}"
    )


def _canonical_json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sbom_content_sha256(document: dict[str, Any]) -> str:
    normalized = copy.deepcopy(document)
    normalized["documentNamespace"] = _SBOM_NAMESPACE_PLACEHOLDER
    return _canonical_json_sha256(normalized)


def _sbom_document_namespace(
    *,
    role: str,
    source_commit: str,
    digest: str,
    workflow: dict[str, Any],
    content_sha256: str,
) -> str:
    return (
        f"https://github.com/{WORKFLOW_REPOSITORY}/actions/runs/{workflow['run_id']}/"
        f"attempts/{workflow['run_attempt']}/sbom/{role}/{source_commit}/sha256/"
        f"{digest.removeprefix('sha256:')}/{content_sha256}"
    )


def _spdx_oci_purls(*, subject: str, digest: str) -> tuple[str, str]:
    """Return the two pinned Syft 1.50.0 forms for our linux/amd64 subject."""
    prefix = f"pkg:oci/{quote(subject, safe='')}@{quote(digest, safe='')}?arch="
    return prefix, f"{prefix}amd64"


def _spdx_element_id(element: dict[str, Any]) -> str:
    spdx_id = element.get("SPDXID")
    if not isinstance(spdx_id, str) or _SPDX_ID.fullmatch(spdx_id) is None:
        raise ValueError("sbom_document")
    return spdx_id


def _validate_spdx_graph(document: dict[str, Any]) -> set[str]:
    external_refs = document.get("externalDocumentRefs", [])
    if not isinstance(external_refs, list):
        raise ValueError("sbom_document")
    if external_refs:
        raise ValueError("sbom_graph")

    node_ids = {"SPDXRef-DOCUMENT"}
    file_ids: set[str] = set()
    package_ids: set[str] = set()
    elements: dict[str, list[Any]] = {}
    for collection in ("packages", "files", "snippets"):
        values = document.get(collection, [])
        if not isinstance(values, list):
            raise ValueError("sbom_document")
        if collection == "packages" and not values:
            raise ValueError("sbom_document")
        elements[collection] = values
        for element in values:
            if not isinstance(element, dict):
                raise ValueError("sbom_document")
            spdx_id = _spdx_element_id(element)
            if spdx_id in node_ids:
                raise ValueError("sbom_graph")
            node_ids.add(spdx_id)
            if collection == "packages":
                package_ids.add(spdx_id)
            if collection == "files":
                file_ids.add(spdx_id)

    for package in elements["packages"]:
        if (
            not isinstance(package.get("name"), str)
            or not package["name"]
            or not isinstance(package.get("downloadLocation"), str)
            or not package["downloadLocation"]
        ):
            raise ValueError("sbom_document")
    for file_entry in elements["files"]:
        if (
            not isinstance(file_entry.get("fileName"), str)
            or not file_entry["fileName"]
            or not isinstance(file_entry.get("checksums"), list)
            or not file_entry["checksums"]
        ):
            raise ValueError("sbom_document")
    for snippet in elements["snippets"]:
        source_file = snippet.get("snippetFromFile")
        if not isinstance(source_file, str) or source_file not in file_ids:
            raise ValueError("sbom_graph")

    relationships = document.get("relationships")
    if not isinstance(relationships, list):
        raise ValueError("sbom_document")
    relationship_triples: set[tuple[str, str, str]] = set()
    containment_edges: set[tuple[str, str]] = set()
    for relationship in relationships:
        if not isinstance(relationship, dict) or any(
            not isinstance(relationship.get(key), str) or not relationship[key]
            for key in ("spdxElementId", "relatedSpdxElement", "relationshipType")
        ):
            raise ValueError("sbom_document")
        source = relationship["spdxElementId"]
        target = relationship["relatedSpdxElement"]
        relationship_type = relationship["relationshipType"]
        if relationship_type not in _SPDX_23_RELATIONSHIP_TYPES:
            raise ValueError("sbom_graph")
        canonical_type = _SPDX_INVERSE_TO_CANONICAL.get(relationship_type, relationship_type)
        if canonical_type != relationship_type:
            canonical_source, canonical_target = target, source
        else:
            canonical_source, canonical_target = source, target
        triple = (canonical_source, canonical_target, canonical_type)
        is_permitted_self_dependency = (
            source == target
            and canonical_type == "DEPENDS_ON"
            and source in package_ids
        )
        if (
            source not in node_ids
            or target not in node_ids
            or (source == target and not is_permitted_self_dependency)
            or triple in relationship_triples
        ):
            raise ValueError("sbom_graph")
        if canonical_type == "CONTAINS":
            containment = (canonical_source, canonical_target)
            if (canonical_target, canonical_source) in containment_edges:
                raise ValueError("sbom_graph")
            containment_edges.add(containment)
        relationship_triples.add(triple)
    return node_ids


def _validate_spdx_23_document(document: dict[str, Any]) -> set[str]:
    required = {"SPDXID", "creationInfo", "dataLicense", "name", "spdxVersion"}
    if (
        not required.issubset(document)
        or document.get("spdxVersion") != "SPDX-2.3"
        or document.get("dataLicense") != "CC0-1.0"
        or document.get("SPDXID") != "SPDXRef-DOCUMENT"
        or not isinstance(document.get("name"), str)
        or not document["name"]
    ):
        raise ValueError("sbom_document")

    namespace = document.get("documentNamespace")
    if not isinstance(namespace, str):
        raise ValueError("sbom_document")
    parsed_namespace = urlsplit(namespace)
    if (
        not parsed_namespace.scheme
        or not parsed_namespace.netloc
        or parsed_namespace.fragment
    ):
        raise ValueError("sbom_document")

    creation_info = document.get("creationInfo")
    if not isinstance(creation_info, dict):
        raise ValueError("sbom_document")
    creators = creation_info.get("creators")
    if (
        not isinstance(creators, list)
        or not creators
        or any(not isinstance(creator, str) or not creator for creator in creators)
        or not isinstance(creation_info.get("created"), str)
        or not creation_info["created"]
    ):
        raise ValueError("sbom_document")

    node_ids = _validate_spdx_graph(document)

    if "documentDescribes" in document:
        described = document["documentDescribes"]
        if (
            not isinstance(described, list)
            or not described
            or any(not isinstance(value, str) or not value for value in described)
            or len(set(described)) != len(described)
            or any(value not in node_ids for value in described)
        ):
            raise ValueError("sbom_document")
    annotations = document.get("annotations", [])
    if not isinstance(annotations, list):
        raise ValueError("sbom_document")
    for annotation in annotations:
        if not isinstance(annotation, dict) or any(
            not isinstance(annotation.get(key), str) or not annotation[key]
            for key in ("annotationDate", "annotationType", "annotator", "comment")
        ):
            raise ValueError("sbom_document")
    return node_ids


def _validate_spdx_image_binding(
    document: dict[str, Any],
    *,
    subject: str,
    digest: str,
    require_document_describes: bool,
) -> str:
    _validate_spdx_23_document(document)
    if document["name"] != subject:
        raise ValueError("sbom_subject_binding")

    root_claims = [
        relationship
        for relationship in document["relationships"]
        if relationship.get("relationshipType") in {"DESCRIBES", "DESCRIBED_BY"}
    ]
    if (
        len(root_claims) != 1
        or root_claims[0].get("spdxElementId") != "SPDXRef-DOCUMENT"
        or root_claims[0].get("relationshipType") != "DESCRIBES"
    ):
        raise ValueError("sbom_graph")
    root_id = root_claims[0]["relatedSpdxElement"]
    roots = [package for package in document["packages"] if package["SPDXID"] == root_id]
    if len(roots) != 1 or not root_id.startswith("SPDXRef-DocumentRoot-Image-"):
        raise ValueError("sbom_subject_binding")
    root = roots[0]
    container_roots = [
        package
        for package in document["packages"]
        if package.get("primaryPackagePurpose") == "CONTAINER"
    ]
    if len(container_roots) != 1 or container_roots[0]["SPDXID"] != root_id:
        raise ValueError("sbom_graph")

    if require_document_describes:
        if document.get("documentDescribes") != [root_id]:
            raise ValueError("sbom_subject_binding")
    elif "documentDescribes" in document and document["documentDescribes"] != [root_id]:
        raise ValueError("sbom_subject_binding")

    expected_checksum = [
        {"algorithm": "SHA256", "checksumValue": digest.removeprefix("sha256:")}
    ]
    expected_external_refs = [
        [
            {
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": locator,
            }
        ]
        for locator in _spdx_oci_purls(subject=subject, digest=digest)
    ]
    if (
        root.get("name") != subject
        or root.get("versionInfo") != digest
        or root.get("primaryPackagePurpose") != "CONTAINER"
        or root.get("filesAnalyzed") is not False
        or root.get("downloadLocation") != "NOASSERTION"
        or root.get("checksums") != expected_checksum
        or root.get("externalRefs") not in expected_external_refs
    ):
        raise ValueError("sbom_subject_binding")
    return root_id


def _parse_bound_sbom_namespace(namespace: str) -> re.Match[str] | None:
    if not namespace.startswith(_BOUND_SBOM_NAMESPACE_PREFIX):
        return None
    match = _BOUND_SBOM_NAMESPACE.fullmatch(namespace)
    if match is None:
        raise ValueError("sbom_binding_state")
    return match


def _binding_annotation(
    document: dict[str, Any],
    *,
    annotations_present: bool,
    original_namespace: str,
    unbound_content_sha256: str,
) -> dict[str, Any]:
    record = {
        "annotations_present": annotations_present,
        "original_namespace": original_namespace,
        "unbound_content_sha256": unbound_content_sha256,
    }
    return {
        "annotationDate": document["creationInfo"]["created"],
        "annotationType": "OTHER",
        "annotator": _SBOM_BINDING_ANNOTATOR,
        "comment": _SBOM_BINDING_COMMENT_PREFIX
        + json.dumps(record, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    }


def _parse_binding_annotation(document: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    annotations = document.get("annotations", [])
    matches = [
        (index, annotation)
        for index, annotation in enumerate(annotations)
        if annotation.get("annotator") == _SBOM_BINDING_ANNOTATOR
        or annotation.get("comment", "").startswith(_SBOM_BINDING_COMMENT_PREFIX)
    ]
    if len(matches) != 1:
        raise ValueError("sbom_binding_state")
    index, annotation = matches[0]
    if (
        annotation.get("annotator") != _SBOM_BINDING_ANNOTATOR
        or annotation.get("annotationType") != "OTHER"
        or annotation.get("annotationDate") != document["creationInfo"]["created"]
        or not isinstance(annotation.get("comment"), str)
        or not annotation["comment"].startswith(_SBOM_BINDING_COMMENT_PREFIX)
    ):
        raise ValueError("sbom_binding_state")
    record = _loads_json(
        annotation["comment"].removeprefix(_SBOM_BINDING_COMMENT_PREFIX),
        "sbom_binding_state",
    )
    if not isinstance(record, dict) or set(record) != _SBOM_BINDING_KEYS:
        raise ValueError("sbom_binding_state")
    if not isinstance(record["annotations_present"], bool):
        raise ValueError("sbom_binding_state")
    if (
        not isinstance(record["original_namespace"], str)
        or _SYFT_IMAGE_NAMESPACE.fullmatch(record["original_namespace"]) is None
        or _HEX_SHA256.fullmatch(str(record["unbound_content_sha256"])) is None
    ):
        raise ValueError("sbom_binding_state")
    return index, record


def _validate_unbound_spdx(
    document: dict[str, Any],
    *,
    subject: str,
    digest: str,
) -> tuple[str, str, bool]:
    namespace = document.get("documentNamespace")
    if not isinstance(namespace, str):
        raise ValueError("sbom_document")
    if _parse_bound_sbom_namespace(namespace) is not None:
        raise ValueError("sbom_binding_state")
    if _SYFT_IMAGE_NAMESPACE.fullmatch(namespace) is None:
        raise ValueError("sbom_binding_state")
    if "documentDescribes" in document:
        raise ValueError("sbom_binding_state")
    annotations = document.get("annotations", [])
    if any(
        isinstance(annotation, dict)
        and (
            annotation.get("annotator") == _SBOM_BINDING_ANNOTATOR
            or str(annotation.get("comment", "")).startswith(_SBOM_BINDING_COMMENT_PREFIX)
        )
        for annotation in annotations
    ):
        raise ValueError("sbom_binding_state")
    creators = document.get("creationInfo", {}).get("creators", [])
    if "Tool: syft-1.50.0" not in creators:
        raise ValueError("sbom_binding_state")
    _validate_spdx_image_binding(
        document,
        subject=subject,
        digest=digest,
        require_document_describes=False,
    )
    return _canonical_json_sha256(document), namespace, "annotations" in document


def _validate_bound_spdx(
    document: dict[str, Any],
    *,
    role: str,
    source_commit: str,
    digest: str,
    workflow: dict[str, Any],
    expected_unbound_content_sha256: str,
) -> str:
    namespace = document.get("documentNamespace")
    if not isinstance(namespace, str):
        raise ValueError("sbom_document")
    match = _parse_bound_sbom_namespace(namespace)
    if match is None:
        raise ValueError("sbom_binding_state")
    expected_tuple = {
        "run_id": str(workflow["run_id"]),
        "run_attempt": str(workflow["run_attempt"]),
        "role": role,
        "source_commit": source_commit,
        "digest": digest.removeprefix("sha256:"),
    }
    if any(match.group(key) != value for key, value in expected_tuple.items()):
        raise ValueError("sbom_binding_state")

    subject = SUBJECTS[role]
    root_id = _validate_spdx_image_binding(
        document,
        subject=subject,
        digest=digest,
        require_document_describes=True,
    )
    annotation_index, binding = _parse_binding_annotation(document)
    restored = copy.deepcopy(document)
    restored_annotations = restored["annotations"]
    restored_annotations.pop(annotation_index)
    if binding["annotations_present"]:
        restored["annotations"] = restored_annotations
    elif restored_annotations:
        raise ValueError("sbom_binding_state")
    else:
        restored.pop("annotations")
    restored.pop("documentDescribes")
    restored["documentNamespace"] = binding["original_namespace"]
    unbound_content_sha256, _, _ = _validate_unbound_spdx(
        restored,
        subject=subject,
        digest=digest,
    )
    if (
        binding["unbound_content_sha256"] != expected_unbound_content_sha256
        or unbound_content_sha256 != expected_unbound_content_sha256
    ):
        raise ValueError("sbom_binding_state")

    content_sha256 = _sbom_content_sha256(document)
    if match.group("content_sha256") != content_sha256:
        raise ValueError("sbom_binding_state")
    if namespace != _sbom_document_namespace(
        role=role,
        source_commit=source_commit,
        digest=digest,
        workflow=workflow,
        content_sha256=content_sha256,
    ):
        raise ValueError("sbom_binding_state")
    return root_id


def _require_canonical_expected_roles(expected_roles: Iterable[str] | None) -> None:
    if expected_roles is None:
        return
    supplied = list(expected_roles)
    canonical = list(SUBJECTS)
    if len(supplied) != len(canonical) or sorted(supplied) != sorted(canonical):
        raise ValueError("expected_roles")


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(name)
    return value


def _required_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError(name)
    return value


def _validate_provenance_files(
    *,
    evidence_root: Path,
    role: str,
    expected_subject: str,
    digest: str,
    source_commit: str,
    workflow: dict[str, Any],
    provenance: dict[str, Any],
    require_reverification: bool,
) -> None:
    bundle_path = evidence_root / f"provenance-{role}.bundle.json"
    verification_path = evidence_root / f"provenance-{role}.verified.json"
    reverification_path = evidence_root / f"provenance-{role}.assembly-verified.json"
    if not bundle_path.is_file():
        raise ValueError("provenance_bundle_missing")
    if not verification_path.is_file():
        raise ValueError("provenance_verification_missing")
    if _sha256(bundle_path) != provenance["bundle_sha256"]:
        raise ValueError("provenance_bundle_sha256")
    if _sha256(verification_path) != provenance["verification_sha256"]:
        raise ValueError("provenance_verification_sha256")
    if require_reverification:
        if not reverification_path.is_file():
            raise ValueError("provenance_assembly_reverification_missing")
        if _sha256(reverification_path) != provenance["reverification_sha256"]:
            raise ValueError("provenance_assembly_reverification_sha256")

    bundle = _load_json(bundle_path, "provenance_bundle")
    if not isinstance(bundle, dict) or bundle.get("mediaType") != (
        "application/vnd.dev.sigstore.bundle.v0.3+json"
    ):
        raise ValueError("provenance_bundle")
    envelope = _required_mapping(bundle.get("dsseEnvelope"), "provenance_bundle_envelope")
    if envelope.get("payloadType") != "application/vnd.in-toto+json":
        raise ValueError("provenance_bundle_payload_type")
    signatures = _required_list(envelope.get("signatures"), "provenance_bundle_signatures")
    if not all(
        isinstance(signature, dict)
        and isinstance(signature.get("sig"), str)
        and bool(signature["sig"])
        for signature in signatures
    ):
        raise ValueError("provenance_bundle_signatures")
    _required_mapping(bundle.get("verificationMaterial"), "provenance_bundle_verification_material")
    try:
        decoded_payload = base64.b64decode(envelope.get("payload", ""), validate=True)
        bundle_statement = _loads_json(decoded_payload, "provenance_bundle_payload")
    except _DuplicateJsonKey:
        raise
    except (binascii.Error, TypeError, ValueError) as exc:
        if str(exc) == "json_duplicate_key":
            raise
        raise ValueError("provenance_bundle_payload") from exc
    verified = _load_json(verification_path, "provenance_verification")
    if require_reverification:
        reverified = _load_json(
            reverification_path,
            "provenance_assembly_reverification",
        )
        if reverified != verified:
            raise ValueError("provenance_assembly_reverification")
    entries = _required_list(verified, "provenance_verification")
    if len(entries) != 1:
        raise ValueError("provenance_verification_count")
    entry = _required_mapping(entries[0], "provenance_verification_entry")
    attestation = _required_mapping(entry.get("attestation"), "provenance_attestation")
    if attestation.get("bundle") != bundle:
        raise ValueError("provenance_assembly_reverification_bundle")
    result = _required_mapping(entry.get("verificationResult"), "provenance_verification_result")
    timestamps = _required_list(result.get("verifiedTimestamps"), "provenance_verified_timestamps")
    for item in timestamps:
        if not isinstance(item, dict) or item.get("type") not in {"Tlog", "TimestampAuthority"}:
            raise ValueError("provenance_verified_timestamps")
        if not isinstance(item.get("uri"), str) or not item["uri"]:
            raise ValueError("provenance_verified_timestamps")
        timestamp = item.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError("provenance_verified_timestamps")
        try:
            datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("provenance_verified_timestamps") from exc

    statement = _required_mapping(result.get("statement"), "provenance_statement")
    if statement.get("predicateType") != "https://slsa.dev/provenance/v1":
        raise ValueError("provenance_predicate_type")
    subjects = _required_list(statement.get("subject"), "provenance_subject")
    if len(subjects) != 1:
        raise ValueError("provenance_subject_count")
    statement_subject = _required_mapping(subjects[0], "provenance_subject")
    if statement_subject.get("name") != expected_subject:
        raise ValueError("provenance_subject_name")
    statement_digest = _required_mapping(
        statement_subject.get("digest"),
        "provenance_subject_digest",
    )
    if statement_digest.get("sha256") != digest.removeprefix("sha256:"):
        raise ValueError("provenance_subject_digest")

    signature = _required_mapping(result.get("signature"), "provenance_signature")
    certificate = _required_mapping(signature.get("certificate"), "provenance_certificate")
    identity = (
        "https://github.com/demonsxxxxxx/ai-platform/.github/workflows/"
        "ai-platform-packaging-publish.yml@refs/heads/main"
    )
    if certificate.get("subjectAlternativeName") != identity:
        raise ValueError("provenance_workflow_identity")
    if certificate.get("issuer") != "https://token.actions.githubusercontent.com":
        raise ValueError("provenance_oidc_issuer")
    if certificate.get("runnerEnvironment") != "github-hosted":
        raise ValueError("provenance_runner_environment")
    if certificate.get("sourceRepositoryURI") != "https://github.com/demonsxxxxxx/ai-platform":
        raise ValueError("provenance_repository")
    if certificate.get("sourceRepositoryDigest") != source_commit:
        raise ValueError("provenance_source_commit")
    if certificate.get("sourceRepositoryRef") != "refs/heads/main":
        raise ValueError("provenance_source_ref")
    if certificate.get("buildConfigURI") != identity:
        raise ValueError("provenance_workflow_ref")
    expected_run_uri = (
        "https://github.com/demonsxxxxxx/ai-platform/actions/runs/"
        f"{workflow['run_id']}/attempts/{workflow['run_attempt']}"
    )
    if certificate.get("runInvocationURI") != expected_run_uri:
        raise ValueError("provenance_run_identity")
    if bundle_statement != statement:
        raise ValueError("provenance_bundle_statement")


def _validate_sbom_file(
    *,
    evidence_root: Path,
    role: str,
    expected_sha256: str,
    expected_unbound_content_sha256: str,
    source_commit: str,
    digest: str,
    workflow: dict[str, Any],
) -> None:
    path = evidence_root / f"sbom-{role}.spdx.json"
    if not path.is_file():
        raise ValueError("sbom_missing")
    if _sha256(path) != expected_sha256:
        raise ValueError("sbom_sha256")
    document = _load_json(path, "sbom_document")
    if not isinstance(document, dict):
        raise ValueError("sbom_document")
    _validate_bound_spdx(
        document,
        role=role,
        source_commit=source_commit,
        digest=digest,
        workflow=workflow,
        expected_unbound_content_sha256=expected_unbound_content_sha256,
    )


def _validate_scan_file(
    *,
    evidence_root: Path,
    role: str,
    immutable_ref: str,
    expected_sha256: str,
) -> None:
    path = evidence_root / f"trivy-{role}.json"
    if not path.is_file():
        raise ValueError("scan_missing")
    if _sha256(path) != expected_sha256:
        raise ValueError("scan_sha256")
    report = _load_json(path, "scan_document")
    if not isinstance(report, dict) or report.get("ArtifactName") != immutable_ref:
        raise ValueError("scan_subject")
    if not isinstance(report.get("SchemaVersion"), int) or report["SchemaVersion"] < 1:
        raise ValueError("scan_document")
    results = report.get("Results")
    if not isinstance(results, list):
        raise ValueError("scan_document")
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("scan_document")
        vulnerabilities = result.get("Vulnerabilities", [])
        if not isinstance(vulnerabilities, list):
            raise ValueError("scan_document")
        for vulnerability in vulnerabilities:
            if (
                not isinstance(vulnerability, dict)
                or vulnerability.get("Severity") not in _TRIVY_SEVERITIES
            ):
                raise ValueError("scan_vulnerability_severity")
            if vulnerability["Severity"] in {"HIGH", "CRITICAL"}:
                raise ValueError("scan_blocking_vulnerability")


def validate_subject(
    subject: Any,
    *,
    source_commit: str,
    workflow: dict[str, Any],
    evidence_root: Path | None = None,
    allow_preassembly: bool = False,
) -> dict[str, Any]:
    value = _object(subject, "subject")
    _exact_keys(value, _SUBJECT_KEYS, "subject")

    role = _nonempty_string(value["role"], "role")
    if role not in SUBJECTS:
        raise ValueError("role")
    if value["platform"] != PLATFORM:
        raise ValueError("platform")

    build = _object(value["build"], "build")
    _exact_keys(build, _BUILD_KEYS, "build")
    context = _object(build["context"], "context")
    _exact_keys(context, _CONTEXT_KEYS, "context")
    if context["path"] != ".":
        raise ValueError("context_path")
    if context["source_commit"] != source_commit:
        raise ValueError("context_source_commit")
    dockerfile = _object(build["dockerfile"], "dockerfile")
    _exact_keys(dockerfile, _DOCKERFILE_KEYS, "dockerfile")
    if dockerfile["path"] != DOCKERFILES[role]:
        raise ValueError("dockerfile_path")
    _fullmatch(_HEX_SHA256, dockerfile["sha256"], "dockerfile_sha256")

    image = _object(value["image"], "image")
    _exact_keys(image, _IMAGE_KEYS, "image")
    expected_subject = SUBJECTS[role]
    if image["subject"] != expected_subject:
        raise ValueError("image_subject")
    digest = _fullmatch(_DIGEST, image["manifest_digest"], "manifest_digest")
    if image["source_tag"] != f"{expected_subject}:{source_commit}":
        raise ValueError("source_tag")
    if image["immutable_ref"] != f"{expected_subject}@{digest}":
        raise ValueError("immutable_ref")

    evidence = _object(value["evidence"], "evidence")
    _exact_keys(evidence, _EVIDENCE_KEYS, "evidence")
    sbom = _object(evidence["sbom"], "sbom")
    _exact_keys(sbom, _SBOM_KEYS, "sbom")
    if sbom["format"] != "spdx-json":
        raise ValueError("sbom_format")
    if sbom["ref"] != f"oci://{expected_subject}@{digest}#sbom-spdx-attestation":
        raise ValueError("sbom_ref")
    sbom_sha256 = _fullmatch(_HEX_SHA256, sbom["sha256"], "sbom_sha256")
    unbound_content_sha256 = _fullmatch(
        _HEX_SHA256,
        sbom["unbound_content_sha256"],
        "sbom_unbound_content_sha256",
    )
    if evidence_root is not None:
        _validate_sbom_file(
            evidence_root=evidence_root,
            role=role,
            expected_sha256=sbom_sha256,
            expected_unbound_content_sha256=unbound_content_sha256,
            source_commit=source_commit,
            digest=digest,
            workflow=workflow,
        )

    provenance = _object(evidence["provenance"], "provenance")
    _exact_keys(
        provenance,
        _PREASSEMBLY_PROVENANCE_KEYS if allow_preassembly else _PROVENANCE_KEYS,
        "provenance",
    )
    if provenance["predicate_type"] != "https://slsa.dev/provenance/v1":
        raise ValueError("provenance_predicate_type")
    attestation_id = _fullmatch(
        _ATTESTATION_ID,
        provenance["attestation_id"],
        "provenance_attestation_id",
    )
    if provenance["ref"] != (
        f"https://github.com/{WORKFLOW_REPOSITORY}/attestations/{attestation_id}"
    ):
        raise ValueError("provenance_ref")
    artifact_name = _artifact_name(
        source_commit=source_commit,
        workflow=workflow,
        role=role,
    )
    if provenance["bundle_ref"] != (
        f"github-artifact://{artifact_name}/provenance-{role}.bundle.json"
    ):
        raise ValueError("provenance_bundle_ref")
    _fullmatch(_HEX_SHA256, provenance["bundle_sha256"], "provenance_bundle_sha256")
    if provenance["verification_ref"] != (
        f"github-artifact://{artifact_name}/provenance-{role}.verified.json"
    ):
        raise ValueError("provenance_verification_ref")
    _fullmatch(
        _HEX_SHA256,
        provenance["verification_sha256"],
        "provenance_verification_sha256",
    )
    if not allow_preassembly:
        ready_artifact_name = _ready_artifact_name(
            source_commit=source_commit,
            workflow=workflow,
        )
        if provenance["reverification_ref"] != (
            f"github-artifact://{ready_artifact_name}/"
            f"provenance-{role}.assembly-verified.json"
        ):
            raise ValueError("provenance_assembly_reverification_ref")
        _fullmatch(
            _HEX_SHA256,
            provenance["reverification_sha256"],
            "provenance_assembly_reverification_sha256",
        )
    if evidence_root is not None:
        _validate_provenance_files(
            evidence_root=evidence_root,
            role=role,
            expected_subject=expected_subject,
            digest=digest,
            source_commit=source_commit,
            workflow=workflow,
            provenance=provenance,
            require_reverification=not allow_preassembly,
        )

    signature = _object(evidence["signature"], "signature")
    _exact_keys(signature, _SIGNATURE_KEYS, "signature")
    if _SIGNATURE_IDENTITY.fullmatch(_nonempty_string(signature["identity"], "signature_identity")) is None:
        raise ValueError("signature_identity")
    if signature["issuer"] != "https://token.actions.githubusercontent.com":
        raise ValueError("signature_issuer")
    if signature["ref"] != f"oci://{expected_subject}@{digest}#cosign-keyless-signature":
        raise ValueError("signature_ref")

    scan = _object(evidence["scan"], "scan")
    _exact_keys(scan, _SCAN_KEYS, "scan")
    if scan["blocking_severities"] != ["HIGH", "CRITICAL"]:
        raise ValueError("blocking_severities")
    if scan["result"] != "passed":
        raise ValueError("scan_result")
    if scan["scanner"] != "trivy@0.70.0":
        raise ValueError("scan_scanner")
    if scan["ref"] != f"github-artifact://{artifact_name}/trivy-{role}.json":
        raise ValueError("scan_ref")
    scan_sha256 = _fullmatch(_HEX_SHA256, scan["sha256"], "scan_sha256")
    if evidence_root is not None:
        _validate_scan_file(
            evidence_root=evidence_root,
            role=role,
            immutable_ref=image["immutable_ref"],
            expected_sha256=scan_sha256,
        )
    return value


def validate_manifest(
    manifest: Any,
    *,
    expected_roles: Iterable[str] | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    _require_canonical_expected_roles(expected_roles)
    value = _object(manifest, "manifest")
    _exact_keys(value, _TOP_KEYS, "manifest")
    if value["schema_version"] != SCHEMA_VERSION:
        raise ValueError("schema_version")
    source_commit = _fullmatch(_COMMIT, value["source_commit"], "source_commit")
    if value["repository"] != REPOSITORY:
        raise ValueError("repository")

    workflow = _object(value["workflow"], "workflow")
    _exact_keys(workflow, _WORKFLOW_KEYS, "workflow")
    if workflow["repository"] != WORKFLOW_REPOSITORY:
        raise ValueError("workflow_repository")
    _fullmatch(_WORKFLOW_REF, workflow["workflow_ref"], "workflow_ref")
    if not isinstance(workflow["run_id"], str) or not workflow["run_id"].isdigit():
        raise ValueError("workflow_run_id")
    if not isinstance(workflow["run_attempt"], int) or isinstance(workflow["run_attempt"], bool) or workflow["run_attempt"] < 1:
        raise ValueError("workflow_run_attempt")
    if workflow["head_sha"] != source_commit:
        raise ValueError("workflow_head_sha")

    subjects = value["subjects"]
    if not isinstance(subjects, list):
        raise ValueError("subjects_array")
    roles = [subject.get("role") if isinstance(subject, dict) else None for subject in subjects]
    if len(roles) != len(SUBJECTS) or sorted(roles, key=str) != sorted(SUBJECTS):
        raise ValueError("subject_roles")
    for subject in subjects:
        validate_subject(
            subject,
            source_commit=source_commit,
            workflow=workflow,
            evidence_root=evidence_root,
        )
    return value


def assemble_manifest(
    *,
    source_commit: str,
    repository: str,
    workflow: dict[str, Any],
    subjects: Iterable[dict[str, Any]],
    expected_roles: Iterable[str] | None = None,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    _require_canonical_expected_roles(expected_roles)
    assembled_subjects = [copy.deepcopy(subject) for subject in subjects]
    roles = [
        subject.get("role") if isinstance(subject, dict) else None
        for subject in assembled_subjects
    ]
    if len(roles) != len(SUBJECTS) or sorted(roles, key=str) != sorted(SUBJECTS):
        raise ValueError("subject_roles")
    if evidence_root is not None:
        ready_artifact_name = _ready_artifact_name(
            source_commit=source_commit,
            workflow=workflow,
        )
        for subject in assembled_subjects:
            role = _nonempty_string(subject.get("role"), "role")
            if role not in SUBJECTS:
                raise ValueError("role")
            path = evidence_root / f"provenance-{role}.assembly-verified.json"
            if not path.is_file():
                raise ValueError("provenance_assembly_reverification_missing")
            provenance = _required_mapping(
                _required_mapping(subject.get("evidence"), "evidence").get("provenance"),
                "provenance",
            )
            provenance["reverification_ref"] = (
                f"github-artifact://{ready_artifact_name}/"
                f"provenance-{role}.assembly-verified.json"
            )
            provenance["reverification_sha256"] = _sha256(path)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "repository": repository,
        "workflow": workflow,
        "subjects": sorted(
            assembled_subjects,
            key=lambda subject: str(subject.get("role", "")),
        ),
    }
    validate_manifest(
        manifest,
        expected_roles=expected_roles,
        evidence_root=evidence_root,
    )
    return manifest


def _load_object(path: Path) -> dict[str, Any]:
    payload = _load_json(path, str(path))
    return _object(payload, str(path))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _subject_command(args: argparse.Namespace) -> None:
    dockerfile_path = Path(args.dockerfile)
    if _sha256(dockerfile_path) != args.dockerfile_sha256:
        raise ValueError("dockerfile_sha256_mismatch")
    subject = SUBJECTS.get(args.role)
    if subject is None:
        raise ValueError("role")
    workflow = {
        "repository": args.workflow_repository,
        "workflow_ref": args.workflow_ref,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "head_sha": args.source_commit,
    }
    bundle_path = Path(args.provenance_bundle)
    verification_path = Path(args.provenance_verification)
    sbom_path = Path(args.sbom_file)
    scan_path = Path(args.scan_file)
    if bundle_path.name != f"provenance-{args.role}.bundle.json":
        raise ValueError("provenance_bundle_path")
    if verification_path.name != f"provenance-{args.role}.verified.json":
        raise ValueError("provenance_verification_path")
    if sbom_path.name != f"sbom-{args.role}.spdx.json":
        raise ValueError("sbom_path")
    if scan_path.name != f"trivy-{args.role}.json":
        raise ValueError("scan_path")
    evidence_roots = {
        path.parent.resolve()
        for path in (bundle_path, verification_path, sbom_path, scan_path)
    }
    if len(evidence_roots) != 1:
        raise ValueError("provenance_evidence_root")
    artifact_name = _artifact_name(
        source_commit=args.source_commit,
        workflow=workflow,
        role=args.role,
    )
    record = {
        "role": args.role,
        "platform": PLATFORM,
        "build": {
            "context": {"path": ".", "source_commit": args.source_commit},
            "dockerfile": {"path": args.dockerfile, "sha256": args.dockerfile_sha256},
        },
        "image": {
            "subject": subject,
            "source_tag": f"{subject}:{args.source_commit}",
            "manifest_digest": args.manifest_digest,
            "immutable_ref": f"{subject}@{args.manifest_digest}",
        },
        "evidence": {
            "sbom": {
                "format": "spdx-json",
                "ref": args.sbom_ref,
                "sha256": _sha256(sbom_path),
                "unbound_content_sha256": args.sbom_unbound_content_sha256,
            },
            "provenance": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "attestation_id": args.provenance_attestation_id,
                "ref": args.provenance_ref,
                "bundle_ref": (
                    f"github-artifact://{artifact_name}/provenance-{args.role}.bundle.json"
                ),
                "bundle_sha256": _sha256(bundle_path),
                "verification_ref": (
                    f"github-artifact://{artifact_name}/provenance-{args.role}.verified.json"
                ),
                "verification_sha256": _sha256(verification_path),
            },
            "signature": {
                "identity": args.signature_identity,
                "issuer": "https://token.actions.githubusercontent.com",
                "ref": args.signature_ref,
            },
            "scan": {
                "blocking_severities": ["HIGH", "CRITICAL"],
                "ref": args.scan_ref,
                "result": "passed",
                "scanner": "trivy@0.70.0",
                "sha256": _sha256(scan_path),
            },
        },
    }
    validate_subject(
        record,
        source_commit=args.source_commit,
        workflow=workflow,
        evidence_root=bundle_path.parent,
        allow_preassembly=True,
    )
    _write_json(Path(args.output), record)


def _spdx_source_hash_command(args: argparse.Namespace) -> None:
    if args.role not in SUBJECTS:
        raise ValueError("role")
    digest = _fullmatch(_DIGEST, args.manifest_digest, "manifest_digest")
    subject = SUBJECTS[args.role]
    if args.image_ref != f"{subject}@{digest}":
        raise ValueError("sbom_subject_binding")
    path = Path(args.sbom_file)
    if path.name != f"sbom-{args.role}.spdx.json":
        raise ValueError("sbom_path")
    document = _load_object(path)
    unbound_content_sha256, _, _ = _validate_unbound_spdx(
        document,
        subject=subject,
        digest=digest,
    )
    print(unbound_content_sha256)


def _bind_spdx_command(args: argparse.Namespace) -> None:
    if args.role not in SUBJECTS:
        raise ValueError("role")
    source_commit = _fullmatch(_COMMIT, args.source_commit, "source_commit")
    digest = _fullmatch(_DIGEST, args.manifest_digest, "manifest_digest")
    subject = SUBJECTS[args.role]
    if args.image_ref != f"{subject}@{digest}":
        raise ValueError("sbom_subject_binding")
    if not isinstance(args.workflow_run_id, str) or not args.workflow_run_id.isdigit():
        raise ValueError("workflow_run_id")
    if args.workflow_run_attempt < 1:
        raise ValueError("workflow_run_attempt")
    workflow = {
        "run_id": args.workflow_run_id,
        "run_attempt": args.workflow_run_attempt,
    }
    expected_unbound_content_sha256 = _fullmatch(
        _HEX_SHA256,
        args.unbound_content_sha256,
        "sbom_unbound_content_sha256",
    )
    path = Path(args.sbom_file)
    if path.name != f"sbom-{args.role}.spdx.json":
        raise ValueError("sbom_path")
    document = _load_object(path)
    namespace = document.get("documentNamespace")
    if not isinstance(namespace, str):
        raise ValueError("sbom_document")
    if _parse_bound_sbom_namespace(namespace) is not None:
        _validate_bound_spdx(
            document,
            role=args.role,
            source_commit=source_commit,
            digest=digest,
            workflow=workflow,
            expected_unbound_content_sha256=expected_unbound_content_sha256,
        )
        return

    unbound_content_sha256, original_namespace, annotations_present = (
        _validate_unbound_spdx(
            document,
            subject=subject,
            digest=digest,
        )
    )
    if unbound_content_sha256 != expected_unbound_content_sha256:
        raise ValueError("sbom_binding_state")
    root_id = _validate_spdx_image_binding(
        document,
        subject=subject,
        digest=digest,
        require_document_describes=False,
    )
    binding_annotation = _binding_annotation(
        document,
        annotations_present=annotations_present,
        original_namespace=original_namespace,
        unbound_content_sha256=unbound_content_sha256,
    )
    document.setdefault("annotations", []).append(binding_annotation)
    document["documentDescribes"] = [root_id]
    content_sha256 = _sbom_content_sha256(document)
    document["documentNamespace"] = _sbom_document_namespace(
        role=args.role,
        source_commit=source_commit,
        digest=digest,
        workflow=workflow,
        content_sha256=content_sha256,
    )
    _validate_spdx_image_binding(
        document,
        subject=subject,
        digest=digest,
        require_document_describes=True,
    )
    _validate_bound_spdx(
        document,
        role=args.role,
        source_commit=source_commit,
        digest=digest,
        workflow=workflow,
        expected_unbound_content_sha256=expected_unbound_content_sha256,
    )
    _write_json(path, document)


def _subject_target_command(args: argparse.Namespace) -> None:
    workflow = {
        "repository": args.workflow_repository,
        "workflow_ref": args.workflow_ref,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "head_sha": args.source_commit,
    }
    record_path = Path(args.subject_record)
    record = validate_subject(
        _load_object(record_path),
        source_commit=args.source_commit,
        workflow=workflow,
        evidence_root=record_path.parent,
        allow_preassembly=True,
    )
    if record["role"] != args.expected_role:
        raise ValueError("role")
    print(f"oci://{record['image']['immutable_ref']}")


def _add_expected_roles(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-role", action="append", required=True, choices=sorted(SUBJECTS))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and verify immutable release image manifests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subject = subparsers.add_parser("subject", help="Create one digest-bound subject record.")
    subject.add_argument("--role", required=True, choices=sorted(SUBJECTS))
    subject.add_argument("--source-commit", required=True)
    subject.add_argument("--dockerfile", required=True)
    subject.add_argument("--dockerfile-sha256", required=True)
    subject.add_argument("--manifest-digest", required=True)
    subject.add_argument("--workflow-repository", required=True)
    subject.add_argument("--workflow-ref", required=True)
    subject.add_argument("--run-id", required=True)
    subject.add_argument("--run-attempt", required=True, type=int)
    subject.add_argument("--sbom-ref", required=True)
    subject.add_argument("--sbom-file", required=True)
    subject.add_argument("--sbom-unbound-content-sha256", required=True)
    subject.add_argument("--provenance-attestation-id", required=True)
    subject.add_argument("--provenance-ref", required=True)
    subject.add_argument("--provenance-bundle", required=True)
    subject.add_argument("--provenance-verification", required=True)
    subject.add_argument("--signature-identity", required=True)
    subject.add_argument("--signature-ref", required=True)
    subject.add_argument("--scan-ref", required=True)
    subject.add_argument("--scan-file", required=True)
    subject.add_argument("--output", required=True)

    bind_spdx = subparsers.add_parser(
        "bind-spdx",
        help="Bind a generated SPDX document to its immutable image subject.",
    )
    bind_spdx.add_argument("--role", required=True, choices=sorted(SUBJECTS))
    bind_spdx.add_argument("--source-commit", required=True)
    bind_spdx.add_argument("--manifest-digest", required=True)
    bind_spdx.add_argument("--image-ref", required=True)
    bind_spdx.add_argument("--workflow-run-id", required=True)
    bind_spdx.add_argument("--workflow-run-attempt", required=True, type=int)
    bind_spdx.add_argument("--unbound-content-sha256", required=True)
    bind_spdx.add_argument("--sbom-file", required=True)

    spdx_source_hash = subparsers.add_parser(
        "spdx-source-hash",
        help="Validate and hash one unbound pinned-Syft image SPDX document.",
    )
    spdx_source_hash.add_argument("--role", required=True, choices=sorted(SUBJECTS))
    spdx_source_hash.add_argument("--manifest-digest", required=True)
    spdx_source_hash.add_argument("--image-ref", required=True)
    spdx_source_hash.add_argument("--sbom-file", required=True)

    target = subparsers.add_parser(
        "subject-target",
        help="Validate a preassembly subject record and print its immutable OCI target.",
    )
    target.add_argument("--subject-record", required=True)
    target.add_argument("--source-commit", required=True)
    target.add_argument("--workflow-repository", required=True)
    target.add_argument("--workflow-ref", required=True)
    target.add_argument("--run-id", required=True)
    target.add_argument("--run-attempt", required=True, type=int)
    target.add_argument("--expected-role", required=True, choices=sorted(SUBJECTS))

    assemble = subparsers.add_parser("assemble", help="Assemble all required subject records.")
    assemble.add_argument("--source-commit", required=True)
    assemble.add_argument("--repository", required=True)
    assemble.add_argument("--workflow-repository", required=True)
    assemble.add_argument("--workflow-ref", required=True)
    assemble.add_argument("--run-id", required=True)
    assemble.add_argument("--run-attempt", required=True, type=int)
    assemble.add_argument("--subject-record", action="append", required=True)
    assemble.add_argument("--evidence-root", required=True)
    _add_expected_roles(assemble)
    assemble.add_argument("--output", required=True)

    verify = subparsers.add_parser("verify", help="Verify a complete ready manifest.")
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--evidence-root", required=True)
    _add_expected_roles(verify)

    args = parser.parse_args()
    if args.command == "subject":
        _subject_command(args)
        return 0
    if args.command == "bind-spdx":
        _bind_spdx_command(args)
        return 0
    if args.command == "spdx-source-hash":
        _spdx_source_hash_command(args)
        return 0
    if args.command == "subject-target":
        _subject_target_command(args)
        return 0
    if args.command == "assemble":
        manifest = assemble_manifest(
            source_commit=args.source_commit,
            repository=args.repository,
            workflow={
                "repository": args.workflow_repository,
                "workflow_ref": args.workflow_ref,
                "run_id": args.run_id,
                "run_attempt": args.run_attempt,
                "head_sha": args.source_commit,
            },
            subjects=[_load_object(Path(path)) for path in args.subject_record],
            expected_roles=args.expected_role,
            evidence_root=Path(args.evidence_root),
        )
        _write_json(Path(args.output), manifest)
        return 0
    validate_manifest(
        _load_object(Path(args.manifest)),
        expected_roles=args.expected_role,
        evidence_root=Path(args.evidence_root),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
