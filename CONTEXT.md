# Agent App Domain Language

This glossary names the business facts used by Agent App governance. It does
not describe storage, routes, frameworks, or current runtime state.

## Core Terms

**Agent Profile Revision**
: An immutable, tenant-owned definition of one enterprise expert. Its identity
  binds the user-facing profile, private execution definition, publication
  scope, and access policy with one content hash.

**Agent App**
: The published enterprise-expert product backed by an Agent Profile Revision.
  It is discoverable only to an authorized principal and may create new work
  only while its current publication remains authorized.

**Agent Workspace**
: The dedicated user experience for one Agent App. Entering it does not create
  a conversation. It shows only safe profile facts and never lets the client
  replace the Agent App's Skill, tools, or private instructions. The platform
  resolves and binds the governed execution model to each admitted Agent Run.

**Agent Conversation**
: A user-owned conversation bound to one Agent App identity when the user
  explicitly starts it. Its initial Profile revision and content hash remain
  provenance; later publications do not rewrite that record. Ordinary new turns
  resolve the current publication rather than executing the initial revision.

**Agent Run**
: One admitted execution for an Agent Conversation, with its own immutable
  Profile, Skill-version, dependency, and model snapshot. A new Run resolves the
  current authorized publication and capabilities. Worker dispatch and
  retry/resume/copy retain the accepted Run snapshot and recheck current
  principal, publication and capability authorization before dispatch.

**Agent Skill Set**
: The stable, tenant-authorized Skill references configured by an Agent Profile
  Revision. Admission resolves and freezes their current versions and dependency
  closure in the Run snapshot. Membership grants availability, not an obligation
  to invoke a Skill.

**Attachment Context**
: The user-owned files currently authorized and available to an Agent
  Conversation. A Skill's ability to process a file type does not by itself
  make an attachment mandatory for every Agent Run.

**Builder Test Conversation**
: An administrator-owned Agent Conversation created only for a controlled test.
  Its runs use the normal governed execution path but reject a changed tested
  revision. Its records remain distinguishable from ordinary-user conversations.

## Capability Truth

**Capability Selected**
: The immutable Agent Profile Revision names a capability. Selection is intent,
  not evidence that execution prepared or used it.

**Capability Staged**
: The platform made the selected capability's exact governed material available
  to the admitted run. Staging is not invocation.

**Capability SDK Registered**
: The execution adapter registered the staged capability with the model SDK.
  Registration is not invocation.

**Capability Invocation Decision**
: The execution SDK's decision whether to invoke an SDK-registered capability,
  which capability to invoke, and in what order for the current user request.
  Authorization, selection, staging, or accepted input types cannot establish
  or require this decision.

**Capability Actually Invoked**
: The server verified an exact SDK hook for a capability in the fixed staged set.
  Inference, platform-runner activity, executor-native activity, text, paths, or
  client claims cannot establish this fact.

**Capability Completed**
: An actually invoked capability reached its verified terminal outcome. A
  capability may remain truthfully uninvoked when the execution SDK determines
  that the current request does not need it.

**Artifact Ready**
: A durable artifact record exists and an authorized download contract can
  resolve it. A filename, path, tool message, or model text cannot establish
  this fact.

## Lifecycle Rule

Withdrawing an Agent App denies new conversations and all new Agent Runs. It
does not rewrite pinned history: authorized users may still read historical
messages and durable artifacts under their current read permissions.
