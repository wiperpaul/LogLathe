# Semantic dataset curation

Status: current
Audience: dataset curators, annotators, and evaluation maintainers
Canonical for: mapping cases, evidence levels, annotation, promotion, grouping, and splits
Last verified: 2026-09-10

This is the canonical guide for turning reviewed Stage 1 clusters into
provider-neutral semantic-mapping cases. It covers the case contract, evidence
levels, blinded annotation, promotion, and leakage-resistant dataset splits.

A case that passes structural validation is not automatically gold evidence.
Expected labels support approach-quality claims only when they were independently
reviewed, adjudicated, promoted with an intact evidence chain, and evaluated on a
locked grouped split.

Operational events and reviewer records may be sensitive. Keep generated queues,
submissions, promoted datasets, and comparison reports under the ignored
`artifacts/` directory or in another access-controlled location. Commit only
synthetic or suitably sanitized cases.

## Evidence levels

Keep the purpose and claim boundary of each dataset visible in its manifest and
provenance:

| Evidence | Repository example or origin | Appropriate use | Unsupported claim |
| --- | --- | --- | --- |
| Contract smoke fixture | [`examples/evaluation/semantic-mapping-cases.jsonl`](../examples/evaluation/semantic-mapping-cases.jsonl) | Validate the JSONL contract and exercise the comparison harness. | Relative or production approach quality. |
| Checked development labels | The `semantic-development` track, including the [`asim-cef-dev` manifest](../evaluation/corpora/asim-cef-dev/manifest.json) | Error discovery, diagnostics, and development comparison against documented source-field meaning and a pinned ASIM catalogue. | ASIM correctness or approach selection: these labels are not independently adjudicated, and the checked corpus has no grouped split. |
| Single-review calibration | Promotion with `--allow-single-review`; provenance is `human_review`. | Calibrate instructions and find disagreements early. | Held-out approach-selection evidence. |
| Adjudicated evaluation evidence | Default promotion after two independent annotations and a final adjudication; provenance is `adjudicated`. | Held-out comparisons when paired with verified groups and a locked split. | Broad production quality unless the sampled systems and outcomes support that generalization. |

The fixture format is shared across these levels so the same tooling can load
them. The dataset's review provenance and split determine what conclusions are
valid. `semantic-development` labels remain unadjudicated even when a frozen split
makes their comparison less prone to leakage; only `semantic-gold` is eligible for
ASIM correctness claims. In particular, retrieval results on the current checked
development corpus are diagnostic because near-identical source-family siblings
are not separated by a grouped split.

## Semantic mapping case contract

The version 1 contract is canonical JSONL: each non-empty line is one independent
`SemanticMappingCase`. Pydantic rejects unknown fields, and the Python writer emits
deterministic, key-sorted JSONL.

| Part | Contents |
| --- | --- |
| `format_version` and `case_id` | The contract version and a unique stable case identifier. |
| `catalogue_revision` | The immutable 40-character Azure-Sentinel commit against which ASIM targets were labelled. |
| `input` | The frozen template, representative events, parameter slots, and source metadata visible to every approach. |
| `expected.source_semantics` | Source-oriented meanings attached to slots, meaningful template constants, or derived concepts. |
| `expected.asim_fields` | Projections from source-semantic IDs to fields in the expected ASIM schema, including normalized constants where appropriate. |
| `provenance` | Label origin plus non-provider decision and annotator references. |

The `input` must not include the cluster's provisional schema suggestion, an
existing parser mapping, candidate rankings, scores, confidence, prompts, or an
approach prediction. Predictions belong in comparison reports, not in cases or
annotation tasks.

Every source-semantic label has a unique `semantic_id`, a `role`, at least one
evidence citation, and one of these origins:

- `slot`: its locator must identify a stored parameter slot.
- `template_constant`: its locator must occur in the stored template. This is how
  a phrase such as `connection allowed` can supply a normalized `EventResult` even
  though it is not a parser slot.
- `derived`: a concept supported by the recorded evidence rather than one literal
  slot or constant.

Every ASIM projection must refer to a known source-semantic ID. Duplicate
`(semantic_id, asim_field)` pairs are invalid, and every projection requires an
evidence citation. During promotion, schema and field names are checked against the
pinned catalogue, and constant values are checked against field types and allowed
values. Case provenance records one of `human_review`, `adjudicated`, `synthetic`,
or `imported` plus any decision, annotator, and explanatory references.

### Dispositions

Annotators must record one explicit outcome:

- `mapped` requires an ASIM schema, at least one source-semantic label, at least one
  ASIM field projection, and no unresolved reasons.
- `unresolved` requires one or more reasons. It may retain useful partial semantic
  work; do not force an answer when the evidence is insufficient or ambiguous.
