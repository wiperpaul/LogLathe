# LogLathe roadmap

Status: active
Current milestone: 2 — continuous assisted ASIM review
Canonical for: remaining and deliberately deferred work
Last reviewed: 2026-09-10

This roadmap describes what remains to be built. Current behavior and durable
invariants are documented in [architecture](docs/architecture.md); completed
implementation sequences are retained under `docs/archive/`.

## Product principles

- Automate preparation; ask a person to confirm judgement, ambiguity, and risk.
- Preserve separate approval records for cluster coherence, semantic mapping,
  validation, and release readiness.
- Do not make reviewers reread the same evidence at each checkpoint.
- Prefill attributable metadata and suggestions instead of asking reviewers to
  retype information the system already has.
- Show why a suggestion was made, its confidence, and what remains unresolved.
- Allow **continue now**, **save for a specialist**, and **defer**. A governance
  boundary does not require a separate page or human session.
- Do not expose an ASIM suggestion before the independent cluster-coherence
  decision unless an explicit experiment demonstrates that doing so is safe.
- Keep adjudicated gold, development labels, upstream hints, parser-derived silver,
  and unlabelled diagnostics in separate evaluation tracks.

## Completed foundation

Milestone 1 established the cluster-review walking skeleton:

- deterministic clustering with representative events and typed slots;
- independent cluster decisions for approval, split, rejection, or insufficient
  evidence;
- portable Potato review tasks;
- `awaiting_mapping` for approved but incomplete work; and
- deterministic compilation only from complete, explicitly approved mappings.

The evaluation foundation now also includes a commit-pinned ASIM catalogue,
independent schema ranking, provider-neutral mapping contracts, blinded annotation
and promotion, controlled robustness, several transparent approaches and priors,
group-aware statistics, and evidence-separated benchmark reports.

## Milestone 2 — Continuous assisted ASIM review

Goal: after cluster approval, carry an eligible reviewer directly into an editable,
prefilled ASIM suggestion without losing context or weakening the Stage 1 decision.

### Immediate checkpoint — first reference pilot

The [OpenSSH reference pilot](docs/reference-pilot.md) now provides pinned public
inputs, ten controlled variants, native Kusto reference capture/replay, value and
row comparisons, and bounded catalogue checks. Public samples pass through the
existing build, Potato review, annotation queue, and reviewed compilation workflow.

The next checkpoint requires a security engineer to review the source clusters.
Then prepare the existing annotation queue for approved clusters, resolve
reference/candidate disagreements, and turn reviewed findings into regression
fixtures. Expand to two additional source families after that first loop. This
pulls a bounded slice of milestone 3 forward; it does not complete the continuous
mapping-review UI.

### Source onboarding and preparation

- Introduce a source-onboarding record for vendor, product, source table, message
  field, format, and collection context.
- Prepare ranked schema and field suggestions before review, recording approach
  identity, confidence, evidence, and unresolved warnings.
- Identify mandatory and recommended ASIM fields that cannot yet be populated.
- Keep suggestions immutable; accepting or editing one creates a separate decision
  record rather than overwriting provider output.

### Progressive review experience

Keep cluster review brief and focused on source defects: malformed records,
incorrect event boundaries, split multiline events, unrelated examples grouped
together, and extraction that loses useful values. Prioritize suspicious clusters
and make the original record and adjacent source context available when diagnosing
boundary problems. Single-example clusters need an explicit indication that
variation has not been demonstrated. Preserve explicit cluster decisions while
reducing repetitive review of similar, apparently coherent patterns.

1. Show cluster evidence without exposing the ASIM answer.
2. Save the cluster decision as an independently auditable checkpoint.
3. On approval, open an **ASIM suggestion** step while keeping the same template,
   events, and slots visible.
4. Present source metadata, ranked schemas, mappings, and transforms as typed,
   editable controls rather than raw JSON.
5. Allow a general reviewer to continue, defer, or send the task to an ASIM
   specialist.
