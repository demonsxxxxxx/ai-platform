# Model group management and model admission

Status: normative backend contract for the model-group migration.

Owner: `execution` bounded context, with Run snapshots owned by `runs`.

## Authority

`model_groups`, `model_group_members`, and `model_group_models` are tenant-scoped
facts. `default` is the one immutable ordinary-default group per tenant. A user
with no business membership is resolved to that group without a stored default
membership row. Each user has at most one stored business membership; this is
also enforced by a unique `(tenant_id, user_id)` index. A model may belong to any
number of business and ordinary-default groups simultaneously. A disabled business
group is not treated as no membership and never falls back to `default`.

The model-group resolver is the single policy used by `/api/ai/models/groups`
and new Run admission. It checks the user's current membership, enabled group
models, global catalog availability, gateway revision, and execution capacity
before returning one model. Each group has a designated default model; chat selects
that model initially and submits it explicitly. A user may choose another model in
the same group. Neither explicit selection nor an unavailable default substitutes
another model. New requests accept only `explicit` selection mode; an `auto`
request is rejected with `model_selection_conflict` before Run admission. An
existing authorized entry point that omits model selection resolves only the
designated group default and freezes the resulting concrete model as `explicit`.
Priority and weight do not participate in model selection.

Schema upgrades and first-time group configuration initialize the default from
the first enabled association ordered by `display_order`, then model ID. A group
with no enabled associations has no default. An explicitly supplied disabled
default is rejected. When batch/mapping writes restore the first enabled
association to a group with no default and no enabled associations, the same
initialization rule applies. Legacy single-group configuration requests that omit the
default preserve an enabled configured default or initialize one by this rule.
Batch/mapping removal of the configured default while other models remain
requires an administrator to choose a replacement first; these operations never
select a replacement implicitly. Catalog or gateway unavailability
never changes the configured default.

Schema `2026.10.29.2` follows the supplier migration and repairs defaults created by earlier backfills when the
selected group association is disabled: select the first enabled association or
clear the default when none exists, and advance that group's revision. Reapplying
the schema leaves repaired and valid defaults unchanged. Enabled associations
whose catalog or gateway is unavailable retain their configured default. Existing
Run snapshots remain frozen. Pause new admission and stop all older API/Worker
instances before applying this schema, then deploy only the compatible application
and frontend before resuming traffic. This additive migration preserves the `.1`
ledger and structure: an older binary can still report schema readiness. Deployment
must enforce the version boundary; mixed-version serving and rollback to an older
writer would re-enable the retired request mode and are unsupported.

Only ordinary chat rejects execution through the ordinary-default group; authorized expert entry points permit it.
a model shared with a business group remains usable through that business group. Direct independent
business/skill execution may use the ordinary default group only after the normal
trusted entry-point admission. The server derives this scene from the trusted
route/profile path; a client `scene` field is not an authorization input.

## Suppliers and model identity

Model suppliers are platform-wide connection configuration, as the previous single
gateway was; tenant-owned groups remain the authorization boundary. Administrators
create suppliers under `/api/ai/admin/model-providers`. Each supplier has its own
address, write-only encrypted Key revisions and model catalog. Selecting a supplier
in management changes only the editor, not any user's model or group.

Schema `2026.10.29.1` requires matching API/Worker readers and writers. Roll out the
schema and new writers together before configuring additional suppliers; old writers
must not run alongside multi-supplier configuration because they globally delist
models. Once multiple suppliers exist, rollback to the single-gateway schema/writer
is unsupported: preserve revisions and apply a forward correction instead.

The existing connection and accumulated catalog migrate to supplier `legacy` without
rewriting model IDs, gateway ciphertext, Run/Attempt snapshots or usage. Earlier Keys
cannot reliably identify separate historical suppliers, so migration does not guess.
New suppliers namespace model IDs by supplier and upstream ID, permitting the same
upstream model name at multiple suppliers. Explicit value-only selection fails on
ambiguity; default selection and frontend submission use the platform model ID.
Each model binds its own supplier's active gateway revision at admission. Existing
Run proxies retain their frozen revision even after a supplier's Key is edited.

