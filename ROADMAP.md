# LogLathe roadmap

Status: active
Current milestone: 2 — continuous assisted ASIM review
Canonical for: remaining and deliberately deferred work
Last reviewed: 2026-10-03

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

The core assisted mapping path is also implemented: Potato presents Cluster /
Schema / Mappings tabs, schema-specific target dropdowns and required rows,
span-based slot/constant selection, separate schema drafts, and explicit mapping
approvals. Source context and supplied fields come from frozen configuration;
editable defaults support template exceptions. An approved, complete mapping
compiles through the existing parser-specification and KQL path.

Native reference coverage now spans the five source families in the
[fixture inventory](evaluation/reference/README.md). Jev schema trials have fixed
definitions, offline previews, request-addressed caches, and replay verification.
These remain advisory experiments alongside deterministic review and compilation.

## Milestone 2 — Continuous assisted ASIM review

Goal: after cluster approval, carry an eligible reviewer directly into an editable,
prefilled ASIM suggestion without losing context or weakening the Stage 1 decision.

### Immediate checkpoint — resolve review findings

Use the [reference workflow](docs/reference-pilot.md) for the existing build,
Potato review, annotation queue, reviewed compilation, and native comparison loop.
The first local OpenSSH cluster review is complete; the newer mixed-source trial
clusters remain unreviewed.

Prioritize a security engineer's review of concrete schema/mapping disagreements
and extraction gaps. The [latest Jev assessment](docs/research/jev-expanded-assessment-2026-10-02.md)
leaves one Cisco ASA SSH internal-error disconnection classified as NetworkSession
against the parser's Authentication/Logoff reference. Its Success result is also
a useful mapping question. Record an independent interpretation and rationale
before changing definitions or treating the parser as a semantic answer key.

Turn accepted corrections into small development regression cases, and compare a
reviewed candidate against native output. Continue cluster review where examples
suggest malformed records, bad boundaries or mixed meanings. All current Jev
source-family predictions are now inspected; reserve any additional test family
before using those outcomes to tune definitions.

The current manual review loop is usable. Automatic transition from the initial
cluster task, arbitrary text-span extraction repair, specialist assignment, and
inline parser validation remain outstanding. Keep further work tied to observed
review problems and reuse the existing workflow and preservation tooling.

### Source onboarding and preparation

- Consolidate the existing queue source metadata and review setup into a reusable
  source-onboarding profile, adding format and collection context where useful.
  Vendor, product, source table and message field are already frozen and read-only
  during mapping review; schema versions, timestamp policy and default/supplied
  mappings are already configured before task preparation.
- Extend the current required-field guidance to recommended fields and richer
  source-context coverage. Suggestions and catalogue snapshots are already frozen
  separately from editable Potato decisions.

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

Initial reject, split, and insufficient-evidence decisions do not enter the approved
queue. The mapping task can revisit an approved cluster's decision, retaining edits
while blocking approval and compilation. Rebuilding or splitting source evidence
requires a new queue and review revision; previous approvals cannot be reused.

### Lifecycle and API work

Frozen source tasks, suggestions, setup and editable drafts already have separate
records and provenance checks. Remaining lifecycle work should extend those
contracts when needed:

- Connect preparation and task transitions without losing the original approval
  records or requiring repeated evidence review.
- Add explicit ownership, specialist assignment, and validation-stage state.
- Make rebuilding, requeueing and dependent-work invalidation easier to inspect.
- Preserve canonical exports, stable IDs and exact evidence revisions on resume.

### Evaluation needed before provider selection

- Assemble the first 30–50 independently reviewed cases across multiple source
  families, including `unresolved` and `not_applicable` outcomes.
- Lock group assignments and held-out labels before tuning approaches.
- Report per-schema and minority-role results beside aggregate metrics.
- Measure median active review time, acceptance/edit rates, deferral, and
  invalidation—not only offline mapping scores.

Exit criteria: cluster approval opens a prepared, attributable mapping task without
manual queue/task commands or repeated evidence reading. Reviewers can edit,
defer or route work while cluster and mapping approvals remain independently
auditable. The existing manually prepared mapping task supplies the core controls.

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

Current partial implementation: mapping approval and compilation check compatible
targets, conversions, constants, mandatory fields and applicable conditional
dependencies. The reference workflow executes KQL natively and checks mandatory
fields, physical types, enumerations, and IP addresses on both reference and
candidate output. Official ASIM tester integration, complete native conditional
requirements, aliases, inline validation and validation decisions remain outstanding.

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
