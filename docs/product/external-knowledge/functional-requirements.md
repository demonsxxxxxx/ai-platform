# Atomic Functional Requirements

Each requirement below states one independently falsifiable behavior. Priority
`P0` belongs to the first release. `P1` belongs to the accepted follow-on
release. Requirement IDs are stable references for issues, tests, review and
acceptance; implementation status belongs in GitHub rather than this document.

## 1. Connection administration

| ID | Priority | Requirement |
| --- | --- | --- |
| KCON-001 | P0 | Only a principal authorized to administer platform connections may create a Knowledge Connection. |
| KCON-002 | P0 | A create request must provide a non-empty display name. |
| KCON-003 | P0 | A create request must select a registered provider key. |
| KCON-004 | P0 | The first release must accept `ragflow` as a registered provider key. |
| KCON-005 | P0 | A create request must provide an absolute HTTP or HTTPS base URL. |
| KCON-006 | P0 | The connection boundary must reject base URLs containing user-info credentials. |
| KCON-007 | P0 | The connection boundary must apply the configured internal-network egress policy before any provider request. |
| KCON-008 | P0 | A create request must provide a non-empty API key through a write-only field. |
| KCON-009 | P0 | A credential must be stored through the server-owned secret boundary. |
| KCON-010 | P0 | No connection read projection may contain the credential value. |
| KCON-011 | P0 | A newly persisted connection must begin in `draft`. |
| KCON-012 | P0 | A connection may become `active` only after an authenticated provider capability check succeeds. |
| KCON-013 | P0 | A network-successful anonymous health response must not establish authenticated readiness. |
| KCON-014 | P0 | The authenticated capability check must prove access to the provider dataset catalog. |
| KCON-015 | P0 | A failed capability check must persist a bounded safe failure class. |
| KCON-016 | P0 | A failed capability check must not persist a raw provider response body. |
| KCON-017 | P0 | Changing the canonical provider base URL must create a new Knowledge Connection rather than a revision of the existing connection. |
| KCON-018 | P0 | Rotating the credential must create a new connection revision. |
| KCON-019 | P0 | An updated credential or transport-policy revision must pass a new authenticated check before catalog enumeration. |
| KCON-020 | P0 | Disabling a connection must prevent new catalog synchronization. |
| KCON-021 | P0 | Disabling a connection must prevent new retrieval admission. |
| KCON-022 | P0 | Disabling a connection must preserve historical citation snapshots. |
| KCON-023 | P0 | A connection referenced by a logical source must not be physically deleted. |
| KCON-024 | P0 | Every connection mutation must write a redacted audit fact. |
| KCON-025 | P0 | Connection lists must use bounded server-side pagination. |
| KCON-026 | P0 | Connection check duration must be bounded by typed timeout settings. |
| KCON-027 | P0 | An ordinary-user projection must not reveal the provider base URL. |
| KCON-028 | P0 | An ordinary-user projection must not reveal the provider key name. |
| KCON-029 | P0 | Only a platform connection administrator may update a Knowledge Connection. |
| KCON-030 | P0 | Only a platform connection administrator may start an authenticated connection check. |
| KCON-031 | P0 | Only a platform connection administrator may disable a Knowledge Connection. |
| KCON-032 | P0 | Only a platform connection administrator or knowledge administrator may read an administrative connection projection. |
| KCON-033 | P0 | Update, check and disable requests must carry a caller-owned operation identity. |
| KCON-034 | P0 | A denied administrative connection request must not disclose whether the connection exists. |
| KCON-035 | P0 | A connection revision referenced by an Agent revision, Run snapshot or audit fact must remain readable. |
| KCON-036 | P0 | One idempotent candidate-activation application command must bind the server-held candidate revision and own its authenticated check, exact-revision complete catalog synchronization and final activation. |
| KCON-037 | P0 | Activating a candidate connection revision must atomically bind its successful catalog synchronization as the active catalog generation. |
| KCON-038 | P0 | A failed or partial candidate-revision synchronization must leave the prior active revision and catalog generation unchanged. |
| KCON-039 | P0 | Connection and Market freshness must use only the last complete synchronization bound to the current active connection revision. |
| KCON-040 | P0 | Changing the registered provider key must create a new Knowledge Connection rather than a revision of the existing connection. |
| KCON-041 | P0 | A new connection whose authenticated check passes must remain `cataloging` until its initial complete synchronization commits. |

