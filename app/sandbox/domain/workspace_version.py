"""Private continuation policy; deliberately separate from artifact delivery."""
from pathlib import PurePosixPath

from app.sandbox.domain.workspace_policy import WORKSPACE_RUNTIME_PRIVATE_ROOTS


class WorkspaceVersionError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def workspace_version_exclusion(relative_path: str, *, skill_names: set[str]) -> str | None:
    parts = PurePosixPath(relative_path).parts
    lowered = tuple(part.casefold() for part in parts)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return "unsafe_path"
    if lowered[0] in {"inputs", "context"}:
        # context/<id> is also used for original callback-staged files/artifacts.
        return "authorized_inputs"
    if any(part in (WORKSPACE_RUNTIME_PRIVATE_ROOTS - {".tmp"}) for part in lowered) or lowered[0] in {
        "runtime", "_audit", "_debug", ".native-skill-tmp",
    }:
        return "runtime_private"
    if any(part in {".ssh", ".aws", ".azure", ".kube", ".config"} for part in lowered):
        return "credentials"
    if any(part == ".env" or part.startswith(".env.") or part in {
        "credentials.json", "credentials", ".credentials.json", "auth.json",
        "id_rsa", "id_ed25519", ".netrc", ".npmrc", ".pypirc",
    } for part in lowered):
        return "credentials"
    if lowered[0] == ".tmp" and lowered[-1] in {"settings.json", "settings.local.json"}:
        return "runtime_private"
    if len(parts) == 1 and lowered[0] in {
        "claude.md", "claude.local.md", ".ai-platform-opensandbox-lease.json",
        "run-state.json", "step-event.json", "step-response.json",
    }:
        return "runtime_private"
    if ".claude" in lowered[1:]:
        return "skill_configuration"
    if lowered[0] == ".claude":
        if parts[0] != ".claude":
            return "skill_configuration"
        if len(parts) == 1 or (len(parts) == 2 and parts[1] == "skills"):
            return None
        if len(parts) >= 3 and parts[1] == "skills" and parts[2] in skill_names:
            if len(parts) == 3 or (len(parts) >= 4 and parts[3] == "output"):
                return None
        return "skill_configuration"
    return None


def authorized_workspace_skill_names(request: object) -> set[str]:
    names: set[str] = set()
    for subject in getattr(request, "tool_policy_subjects", ()):
        if not isinstance(subject, dict):
            continue
        declared = subject.get("declared_identities")
        allowed = subject.get("allowed_skill_names")
        if (subject.get("identity") == "Skill"
            and all(subject.get(key) is True for key in ("registered", "declared", "active", "distributed"))
            and isinstance(declared, list) and "Skill" in declared and isinstance(allowed, list)):
            for name in allowed:
                if isinstance(name, str) and name and "/" not in name and "\\" not in name and name not in {".", ".."}:
                    names.add(name)
    return names
