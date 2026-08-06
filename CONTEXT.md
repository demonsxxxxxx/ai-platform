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
  only while that exact publication remains authorized.

**Agent Workspace**
: The dedicated user experience for one Agent App. Entering it does not create
  a conversation. It shows only safe profile facts and never lets the client
  replace the Agent App's fixed model, Skill, tools, or private instructions.

**Agent Conversation**
: A user-owned conversation pinned to one Agent App identity, Agent Profile
  Revision, and content hash when the user explicitly starts it. Later profile
  revisions never migrate that conversation.

**Agent Run**
: One admitted execution attempt for an Agent Conversation. Every new run,
  retry, resume, or copy must reauthorize the current principal and the pinned
  capabilities before any work is dispatched.

**Builder Test Conversation**
: An administrator-owned Agent Conversation created only for a controlled test.
  Its runs use the normal governed execution path and its records remain
  explicitly distinguishable from ordinary-user conversations.

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

**Capability Actually Invoked**
: The server verified an exact SDK hook for a capability in the fixed staged set.
  Inference, platform-runner activity, executor-native activity, text, paths, or
  client claims cannot establish this fact.

**Capability Completed**
: An actually invoked capability reached its verified terminal outcome. A
  required capability must reach the required verified outcome before the Agent
  Run may succeed; an optional capability may remain truthfully uninvoked.

**Artifact Ready**
: A durable artifact record exists and an authorized download contract can
  resolve it. A filename, path, tool message, or model text cannot establish
  this fact.

## Lifecycle Rule

Withdrawing an Agent App denies new conversations and all new Agent Runs. It
does not rewrite pinned history: authorized users may still read historical
messages and durable artifacts under their current read permissions.