## 2. Dataset catalog synchronization

| ID | Priority | Requirement |
| --- | --- | --- |
| KSRC-001 | P0 | Only a knowledge administrator may call manual active-revision synchronization; candidate synchronization may start only inside a platform connection administrator's candidate-activation command. |
| KSRC-002 | P0 | Manual catalog synchronization may start only for the active revision of an active connection. |
| KSRC-003 | P0 | A synchronization request must carry a caller-owned operation identity. |
| KSRC-004 | P0 | Repeating one operation identity for one connection must return one synchronization job. |
| KSRC-005 | P0 | At most one synchronization job may actively enumerate one connection. |
| KSRC-006 | P0 | Dataset enumeration must use bounded provider page sizes. |
| KSRC-007 | P0 | Dataset enumeration must continue until the provider reports no remaining page. |
| KSRC-008 | P0 | A logical source identity must be unique by connection and provider resource ID. |
| KSRC-009 | P0 | A newly discovered provider dataset must create one logical source. |
| KSRC-010 | P0 | A newly discovered logical source must begin in `pending_review`. |
| KSRC-011 | P0 | Synchronization must update the provider-owned name projection. |
| KSRC-012 | P0 | Synchronization must preserve an administrator-defined display alias. |
| KSRC-013 | P0 | Synchronization must store only allowlisted bounded provider metadata. |
| KSRC-014 | P0 | Synchronization must not persist provider document contents. |
| KSRC-015 | P0 | Synchronization must not persist provider chunks. |
| KSRC-016 | P0 | Synchronization must not persist provider embeddings. |
| KSRC-017 | P0 | A complete successful synchronization must record its completion time. |
| KSRC-018 | P0 | A partial synchronization must not replace the prior complete catalog view. |
| KSRC-019 | P0 | A failed synchronization must not mark unseen sources as missing. |
| KSRC-020 | P0 | A source absent from a complete successful synchronization must become `missing`. |
| KSRC-021 | P0 | A missing source must retain its Agent binding history. |
| KSRC-022 | P0 | A missing source must be unavailable for new publication. |
| KSRC-023 | P0 | Enabling a source must require a valid source ACL. |
| KSRC-024 | P0 | Disabling a source must deny new retrieval admission. |
| KSRC-025 | P0 | Disabling a source must preserve historical citation snapshots. |
| KSRC-026 | P0 | Source lists must use bounded server-side pagination. |
| KSRC-027 | P0 | Source lists must support exact connection filtering. |
| KSRC-028 | P0 | Source lists must support status filtering. |
| KSRC-029 | P0 | Source lists must support bounded name search. |
| KSRC-030 | P0 | Public source projections must use logical source IDs. |
| KSRC-031 | P0 | Public source projections must not contain provider resource IDs. |
| KSRC-032 | P0 | A source retrieval test may run only for a principal authorized to manage that source. |
| KSRC-033 | P0 | A source retrieval test must apply the same response validation and payload bounds as a Run retrieval. |
| KSRC-034 | P1 | An operator-configured schedule may start synchronization through the same application command as a manual request. |
| KSRC-035 | P0 | Only a knowledge administrator may update a source display alias or safe description. |
| KSRC-036 | P0 | Only a knowledge administrator may activate, disable or re-enable a logical source. |
| KSRC-037 | P0 | A source retrieval test must not create an Agent Conversation, Run or message. |
| KSRC-038 | P0 | A source retrieval test must not create durable Run evidence or citation rows. |
| KSRC-039 | P0 | A source retrieval test audit fact must contain result count, duration and safe outcome without query or chunk content. |
| KSRC-040 | P0 | A source retrieval test result must not appear in Agent Market, Workspace or ordinary conversation history. |
| KSRC-041 | P0 | Candidate-revision catalog enumeration may start only inside the connection-activation operation after that revision passes its authenticated check. |
| KSRC-042 | P0 | A catalog observation must record the exact connection revision that produced it. |

