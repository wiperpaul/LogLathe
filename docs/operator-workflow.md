# Operator workflow

Status: current
Audience: security and detection engineers running the parser workflow
Canonical for: build, cluster and mapping review, catalogue sync, compilation, and generated artifacts
Last verified: 2026-09-11

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

## Review ASIM mappings in Potato

After cluster review and catalogue sync, freeze the approved source evidence with
the existing queue command. Vendor, product, source table, and message field are
required setup values carried by that queue. Reviewers see them as read-only context.

Create `artifacts/demo/review-setup.json` with the available target schema versions
and timestamp policy before preparing the mapping task. For example:

```json
{
  "schema_versions": {"Authentication": "0.1.3"},
  "time_generated": "map"
}
```

Only schemas with a version in this setup are selectable. The catalogue CSV does
not contain semantic schema versions; setup must pin the versions your target
supports. Prepare assisted suggestions from the queue:

```console
uv run asim-forge evaluation queue artifacts/demo artifacts/demo/potato/annotation_output/<reviewer>/user_state.json --catalog artifacts/asim-catalog --group-id demo-family --group-strategy source-family --vendor Demo --product Gateway --source-table Syslog --message-field SyslogMessage --output artifacts/demo/annotation
uv run asim-forge review prepare artifacts/demo/annotation --catalog artifacts/asim-catalog --setup artifacts/demo/review-setup.json --cluster-reviews artifacts/demo/potato/annotation_output/<reviewer>/user_state.json --output artifacts/demo/mapping
uv run potato start artifacts/demo/mapping/potato/config.yaml -p 8000
```

Open `http://localhost:8000` and enter your reviewer name. This uses Potato's task
layout support, login, navigation, autosave, and `user_state.json`. Source templates,
examples, slots, and the original cluster notes accompany the editable suggestion.
The separate task directory preserves the completed cluster review. Within each
mapping item, **Cluster**, **Schema**, and **Mappings** are revisitable tabs, with
one panel visible at a time. Open **Cluster** to inspect the original Potato evidence tables, change the
cluster decision, and record why. A split, rejection, or request for more evidence
blocks mapping approval and compilation while retaining mapping edits. Restoring
cluster approval requires schema confirmation and mapping approval again.

The revised cluster decision is saved with this assisted engineering review; the
original source-review snapshot is preserved. It is not automatically synchronized
back into a separately running cluster task. Actual splitting still requires
correcting/rebuilding clusters and preparing new evidence; changed cluster files
cannot reuse the old mapping approvals. Automatic transition from the initial
cluster task into a prepared mapping task remains future work.

First assess and confirm **one schema per template**. Confirmation opens the
**Mappings** tab. Return to **Schema** to reassess the choice. Changing the schema
requires confirmation again and loads field suggestions projected against that
schema's catalogue. Rows and evidence from the previous schema do not seed it,
even when field names overlap. Switching back restores the first schema's edits.
Each schema's draft is saved independently, including edits to shared fields.

Select a mapping in the compact list, then select its exact source text in a
representative event. NER-style highlights show the template's extracted slots
and their ASIM labels. A matching span assigns that existing slot. If the
selection falls outside a slot or covers only part of one, the review retains
the selected text and offsets but cannot approve it as a parser mapping; mark
**Needs extraction work**. Expand **Template and extracted slots** to assign a
slot directly when an example cannot be selected. Source-table fields and
fixed values remain available from the source dropdown for exceptional mappings.
The selected source span is review evidence, not a new extraction rule; a later
cluster/extraction change still requires a fresh review bundle.
Select ASIM fields from the catalogue dropdown, grouped by requirement class, and
check their conversions. Setup-owned fields appear in a collapsed section and are
excluded from the dropdown. Fields requiring unsupported conversions are also
excluded. Only the active schema's compatible mappings are eligible for compilation;
inactive drafts remain saved in Potato. Parser naming remains under **Parser settings**.

