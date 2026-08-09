import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import trivy_failure_evidence


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "trivy_failure_evidence.py"
SOURCE_COMMIT = "a" * 40
MANIFEST_DIGEST = "sha256:" + "b" * 64
SUBJECT = "ghcr.io/demonsxxxxxx/ai-platform-backend"
IMAGE_REF = f"{SUBJECT}@{MANIFEST_DIGEST}"


def _valid_report() -> dict[str, object]:
    return {
        "SchemaVersion": 2,
        "CreatedAt": "2026-08-08T00:00:00Z",
        "ArtifactName": IMAGE_REF,
        "ArtifactType": "container_image",
        "Metadata": {
            "ImageID": "sha256:" + "c" * 64,
            "RepoDigests": [IMAGE_REF],
        },
        "Results": [
            {
                "Target": "wolfi",
                "Class": "os-pkgs",
                "Type": "wolfi",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2026-0001",
                        "PkgName": "example",
                        "InstalledVersion": "1.0",
                        "FixedVersion": "1.1",
                        "Status": "fixed",
                        "Severity": "CRITICAL",
                        "PrimaryURL": "https://example.invalid/CVE-2026-0001",
                    }
                ],
            }
        ],
    }


def _run_capture(
    tmp_path: Path,
    *,
    report: object | None = None,
    raw: bytes | None = None,
    arguments: dict[str, str] | None = None,
    output_name: str = "trivy-failure-diagnostic-backend.json",
) -> subprocess.CompletedProcess[str]:
    scan_name = "trivy-backend.json"
    if raw is None:
        raw = json.dumps(_valid_report() if report is None else report).encode("utf-8")
    (tmp_path / scan_name).write_bytes(raw)
    values = {
        "role": "backend",
        "source-commit": SOURCE_COMMIT,
        "github-sha": SOURCE_COMMIT,
        "workflow-repository": "demonsxxxxxx/ai-platform",
        "workflow-ref": (
            "demonsxxxxxx/ai-platform/.github/workflows/"
            "ai-platform-packaging-publish.yml@refs/heads/main"
        ),
        "run-id": "31233605951",
        "run-attempt": "1",
        "manifest-digest": MANIFEST_DIGEST,
        "image-ref": IMAGE_REF,
        "scan-file": scan_name,
        "output": output_name,
    }
    if arguments:
        values.update(arguments)
    command = [sys.executable, str(TOOL), "capture"]
    for key, value in values.items():
        command.extend((f"--{key}", value))
    return subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
    )


def test_capture_emits_only_bounded_redacted_untrusted_evidence(tmp_path: Path):
    completed = _run_capture(tmp_path)
    assert completed.returncode == 0, completed.stderr

    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert set(evidence) == {
        "authority",
        "image_ref",
        "manifest_digest",
        "platform",
        "reason_code",
        "role",
        "run_attempt",
        "run_id",
        "schema_version",
        "source_commit",
        "trivy_policy",
        "trivy_report",
        "workflow_ref",
        "workflow_repository",
    }
    assert evidence["authority"] == "untrusted_failure_diagnostic"
    assert evidence["reason_code"] == "trivy_blocking_findings"
    assert evidence["trivy_report"]["blocking_vulnerability_count"] == 1
    serialized = json.dumps(evidence, sort_keys=True)
    assert "CVE-2026-0001" not in serialized
    assert "example.invalid" not in serialized
    assert "InstalledVersion" not in serialized


def test_capture_accepts_real_trivy_multiline_description_without_projecting_it(
    tmp_path: Path,
):
    report = _valid_report()
    description = "Vendor\tadvisory paragraph.\n" * 20 + "Final advisory paragraph."
    vulnerability = report["Results"][0]["Vulnerabilities"][0]
    vulnerability["Description"] = description
    vulnerability["Fingerprint"] = "sha256:" + "d" * 64

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode == 0, completed.stderr
    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    serialized = output.read_text(encoding="utf-8")
    assert description not in serialized
    assert "Final advisory paragraph." not in serialized


def test_capture_accepts_trivy_image_history_created_by_tab_without_projecting_it(
    tmp_path: Path,
):
    report = _valid_report()
    created_by = "/bin/sh\t-c echo exact-image-history"
    report["Metadata"]["ImageConfig"] = {"history": [{"created_by": created_by}]}

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode == 0, completed.stderr
    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    serialized = output.read_text(encoding="utf-8")
    assert created_by not in serialized
    assert "exact-image-history" not in serialized