- `not_applicable` records a deliberate no-ASIM outcome. It cannot contain an ASIM
  schema, ASIM field targets, or unresolved reasons.

This makes abstention measurable instead of treating every missing mapping as an
error. The source-role vocabulary is intentionally open in format version 1.

Validate a standalone case file with:

```console
uv run asim-forge evaluation validate path/to/cases.jsonl
```

The Python API exposes `load_semantic_mapping_cases` and
`write_semantic_mapping_cases` for typed loading and deterministic output.

## Pilot composition

Aim for an initial **30-50-case calibration set** before tuning mapping behavior.
Draw it from several independent source or product families rather than selecting
the most frequent templates from one corpus. This target is for instruction
calibration and error discovery; it is not, by itself, enough to establish a
statistically reliable winner.

Include a deliberate mix of:

- authentication events: success, failure, logout, invalid users, authentication
  methods, and ambiguous actor/target relationships;
- network sessions: source and destination addresses and ports, allow/deny
  constants, missing direction, and reporting-device ambiguity;
- audit events: actors, target objects, actions, results, policy or configuration
  changes, and partial evidence;
- boundary outcomes: genuine `unresolved` and `not_applicable` cases.

Public research corpora, Microsoft parser-development samples, and existing parser
outputs are candidate evidence, not independent ASIM labels. Incident, anomaly,
prompt-injection, source-corpus, and template labels may guide sampling, but must
not be copied into semantic or field expectations. After the vocabulary and review
instructions stabilize, build a larger multi-source set and report per-family
results for approach-selection claims.

## 1. Freeze evidence and prepare a blinded queue

Start with a completed build directory, one authoritative cluster-review decision
per reviewed cluster, and a commit-pinned ASIM catalogue. Assign the strongest
plausible leakage group before anyone labels cases or views approach predictions.
For a pilot, this is normally the source or product family.

```powershell
uv run asim-forge evaluation queue artifacts/source-build path/to/reviews.jsonl `
  --catalog artifacts/asim-catalog `
  --group-id vendor-product.authentication `
  --group-strategy source-family `
  --output artifacts/semantic-annotation/vendor-product
```

Only clusters whose coherence review is `approved` enter the queue. `rejected`,
`needs_split`, `insufficient_evidence`, and unreviewed clusters stay out. Approval
says the examples form a usable event pattern; it does not provide a semantic or
ASIM answer.

The queue's `system` defaults to the build manifest value. When onboarding metadata
exists, pass `--vendor`, `--product`, `--source-table`, and `--message-field`. If the
build system name contains a corpus or outcome label, replace it with a neutral
identifier using `--system`.

The command creates:

| File | Purpose |
| --- | --- |
| `tasks.jsonl` | Typed, unlabeled tasks containing the frozen input, catalogue revision, preassigned group, and build/review provenance. |
| `queue-manifest.json` | Protocol and catalogue revisions, selection counts, source hashes, output hashes, and queue-level provenance. |
| `submission-schema.json` | The JSON Schema that every annotation and adjudication decision must satisfy. |

### Blinding and integrity boundary

Queue creation replaces representative-event filenames with stable neutral names
such as `source-001`; original paths stay in the sealed build evidence. It omits
the cluster schema suggestion, historical engineering mappings, and all semantic
approach outputs. The frozen template, representative events, parameter slots, and
source metadata remain available because they are the evidence annotators must
interpret.

Each task carries:

- an `input_fingerprint` over all approach-visible evidence; and
- a `task_revision` binding that fingerprint to the case ID, catalogue revision,
  pre-label group, annotation protocol, and build/review provenance.

Every decision repeats both values. The queue manifest hashes the canonical task
file and submission schema and records hashes for its source build, cluster, and
review artifacts. Promotion verifies the manifest, hashes, task count, catalogue
revision, and task metadata before it considers any label. Deleted, inserted,
spliced, regrouped, stale, or schema-altered inputs are rejected.

The build-level queue command accepts only `source` and `source-family` grouping
because it assigns one conservative group to every approved cluster in that build.
The broader split contract also supports `template-family` and `manual`, but those
require a separate per-case curation process; do not mislabel a build-wide group as
a template family.

If any template, example, slot, source metadata, cluster review, group, provenance,
protocol, or catalogue revision changes, create a new queue. Never append
predictions to an existing task.

## 2. Record independent decisions

Use the generated `submission-schema.json` to validate decision JSONL. A decision
identifies the task and is either an independent `annotation` or the final
`adjudication`.

Review each task in this order:

1. Choose `mapped`, `unresolved`, or `not_applicable`.
2. Describe the source semantics, including meaningful static template text as
   well as parameter slots.
3. For `mapped`, project those semantics into fields from the pinned ASIM
   catalogue.
4. Cite the template, representative event, source metadata, catalogue, or review
   evidence supporting each choice.