This arrangement uses Potato's supported
[task-layout customization](https://potatoannotator.readthedocs.io/en/latest/configuration/ui_configuration/#task-layout-customization).
The span interaction follows Potato's
[NER span-labeling pattern](https://potatoannotator.readthedocs.io/en/latest/annotation-types/schemas_and_templates/#text-span-selection-span)
while the linked ASIM mapping draft remains in Potato's existing annotation state.
The [MultiCoNER](https://www.potatoannotator.com/showcase/complex-ner) and
[CrossRE](https://www.potatoannotator.com/showcase/crossre-cross-domain-relations)
showcase adaptations provide references for keeping source text and labels together.
Potato's [conditional questions](https://potatoannotator.readthedocs.io/en/latest/configuration/conditional_logic/)
support follow-ups within an item; its
[workflow phases](https://potatoannotator.readthedocs.io/en/latest/workflow/surveyflow/)
cover consent, training, annotation, and related study pages. The per-cluster approval
dependencies are checked by LogLathe's review adapter and compiler. The tabs are
LogLathe's layout choice, not a prescribed Potato workflow.

The mapper's internal source roles are available under suggestion evidence. They
describe inferred event meaning and are not OCSF fields or an ASIM-to-OCSF mapping.
Reviewers do not edit that vocabulary; use notes to flag an incorrect interpretation.
Each mapping can use an extracted slot, a source-table column, or a fixed value.
Choose **Needs extraction work** when the necessary value is embedded in literal
text or needs an unsupported derivation; describe the gap in the notes. Choose
**Defer** when evidence or specialist input is needed. **Approve mapping** accepts
the current mappings; subsequent edits return the task to draft. Use Potato's
**Next** button to continue. Its progress counter counts saved tasks, including
drafts; the mapping status shows whether you approved the mapping.

`EventSchema`, `EventSchemaVersion`, `EventVendor`, and `EventProduct` are supplied
from the chosen schema and frozen setup. `Type` is a standard Log Analytics column.
These appear separately from reviewer mappings. Mandatory fields use the
pinned catalogue's common fields plus schema-specific overrides. Missing required
targets are prefilled with blank sources or values and marked **Required · Needs
value**. Conditional fields with a mapped dependency are also prefilled. Approval
requires completing these rows; use **Needs extraction work** or **Defer** when the
evidence cannot support a mapping. ASIM guidance confirms that schema
requirements differ: AuditEvent additionally requires `Operation` and `Object`.
See [common fields](https://learn.microsoft.com/en-us/azure/sentinel/normalization-common-fields)
and the [AuditEvent schema](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-audit).

The `time_generated` setup option has three values:

| Value | Review behavior |
| --- | --- |
| `map` (default) | Keep `TimeGenerated` as a mapping requirement. |
| `source` | Use the existing source-table column; it is shown as supplied. A reference fixture must declare it as `datetime`. |
| `ingestion` | Suppress the source-mapping requirement and show the explicit assumption that ingestion will supply the column. |

The ingestion option records an assumption in the parser specification and KQL
comment. It neither creates a DCR nor replaces a missing timestamp with query time.
The column must exist when the query-time parser runs. Azure Monitor documents
adding a missing `TimeGenerated` to the DCR transformation when creating a custom
table; verify your actual ingestion setup against that contract.
([Custom tables](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/create-custom-table))
`EventStartTime` and `EventEndTime` remain mapping requirements: map source event
times, or explicitly map the available `TimeGenerated` column when the ASIM fallback
is appropriate.

The default `semantic-frame` approach is shared with the evaluation harness.
`--approach direct-lexical` and `--approach matcher-ensemble` select the other
event-reading baselines. The suggestion, catalogue, and source evidence are frozen
and checked when compiling; use a new empty directory when preparing changed inputs.
`--cluster-reviews` must identify the exact review state used to prepare the queue.

The [reference pilot](reference-pilot.md#review-mappings-against-native-reference-output)
can attach native parser output and source-table values. These are visible to the
reviewer but never enter the mapping approach's request. Assisted reviews retain
their own provenance and are not promoted into independent semantic gold. Use the
[dataset-curation workflow](dataset-curation.md) for blinded labels.

## Compile reviewed mappings

Compile saved mapping decisions through the existing compiler, supplying the frozen
bundle that the reviewer saw:

```console
uv run asim-forge compile artifacts/demo/clusters.jsonl artifacts/demo/mapping/potato/annotation_output/<reviewer>/user_state.json --mapping-bundle artifacts/demo/mapping --output artifacts/demo/compiled
```

Only explicit mapping approvals generate candidates. Draft, deferred, and
extraction-needed tasks are counted separately in the compile manifest. Approved
mappings must have supported target fields, compatible conversions, valid constants,
and unchanged setup metadata. The compiler emits the configured version for the
chosen schema automatically. Assisted mapping approvals require complete mandatory
and applicable conditional mappings. An approval does not certify that extracted
values satisfy the full ASIM schema; native reference comparison remains necessary.

Canonical engineering-review JSONL remains supported. Compile the checked example:

```console
uv run asim-forge compile artifacts/demo/clusters.jsonl examples/sample-review/reviews.jsonl --output artifacts/demo/compiled
```

The sample decisions produce two parser candidates and skip one rejected cluster.
For each approved, fully mapped review, compilation writes:

- one `*.parser-spec.json` specification;
- one `*.kql` parser candidate; and
- `compile-manifest.json`, including generated outputs and rejected, deferred, and
   `awaiting_mapping` and mapping-review status counts.

Compilation is deterministic, but generation is not validation. Treat every KQL
file as a reviewable candidate until schema-aware and Sentinel execution checks have
been completed outside this workflow.

## Semantic dataset and evaluation workflows

Cluster evidence can be promoted into blinded semantic annotation tasks only after
cluster review. Use the [dataset-curation guide](dataset-curation.md) for queue,
decision, promotion, and grouped-split commands.

Use the [evaluation guide](evaluation.md) for approach comparison, controlled
robustness, corpus benchmarking, metrics, and release-report behavior.