@pytest.mark.parametrize("location", ["cvss", "metadata", "trivy"])
def test_capture_rejects_multiline_description_outside_direct_vulnerability_field(
    tmp_path: Path, location: str
):
    report = _valid_report()
    unsafe_value = f"nested {location} line one\nnested {location} line two"
    vulnerability = report["Results"][0]["Vulnerabilities"][0]
    if location == "cvss":
        vulnerability["CVSS"] = {"Description": unsafe_value}
    elif location == "metadata":
        report["Metadata"]["ImageConfig"] = {"Description": unsafe_value}
    else:
        report["Trivy"] = {"Description": unsafe_value}

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_string"
    assert unsafe_value not in completed.stdout
    assert unsafe_value not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    "location",
    [
        "key",
        "title",
        "target",
        "cvss_description",
        "history_comment",
        "metadata_description",
        "trivy_description",
        "history_wrong_depth",
        "history_alias",
        "history_wrong_root",
        "vulnerability_alias",
    ],
)
def test_capture_rejects_tab_outside_exact_trivy_projection_paths(
    tmp_path: Path, location: str
):
    report = _valid_report()
    unsafe_value = f"{location}\tmust-not-be-admitted"
    vulnerability = report["Results"][0]["Vulnerabilities"][0]
    if location == "key":
        report["Metadata"][unsafe_value] = "benign-value"
    elif location == "title":
        vulnerability["Title"] = unsafe_value
    elif location == "target":
        report["Results"][0]["Target"] = unsafe_value
    elif location == "cvss_description":
        vulnerability["CVSS"] = {"Description": unsafe_value}
    elif location == "history_comment":
        report["Metadata"]["ImageConfig"] = {"history": [{"comment": unsafe_value}]}
    elif location == "metadata_description":
        report["Metadata"]["ImageConfig"] = {"Description": unsafe_value}
    elif location == "trivy_description":
        report["Trivy"] = {"Description": unsafe_value}
    elif location == "history_wrong_depth":
        report["Metadata"]["ImageConfig"] = {"history": {"created_by": unsafe_value}}
    elif location == "history_alias":
        report["Metadata"]["ImageConfig"] = {"history": [{"CreatedBy": unsafe_value}]}
    elif location == "history_wrong_root":
        report["Metadata"]["history"] = [{"created_by": unsafe_value}]
    else:
        vulnerability["description"] = unsafe_value

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_string"
    assert unsafe_value not in completed.stdout
    assert unsafe_value not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


_OTHER_CONTROL_CHARACTERS = tuple(
    chr(codepoint)
    for start, stop in ((0x00, 0x20), (0x7F, 0xA0))
    for codepoint in range(start, stop)
    if codepoint not in {0x09, 0x0A}
)


@pytest.mark.parametrize("character", _OTHER_CONTROL_CHARACTERS)
def test_capture_rejects_every_other_cc_in_direct_vulnerability_description(
    tmp_path: Path, character: str
):
    report = _valid_report()
    unsafe_value = f"before{character}after"
    report["Results"][0]["Vulnerabilities"][0]["Description"] = unsafe_value

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_string"
    assert unsafe_value not in completed.stdout
    assert unsafe_value not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


