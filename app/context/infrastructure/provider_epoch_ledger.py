"""Read and verify one bounded native ledger against its original append receipts."""
from __future__ import annotations

import json
from typing import Any, Mapping

from app.context.domain.provider_media import PROVIDER_MEDIA_FORMAT, restore_provider_images
from app.context.domain.provider_sessions import (
    MAX_PROVIDER_SESSION_ENTRIES, ProviderSessionConflictError, normalize_provider_subpath,
    provider_entry_json_bytes, normalize_provider_entry_batch,
)


async def read_provider_ledger(conn: Any, *, epoch: Mapping[str, Any]):
    cursor = await conn.execute(
        "select sequence, subpath, entry_json, image_references, image_format, entry_canonical_json::text as entry_canonical_json, "
        "(entry_canonical_json is null or entry_canonical_json::jsonb = entry_json) as canonical_matches "
        "from provider_session_entries "
        "where epoch_id = %s order by sequence asc limit %s for share",
        (epoch["id"], MAX_PROVIDER_SESSION_ENTRIES + 1),
    )
    rows = list(await cursor.fetchall())
    cursor = await conn.execute(
        "select expected_sequence, entry_count, last_sequence, batch_sha256, run_id, attempt_id, owner_generation, created_at "
        "from provider_session_append_receipts where epoch_id = %s "
        "order by expected_sequence asc limit %s for share",
        (epoch["id"], MAX_PROVIDER_SESSION_ENTRIES + 1),
    )
    receipts = list(await cursor.fetchall())
    return rows, receipts


def verify_provider_ledger(*, epoch: Mapping[str, Any], rows: list[dict], receipts: list[dict], media=None):
    from app.context.infrastructure.provider_epochs import _canonical, batch_digest, _has_ambiguous_legacy_number
    if len(rows) > MAX_PROVIDER_SESSION_ENTRIES:
        raise ProviderSessionConflictError("provider_session_transcript_too_large")
    if (type(epoch.get("entry_count")) is not int
        or type(epoch.get("next_sequence")) is not int
        or epoch["entry_count"] != len(rows)
        or epoch["next_sequence"] != len(rows) + 1
        or any(type(row.get("sequence")) is not int or row["sequence"] != i
               for i, row in enumerate(rows, 1))):
        raise ProviderSessionConflictError("provider_session_integrity_mismatch")
    expected = 1
    total_bytes = 0
    structured_bytes = 0
    try:
        for receipt in receipts:
            count = receipt.get("entry_count")
            if (type(count) is not int or count < 1
                or type(receipt.get("expected_sequence")) is not int
                or type(receipt.get("last_sequence")) is not int
                or receipt["expected_sequence"] != expected
                or receipt["last_sequence"] != expected + count - 1
                or receipt["last_sequence"] >= epoch["next_sequence"]):
                raise ProviderSessionConflictError("provider_session_integrity_mismatch")
            batch_rows = rows[expected - 1:expected + count - 1]
            path = batch_rows[0]["subpath"]
            if (not isinstance(path, str)
                or (normalize_provider_subpath(path) or "") != path
                or any(row.get("subpath") != path for row in batch_rows)):
                raise ProviderSessionConflictError("provider_session_integrity_mismatch")
            entries = []
            ambiguous_legacy = False
            for row in batch_rows:
                original = row.get("entry_canonical_json")
                if original is None:
                    entry = row["entry_json"]
                    ambiguous_legacy |= _has_ambiguous_legacy_number(entry)
                else:
                    entry = json.loads(original)
                    if (row.get("canonical_matches") is not True
                        or _canonical(entry).decode("utf-8") != original):
                        raise ProviderSessionConflictError("provider_session_integrity_mismatch")
                structured_bytes += provider_entry_json_bytes(entry)
                refs = row["image_references"]
                if refs:
                    if row["image_format"] != PROVIDER_MEDIA_FORMAT or original is None:
                        raise ProviderSessionConflictError("provider_session_integrity_mismatch")
                    entry = restore_provider_images(entry, refs, values=media.values if media else {})
                entries.append(entry)
            batch, batch_bytes = normalize_provider_entry_batch(entries, subpath=path)
            if any(item.entry != original for item, original in zip(batch, entries)):
                raise ProviderSessionConflictError("provider_session_integrity_mismatch")
            if batch_digest(path, [item.entry for item in batch]) != receipt.get("batch_sha256"):
                # JSONB cannot prove whether a legacy large integer was a float
                # or whether zero was negative. Never authenticate guessed bytes.
                code = ("provider_session_integrity_unavailable" if ambiguous_legacy
                        else "provider_session_integrity_mismatch")
                raise ProviderSessionConflictError(code)
            for row, item in zip(batch_rows, batch):
                row["entry_json"] = item.entry
            total_bytes += batch_bytes
            expected += count
    except ProviderSessionConflictError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderSessionConflictError("provider_session_integrity_mismatch") from exc
    if expected != epoch["next_sequence"]:
        raise ProviderSessionConflictError("provider_session_integrity_unavailable")
    if (type(epoch.get("structured_bytes")) is not int or structured_bytes != epoch["structured_bytes"]
        or type(epoch.get("transcript_bytes")) is not int or total_bytes != epoch["transcript_bytes"]):
        raise ProviderSessionConflictError("provider_session_integrity_mismatch")
    return rows
