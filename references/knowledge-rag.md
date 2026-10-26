# Knowledge and RAG Reference

Load this reference when knowledge retrieval, recurring-issue lookup, lesson capture, or
context-budget design is relevant.

## Contents

1. Memory tiers
2. Retrieval contract
3. Evidence cards and budgets
4. Learning lifecycle
5. Record schema
6. Freshness, noise, and permissions
7. Provider and failure behavior
8. Evaluation

## 1. Memory Tiers

Separate memory by lifetime and authority:

| Tier | Content | Retrieval behavior |
|---|---|---|
| Stable policy | skill rules, safety, workflow contracts | Always available, kept compact |
| Dynamic durable knowledge | verified facts, failure patterns, confirmed corrections | Scoped top-K retrieval only |
| Session state | hypotheses, task packet, agent state, raw investigation notes | Session-local; never durable by default |

Do not turn SKILL.md, worklogs, or one Markdown file into an append-only database. Keep
permanent reference documents for human-curated material; store atomic lessons in an
indexed knowledge service.

## 2. Retrieval Contract

Local memory routing is the first retrieval step for every input. Run
`scripts/lookup_memory_context.py` against the approved shared memory root,
open only the returned `selectedPaths`, and preserve its
`memory-context-lookup-v1` receipt. Simple inputs remain read-only by default;
they are not exempt from this bounded lookup. An optional provider may run only
after the local route is checked.

Use a provider-neutral interface:

~~~text
retrieve_knowledge(
  query,
  project,
  repository,
  branch_or_tree,
  task_type,
  fingerprints,
  access_scope,
  token_budget,
  max_records
) -> retrieval_result

fetch_knowledge(
  record_id,
  access_scope,
  purpose
) -> validated_record_or_detail

propose_lesson(
  claim,
  kind,
  scope,
  fingerprints,
  evidence,
  confidence,
  access_scope
) -> candidate_id

promote_lesson(
  candidate_id,
  decision,
  evidence,
  reviewer
) -> lifecycle_result

record_knowledge_feedback(
  record_id,
  outcome,
  evidence
) -> feedback_ack

invalidate_knowledge(
  record_id,
  reason,
  supersedes
) -> lifecycle_result
~~~

The adapter must return an explicit result envelope; an empty card list is not
enough to distinguish no match from provider failure:

~~~yaml
retrieval_result:
  outcome: matched | no_relevant_memory | unavailable | denied | failed | invalid
  cards: []
  query_fingerprint: normalized-query-hash
  scope_applied: project/repository/tree/access-scope
  provider_attempted: true | false
  expansion_count: 0 | 1
  limitation: optional-safe-summary
~~~

`unavailable`, `denied`, `failed`, and `invalid` are source-first outcomes, not
retrieval successes. They must be recorded in the task evidence ledger and must
not be converted to `no_relevant_memory` or a fabricated card. A provider may
return `no_relevant_memory` only after the requested scope and configured
confidence threshold were actually applied. `retrieve_knowledge` may expand a
query at most once, preserving the same access scope; after that, continue with
source-first investigation.

The bundled dependency-free adapter implements the exact-fingerprint and scoped
lexical portions of this order. Semantic/vector retrieval remains a host-provider
capability: the package accepts only the provider's already-scoped record boundary
and never claims vector results when no provider receipt exists. Its default expansion
count is zero; a host may perform at most one bounded expansion without widening scope.

Keep read, candidate-write, promotion, feedback, and invalidation permissions separate.
The adapter must enforce project/repository/tenant scope and return only records the
caller may see. These operations are contracts for the provider layer; agents must not
write directly to an index, Markdown file, vector store, or graph.

Retrieval order:

1. Normalize project, repository, task type, issue/error strings, paths, symbols, routes,
   ticket keys, and relevant user corrections.
2. Perform exact lookup for issue keys, normalized errors, paths, symbols, and fingerprints.
3. Apply access, project, repository, status, freshness, and task-type filters.
4. Run hybrid lexical plus vector search over the narrowed candidate set.
5. Rerank by exact match, scope, evidence quality, confidence, and freshness.
6. Deduplicate related chunks and return compact cards.
7. Return no_relevant_memory below the configured confidence threshold.
8. Expand once at most by default; then continue with source-first investigation.

Retrieved knowledge is a hypothesis until validated against current source, tests, runtime
state, or explicit user confirmation. Current evidence wins over memory.

Apply access, tenant/project/repository/tree, lifecycle-status, and freshness
filters before lexical/vector retrieval. Never retrieve broadly and filter after
the model has seen the records. If a record is outside scope, expired, rejected,
superseded, quarantined, or otherwise unauthorized, omit it and record only the
safe limitation needed for audit.