## 3. Source authorization

| ID | Priority | Requirement |
| --- | --- | --- |
| KACL-001 | P0 | A source ACL must select either `enterprise` or `restricted` visibility. |
| KACL-002 | P0 | A newly discovered source must default to restricted administrative visibility. |
| KACL-003 | P0 | A restricted source ACL may name allowed departments. |
| KACL-004 | P0 | A restricted source ACL may name allowed roles. |
| KACL-005 | P0 | A restricted source ACL may name allowed users. |
| KACL-006 | P0 | Every selected department must resolve through the current company department directory. |
| KACL-007 | P0 | Every selected role must resolve through the current role catalog. |
| KACL-008 | P0 | Every selected user must resolve through the current identity authority. |
| KACL-009 | P0 | Department hierarchy semantics must match the existing Agent Profile ACL semantics. |
| KACL-010 | P0 | An ordinary principal may use a restricted source only when the canonical ACL evaluator grants access. |
| KACL-011 | P0 | Source authorization must be evaluated server-side. |
| KACL-012 | P0 | A browser-supplied department value must not influence source authorization. |
| KACL-013 | P0 | A browser-supplied role value must not influence source authorization. |
| KACL-014 | P0 | An upstream identity refresh that removes access must deny the next Run. |
| KACL-015 | P0 | An identity authority failure must preserve the stricter last-known decision or deny access. |
| KACL-016 | P0 | A source ACL mutation must increment the source authorization version. |
| KACL-017 | P0 | A source ACL mutation must write a redacted audit fact. |
| KACL-018 | P0 | An ordinary-user denial must not reveal the source name or provider identity. |
| KACL-019 | P0 | A source manager may view the safe ACL projection. |
| KACL-020 | P0 | A source manager may not read the connection credential through the ACL surface. |
| KACL-021 | P0 | Only a knowledge administrator may replace a source ACL. |
| KACL-022 | P0 | A denied source ACL mutation must not disclose the source name or provider identity. |

## 4. Agent Profile binding and publication

| ID | Priority | Requirement |
| --- | --- | --- |
| KAGT-001 | P0 | Knowledge configuration must appear separately from the Agent Skill Set. |
| KAGT-002 | P0 | Knowledge configuration must appear separately from MCP tool selection. |
| KAGT-003 | P0 | An Agent draft may bind zero knowledge sources. |
| KAGT-004 | P0 | An Agent draft may bind at most eight knowledge sources. |
| KAGT-005 | P0 | One Agent draft must not contain the same logical source more than once. |
| KAGT-006 | P0 | The Builder may offer only active logical sources to a new selection. |
| KAGT-007 | P0 | The Builder must retain an existing unavailable selection long enough to explain its validation error. |
| KAGT-008 | P0 | The Builder must display the logical source name. |
| KAGT-009 | P0 | The Builder must display a safe source visibility summary. |
| KAGT-010 | P0 | The Builder must display a safe source availability summary. |
| KAGT-011 | P0 | The Builder must not display a provider resource ID. |
| KAGT-012 | P0 | The Builder must not display a connection credential. |
| KAGT-013 | P0 | A draft with knowledge sources must select one retrieval profile. |
| KAGT-014 | P0 | Draft save must persist logical source IDs in deterministic order. |
| KAGT-015 | P0 | Draft read must restore the same logical source order. |
| KAGT-016 | P0 | Publishing must reauthorize every selected source. |
| KAGT-017 | P0 | Publishing must require every selected source to be active. |
| KAGT-018 | P0 | Publishing must require the retrieval profile to be active. |
| KAGT-019 | P0 | Publishing must reject an Agent visibility broader than a required source visibility. |
| KAGT-020 | P0 | Publication must pin the exact logical source IDs. |
| KAGT-021 | P0 | Publication must pin the exact source authorization versions. |
| KAGT-022 | P0 | Publication must pin the exact retrieval profile version. |
| KAGT-023 | P0 | Publication content hashing must include the ordered knowledge bindings. |
| KAGT-024 | P0 | Publication content hashing must include the retrieval profile version. |
| KAGT-025 | P0 | Editing a knowledge binding must create a new Agent Profile Revision on publication. |
| KAGT-026 | P0 | A later Agent revision must not mutate an existing Agent Conversation. |
| KAGT-027 | P0 | Withdrawing an Agent App must preserve historical citations. |
| KAGT-028 | P0 | A draft retrieval test must remain distinguishable from an ordinary-user Run. |
| KAGT-029 | P0 | A draft retrieval test must use the normal source authorization boundary. |
| KAGT-030 | P1 | An Agent draft may select `agent_directed` retrieval only after that execution mode is enabled by platform policy. |
| KAGT-031 | P0 | A draft retrieval test must use a Builder Test Conversation rather than an ordinary Agent Conversation. |
| KAGT-032 | P0 | A draft retrieval test must use the normal governed Run and retrieval path. |
| KAGT-033 | P0 | Every Run, evidence item, message and citation created by a draft retrieval test must be marked `builder_test`. |
| KAGT-034 | P0 | Builder test messages and citations must not appear in Agent Market or ordinary-user conversation history. |
| KAGT-035 | P0 | Builder test evidence and citations must follow the same authorization and retention rules as their owning test Run and message. |