def test_capture_rejects_secret_like_image_history_created_by(tmp_path: Path):
    report = _valid_report()
    unsafe_value = "authorization: bearer opaque-sensitive-value"
    report["Metadata"]["ImageConfig"] = {"history": [{"created_by": unsafe_value}]}

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_string"
    assert unsafe_value not in completed.stdout
    assert unsafe_value not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    "fingerprint",
    [
        7,
        "sha512:" + "d" * 64,
        "sha256:" + "D" * 64,
        "sha256:" + "d" * 63,
        "sha256:" + "d" * 65,
    ],
)
def test_capture_rejects_malformed_trivy_fingerprint(tmp_path: Path, fingerprint: object):
    report = _valid_report()
    report["Results"][0]["Vulnerabilities"][0]["Fingerprint"] = fingerprint

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_vulnerability"
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    ("field", "unsafe_value"),
    [
        ("Description", "line one\rline two"),
        ("Description", "line one\u0000line two"),
        ("Description", "line one\u202eline two"),
        ("Description", "authorization: bearer opaque-sensitive-value"),
        ("Description", "x" * 8_193),
        ("Target", "wolfi\nforged-target"),
    ],
)
def test_capture_rejects_adjacent_multiline_description_hostiles(
    tmp_path: Path, field: str, unsafe_value: str
):
    report = _valid_report()
    if field == "Description":
        report["Results"][0]["Vulnerabilities"][0][field] = unsafe_value
    else:
        report["Results"][0][field] = unsafe_value

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_string"
    assert unsafe_value not in completed.stdout
    assert unsafe_value not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    ("case", "mutate"),
    [
        ("unknown_top_key", lambda report: report.update({"Token": "must-not-leak"})),
        ("wrong_schema", lambda report: report.update({"SchemaVersion": 1})),
        ("wrong_results_type", lambda report: report.update({"Results": {}})),
        ("too_many_results", lambda report: report.update({"Results": [{}] * 257})),
        (
            "secret_like_nested_key",
            lambda report: report["Metadata"].update({"authorization": "must-not-leak"}),
        ),
        (
            "control_character",
            lambda report: report["Results"][0].update({"Target": "bad\u0000target"}),
        ),
        (
            "excessive_string",
            lambda report: report["Results"][0].update({"Target": "x" * 8193}),
        ),
    ],
)
def test_capture_rejects_closed_world_bounds_without_leaking_input(
    tmp_path: Path, case: str, mutate
):
    report = _valid_report()
    mutate(report)
    completed = _run_capture(tmp_path, report=report)
    assert completed.returncode != 0, case
    assert "must-not-leak" not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


def test_capture_rejects_oversize_deep_duplicate_and_malformed_json(tmp_path: Path):
    hostile = {
        "oversize": b" " * (4 * 1024 * 1024 + 1),
        "deep": b"\n" + b"[" * 33 + b"0" + b"]" * 33,
        "duplicate": (
            b'{"SchemaVersion":2,"CreatedAt":"2026-08-08T00:00:00Z",'
            + b'"ArtifactName":"'
            + IMAGE_REF.encode()
            + b'","ArtifactType":"container_image","Metadata":{},'
            + b'"Results":[{"Target":"a","Target":"b"}]}'
        ),
        "malformed": b'{"SchemaVersion":',
    }
    for case, raw in hostile.items():
        case_root = tmp_path / case
        case_root.mkdir()
        completed = _run_capture(case_root, raw=raw)
        assert completed.returncode != 0, case
        assert not (case_root / "trivy-failure-diagnostic-backend.json").exists()


def test_capture_rejects_package_inventory_with_secret_and_unknown_fields(tmp_path: Path):
    report = _valid_report()
    report["Results"][0]["Packages"] = [
        {"accessToken": "opaque-value", "UnexpectedField": "accepted"}
    ]

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert "opaque-value" not in completed.stdout
    assert "opaque-value" not in completed.stderr
    assert "accessToken" not in completed.stderr
    assert "UnexpectedField" not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize("key", ["accessToken", "access_token", "ACCESS_TOKEN"])
def test_capture_rejects_normalized_secret_key_variants(tmp_path: Path, key: str):
    report = _valid_report()
    report["Metadata"][key] = "opaque-value"

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_key"
    assert "opaque-value" not in completed.stdout
    assert "opaque-value" not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    "key",
    [
        "secret_value",
        "api_secret_value",
        "value_secret",
        "token-value",
        "api-token-value",
        "value-token",
        "password_hash",
        "client_password_hash",
        "hash_password",
        "ApiSecretValue",
        "APISecretValue",
        "HTTPAccessTokenHeader",
    ],
)
def test_capture_rejects_secret_tokens_at_every_key_position(tmp_path: Path, key: str):
    report = _valid_report()
    report["Results"][0]["Vulnerabilities"][0]["CVSS"] = {
        key: "opaque-sensitive-value"
    }

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_key"
    assert key not in completed.stdout
    assert key not in completed.stderr
    assert "opaque-sensitive-value" not in completed.stdout
    assert "opaque-sensitive-value" not in completed.stderr
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    "key",
    ["secretary", "secretariat", "tokenizer", "tokenization", "passwordless"],
)
def test_capture_allows_nonsecret_near_match_key_tokens(tmp_path: Path, key: str):
    report = _valid_report()
    report["Results"][0]["Vulnerabilities"][0]["CVSS"] = {key: "benign-value"}

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode == 0, completed.stderr
    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    serialized = output.read_text(encoding="utf-8")
    assert key not in serialized
    assert "benign-value" not in serialized