The local filesystem receipt carries content hashes and route identity, never
the raw request or full memory documents. It starts at the root indexes and
uses `route-map.json` plus `module-registry.json`; it does not recursively walk
the memory tree. Current source must revalidate every selected note.

## 3. Evidence Cards and Budgets

Return cards, not full documents:

~~~text
Lesson: <one concise claim>
Match: <signal that matched>
Confidence: <low|medium|high>
Freshness: <last verified / review / expiry>
Evidence: <repo, commit/tree, path, symbol, test or check>
Action: <what the agent must revalidate>
~~~

Each card must be independently actionable and must carry a stable record
reference plus a usability decision:

~~~yaml
evidence_card:
  record_id: stable-record-id
  claim: concise-claim
  match: exact-signal | scoped-metadata | hybrid | user-correction
  confidence: low | medium | high
  freshness:
    last_verified_at: timestamp | unknown
    review_at: timestamp | unknown
    expires_at: timestamp | none
    state: current | due_for_review | stale | unknown
  scope: session | repo | project | tenant | global
  evidence_refs: [repository/tree/path/symbol/check]
  action: required revalidation
  usability: lead_only | usable_after_validation | do_not_use
  conflict_group: optional-group-id
~~~

`stale`, `unknown`, and `do_not_use` cards may explain why a lead was
considered, but they cannot establish current behavior or close an acceptance
criterion. A card with a `conflict_group` must be compared with current source,
tests, runtime evidence, or explicit user direction. Preserve contradictory
claims as separate records; select the current evidence for execution, and mark
the unresolved conflict as `needs_review` or `blocked` when it affects safety,
authorization, data integrity, or acceptance.

Proposed starting budgets:

| Task profile | Cards | Retrieval context |
|---|---:|---:|
| Small/local | 0–3 | 1,200 tokens |
| Normal | 3–5 | 2,000 tokens |
| Complex/multi-layer | 5–8 | 3,500 tokens |

Treat budgets as measurable defaults, not promises. Do not automatically load full source
files, historical documents, subagent transcripts, or unrelated tool output. Fetch detail
only when a card requires it. Cache normalized query + scope + index version for a bounded
period when the provider supports it.

## 4. Learning Lifecycle

~~~text
Observed
  ↓
Session candidate
  ↓
Secret/privacy scan
  ↓
Normalize and summarize
  ↓
Duplicate/conflict search
  ↓
Evidence validation
  ↓
Verified / confirmed / needs_review
  ↓
Asynchronous indexing
  ↓
Revalidate on future retrieval
  ↓
Supersede / expire / reject / quarantine
~~~

At completion, the Learning Curator produces either:

- session-only observation;
- candidate lesson awaiting evidence;
- verified or confirmed durable lesson;
- rejected, stale, redacted, quarantined, or blocked result;
- explicit none when no reusable knowledge exists.

Never promote an agent opinion without evidence. Process changes remain proposals until
explicitly approved. Direct writes to SKILL.md are forbidden unless explicitly authorized.
Keep learning_state separate from durable record status: blocked and redacted can remain
session-local outcomes, while candidate, needs_review, verified, confirmed, superseded,
rejected, and quarantined are record lifecycle states.

### Learning outcome and write-back receipt

Every task that reaches completion, blocking, or explicit pause must emit one
learning outcome, even when no lesson is saved:

~~~yaml
learning_outcome:
  state: verified | confirmed | candidate | needs_review | rejected | redacted | blocked | none
  candidate_id: optional-id
  duplicate_of: optional-record-id
  conflict_group: optional-group-id
  evidence_refs: [repository/tree/path/symbol/check]
  limitation: optional-safe-summary
~~~

Use this bounded write-back sequence:

~~~text
observe -> privacy_scan -> normalize -> duplicate/conflict_search
        -> validate_evidence -> propose_lesson
        -> promote_or_review -> async_index -> re-read_receipt
~~~

Each transition is permissioned separately. `propose_lesson` may create only a
candidate, `promote_lesson` requires promotion authority and evidence, and
`invalidate_knowledge` or supersession requires explicit invalidation authority.
An asynchronous index acknowledgement is not promotion. A write-back receipt
must state `saved`, `updated`, `candidate_pending_review`, `rejected`,
`redacted`, `quarantined`, or `failed`; `failed` maps to `learning: blocked`,
keeps the sanitized candidate session-local, and must never be reported as saved.

Write-back failures, timeouts, authorization errors, duplicate conflicts, and
privacy-scan failures are observable limitations. Retry only through the
adapter's bounded retry policy; never loop, bypass access checks, write directly
to an index/vector store, or silently overwrite a conflicting lesson.