## 5. Market and conversation admission

| ID | Priority | Requirement |
| --- | --- | --- |
| KADM-001 | P0 | Market discovery must require access to the Agent App. |
| KADM-002 | P0 | Market discovery must require access to every required bound source. |
| KADM-003 | P0 | Market detail must repeat the same authorization decision as Market discovery. |
| KADM-004 | P0 | Opening an Agent Workspace must not create a conversation. |
| KADM-005 | P0 | Explicit conversation creation must pin the published Agent revision and content hash. |
| KADM-006 | P0 | Conversation creation must not accept browser-selected source IDs. |
| KADM-007 | P0 | Every new Run must restore knowledge bindings from the pinned Agent revision. |
| KADM-008 | P0 | Every new Run must reauthorize the current principal against the Agent ACL. |
| KADM-009 | P0 | Every new Run must reauthorize the current principal against every required source ACL. |
| KADM-010 | P0 | Every new Run must verify every required source status. |
| KADM-011 | P0 | Every new Run must resolve one active connection revision per source. |
| KADM-012 | P0 | Every new Run must verify the retrieval profile version. |
| KADM-013 | P0 | A user-visible or orchestrator retry must create a new Run identity and repeat knowledge authorization before dispatch. |
| KADM-014 | P0 | A user-visible or orchestrator resume must create a new Run identity and repeat knowledge authorization before dispatch. |
| KADM-015 | P0 | Copy must create a new Run identity and repeat knowledge authorization before dispatch. |
| KADM-016 | P0 | An authorization failure must produce zero provider calls. |
| KADM-017 | P0 | A connection-resolution failure must produce zero Engine calls. |
| KADM-018 | P0 | A Run knowledge snapshot must bind the admitted Agent revision. |
| KADM-019 | P0 | A Run knowledge snapshot must bind the admitted logical sources. |
| KADM-020 | P0 | A Run knowledge snapshot must bind the connection revisions used for retrieval. |
| KADM-021 | P0 | A Run knowledge snapshot must bind the retrieval profile version. |
| KADM-022 | P0 | A Run knowledge snapshot must record the source authorization versions. |
| KADM-023 | P0 | A Run knowledge snapshot must not contain a credential value. |
| KADM-024 | P0 | A Run knowledge snapshot must remain within the platform context payload bound. |
| KADM-025 | P0 | Run admission and every catalog or connection writer that locks both sources and connections must use the same source-then-connection ascending-ID lock order and re-read governed facts after locking. |

## 6. Retrieval and evidence