def test_capture_rejects_all_nonempty_package_inventory(tmp_path: Path):
    report = _valid_report()
    report["Results"][0]["Packages"] = [{"Name": "example"}]

    completed = _run_capture(tmp_path, report=report)

    assert completed.returncode != 0
    assert completed.stderr.strip() == "trivy_diagnostic_packages"
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        {"role": "worker"},
        {"source-commit": "c" * 40},
        {"github-sha": "c" * 40},
        {"workflow-repository": "attacker/repo"},
        {
            "workflow-ref": (
                "demonsxxxxxx/ai-platform/.github/workflows/evil.yml@refs/heads/main"
            )
        },
        {"run-id": "0"},
        {"run-attempt": "not-a-number"},
        {"manifest-digest": "sha256:" + "d" * 64},
        {"image-ref": "ghcr.io/demonsxxxxxx/ai-platform-backend:latest"},
        {"scan-file": "../trivy-backend.json"},
    ],
)
def test_capture_rejects_identity_and_path_drift(tmp_path: Path, arguments: dict[str, str]):
    completed = _run_capture(tmp_path, arguments=arguments)
    assert completed.returncode != 0
    assert not (tmp_path / "trivy-failure-diagnostic-backend.json").exists()


def test_capture_refuses_accepted_evidence_and_existing_output_names(tmp_path: Path):
    accepted = _run_capture(tmp_path, output_name="subject-backend.json")
    assert accepted.returncode != 0
    assert not (tmp_path / "subject-backend.json").exists()

    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    output.write_text("preserve-me", encoding="utf-8")
    collision = _run_capture(tmp_path)
    assert collision.returncode != 0
    assert output.read_text(encoding="utf-8") == "preserve-me"


def test_bounded_read_rejects_a_synchronized_stat_open_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scan = tmp_path / "trivy-backend.json"
    original = json.dumps(_valid_report()).encode("utf-8")
    replacement = json.dumps({**_valid_report(), "ReportID": "replacement"}).encode("utf-8")
    scan.write_bytes(original)
    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_bytes(replacement)
    displaced = tmp_path / "displaced.json"
    real_lstat = os.lstat
    replaced = False

    def replace_after_stat(path, *args, **kwargs):
        nonlocal replaced
        result = real_lstat(path, *args, **kwargs)
        if Path(path) == scan and not replaced:
            scan.replace(displaced)
            replacement_path.replace(scan)
            replaced = True
        return result

    monkeypatch.setattr(trivy_failure_evidence.os, "lstat", replace_after_stat)
    with pytest.raises(ValueError, match="trivy_diagnostic_path"):
        trivy_failure_evidence._bounded_read(scan)
    assert replaced