`GET /api/ai/admin/models` remains the complete cross-supplier catalog for the group
editor and supplier inventory; provider-scoped GET/discovery/publication isolate the
editable catalog. The old discovery/publication calls without `provider_id` address
only the migrated `legacy` supplier, for existing API consumers. No global active
connection fallback or cross-supplier model substitution is allowed.

Supplier deletion first reads `/model-providers/{id}/impact`, then posts to `retire`
with the observed connection revision and explicit confirmation. Group associations,
designated defaults (including disabled groups) and nonterminal Runs block deletion.
The transaction takes catalog then group-policy locks, rechecks impact, disables the
supplier's models and revisions, and archives the supplier/catalog. Normal lists omit
these entries; immutable connection revisions and historical model rows remain for
Run/Attempt, usage and audit identity. This is configuration retirement, not historical
credential erasure. No physical-delete or restore API is exposed.

For a Key already changed before this migration, `missing_only=true` on the same
impact/retire flow cleans old unavailable models without deleting the retained
supplier or currently provided models. The same references and active-Run checks
apply to the affected models. If later rediscovered, a cleaned model may be explicitly
published again; cleaning never changes groups or substitutes defaults.

## Group retirement

Disabling a business group is reversible: its members and model associations stay
in place, but new admissions are denied. Ordinary default identity remains immutable.

`POST /api/ai/admin/model-groups/{group_id}/retire` logically deletes a business
group using `archived_at`; no physical group delete occurs. The UI exposes one
Delete action instead of separate archive and physical-delete actions. The existing
`delete`/`confirm_impact` request fields remain accepted for known clients; both
legacy archive and delete spellings use the same atomic retirement rules.

The request fences the source with `revision`. If members exist, choose exactly
one destination: `target_group_id` plus `target_group_revision` for another enabled,
unarchived business group in the same tenant, or `confirm_default_fallback=true`.
The latter removes stored membership and restores implicit ordinary-default status,
which removes chat eligibility. A generic impact confirmation cannot make this choice.
Empty groups require no member destination. Source/target/self/tenant checks occur
before writes; a stale source or target aborts the transaction.

All nonterminal Runs block retirement; admin details expose `active_run_count`, and
an admission race is rechecked under the shared group-policy advisory lock. A denied
retirement returns a safe count without private execution payloads. Existing terminal
Run/Attempt snapshots are neither rewritten nor removed and do not block deletion.

Within the owning application transaction, retirement transfers or clears members,
advances affected user and source/target group revisions, clears the designated
default and this group's model associations, and sets `enabled=false, archived_at`.
Model entities and associations in other groups are untouched. Deleted groups are
hidden from normal admin/public lists; admin detail remains available for historical
inspection. Metadata, model/member/batch and mapping writes reject archived groups.
There is no restore/physical-cleanup API. Code rollback cannot recover transferred
members: an authorized correction uses current membership APIs and audit evidence.

## Frozen snapshots

A grouped Run stores `model_group_id`, code, type, revision, selection mode,
trusted usage scene, policy version, model identity, gateway revision, and token
limits on the Run. Worker dispatch compiles grouped executions as
`ai-platform.execution-spec.v4`; v1-v3 canonical specifications remain unchanged
for historical reads and recovery of an existing attempt. A new copy/retry/resume
reauthorizes the original model against current membership, then binds a complete
v4 child snapshot without rewriting the source. A continuation that omits a new
model choice in the same group keeps the prior concrete model; changing group
starts a new selection.
Session detail restores only the latest frozen model ID/value and group ID as
explicit selection metadata. It never exposes arbitrary Run input or grants;
each subsequent submission reauthorizes that choice.
Gateway and reconciliation compare the v4 group fields to the Run before accepting
the lease. Auxiliary chat completions require a matching succeeded v4 business-group or authorized default-group expert
attempt; old ungrouped histories remain readable but do not obtain a new completion.

Actual token usage remains attributed to the frozen Run model and group; no group
price, quota, channel pool, or fallback platform is introduced.