| ID | Priority | Requirement |
| --- | --- | --- |
| KRET-001 | P0 | Deterministic retrieval must occur after Run admission. |
| KRET-002 | P0 | Deterministic retrieval must complete before the Engine receives the current request. |
| KRET-003 | P0 | Provider calls must occur in the Knowledge infrastructure adapter. |
| KRET-004 | P0 | A route must not call RAGFlow directly. |
| KRET-005 | P0 | An Engine adapter must not read a RAGFlow credential. |
| KRET-006 | P0 | The RAGFlow adapter must call the authenticated native retrieval API. |
| KRET-007 | P0 | One provider request must name only server-resolved provider dataset identities. |
| KRET-008 | P0 | One provider request must contain a bounded query derived from the current user message. |
| KRET-009 | P0 | One source call must use the retrieval profile's per-source result limit. |
| KRET-010 | P0 | One source call must use the retrieval profile's score threshold. |
| KRET-011 | P0 | Metadata filters must be absent in the first release. |
| KRET-012 | P0 | Provider calls must use the admitted connection revision. |
| KRET-013 | P0 | Provider calls must apply a per-call timeout. |
| KRET-014 | P0 | Multi-source calls must apply a bounded parallelism limit. |
| KRET-015 | P0 | Multi-source calls must apply one overall deadline. |
| KRET-016 | P0 | A provider response must be parsed as a typed provider response. |
| KRET-017 | P0 | A provider result without content must be rejected. |
| KRET-018 | P0 | A provider result without a stable document identity must be rejected. |
| KRET-019 | P0 | A provider result without a stable chunk identity must be rejected when the native API promises that identity. |
| KRET-020 | P0 | A non-numeric provider score must be rejected. |
| KRET-021 | P0 | Provider content must be truncated at the configured per-chunk UTF-8 bound. |
| KRET-022 | P0 | Provider metadata must be reduced to an allowlisted bounded projection. |
| KRET-023 | P0 | Raw provider errors must not cross the Knowledge boundary. |
| KRET-024 | P0 | Results must be deduplicated by provider, source, document and chunk identity. |
| KRET-025 | P0 | Multi-source results must be fused with the retrieval profile's declared strategy. |
| KRET-026 | P0 | The first release must use deterministic reciprocal-rank fusion. |
| KRET-027 | P0 | Fused evidence must use a deterministic tie-breaker. |
| KRET-028 | P0 | Fused evidence must be capped by the final evidence count. |
| KRET-029 | P0 | Fused evidence content must be capped by the total evidence byte budget. |
| KRET-030 | P0 | Every accepted evidence item must receive a platform evidence ID. |
| KRET-031 | P0 | Every accepted evidence item must retain its logical source ID. |
| KRET-032 | P0 | Every accepted evidence item must retain its provider document identity. |
| KRET-033 | P0 | Every accepted evidence item must retain its provider chunk identity when available. |
| KRET-034 | P0 | Every accepted evidence item must retain its provider position when available. |
| KRET-035 | P0 | Every accepted evidence item must retain its original provider score. |
| KRET-036 | P0 | Engine input must distinguish retrieved evidence from system instructions. |
| KRET-037 | P0 | Engine input must distinguish retrieved evidence from the current user message. |
| KRET-038 | P0 | Engine input must instruct citation through platform evidence IDs. |
| KRET-039 | P0 | A valid empty provider result must produce `no_evidence`. |
| KRET-040 | P0 | A `no_evidence` outcome must not claim a grounded answer. |
| KRET-041 | P0 | A knowledge-required provider failure must terminate before Engine dispatch. |
| KRET-042 | P0 | A successful retrieval attempt must write a bounded redacted audit fact. |
| KRET-043 | P0 | A failed retrieval attempt must write a bounded redacted audit fact. |
| KRET-044 | P1 | Agent-directed retrieval must call the same Knowledge application operation as deterministic retrieval. |
| KRET-045 | P1 | Agent-directed retrieval must accept only the current Run's admitted logical source scope. |
| KRET-046 | P0 | A RAGFlow retrieval call must map the admitted profile's candidate-pool size to `knn_top_k` independently from its per-source result limit. |
| KRET-047 | P0 | One generation-fenced compare-and-set must commit exactly one retrieval terminal receipt after provider work and permits are released. |
| KRET-048 | P0 | A persisted user cancellation at or before the fixed deadline must select `cancelled`, while deadline exhaustion first must select the timeout failure. |
| KRET-049 | P0 | Permit waiting and in-flight provider transport must observe one cancellation control and confirm release before terminal commit. |

