import base64

import json





from app.skills.release_readiness import build_skill_version_release_review


def _passed_review_manifest(
    skill_id: str,
    evidence_files: dict[str, list[str]],
    *,
    sbom_reviewed=True,
    license_policy_reviewed=True,
    vulnerability_reviewed=True,
) -> str:
    return json.dumps(
        {
            "schema_version": "ai-platform.skill-release-review.v1",
            "status": "passed",
            "skill_id": skill_id,
            "reviewer": "release-admin",
            "reviewed_at": "2026-06-08T10:00:00Z",
            "sbom_reviewed": sbom_reviewed,
            "license_policy_reviewed": license_policy_reviewed,
            "vulnerability_reviewed": vulnerability_reviewed,
            "evidence_files": evidence_files,
        },
        ensure_ascii=False,
    )


def _skill_version(
    *,
    skill_id: str = "general-chat",
    version: str = "hash-reviewed",
    content_hash: str = "hash-reviewed",
    files: list[dict] | None = None,
) -> dict:
    return {
        "skill_id": skill_id,
        "version": version,
        "content_hash": content_hash,
        "description": "Reviewed Skill version.",
        "source": {
            "kind": "uploaded",
            "files": files
            or [
                {
                    "relative_path": "SKILL.md",
                    "content_base64": "c2tpbGw=",
                    "size_bytes": 5,
                }
            ],
        },
        "dependency_ids": [],
        "status": "active",
        "created_by": "dev-admin",
        "created_at": None,
    }


def _source_file(relative_path: str, text: str) -> dict:
    content = text.encode("utf-8")
    return {
        "relative_path": relative_path,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "size_bytes": len(content),
    }


def test_skill_version_release_review_rejects_missing_or_pending_evidence(tmp_path):
    missing_review = build_skill_version_release_review(
        _skill_version(),
        skill_release_evidence_root=tmp_path / "release-evidence" / "skill-release",
    )

    assert missing_review["schema_version"] == "ai-platform.skill-version-release-review.v1"
    assert missing_review["status"] == "blocked"
    assert missing_review["skill_id"] == "general-chat"
    assert missing_review["version"] == "hash-reviewed"
    assert missing_review["content_hash"] == "hash-reviewed"
    assert "signed_package_or_sbom_evidence_missing" in missing_review["blockers"]
    assert "dependency_license_policy_evidence_missing" in missing_review["blockers"]
    assert "dependency_vulnerability_evidence_missing" in missing_review["blockers"]

    evidence_root = tmp_path / "release-evidence" / "skill-release"
    skill_evidence_dir = evidence_root / "general-chat"
    skill_evidence_dir.mkdir(parents=True)
    skill_evidence_dir.joinpath("sbom.json").write_text(
        json.dumps({"status": "pending_review"}, ensure_ascii=False),
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("third-party-notices.txt").write_text(
        "Status: pending_review\nOperator action: set license_policy_reviewed=true.\n",
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("vulnerability-report.json").write_text(
        json.dumps({"review_required": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("ai-platform-skill-release-review.json").write_text(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": ["external-release-evidence/general-chat/sbom.json"],
                "license_policy": ["external-release-evidence/general-chat/third-party-notices.txt"],
                "vulnerability_scan": ["external-release-evidence/general-chat/vulnerability-report.json"],
            },
        ),
        encoding="utf-8",
    )

    pending_review = build_skill_version_release_review(
        _skill_version(),
        skill_release_evidence_root=evidence_root,
    )

    assert pending_review["status"] == "blocked"
    assert pending_review["release_review"]["status"] == "invalid_or_incomplete"
    assert "sbom_or_signed_package_evidence_not_reviewed" in pending_review["release_review"][
        "evidence_file_errors"
    ]
    assert "dependency_license_policy_review_not_verified" in pending_review["blockers"]


def test_skill_version_release_review_handles_malformed_source_file_base64_fail_closed(tmp_path):
    review = json.loads(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": ["evidence/sbom.json"],
                "license_policy": ["legal/LICENSE"],
                "vulnerability_scan": ["security/vulnerability-report.json"],
            },
        )
    )
    review["skill_content_hash"] = "hash-reviewed"
    files = [
        _source_file("SKILL.md", "# general-chat\n"),
        {
            "relative_path": "evidence/sbom.json",
            "content_base64": "not valid base64",
            "size_bytes": 16,
        },
        _source_file("legal/LICENSE", "Reviewed license policy evidence."),
        _source_file("security/vulnerability-report.json", '{"status": "reviewed"}'),
        _source_file("ai-platform-skill-release-review.json", json.dumps(review, ensure_ascii=False)),
    ]

    release_review = build_skill_version_release_review(
        _skill_version(files=files),
        skill_release_evidence_root=tmp_path / "release-evidence" / "skill-release",
    )

    assert release_review["status"] == "blocked"
    assert release_review["release_review"]["status"] == "invalid_or_incomplete"
    assert "sbom_or_signed_package_evidence_file_unreadable" in release_review["release_review"][
        "evidence_file_errors"
    ]
    assert "signed_package_or_sbom_review_not_verified" in release_review["blockers"]


def test_skill_version_release_review_requires_matching_target_content_hash(tmp_path):
    evidence_root = tmp_path / "release-evidence" / "skill-release"
    skill_evidence_dir = evidence_root / "general-chat"
    skill_evidence_dir.mkdir(parents=True)
    skill_evidence_dir.joinpath("sbom.json").write_text("{}", encoding="utf-8")
    skill_evidence_dir.joinpath("third-party-notices.txt").write_text(
        "reviewed license text",
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("vulnerability-report.json").write_text("{}", encoding="utf-8")
    review = json.loads(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": ["external-release-evidence/general-chat/sbom.json"],
                "license_policy": ["external-release-evidence/general-chat/third-party-notices.txt"],
                "vulnerability_scan": ["external-release-evidence/general-chat/vulnerability-report.json"],
            },
        )
    )
    review["skill_content_hash"] = "different-hash"
    skill_evidence_dir.joinpath("ai-platform-skill-release-review.json").write_text(
        json.dumps(review, ensure_ascii=False),
        encoding="utf-8",
    )

    release_review = build_skill_version_release_review(
        _skill_version(content_hash="hash-reviewed"),
        skill_release_evidence_root=evidence_root,
    )

    assert release_review["status"] == "blocked"
    assert release_review["release_review"]["status"] == "invalid_or_incomplete"
    assert "review_content_hash_mismatch" in release_review["release_review"]["review_flag_errors"]
    assert "signed_package_or_sbom_review_not_verified" in release_review["blockers"]