### Standalone durable learning adapter

When the host does not provide an acknowledged knowledge provider, the
standalone skill uses `scripts/record_task_learning.py` as its local fallback.
The adapter applies the same sanitization, scope, evidence, matching, and
conflict rules to a locked, atomically replaced JSON store at
`<memory-root>/autonomous-engineering/learning-store.json`. It never stores the raw
prompt or transcript. Matching claims are updated, additive evidence expands a
related lesson, and changed claims supersede the previous record so history is
preserved. A completed, blocked, or paused task is durably captured as a
candidate; explicit promotion authority and reviewer evidence are still
required before it becomes verified. Update/expand/supersede requires the same
project, repository, branch/tree, tenant, access scope, memory tier, and lesson
scope; a different tree or access boundary creates a separate record. Public
outcome remains `updated`, while `updateKind` records the exact mutation.

After a successful store write, the adapter queues one uniquely named,
immutable, no-clobber ad-hoc note carrying repository/application/module/
feature routing, evidence, and exact project/repository/tree/access binding.
That note is `candidate_not_canonical`; it does not change root indexes,
feature files, route maps, or module registries. Only the exact singleton scope
`project:<projectId>:read` is eligible; tenant-bound, qualified, mixed, or
unknown scopes return `scope_restricted` and remain only in the scoped JSON
store. An identical retry uses the lesson observation time and stable digest,
so it returns `already_queued` instead of creating a duplicate.
Simple inputs skip durable write-back unless they explicitly carry
`reusableLearning=true` plus the normal sanitized evidence packet.

## 5. Record Schema

Use a compact, provider-neutral record:

~~~yaml
schema_version: rag-record-v1
id: stable-record-id
claim: concise-normalized-lesson
kind: project_fact | user_preference | correction | failure_pattern | process_proposal
scope: session | repo | project | tenant | global
memory_tier: stable_policy | dynamic_durable | session
project_id: optional
repository: optional
branch_or_tree: optional
access_scope: required-permission-scope

fingerprints:
  error_code: optional
  normalized_error: optional
  symbol: optional
  path: optional
  route: optional
  issue_key: optional
  stack_hash: optional
  task_type: optional

status: candidate | needs_review | verified | confirmed | superseded | rejected | quarantined
confidence: low | medium | high
canonical_key: normalized-deduplication-key
content_hash: hash

evidence:
  - repository: repository-name
    commit_or_tree: hash
    path: relative/path
    symbol: function-or-class
    check: command-or-test
    source_type: code | test | runtime | user | review

files_involved: optional
business_logic: optional-summary
fix_strategy: optional-summary
related_records: optional-record-ids
tenant_id: optional-tenant-boundary
observed_at: timestamp
last_verified_at: timestamp
review_at: timestamp
expires_at: timestamp
supersedes: optional-record-id
contradiction_group: optional-group-id
sensitivity: none | redacted
created_by: agent-or-user
embedding_model: optional
embedding_version: optional
~~~

Keep learning_state separate from record status. learning_state describes this input;
record status describes a persisted record.

## 6. Freshness, Noise, and Permissions

- Link code facts to a commit or tree and revalidate after meaningful code changes.
- Give runtime failures shorter review windows than stable architecture facts.
- Hide expired, superseded, rejected, and unauthorized records by default.
- Version embeddings and indexes so reindexing can be rolled out and rolled back.
- Deduplicate by canonical key and content hash before embedding.
- Prefer no_relevant_memory over a weak plausible match.
- Track helpful, irrelevant, stale, and incorrect retrieval feedback.
- Group conflicts; never silently merge contradictory claims.
- Apply access filters before retrieval, not after the model sees the result.
- Reject or redact secrets, tokens, cookies, private keys, DSNs, PII, customer data,
  unique identifiers, raw prompts, transcripts, patches, and unredacted logs.
- Keep read and write permissions separate and audit create, promote, supersede, reject,
  quarantine, and delete operations.

### Decision gates

Before retrieval, normalize and privacy-scan the query, fingerprints, and access
scope. If required project, repository, tree, tenant, or access-scope data is
missing, return `denied` or `invalid` rather than widening the search. Before
write-back, scan the proposed claim, evidence, metadata, and generated embedding
inputs; reject or redact the candidate if the scan cannot prove that it contains
no secret, credential, token, cookie, private key, DSN, PII, customer data,
unique identifier, raw prompt, transcript, patch, or unredacted log.

Use a deterministic freshness decision for every retrieved record:

~~~text
if unauthorized/hidden -> omit
else if superseded/expired/rejected/quarantined -> stale_or_hidden
else if code evidence tree != current tree -> stale
else if review_at is reached -> due_for_review
else if last_verified_at is missing -> unknown
else -> current
~~~

Records marked `stale`, `due_for_review`, or `unknown` remain useful only as
leads. They require current-source validation before influencing a plan. When
two records in the same scope make incompatible claims, assign one
`contradiction_group`, preserve both records, and do not silently merge or
overwrite either. Current source or explicit user direction wins for execution;
the conflict remains `needs_review` until an authorized lifecycle operation
supersedes, rejects, or confirms a record.

## 7. Provider and Failure Behavior

Use a host knowledge adapter when one is available and authorized:

- search_knowledge for scoped semantic, code, and document search;
- query_doc_graph or traverse_graph for architectural and structural relationships;
- permanent-document ingestion for curated reference documents;
- a separate lesson retrieval/capture contract for candidate and durable agent learning.

Do not use a permanent-document upsert path as an uncontrolled session-memory append path.

### Optional local durable-memory fallback

When the separate lesson retrieval/capture contract is unavailable, use the
filesystem fallback under the approved memory root selected through
[shared storage configuration](storage-configuration.md). The locked JSON store
remains the dynamic retrieval record.
The only Markdown write permitted by this fallback is a new immutable file
under `extensions/ad_hoc/notes/`; it is a candidate queue item, not a canonical
memory edit.

1. Search related JSON records before creating or updating a lesson.
2. Require exact project, repository/tree, tenant, access scope, memory tier,
   and lesson scope before merging evidence.
3. Resolve repository → application/service → module → feature from the lookup
   route or current code context.
4. Publish a stable-observation-time, collision-resistant note with create-exclusive,
   no-overwrite semantics and a `source_document_candidate_not_canonical`
   status.
5. Preserve corrections and superseded records; never silently replace
   history or another producer's note.
6. Re-read the JSON record and note receipt. A collision with different
   content is `conflict`; a failure before persistence is `blocked`; and a
   failure after the store or note changed is `partial` with the actual side
   effect preserved in the receipt.
7. Queue Markdown only for non-tenant access with the exact singleton scope
   `project:<projectId>:read`. Preserve exact
   scope binding in the note and receipt; otherwise return `scope_restricted`.
8. Leave `MEMORY.md`, feature documents, `INDEX.md`, `route-map.json`, and
   `module-registry.json` unchanged. A separate lead-owned ingester performs
   deduplication, current-source revalidation, canonical merge, index updates,
   and promotion receipts.

This fallback is host-scoped durable candidate memory, not a substitute for
source validation or provider lifecycle states. Current code and explicit user
direction still override it.

If the provider is missing, slow, unauthorized, or low-confidence:

1. emit a `retrieval_result` with `unavailable`, `denied`, `failed`, or `invalid`
   as appropriate, including the safe limitation and scope that was attempted;
2. continue source-first with repository search, code tracing, tests, and runtime
   evidence;
3. do not invent a retrieval result, evidence card, provider response, or
   integration receipt;
4. capture a candidate only if privacy, scope, deduplication, conflict, and
   evidence rules pass;
5. retry indexing asynchronously through the adapter's bounded policy; never
   block the engineering fix on retrieval or indexing.

The source-first path must leave a receipt that separates what was attempted
from what was proven:

~~~yaml
source_first_receipt:
  retrieval_outcome: matched | no_relevant_memory | unavailable | denied | failed | invalid
  source_evidence:
    - repository: repository-name
      tree: commit-or-tree
      path: relative/path
      symbol: optional-symbol
      check: command-or-test
      result: pass | fail | not_run
  knowledge_limitation: optional-safe-summary
  learning: saved | updated | candidate_pending_review | none | rejected | redacted | blocked
  provider_integration: not_claimed
~~~

`provider_integration: not_claimed` is required when no provider result or
adapter receipt exists. Source evidence may support the task independently, but
it does not retroactively prove that retrieval succeeded. If source-first
checks also fail or cannot run, report the missing coverage and keep the task
open or blocked according to the engineering controller's closure rules.

## 8. Evaluation

Measure:

- retrieval precision/relevance and known-issue hit rate;
- stale, duplicate, irrelevant, and contradictory result rate;
- retrieval p50/p95 latency;
- lesson tokens injected per task;
- embedding/model tokens and cost;
- promotion, rejection, quarantine, and conflict rates;
- user correction and retrieval-feedback rate;
- source-first fallback success;
- time from repeated report to useful evidence card.

A successful system finds recurring issues faster while decreasing injected context and preserving
source/test verification.
