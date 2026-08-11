# Expert Domain Language

This glossary names the business facts used by Expert governance. It does
not describe storage, routes, frameworks, or current runtime state.

## Core Terms

**Expert Revision**
: An immutable, tenant-owned definition of one enterprise expert. Its identity
  binds the user-facing profile, private execution definition, publication
  scope, and access policy with one content hash.

**Expert**
: The published enterprise product backed by an Expert Revision.
  It is discoverable only to an authorized principal and may create new work
  only while that exact publication remains authorized.

**Expert Workspace**
: The dedicated user experience for one Expert. Entering it does not create
  a conversation. It shows only safe profile facts and never lets the client
  replace the Expert's fixed model, bound Skills, tools, or server-owned instructions.

**Expert Conversation**
: A user-owned conversation pinned to one Expert identity, Expert Revision,
  and content hash when the user explicitly starts it. Later Expert
  revisions never migrate that conversation.

**Expert Run**
: One admitted execution attempt for an Expert Conversation. Every new run,
  retry, resume, or copy must reauthorize the current principal and the pinned
  capabilities before any work is dispatched.

**Expert Instruction**
: An administrator-authored, server-owned instruction in an Expert Revision. It
  is authoritative for every Expert Run and is never supplied or overridden by a
  client. It enters model context and is therefore not a secrets store, even though
  ordinary-user configuration projections, events, errors, and logs must not expose
  it directly.

**Welcome Message**
: Public presentation content shown once when an Expert Conversation starts.
  It is not an Expert Instruction and does not enter model context.

**Bound Skill**
: A governed Skill Revision made available to an Expert Run. Binding grants an
  authorized choice to the Harness; it never requires invocation.

**Expert Test Conversation**
: An administrator-owned Expert Conversation created only for a controlled test.
  Its runs use the normal governed execution path and its records remain
  explicitly distinguishable from ordinary-user conversations.

## Capability Truth

**Capability Bound**
: The immutable Expert Revision names a capability. Binding is availability,
  not evidence that execution prepared or used it.

**Capability Staged**
: The platform made the selected capability's exact governed material available
  to the admitted run. Staging is not invocation.

**Capability SDK Registered**
: The execution adapter registered the staged capability with the model SDK.
  Registration is not invocation.

**Capability Actually Invoked**
: The server verified an exact SDK hook for a capability in the fixed staged set.
  Inference, platform-runner activity, executor-native activity, text, paths, or
  client claims cannot establish this fact.

**Capability Completed**
: An actually invoked capability reached its verified terminal outcome. A
  Bound Skill may remain truthfully uninvoked without preventing an Expert Run
  from succeeding.

**Artifact Ready**
: A durable artifact record exists and an authorized download contract can
  resolve it. A filename, path, tool message, or model text cannot establish
  this fact.

## Lifecycle Rule

Withdrawing an Expert denies new conversations and all new Expert Runs. It
does not rewrite pinned history: authorized users may still read historical
messages and durable artifacts under their current read permissions.