## 7. Citations and history

| ID | Priority | Requirement |
| --- | --- | --- |
| KCIT-001 | P0 | A final answer may cite only evidence accepted for the same Run. |
| KCIT-002 | P0 | A cited evidence ID must resolve to one durable citation snapshot. |
| KCIT-003 | P0 | A citation snapshot must bind the durable assistant message. |
| KCIT-004 | P0 | A citation snapshot must store the logical source ID. |
| KCIT-005 | P0 | A citation snapshot must store the provider document identity. |
| KCIT-006 | P0 | A citation snapshot must store the provider chunk identity when available. |
| KCIT-007 | P0 | A citation snapshot must store a bounded title. |
| KCIT-008 | P0 | A citation snapshot must store a bounded excerpt. |
| KCIT-009 | P0 | A citation snapshot must store a content digest. |
| KCIT-010 | P0 | A citation snapshot must store the provider position when available. |
| KCIT-011 | P0 | A citation snapshot must store the original provider score. |
| KCIT-012 | P0 | A citation snapshot must not store a provider credential. |
| KCIT-013 | P0 | A citation snapshot must not store a provider base URL. |
| KCIT-014 | P0 | Citation persistence must commit before terminal hydrate makes citations public. |
| KCIT-015 | P0 | Citation history hydration must read only durable citation snapshots. |
| KCIT-016 | P0 | Citation history hydration must enforce conversation read authorization. |
| KCIT-017 | P0 | A source disabled after answer completion must not erase the citation snapshot. |
| KCIT-018 | P0 | A source missing after answer completion must not erase the citation snapshot. |
| KCIT-019 | P0 | A citation projection must not expose a provider resource ID. |
| KCIT-020 | P0 | A citation projection must not expose source ACL details. |
| KCIT-021 | P0 | Inline markers must map deterministically to citation snapshot order. |
| KCIT-022 | P0 | Refresh must render the same citation marker order. |
| KCIT-023 | P0 | SSE terminal replay followed by durable hydrate must not create duplicate citation items. |
| KCIT-024 | P0 | A citation click must never trigger an unauthenticated provider request from the browser. |

## 8. User experience

| ID | Priority | Requirement |
| --- | --- | --- |
| KUI-001 | P0 | Knowledge Connections must display a safe status for each connection. |
| KUI-002 | P0 | Knowledge Connections must display the last authenticated check time. |
| KUI-003 | P0 | Knowledge Connections must treat a credential input as write-only. |
| KUI-004 | P0 | Knowledge Connections must provide a bounded connection test action. |
| KUI-005 | P0 | Knowledge Sources must provide manual synchronization. |
| KUI-006 | P0 | Knowledge Sources must display the last complete synchronization time. |
| KUI-007 | P0 | Knowledge Sources must display source status. |
| KUI-008 | P0 | Knowledge Sources must support source ACL editing. |
| KUI-009 | P0 | Knowledge Sources must provide retrieval testing for authorized administrators. |
| KUI-010 | P0 | Agent Builder must support keyboard-accessible multi-source selection. |
| KUI-011 | P0 | Agent Builder must show an ACL incompatibility before publish. |
| KUI-012 | P0 | Agent Builder must show an unavailable source before publish. |
| KUI-013 | P0 | Agent Builder must not block a knowledge-free Agent draft. |
| KUI-014 | P0 | Agent Market must show only safe knowledge capability text. |
| KUI-015 | P0 | Agent Market must show a bounded source freshness summary. |
| KUI-016 | P0 | Agent Workspace must expose citation markers on a cited answer. |
| KUI-017 | P0 | Agent Workspace must provide a citation detail drawer. |
| KUI-018 | P0 | Citation details must display the source title. |
| KUI-019 | P0 | Citation details must display the bounded excerpt. |
| KUI-020 | P0 | Citation details must display a provider position when available. |
| KUI-021 | P0 | A no-evidence state must provide a retry or reformulation action. |
| KUI-022 | P0 | A provider-unavailable state must provide a later-retry action. |
| KUI-023 | P0 | A denied state must not identify the restricted source. |
| KUI-024 | P0 | Every Knowledge administration list must remain scrollable at a narrow viewport. |
| KUI-025 | P0 | Every Knowledge administration list must use bounded pagination rather than unbounded browser loading. |