def test_bounded_read_uses_descriptor_safety_flags_and_rechecks_fstat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scan = tmp_path / "trivy-backend.json"
    expected = json.dumps(_valid_report()).encode("utf-8")
    scan.write_bytes(expected)
    real_open = os.open
    real_fstat = os.fstat
    opened_flags: list[int] = []
    fstat_calls = 0

    def observe_open(path, flags, *args, **kwargs):
        opened_flags.append(flags)
        return real_open(path, flags, *args, **kwargs)

    def observe_fstat(descriptor: int):
        nonlocal fstat_calls
        fstat_calls += 1
        return real_fstat(descriptor)

    monkeypatch.setattr(trivy_failure_evidence.os, "open", observe_open)
    monkeypatch.setattr(trivy_failure_evidence.os, "fstat", observe_fstat)
    assert trivy_failure_evidence._bounded_read(scan) == expected
    assert len(opened_flags) == 1
    for flag_name in ("O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, flag_name, 0)
        if flag:
            assert opened_flags[0] & flag
    assert fstat_calls >= 2


def test_regular_identity_rejects_a_windows_reparse_attribute():
    value = SimpleNamespace(
        st_dev=1,
        st_ino=2,
        st_mode=stat.S_IFREG | 0o600,
        st_size=3,
        st_mtime_ns=4,
        st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
    )
    with pytest.raises(ValueError, match="trivy_diagnostic_path"):
        trivy_failure_evidence._regular_identity(value, "trivy_diagnostic_path")


def test_bounded_read_rejects_path_replacement_after_descriptor_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    scan = tmp_path / "trivy-backend.json"
    scan.write_bytes(json.dumps(_valid_report()).encode("utf-8"))
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b"replacement-identity")
    real_read = os.read
    real_lstat = os.lstat
    replaced = False

    def replace_during_read(descriptor: int, length: int) -> bytes:
        nonlocal replaced
        replaced = True
        return real_read(descriptor, length)

    def observe_replacement(path, *args, **kwargs):
        if replaced and Path(path) == scan:
            return real_lstat(replacement, *args, **kwargs)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(trivy_failure_evidence.os, "read", replace_during_read)
    monkeypatch.setattr(trivy_failure_evidence.os, "lstat", observe_replacement)
    with pytest.raises(ValueError, match="trivy_diagnostic_path"):
        trivy_failure_evidence._bounded_read(scan)
    assert replaced


def test_failed_exclusive_write_never_path_unlinks_a_possible_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    unlink_attempted = False

    def fail_fsync(_: int) -> None:
        raise OSError("synchronized write failure")

    def reject_path_unlink(self: Path, *args, **kwargs) -> None:
        nonlocal unlink_attempted
        unlink_attempted = True
        raise AssertionError(f"must not unlink by pathname after descriptor failure: {self}")

    monkeypatch.setattr(trivy_failure_evidence.os, "fsync", fail_fsync)
    monkeypatch.setattr(Path, "unlink", reject_path_unlink)
    with pytest.raises((OSError, ValueError)):
        trivy_failure_evidence._write_exclusive(output, {"authority": "untrusted"})
    assert not unlink_attempted


@pytest.mark.skipif(os.name == "nt", reason="POSIX replacement semantics")
def test_failed_exclusive_write_leaves_a_replacement_directory_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    output = tmp_path / "trivy-failure-diagnostic-backend.json"
    displaced = tmp_path / "created-by-capture"

    def replace_then_fail(_: int) -> None:
        output.replace(displaced)
        output.write_text("replacement-must-survive", encoding="utf-8")
        raise OSError("synchronized replacement")

    monkeypatch.setattr(trivy_failure_evidence.os, "fsync", replace_then_fail)
    with pytest.raises((OSError, ValueError)):
        trivy_failure_evidence._write_exclusive(output, {"authority": "untrusted"})
    assert output.read_text(encoding="utf-8") == "replacement-must-survive"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO and O_NONBLOCK semantics")
def test_capture_rejects_a_fifo_without_blocking(tmp_path: Path):
    scan = tmp_path / "trivy-backend.json"
    os.mkfifo(scan)
    command = [
        sys.executable,
        str(TOOL),
        "capture",
        "--role",
        "backend",
        "--source-commit",
        SOURCE_COMMIT,
        "--github-sha",
        SOURCE_COMMIT,
        "--workflow-repository",
        "demonsxxxxxx/ai-platform",
        "--workflow-ref",
        "demonsxxxxxx/ai-platform/.github/workflows/ai-platform-packaging-publish.yml@refs/heads/main",
        "--run-id",
        "31233605951",
        "--run-attempt",
        "1",
        "--manifest-digest",
        MANIFEST_DIGEST,
        "--image-ref",
        IMAGE_REF,
        "--scan-file",
        scan.name,
        "--output",
        "trivy-failure-diagnostic-backend.json",
    ]
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )
    assert completed.returncode != 0
    assert "trivy_diagnostic_path" in completed.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_bounded_read_rejects_a_symlink(tmp_path: Path):
    target = tmp_path / "target.json"
    target.write_bytes(json.dumps(_valid_report()).encode("utf-8"))
    scan = tmp_path / "trivy-backend.json"
    scan.symlink_to(target)
    with pytest.raises(ValueError, match="trivy_diagnostic_path"):
        trivy_failure_evidence._bounded_read(scan)