## Public API

- `GET /api/ai/models/groups` returns only the caller's business memberships
  (and the default group only when they have no business membership), with safe
  group/model labels, IDs and availability. Disabled memberships retain their
  `is_primary`/`group_disabled` state but expose no models, so the client can
  distinguish a disabled primary from no membership and must not silently choose
  another group. They are not selectable. The response excludes members, remarks,
  priorities, weights, credentials, gateway URLs, and runtime paths.
- `GET /api/ai/admin/model-groups` returns tenant-scoped administrative counts. For the immutable ordinary default group, `member_count` is the number of active users with no business-group membership, `members_are_implicit=true`, and no member rows are stored.
- `POST /api/ai/admin/model-groups` creates a business group.
- `PATCH /api/ai/admin/model-groups/{group_id}` updates mutable business fields
  with a required revision fence.
- `PUT /api/ai/admin/model-groups/{group_id}/models` replaces one group's model
  associations and its `default_model_id` with a revision fence. Legacy priority/weight
  fields remain accepted for existing clients but no longer choose the model. Batch removal
  or confirmed migration that removes a group's default while retaining other models is
  rejected; an administrator must first choose a replacement default. Sharing models
  with another group does not change existing Run snapshots or require draining Runs.
- `POST /api/ai/admin/model-groups/models/batch` atomically adds, replaces, or
  removes selected `model_ids` across `group_ids`. It requires an expected
  revision for every group and supports `preview: true`; both preview and apply
  return per-group before/after model sets, member counts, affected IDs, and the
  next revision.
- `GET /api/ai/admin/model-groups/{group_id}` returns the admin-only group
  details, including member `user_id`, `work_id` only for a verified
  `company-login` identity, `auth_source`, and display name, plus model
  association details. The ordinary default group returns
  `members_are_implicit=true`, its implicit active-user `member_count`, and an
  empty `members` array because default membership is computed, not stored.
- `GET /api/ai/admin/users/{user_id}/model-groups` returns the same-tenant
  user's business memberships and implicit-default state.
- `PUT /api/ai/admin/users/{user_id}/model-groups` atomically replaces that
  user's single business membership. `group_ids` accepts zero or one ID; more
  than one is rejected. `expected_revision` fences stale editors. The retained
  `primary_group_id` compatibility field is always the sole business group. Clearing the last membership requires
  `confirm_default_fallback: true` and creates no default membership row.
- `PUT /api/ai/admin/users/model-groups/batch` applies bounded cross-user
  membership replacements in one transaction, with per-user expected revisions
  and `before`/`after` projections for the management UI.
- `PUT /api/ai/admin/model-groups/{group_id}/members` replaces the group's members.
  Adding a user transfers their sole membership from the prior group to this group;
  `is_primary` remains a compatibility field and stored memberships are always primary.
  Both editing surfaces advance revisions of every changed user and group;
  no-op writes preserve revisions. Membership removal that leaves no business
  group requires `confirm_default_fallback: true`.

User-directory identity is explicit: the user-management path sends a stable
platform `user_id`; the employee-directory path may send a `work_id`, which the
server resolves only to exactly one same-tenant active `users` row whose
`auth_source` is `company-login` and whose `external_id` matches the work ID.
Display-name or string equality is never used as an identity binding. Ambiguous
or unmapped work IDs fail closed.

The employee picker joins the existing pure DingTalk department directory with
employee `departId` values from the same source. The admin-only employee list
accepts an optional exact `department_id` filter before search/pagination; it
still projects only work ID and display name. Department filtering selects direct
members, not descendants or a department grant. Invalid or ambiguous department
metadata cannot match a filtered query but does not remove a valid employee from
unfiltered search. Selecting a person remains an explicit membership write with
normal identity and revision validation.

All writes require the existing model-admin predicate and tenant from the
authenticated principal. New publication rejects the retired per-model
`access_mode`/`allowed_user_ids`/`is_default` fields; migration inventory may read
those columns as historical input, but runtime listing and admission never use
them for authorization.

## Confirm an explicit migration mapping

