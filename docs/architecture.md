# Architecture

Status: current
Audience: contributors, integrators, and reviewers of architectural changes
Canonical for: pipeline boundaries, ownership, and design invariants
Last verified: 2026-09-10

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
      +-- approved but not mapped ------------------> awaiting_mapping
      +-- approved with complete mapping ----------> parser spec + KQL candidate
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
| `compiler.py` | Deterministic parser-specification and KQL candidate generation |
| `benchmarking.py` | Evidence-separated corpus execution and reporting |

Concrete workflows are documented in the [operator guide](operator-workflow.md),
[evaluation guide](evaluation.md), and [dataset-curation guide](dataset-curation.md).
Future changes and deliberately deferred work belong in the [roadmap](../ROADMAP.md),
not in this description of the current system.
