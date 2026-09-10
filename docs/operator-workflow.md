# Operator workflow

Status: current
Audience: security and detection engineers running the parser workflow
Canonical for: build, cluster review, catalogue sync, compilation, and generated artifacts
Last verified: 2026-09-10

LogLathe's installed command is `asim-forge`. The distribution and CLI retain that
name for compatibility with existing environments and artifacts.

## Prerequisites

- Python 3.11 through 3.14
- [uv](https://docs.astral.sh/uv/)
- line-oriented `.log` or `.txt` inputs

Install the locked dependencies:

```console
uv sync --locked
```

Generated artifacts and review state can contain sensitive telemetry. Keep them
below the ignored `artifacts/` directory or in another access-controlled location.

## Build clusters

Build clusters and a portable review bundle from the checked sample:

```console
uv run asim-forge build examples/sample-logs --output artifacts/demo --system demo
```

`--system` is a stable source identifier. It becomes part of artifact provenance,
so avoid values that reveal labels an eventual blinded annotator should not see.

The sample contains nine events in three clusters. The command writes:

| File | Purpose |
| --- | --- |
| `clusters.jsonl` | Stable cluster IDs, templates, representative events, parameter slots, and a compatibility schema suggestion |
| `schema-rankings.jsonl` | Independent schema-ranking predictions, evidence, confidence, and abstention |
| `manifest.json` | Inputs, counts, masks, DeepParse revision, and output provenance |
| `potato/items.jsonl` | Portable Stage 1 review items |
| `potato/config.yaml` | Potato task configuration |

## Review cluster coherence

Install the optional review dependency and start Potato:

```console
uv sync --extra review
uv run potato start artifacts/demo/potato/config.yaml -p 8000
```

The task shows templates, representative events, and parameter slots. A reviewer
can approve a cluster, request a split, reject it, ask for more evidence, or add
notes. It does not ask the cluster reviewer to select ASIM fields.

Potato stores state below
`potato/annotation_output/<reviewer>/user_state.json`. The compiler can read that
file directly, but a Stage 1 approval normally becomes `awaiting_mapping` because it
does not contain the later source metadata and field decisions:

```console
uv run asim-forge compile artifacts/demo/clusters.jsonl artifacts/demo/potato/annotation_output/<reviewer>/user_state.json --output artifacts/demo/compiled
```

Canonical engineering-review JSONL remains available for reproducible workflows;
the checked example is
[`examples/sample-review/reviews.jsonl`](../examples/sample-review/reviews.jsonl).

## Sync the ASIM catalogue

LogLathe consumes the CSV used by Microsoft's `ASimSchemaTester`; it does not keep
a hand-copied field list.

```console
uv run asim-forge catalog sync --output artifacts/asim-catalog --revision master
```

A branch or tag is resolved to an immutable Azure-Sentinel commit. The command
stores the upstream CSV unchanged and records the resolved revision, content
SHA-256, schema coverage, and field count. Use the resolved 40-character commit in
later runs when exact reproduction matters. `GITHUB_TOKEN` is optional for
occasional public access.

The catalogue snapshot contains:

| File | Purpose |
| --- | --- |
| `asim-catalog.csv` | Unchanged commit-pinned upstream catalogue |
| `catalog-manifest.json` | Source revision, integrity hash, and catalogue coverage |

Human-readable schema descriptions and semantic schema versions are not present in
the tester CSV. They remain a separate future enrichment concern.

## Compile reviewed mappings

Compile the checked engineering decisions:

```console
uv run asim-forge compile artifacts/demo/clusters.jsonl examples/sample-review/reviews.jsonl --output artifacts/demo/compiled
```

The sample decisions produce two parser candidates and skip one rejected cluster.
For each approved, fully mapped review, compilation writes:

- one `*.parser-spec.json` specification;
- one `*.kql` parser candidate; and
- `compile-manifest.json`, including generated outputs and rejected, deferred, and
  `awaiting_mapping` counts.

Compilation is deterministic, but generation is not validation. Treat every KQL
file as a reviewable candidate until schema-aware and Sentinel execution checks have
been completed outside this workflow.

## Semantic dataset and evaluation workflows

Cluster evidence can be promoted into blinded semantic annotation tasks only after
cluster review. Use the [dataset-curation guide](dataset-curation.md) for queue,
decision, promotion, and grouped-split commands.

Use the [evaluation guide](evaluation.md) for approach comparison, controlled
robustness, corpus benchmarking, metrics, and release-report behavior.
