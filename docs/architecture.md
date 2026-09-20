# Architecture

Status: current
Audience: contributors, integrators, and reviewers of architectural changes
Canonical for: pipeline boundaries, ownership, and design invariants
Last verified: 2026-09-11

LogLathe is a human-in-the-loop workbench for turning security-log evidence into
reviewable normalization candidates. Its installed Python distribution and command
remain named `asim-forge` for compatibility.

The implementation is ASIM-first, but the source-side representation and evaluation
interfaces are designed so another normalization target can be introduced without
reinterpreting existing ASIM evidence.

## Current pipeline

```text
line-oriented logs
      |
      v
DeepParse mask synthesis and Drain clustering
      |
      v
schema-free parsed clusters, representative events, and parameter slots
      |                                  |
      v                                  v
independent cluster review       source-concept schema ranking
      |                          (rank, evidence, confidence,
      |                           selection or abstention)
      |
      +-- rejected / split / insufficient evidence --> no parser
      +-- approved --> frozen source queue --> assisted mapping review in Potato
                                                   |
                               draft / defer / extraction needed --> no parser
                               explicitly approved mapping --> parser spec + KQL
```

Semantic evaluation is a separate path:

```text
provider-neutral labelled cases + commit-pinned ASIM catalogue
                              |
                              v
                         MappingRequest
                              |
                  registered mapping approach
                              |
                              v
                SemanticMappingPrediction
                              |
                              v
                 shared metrics and reports
```

Expected labels never enter an ordinary `MappingRequest`. Diagnostic oracle modes
are applied explicitly by the evaluation harness and are recorded in the report.

## Decision boundaries

- **Cluster coherence is independent.** A reviewer decides whether examples form a
  stable event pattern without seeing an ASIM answer that could anchor that decision.
- **Schema ranking is not field mapping.** Schema candidates and abstention are
  produced before source roles or target fields are considered.
- **Suggestions are not approvals.** Rankings, confidence, evidence, and warnings
  remain distinct from human decisions.
- **Gold labels are not predictions.** Cases contain expected source semantics and
  ASIM projections; approach output lives in a separate prediction contract.
- **Compilation is deterministic.** Stage 1 approval alone cannot generate KQL. A
  complete, explicitly approved mapping is required.
- **Validation and deployment are separate.** Generated KQL is a candidate. LogLathe
  does not silently validate it in Sentinel or deploy it.
- **Provenance is part of every boundary.** Input, DeepParse, catalogue, approach,
  case, decision, and output revisions are recorded where they apply.

## Assisted mapping review

`mapping_review.py` consumes the existing verified annotation queue and registered
mapping approaches. Each immutable task binds source evidence, catalogue,
prediction, and optional native reference output. Reference answers are joined
after prediction and stay outside `MappingRequest`.

Source metadata comes from queue setup and is read-only during review. Mapping
setup pins available schema versions, the `TimeGenerated` policy, and optional
editable mapping defaults. One confirmed
schema applies to the entire template; a schema change requires confirmation again.
Required target fields use configured defaults where explicit suggestions are
absent; remaining targets are prefilled with blank sources or values. Approval and
compilation reject missing or blank mandatory and applicable conditional mappings.
The draft's `schema_rows` cache preserves edits when switching schemas; only its
active `rows` participate in validation and compilation.
Setup supplies `EventSchemaVersion` to the compiler. Platform-supplied fields and
explicit ingestion assumptions appear separately from missing mappings, without
weakening the native output conformance checks.

`potato_bundle.py` renders the same source evidence for both review stages. The
mapping task uses Potato's supported task-layout extension and one structured text
annotation; Potato owns identity, navigation, saving, and resume. Editable draft
records are distinct from immutable predictions. Only explicit mapping approval
allows the existing compiler to generate a candidate. Edits return an approval to
draft, and mismatched evidence revisions are rejected during compilation.

The mapping item exposes cluster, schema, and field review as revisitable tabs,
showing one panel at a time.
Its draft can revise cluster coherence using the existing `ReviewStatus` values;
the adapter carries that decision into the existing `ReviewDecision.status` gate.
Split, rejected, and insufficient-evidence items cannot compile, even if their
saved mapping status still says approved. Mapping edits are retained for repair.
Older drafts inherit the frozen queue's approved cluster status. The original
source-review snapshot stays separate, and these later assisted revisions do not
become blinded annotation input. Rebuilding or splitting actual cluster evidence
still requires a new queue and review revision.