def test_skill_version_release_review_accepts_reviewed_uploaded_source_files_with_external_evidence(tmp_path):
    evidence_root = tmp_path / "release-evidence" / "skill-release"
    skill_evidence_dir = evidence_root / "general-chat"
    skill_evidence_dir.mkdir(parents=True)
    skill_evidence_dir.joinpath("evidence").mkdir()
    skill_evidence_dir.joinpath("legal").mkdir()
    skill_evidence_dir.joinpath("security").mkdir()
    skill_evidence_dir.joinpath("evidence/sbom.json").write_text(
        json.dumps(
            {
                "schema_version": "ai-platform.skill-release-source-sbom.v1",
                "metadata": {"component": {"name": "general-chat", "version": "hash-reviewed"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("legal/LICENSE").write_text(
        "Reviewed license policy evidence.",
        encoding="utf-8",
    )
    skill_evidence_dir.joinpath("security/vulnerability-report.json").write_text(
        json.dumps({"status": "reviewed", "finding_count": 0, "findings": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    review = json.loads(
        _passed_review_manifest(
            "general-chat",
            {
                "sbom_or_signed_package": [
                    "external-release-evidence/general-chat/evidence/sbom.json"
                ],
                "license_policy": ["external-release-evidence/general-chat/legal/LICENSE"],
                "vulnerability_scan": [
                    "external-release-evidence/general-chat/security/vulnerability-report.json"
                ],
            },
        )
    )
    review["skill_content_hash"] = "hash-reviewed"
    skill_evidence_dir.joinpath("ai-platform-skill-release-review.json").write_text(
        json.dumps(review, ensure_ascii=False),
        encoding="utf-8",
    )

    release_review = build_skill_version_release_review(
        _skill_version(),
        skill_release_evidence_root=evidence_root,
    )

    assert release_review == {
        "schema_version": "ai-platform.skill-version-release-review.v1",
        "status": "passed",
        "skill_id": "general-chat",
        "version": "hash-reviewed",
        "content_hash": "hash-reviewed",
        "package_evidence": {
            "metadata_files": [],
            "requirements_files": [],
            "sbom_files": ["sbom.json"],
            "signed_package_evidence_files": [],
            "license_files": ["LICENSE"],
            "vulnerability_evidence_files": ["vulnerability-report.json"],
        },
        "release_review": {
            "status": "passed",
            "files": ["ai-platform-skill-release-review.json"],
            "evidence_files_verified": True,
            "review_flag_errors": [],
            "sbom_reviewed": True,
            "license_policy_reviewed": True,
            "vulnerability_reviewed": True,
            "content_hash_verified": True,
        },
        "blockers": [],
        "does_not_close_g6": True,
    }