## 9. Operations and governance

| ID | Priority | Requirement |
| --- | --- | --- |
| KOPS-001 | P0 | Provider request metrics must record duration without raw query text. |
| KOPS-002 | P0 | Provider request metrics must record outcome class without raw response content. |
| KOPS-003 | P0 | Provider request metrics must identify the logical connection without exposing the credential. |
| KOPS-004 | P0 | Retrieval metrics must record admitted source count. |
| KOPS-005 | P0 | Retrieval metrics must record accepted evidence count. |
| KOPS-006 | P0 | Retrieval metrics must distinguish no-evidence from provider failure. |
| KOPS-007 | P0 | Readiness must distinguish provider configuration validity from provider reachability. |
| KOPS-008 | P0 | API startup must fail when the configured provider key is unregistered. |
| KOPS-009 | P0 | API startup must not fail solely because an optional connection is temporarily unreachable. |
| KOPS-010 | P0 | An active bound connection with invalid secret configuration must surface an operator readiness failure. |
| KOPS-011 | P0 | Provider retries must use the admitted retrieval profile's typed maximum retry count, exponential base delay, delay cap and jitter ratio. |
| KOPS-012 | P0 | Provider retries must remain within the overall retrieval deadline. |
| KOPS-013 | P0 | Provider retries must not duplicate a durable citation snapshot. |
| KOPS-014 | P0 | Logs must not contain raw queries. |
| KOPS-015 | P0 | Logs must not contain chunk content. |
| KOPS-016 | P0 | Logs must not contain provider response bodies. |
| KOPS-017 | P0 | Logs must not contain provider credentials. |
| KOPS-018 | P0 | A schema migration must be additive and idempotent. |
| KOPS-019 | P0 | Rollback must preserve knowledge bindings and citation snapshots. |
| KOPS-020 | P0 | Runtime acceptance must bind source, image, principal, Agent revision, Run, sources and citations to one exact subject. |
| KOPS-021 | P0 | An expired catalog-synchronization lease must move the job to `reconcile_required` before a successor may commit. |
| KOPS-022 | P0 | The operator projection must report bounded counts for queued, running, failed and reconcile-required synchronization jobs. |
| KOPS-023 | P0 | An active connection whose authenticated check fails because its secret is absent or rejected must raise an operator alert. |
| KOPS-024 | P0 | Retrieval failure-ratio alerts must use a typed rolling window, minimum sample count and configurable threshold. |
| KOPS-025 | P0 | Provider-permit saturation alerts must use a typed wait-duration threshold and rolling window. |
| KOPS-026 | P0 | Alert payloads must contain only logical IDs, safe failure classes, counts and durations. |
| KOPS-027 | P0 | A connection alert must clear only after a later authenticated check succeeds. |
| KOPS-028 | P0 | A synchronization alert must clear only after a successor synchronization completes or an operator records an explicit disposition. |
| KOPS-029 | P0 | An operator retry must call the same catalog-synchronization application command with a new operation identity linked to the prior job. |
| KOPS-030 | P0 | Every operator retry, disable and alert disposition must write a redacted audit fact. |
| KOPS-031 | P0 | The v1 retrieval-failure alert default must be 20 percent over five minutes with at least 20 attempts. |
| KOPS-032 | P0 | The v1 retrieval-timeout alert default must be 10 percent over five minutes with at least 20 attempts. |
| KOPS-033 | P0 | The v1 provider-permit saturation default must be a p95 wait above 1000 milliseconds over five minutes with at least 20 waits. |
| KOPS-034 | P0 | Alert thresholds, windows and minimum sample counts must be typed operator settings rather than code-only constants. |
| KOPS-035 | P0 | KADR-01 and every later External Knowledge slice must publish a machine-readable manifest whose atomic case IDs exactly match that slice's exclusive ownership in the traceability matrix before merge. |
| KOPS-036 | P0 | Only the centralized typed transient-provider outcome may consume the bounded provider retry budget; every other provider outcome must fail without retry. |