5. Collect annotations from at least two distinct reviewers, then record a final
   adjudication that references those source decisions and resolves disagreements.

An annotation cannot reference another decision. An adjudication must reference at
least two annotations for the same case, catalogue, input fingerprint, and task
revision, and those annotations must come from distinct reviewers. Decision IDs
must be unique. Record genuine ambiguity as `unresolved` and a defensible no-ASIM
outcome as `not_applicable`.

Raw JSONL and the generated schema are suitable for a small technical pilot and
for integration with an annotation system. They are curator tooling, not a
finished general-review interface.

## 3. Promote reviewed cases

Promotion joins eligible decisions to the exact task evidence and validates their
targets against the same pinned catalogue:

```powershell
uv run asim-forge evaluation promote `
  artifacts/semantic-annotation/vendor-product `
  path/to/decisions.jsonl `
  --catalog artifacts/asim-catalog `
  --output artifacts/semantic-pilot/vendor-product
```

The output directory contains:

| File | Purpose |
| --- | --- |
| `cases.jsonl` | Provider-neutral semantic-mapping cases selected from completed decisions. |
| `case-groups.jsonl` | The pre-label group ID and strategy for each promoted case, kept outside the case contract. |
| `promotion-manifest.json` | Input, promotion, label-source, and skipped-task counts plus hashes for the evidence chain and outputs. |

Default promotion selects only an adjudication. Unsubmitted tasks and tasks still
awaiting adjudication are reported as skipped; malformed or stale decisions are
rejected rather than converted into labels.

For early instruction calibration only, a curator may explicitly promote exactly
one available annotation for a case:

```powershell
uv run asim-forge evaluation promote `
  artifacts/semantic-annotation/vendor-product `
  path/to/decisions.jsonl `
  --catalog artifacts/asim-catalog `
  --output artifacts/semantic-pilot/vendor-product-calibration `
  --allow-single-review
```

Those cases carry `human_review` provenance. Keep them out of approach-selection
claims until they receive independent review and adjudication.

## 4. Author and lock grouped splits

Combine promoted cases from multiple independent families, preserving each
`case-groups.jsonl` assignment. Split membership stays outside the case JSONL so a
new experiment can choose different partitions, but the pre-label group assignment
cannot change after labels or approach behavior are visible.

A split manifest has this shape:

```json
{
  "format_version": "1",
  "split_id": "security-pilot.source-family.v1",
  "catalogue_revision": "027a0f9338bfabcb27b784571b771c54572ebf01",
  "group_strategy": "source-family",
  "reference_partitions": ["train"],
  "entries": [
    {
      "case_id": "vendor-a.authentication.accepted",
      "group_id": "vendor-a.ssh-authentication",
      "partition": "train"
    },
    {
      "case_id": "vendor-b.authentication.failed",
      "group_id": "vendor-b.ssh-authentication",
      "partition": "test"
    }
  ]
}
```

The supported partitions are `train`, `validation`, and `test`; reference
partitions default to `train`. The manifest must:

- cover every case exactly once and contain no unknown cases;
- keep every group wholly within one partition;
- use one grouping strategy matching the promotion sidecars;
- preserve every `(case_id, group_id)` assignment; and
- use the same catalogue revision as all cases.

Prefer an entire source or product family as the leakage unit when enough data is
available. A template-family split is acceptable for experiments within one source
only when mechanically similar templates stay together. Resolve uncertainty by
using the larger, more conservative group.

Validate the promoted artifacts, exact split coverage, groups, and catalogue
revision together:

```powershell
uv run asim-forge evaluation validate path/to/cases.jsonl `
  --split path/to/split.json `
  --case-groups path/to/case-groups.jsonl `
  --promotion-manifest path/to/promotion-manifest.json
```

Evaluate a held-out partition with the same integrity sidecars:

```powershell
uv run asim-forge evaluation compare path/to/cases.jsonl `
  --split path/to/split.json `
  --case-groups path/to/case-groups.jsonl `
  --promotion-manifest path/to/promotion-manifest.json `
  --partition test `
  --catalog artifacts/asim-catalog `
  --output artifacts/semantic-comparison.json
```

Reference-dependent approaches receive only cases in `reference_partitions`;
evaluation cases cannot also be references. Direct approaches receive the same
selected evaluation cases but no reference labels. A comparison without a split
remains useful as a harness or development diagnostic, but it is not held-out
approach-comparison evidence.

Lock final test labels before changing mapping approaches. Tune only against the
training and validation partitions, and preserve the build, review decisions,
catalogue snapshot, queue, submissions, promotion artifacts, group sidecars, and
split manifest as one auditable evidence chain. See the
[evaluation guide](evaluation.md) for comparison metrics, controlled conditions,
benchmark tracks, and release policy.