Apply the schema through the existing migration entry point before using the group
administration APIs. The schema creates no business grants and does not distribute
old globally available models. Freeze new admission and drain active Runs in the
covered tenants before confirming a permission mapping; the tool also checks this
under the same catalog/group locks used by admission.

```bash
python tools/model_group_migration.py --inventory
python tools/model_group_migration.py --preview mapping.json > preview.json
# Review every permission difference, then copy preview.expected_state into mapping.expected_state.
python tools/model_group_migration.py --confirm --mapping mapping.json
```

The `model-groups.v3` file explicitly lists complete model sets for named groups
and zero or one business membership for each named user. Omitted groups/users retain
their relationships. Changing a shared group's models affects its other members;
the preview includes those affected users too, along with retained groups/users
and explicit covered/outside tenant inventories. Neither `access_mode=all` nor
`is_default` is approval. The following values are synthetic:

```json
{
  "version": "model-groups.v3",
  "groups": [
    {"tenant_id": "tenant-example", "code": "default", "name": "普通默认组", "models": []},
    {"tenant_id": "tenant-example", "code": "sales", "name": "销售组",
     "models": [{"model_id": "mdl_example", "priority": 100, "weight": 100}]}
  ],
  "users": [
    {"tenant_id": "tenant-example", "user_id": "user-example",
     "group_codes": ["sales"], "primary_group_code": "sales"},
    {"tenant_id": "tenant-example", "work_id": "WORK-EXAMPLE",
     "group_codes": [], "primary_group_code": null}
  ]
}
```

`work_id` requires the verified company-login mapping above. An empty user
`group_codes` explicitly revokes all their old business memberships and returns
them to the implicit default group. `expected_state` binds the reviewed mapping,
catalog versions, group revisions and user identity/membership revisions; edits
require another preview. Apply is transactional and repeated identical mappings
make no revision changes. Keep the inventory and reviewed mapping as the recovery
record; rollback of permissions is another reviewed mapping, not old ACL activation.

## Retirement and compatibility

- Schema `2026.10.28.1` removes `trg_model_group_models_guard` and
  `ai_platform_guard_model_group_models`; application, batch and mapping mutual-exclusion
  checks and category-conversion drain guards are removed. Tenant/group/model admission
  and immutable historical snapshots remain in force.
- Existing multi-group users retain their unique primary membership; other rows are
  removed transactionally and affected user/group revisions advance. A multi-group user
  without exactly one primary aborts migration with
  `model_group_single_membership_migration_primary_required`; explicitly assign a
  primary before retrying. A sole membership is normalized to primary.
- Multi-group selection and per-member primary buttons are retired. Old request array
  fields remain for existing API clients but reject multiple groups. Rollback to a
  multi-group writer is unsupported; deploy the schema and compatible writers together.

- Runtime per-model ACL and global-default routing are replaced by group routing.
  Legacy catalog columns remain read-only input for administrator migration, with
  publication rejecting attempts to write them. They are not unioned into grants.
- Old `model_id`/`model` request clients remain supported: an omitted mode infers
  explicit selection, and an omitted group resolves the user's primary group.
- The weighted selector, public `auto` execution mode, continuation mode branch,
  and frontend mode state are retired. Historical Run/ExecutionSpec/queue values
  may still contain `auto`: their readers and SQL constraints retain that value
  for decoding and existing-attempt recovery, not model routing. Copy/retry/resume
  reauthorizes the frozen concrete model and writes a new `explicit` snapshot.
  Those historical readers can be removed only after their retained records and
  recoverable attempts have expired under the data lifecycle contract. Priority
  and weight columns and accepted administration fields remain inert for existing
  configuration/mapping consumers; removing them requires a separately verified
  consumer migration and schema rollout.
- Old Run/ExecutionSpec v1-v3 bytes and values are retained for historical decoding;
  new model binding requires a complete group snapshot. New replay uses v4.
- The unused `inherit_run_model` API and its stale inheritance assertions are
  retired. HTTP replay and owning rollback tests now use one reauthorized binding
  rather than inheriting stale capacity/authorization first.