The existing `FieldMapping` contract accepts exactly one source: a parameter slot,
a source-table column, a scalar constant, or another normalized output field,
followed by a supported conversion. Source columns are captured before output
assignments. Output references are ordered after their producers; missing or
circular dependencies are rejected. Setup defaults reuse this typed contract,
excluding template-specific slots, and remain editable review mappings. Absent
defaults are omitted from task fingerprints to keep older frozen bundles readable.
Legacy slot mappings remain readable. Compiler output retains the assisted review's
task, catalogue, cluster, and saved-state provenance. These engineering decisions
do not enter the independent-label promotion workflow.

Reviewer-selected spans are evidence for a mapping. A slot binding requires the
exact capture boundaries; a constant binding can retain a selected span wholly
inside verified literal template text. Shared template alignment identifies
literal and captured regions, rejecting ambiguous boundaries. Enumerated constant
outputs are selected from the pinned catalogue and validated before compilation.
The frozen template scopes constant outputs; unchanged sample values inside a
capture do not establish a template constant.

## Schema-ranking boundary

`DeepParseClusterer` owns no schema state. Build orchestration creates a
`SchemaRankingRequest` for each parsed cluster and writes the resulting predictions
to `schema-rankings.jsonl`.

The initial `source-concept` ranker consumes the cluster template and an explicit
candidate-schema list. It uses target-neutral source tokens, provides attributable
evidence, and abstains when there is no evidence or when the leading candidates tie.
It never predicts target fields.

For compatibility, `clusters.jsonl` still carries a legacy `schema_suggestion`
projection. `asim_forge.suggestions.suggest_schema` and
`asim_forge.source_normalization` are compatibility imports rather than the owners
of the current architecture.

## Source and target seams

The stable centre is a target-neutral description of the source event:

```text
unstructured text -> DeepParse ---------+
CEF/JSON/CSV ------> structure adapter --+-> source semantics -> ASIM adapter
                                                  +-----------> future target adapter
```

Today, ingestion supports line-oriented `.log` and `.txt` files. It removes decoded
byte-order marks, normalizes a bounded source vocabulary, and retains nearby
CEF/JSON-style keys as mapping context. Explicit structure-preserving adapters for
JSON, CEF/LEEF, syslog, key/value records, and multiline events remain future work.

Those adapters must preserve the original record, decoded structure,
transformations, and provenance. Target field names must not feed back into source
clustering or physical type detection.

ASIM target fields are currently flat. A future target such as OCSF may require
nested paths, arrays, class/type identifiers, enumerations, and a separately pinned
catalogue. Results for different targets must be evaluated against their own labels
and must not be compared as though the tasks had identical granularity.

## Package ownership

| Package | Responsibility |
| --- | --- |
| `clustering.py` | DeepParse adapter and schema-free parsed clusters |
| `schema_ranking/` | Schema requests, candidates, evidence, confidence, and abstention |
| `source_semantics/` | Target-neutral token normalization and source-frame vocabulary |
| `semantic_mapping/` | Mapping contracts, approaches, field ranking, metrics, and comparisons |
| `semantic_annotation/` | Blinded queue creation, typed decisions, integrity, and promotion |
| `catalog.py` | Commit-pinned Microsoft ASIM field catalogue |
| `reviews.py` | Canonical and Potato cluster-decision readers |
| `mapping_review.py` | Assisted mapping task preparation, draft validation, and adaptation to the existing compiler |
| `potato_bundle.py` | Shared source evidence and Potato task configurations/layouts |
| `compiler.py` | Deterministic parser-specification and KQL candidate generation |
| `benchmarking.py` | Evidence-separated corpus execution and reporting |
| `reference/` | Pinned source-table fixtures, native Kusto captures, and output comparison; reuses the build and Potato workflow |

Concrete workflows are documented in the [operator guide](operator-workflow.md),
[evaluation guide](evaluation.md), and [dataset-curation guide](dataset-curation.md).
The [reference pilot guide](reference-pilot.md) covers native functional checks and
how public samples enter the existing review workflow.
Future changes and deliberately deferred work belong in the [roadmap](../ROADMAP.md),
not in this description of the current system.
