# DingTalk department ID reselection

Purpose: read-only inventory and administrator-owned migration of department grants.
This procedure describes an activation dependency, not an executed migration or a
release authorization. The [Identity contract](../architecture/single-enterprise-data-lifecycle.md#dingtalk-department-authority)
owns scope semantics; the [release runbook](release-operations-runbook.md) owns deployment.

## Before activation

1. Configure the internal employee-department endpoint base URL for API and Worker;
   it requires no service key or authentication header. Both must use the same
   service and current pure directory. Verify exact work-ID association,
   all active memberships, ancestor closure, and safe unavailable behavior using
   authorized synthetic or redacted data.
2. Inventory every tenant's nonempty department configuration using the read-only
   query below. Record latest draft, current publication, historical Profile rows,
   Skill/MCP distributions, and MCP registry rows separately. Restricted-but-empty
   department lists retain their existing role/user semantics; do not replace them
   with an inferred department.
3. Administrators manually choose IDs by the displayed DingTalk path for every old
   name value, including unique names. A numeric-looking value is only a candidate
   ID until checked against the current directory and its intended path. Missing,
   abolished, duplicate-name, and ambiguous selections require an administrator's
   explicit choice; no inferred mapping or bulk database rewrite is permitted.
4. Update Skills and MCP servers through their existing governance forms. MCP
   registry and capability distribution are separate persisted surfaces; verify
   both agree after the owning write succeeds. Preserve lifecycle, visibility,
   roles, quotas, and archive evidence.
5. For each expert, preserve the current definition, revision and hash in the
   administrator's restricted inventory. Open the builder, remove unresolved old
   name chips, choose IDs, save a new draft using the current revision precondition,
   then publish the new draft. Publication appends another immutable revision.
6. Repeat the inventory. Current active configurations must have reviewed current
   IDs. Old names may remain only in immutable/history or explicitly inactive
   configurations with an owner and planned reselection. Verify that old revision
   hashes and existing Session/Run admission pins are unchanged. Recheck access
   with authorized parent, child, sibling, multiple-membership, and source-outage
   cases before completing release acceptance.

An old name grant does not grant access after this source change. Activating the
new code before manual reselection can hide department-restricted experts, Skills,
and MCP tools from ordinary users. A source outage similarly denies those grants;
existing administrator, explicit-user, and unrestricted rules remain in effect.
Coordinate the release and administrator reselection window explicitly.

## Read-only inventory

Run only against the approved environment using an authorized PostgreSQL read-only
connection. Use a parameterized tenant value; the example uses psql's quoted
`:'tenant_id'` variable (`psql ... -v tenant_id=default`). Do not put credentials in
command text or export private instructions, user IDs, raw metadata, endpoints,
credential envelopes, or environment files. Protect even the reduced inventory as
administrator data. Repeat per approved tenant; never drop the tenant predicate.

```sql
begin transaction read only;

with profile_grants as (
  select 'profile_revision'::text as surface, r.agent_id as capability_id,
         r.revision::text as revision, r.revision_status as status,
         r.content_hash,
         r.revision = p.latest_revision as is_latest,
         (p.lifecycle_status = 'published'
          and r.revision = p.published_revision
          and r.content_hash = p.published_hash
          and r.revision_status = 'published') as is_current_publication,
         d.value #>> '{}' as department_value,
         jsonb_typeof(d.value) = 'string' as is_string,
         false as has_archive_marker
  from agent_profile_revisions r
  join agent_profiles p using (tenant_id, agent_id)
  cross join lateral jsonb_array_elements(r.allowed_department_ids) d(value)
  where r.tenant_id = :'tenant_id'
), distribution_grants as (
  select 'distribution:' || capability_kind as surface, capability_id,
         null::text as revision, status, null::text as content_hash,
         true as is_latest, false as is_current_publication,
         d.value as department_value, true as is_string,
         metadata_json ? 'archived_at' as has_archive_marker
  from tenant_capability_distributions
  cross join lateral unnest(department_ids) d(value)
  where tenant_id = :'tenant_id'
), registry_grants as (
  select 'mcp_registry'::text as surface, name as capability_id,
         null::text as revision, status, null::text as content_hash,
         true as is_latest, false as is_current_publication,
         d.value as department_value, true as is_string,
         false as has_archive_marker
  from mcp_servers
  cross join lateral unnest(department_ids) d(value)
  where tenant_id = :'tenant_id'
), grants as (
  select * from profile_grants
  union all select * from distribution_grants
  union all select * from registry_grants
)
select surface, capability_id, revision, status, content_hash,
       is_latest, is_current_publication, has_archive_marker, department_value,
       case when is_string and department_value ~ '^[1-9][0-9]{0,9}$'
            then 'candidate_id_needs_directory_review'
            else 'legacy_name_or_invalid_needs_reselection' end as disposition
from grants
order by surface, capability_id, revision, department_value;

rollback;
```

`has_archive_marker` only flags a row for lifecycle review. The owning archive
resolver validates the exact UTC timestamp; key presence alone does not prove an
archive. Disabled, deleted, archived and historical rows remain in this inventory
so they cannot be silently omitted from the disposition. The query intentionally
does not mutate any value or automatically map a name to an ID.

Administrator API alternatives for Profiles are `GET /api/ai/admin/agent-profiles`
(latest revisions) and `GET /api/ai/admin/agent-profiles/{agent_id}/history`
(immutable history). Their responses contain private Profile fields; reduce them
to the inventory columns above before recording evidence. Saving uses
`PUT /api/ai/admin/agent-profiles/{agent_id}`; publishing uses
`POST /api/ai/admin/agent-profiles/{agent_id}/publish`. Use the current request
contract and revision preconditions, not direct SQL updates.

## Retirement and compatibility disposition

| Superseded surface | Disposition and current owner |
| --- | --- |
| Department-query shared-key authentication | Removed from the external action, platform adapter, cache key, Settings and deployment templates. No credential compatibility path remains; the endpoint uses the existing internal DingTalkAPI network-access model. |
| Company JWT `depart`, user-info `department`, gateway scalar department grants | Removed from authorization. Original company fields stay only for compatible JWT renewal; Identity owns normalized DingTalk sets. |
| Name-valued `authority_id` and duplicate-name disabling | Replaced by canonical DingTalk IDs; names/paths are display-only in the current directory. |
| Scalar-only Profile/Skill/MCP discovery, admission and replay | Common authorizers consume effective direct-and-ancestor sets. Historical Run scalar columns remain audit provenance only. |
| Independent MCP registry SQL department filter | Removed; MCP selection and distribution authorizers own access. |
| MCP free-text department entry | Replaced by the shared directory selector for regular and system servers. |
| Old mutable Skill/MCP name grants | Administrator manually reselects current IDs through owning governance writes; no name fallback or automatic migration. |
| Old immutable Profile revisions and hashes | Retained by Agent Apps for history and exact provenance. Never rewrite; reselect via a new draft and publication. Current authorization still applies to execution. |
| Legacy principal snapshots without ID arrays | Read as empty, unavailable department scope. Fresh verified lookup supplies current IDs; no scalar fallback. |

Owning regression selectors are `tests/test_dingtalk_department_authority.py`,
`tests/test_department_directory.py`, `tests/test_capability_distribution.py`,
`tests/test_agent_profile_lifecycle.py`, `tests/test_authorized_skill_catalog.py`,
`tests/test_capability_distribution_routes.py`, `tests/test_mcp_routes.py`,
`tests/test_auth_routes.py`, `tests/test_auth_session_operations.py`, and
`tests/test_runs_replay.py`. Frontend selectors include department directory
selection/API adapters, Agent Builder adapters/controllers, MCP form mounted
reselection, and department catalog scope mounted checks. Search active callers
and docs for scalar/name grants when reviewing the diff; a checklist does not
replace that absence inventory or target-environment acceptance.