6. Save the mapping decision separately, then offer parser preview and validation.

Reject, split, and insufficient-evidence decisions do not open mapping review.
Changing an approved cluster invalidates dependent suggestions and mappings rather
than silently reusing stale work.

### Lifecycle and API work

- Split the combined review record into `ClusterDecision`, `SourceMetadata`,
  `AsimSuggestion`, and `AsimMappingDecision` records.
- Link records with stable source and cluster IDs plus explicit revisions.
- Add lifecycle states such as `awaiting_cluster_review`, `awaiting_mapping`,
  `mapping_in_progress`, `awaiting_validation`, and `invalidated`.
- Retain canonical JSONL export while the UI edits typed fields.
- Record resumed work against the exact evidence and suggestion revision originally
  shown.

### Evaluation needed before provider selection

- Assemble the first 30–50 independently reviewed cases across multiple source
  families, including `unresolved` and `not_applicable` outcomes.
- Lock group assignments and held-out labels before tuning approaches.
- Report per-schema and minority-role results beside aggregate metrics.
- Measure median active review time, acceptance/edit rates, deferral, and
  invalidation—not only offline mapping scores.

Exit criteria: an approved cluster can proceed through an attributable, editable
mapping review without raw JSON editing or repeated evidence reading, while cluster
and mapping approvals remain independently auditable.

## Milestone 3 — Schema-aware validation and parser refinement

Goal: turn an approved mapping into a parser candidate with actionable checks.

- Enrich the pinned catalogue with separately versioned human-readable schema
  descriptions where authoritative sources permit it.
- Validate target fields, types, requirements, aliases, enumerations, and
  normalization rules.
- Generate parser preview and sample normalized output.
- Run deterministic fixtures and ASIM schema/data tests where available.
- Return failures to the mapping rows that caused them.
- Require an explicit validation decision; successful generation is not approval.

Exit criteria: every candidate reports target coverage, test evidence, warnings,
and the exact input, catalogue, mapping, and generator revisions used.

Current partial implementation: the reference pilot executes KQL natively and
checks mandatory fields, physical types, enumerations, and IP addresses on both
reference and candidate output. Official ASIM tester integration, conditional
requirements, aliases, and complete validation decisions remain outstanding.

## Milestone 4 — Agreement, packaging, and release gates

Goal: make reviewed candidates safe to collaborate on and promote.

- Support reviewer roles, assignment, disagreement resolution, and sign-off.
- Produce source-controlled parser packages and reviewable diffs.
- Test against an authorized Sentinel workspace when configured.
- Add release policy, versioning, rollback metadata, and deployment approval.
- Keep deployment opt-in and separate from review completion.

Exit criteria: a candidate can move from reviewed evidence to a reproducible release
package with explicit ownership and no implicit deployment.

## Later target-neutral expansion

OCSF is the intended next normalization target after the ASIM evaluation and review
loop is credible. It requires versioned target/catalogue interfaces, nested target
paths and arrays, target-specific validation, and results reported separately from
ASIM rather than compared as equivalent tasks.

Existing ASIM fixtures and artifacts need a compatibility reader before any public
contract changes. Source semantics and evidence must remain independent of both
target vocabularies.

## Deliberately deferred

- Automatic approval based only on confidence.
- Automatic deployment of generated parsers.
- Requiring one person to perform cluster, mapping, validation, and release review.
- A large vendor-specific semantic rule engine without ablation evidence.
- Unrestricted model or agent access to operational systems.
- Production support for structure-preserving JSON/JSONL, CEF/LEEF, RFC syslog,
  generic key/value, CSV, Windows Event, and multiline stack-trace adapters.
- Promoting broad syntactic masks for ports, users, hostnames, actions, or outcomes
  when their meaning belongs in semantic mapping.

Future structure adapters must retain the original record, decoded structure,
transformations, and provenance. Rare events must become explicit outliers,
abstentions, or review tasks rather than being silently discarded.
