"""Identity-owned DingTalk department scope; names never grant access."""

from dataclasses import dataclass
from typing import Mapping


def is_department_id(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 10 and value[0] != "0"
            and value.isascii() and value.isdecimal())


@dataclass(frozen=True)
class DepartmentAuthority:
    direct_department_ids: tuple[str, ...] = ()
    department_ids: tuple[str, ...] = ()
    department_authority_status: str = "unavailable"

    def principal_fields(self) -> dict[str, object]:
        return {
            "department_id": self.direct_department_ids[0] if self.direct_department_ids else "",
            "direct_department_ids": self.direct_department_ids,
            "department_ids": self.department_ids,
            "department_authority_status": self.department_authority_status,
        }


def resolve_department_scope(direct_ids: list[str], parents: Mapping[str, str | None]) -> DepartmentAuthority:
    """Reject the whole membership if any path is missing, cyclic, or invalid."""
    if not direct_ids or len(direct_ids) > 128 or any(not is_department_id(item) for item in direct_ids):
        raise ValueError("department_authority_invalid")
    effective: set[str] = set()
    for direct_id in direct_ids:
        path: set[str] = set()
        current: str | None = direct_id
        while current is not None:
            if not is_department_id(current) or current not in parents or current in path or len(path) >= 12:
                raise ValueError("department_authority_invalid")
            path.add(current)
            current = parents[current]
        effective.update(path)
    return DepartmentAuthority(tuple(sorted(set(direct_ids))), tuple(sorted(effective)), "available")


def department_authority_from_snapshot(snapshot: Mapping[str, object]) -> DepartmentAuthority:
    fields = ("direct_department_ids", "department_ids", "department_authority_status")
    if not any(name in snapshot for name in fields):
        return DepartmentAuthority()
    direct = snapshot.get(fields[0])
    effective = snapshot.get(fields[1])
    status = snapshot.get(fields[2])
    if (not isinstance(direct, (list, tuple)) or not isinstance(effective, (list, tuple))
        or not isinstance(status, str) or status not in {"available", "unavailable"}
        or len(direct) > 128 or len(effective) > 1536
        or any(not is_department_id(item) for item in (*direct, *effective))
        or len(direct) != len(set(direct)) or len(effective) != len(set(effective))
        or not set(direct).issubset(effective)
        or (status == "available" and not direct)
        or (status == "unavailable" and (direct or effective))):
        raise ValueError("department_authority_invalid")
    return DepartmentAuthority(tuple(sorted(direct)), tuple(sorted(effective)), status)