## 10. Data lifecycle

| ID | Priority | Requirement |
| --- | --- | --- |
| KLIFE-001 | P0 | Accepted evidence must be persisted as durable Run-owned data before Engine dispatch. |
| KLIFE-002 | P0 | Durable evidence must remain readable for duplicate delivery, worker recovery, Engine continuation and citation finalization while its owning Run remains retained. |
| KLIFE-003 | P0 | A successful retrieval attempt's evidence rows must be immutable. |
| KLIFE-004 | P0 | Evidence retention must follow the owning Run retention setting. |
| KLIFE-005 | P0 | Citation retention must follow the owning assistant message retention setting. |
| KLIFE-006 | P0 | Connection revisions and source ACL versions must remain readable while referenced by an Agent revision, Run snapshot or audit fact. |
| KLIFE-007 | P0 | Synchronization receipts, retrieval-attempt metadata and Knowledge audit facts must use the platform retention default of `0`, meaning retained. |
| KLIFE-008 | P0 | A non-zero Knowledge retention setting must be rejected until a separately approved reference-safe cleaner exists. |
| KLIFE-009 | P0 | The first release must not expose a physical-delete API for connections, sources, evidence or citations. |
| KLIFE-010 | P0 | Any future Conversation or Run deletion workflow must own the corresponding citation and evidence disposition through a reviewed cross-context contract. |
| KLIFE-011 | P0 | Knowledge persistence must store only an opaque secret reference and never credential bytes. |
| KLIFE-012 | P0 | A disabled or superseded connection revision may resolve its credential only when an immutable lifecycle receipt binds that revision to the Run Knowledge Snapshot's serialized admission epoch, the caller is the exact current attempt, and its fixed deadline has not elapsed. |
| KLIFE-013 | P0 | Physical secret destruction must remain an explicit secret-authority operation and must not be inferred from deletion of a Knowledge row. |
| KLIFE-014 | P0 | Ephemeral source-test evidence must be released when the bounded source-test response is completed or aborted. |

## 11. Source migration

| ID | Priority | Requirement |
| --- | --- | --- |
| KMIG-001 | P0 | An accepted ADR must establish the Knowledge bounded-context authority before product code is added. |
| KMIG-002 | P0 | The source architecture map must name the Knowledge context and its dependency boundary. |
| KMIG-003 | P0 | The runtime authority map must name Knowledge as the retrieval admission authority. |
| KMIG-004 | P0 | Generic MCP authorization must remain owned by the MCP context. |
| KMIG-005 | P0 | A generic Knowledge execution capability may delegate to Knowledge without owning provider policy. |
| KMIG-006 | P0 | Current RAGFlow-specific source surfaces must receive an itemized deletion disposition. |
| KMIG-007 | P0 | Removing a seeded database identity must include persisted-row compatibility analysis. |
| KMIG-008 | P0 | Removing a public or admin route behavior must include a consumer inventory. |
| KMIG-009 | P0 | Historical citation-like records must retain a readable safe projection when they exist. |
| KMIG-010 | P0 | New code must not add RAGFlow conditionals to global route or repository modules. |
| KMIG-011 | P0 | New provider selection must use one typed Knowledge provider registry. |
| KMIG-012 | P0 | Test doubles must remain under test support rather than the production provider registry. |
| KMIG-013 | P0 | The migration contract must pin the exact reviewed source baseline and name every current RAGFlow-specific production surface and persisted identity. |
| KMIG-014 | P0 | Every migration item must state its canonical replacement, interim compatibility reader, deletion gate and rollback boundary. |
| KMIG-015 | P0 | The accepted Knowledge authority maps must preserve the single-enterprise identity model and must not add a configurable or user-facing tenant boundary. |
