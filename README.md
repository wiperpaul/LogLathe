# LogLathe

LogLathe is an early-stage, human-in-the-loop toolkit for turning raw security logs
into auditable Microsoft Advanced Security Information Model (ASIM) parser
candidates. It also provides a controlled evaluation harness for comparing schema
and field-mapping approaches before their suggestions reach a reviewer.

The project is intended for security and detection engineers working with
heterogeneous telemetry. The current implementation is ASIM-first; the longer-term
architecture allows other normalization targets, such as OCSF, to be added behind
explicit contracts.

The installed Python distribution and command remain named `asim-forge` for
compatibility. A generated Kusto Query Language (KQL) file is a reviewable parser
candidate, not a production-ready Microsoft Sentinel parser.

## Current workflow

```text
line-oriented logs
      |
      v
DeepParse offline masks + Drain clustering
      |
      v
typed clusters + representative events + parameter slots
      |                                  |
      v                                  v
initial Potato cluster review    source-concept schema ranking
      |
      +-- rejected / split / insufficient evidence --> no parser
      +-- approved --> frozen queue + source setup + pinned catalogue
                              |
                              v
                  Potato Cluster / Schema / Mappings tabs
                              |
                draft / defer / extraction gap --> no parser
                complete, approved mapping -----> parser spec + KQL candidate
                                                        |
                                                        v
                                             optional native reference checks
```

Cluster coherence, semantic suggestions, human mapping decisions, compilation,
validation, and release remain separate boundaries. In particular, the cluster
reviewer does not see an ASIM suggestion that could anchor the initial decision.
The later mapping task retains that decision and its notes, supports one confirmed
schema per template, and lets the reviewer revisit cluster coherence.

## What exists today

| Capability | Status |
| --- | --- |
| Line-oriented `.log` and `.txt` ingestion | Implemented |
| Deterministic DeepParse mask-first clustering | Implemented |
| Independent schema-ranking contracts and abstention | Implemented |
| Portable Potato cluster-review task | Implemented |
| Typed review, parser-specification, and KQL generation | Implemented |
| Commit-pinned Microsoft ASIM field catalogue | Implemented |
| Provider-neutral mapping fixtures and blinded gold promotion | Implemented |
| Priors plus lexical, semantic-frame, matcher-ensemble, and retrieval approaches | Implemented |
| Ranking, mapping, facet, candidate, robustness, and statistical metrics | Implemented |
| Evidence-separated corpus reports and evaluation prereleases | Implemented |
| Potato ASIM mapping review with schema-specific fields and span selection | Implemented; manually prepared after cluster review |
| Editable mapping defaults and setup-supplied device/time fields | Implemented |
| Native Kusto reference fixtures and bounded catalogue checks | Implemented across five source families |
| Optional Jev schema-ranking trials, versioned definitions, and cached replay | Implemented; experimental, outside the default build |
| LLM-assisted critique of attempted mappings | Documented manual practice |
| Complete ASIM tester integration and Sentinel execution | Planned |
| Additional normalization targets such as OCSF | Planned; contributions welcome |
| Automatic production deployment | Not implemented |

LogLathe is pre-alpha. The working review path includes required-field controls,
NER-style span labels, per-schema drafts, and explicit approval gates. Automatic
handoff between the initial cluster task and mapping task, arbitrary extraction
repair, specialist assignment, and inline native validation remain future work.

Recent work compares frozen Jev schema definitions on public parser samples.
Clarified authentication definitions improved the latest source-family comparison;
extra probes and staging gave no additional top-choice gain. These results measure
agreement with official parsers and retain unresolved semantic disagreements. See
the [Jev guide](docs/jev-schema-experiment.md) and its dated assessments.

## Quick start

LogLathe supports Python 3.11 through 3.14 and uses
[uv](https://docs.astral.sh/uv/) for dependency management.

```console
git clone https://github.com/wiperpaul/LogLathe.git
cd LogLathe
uv sync --locked
```

Build the checked sample and compile its checked engineering decisions:

```console
uv run asim-forge build examples/sample-logs --output artifacts/demo --system demo
uv run asim-forge compile artifacts/demo/clusters.jsonl examples/sample-review/reviews.jsonl --output artifacts/demo/compiled
```

The sample contains nine events in three clusters. Its decisions produce two parser
candidates and skip one rejected cluster.

To review the clusters yourself, install Potato and start the generated task:

```console
uv sync --locked --extra review
uv run potato start artifacts/demo/potato/config.yaml -p 8000
```

Open `http://localhost:8000`. Cluster approval leaves the task awaiting mapping;
the [operator guide](docs/operator-workflow.md#review-asim-mappings-in-potato)
continues through source configuration, schema confirmation, mapping, and compilation.
For real public examples with checked native output, start with the
[reference workflow](docs/reference-pilot.md) and [fixture inventory](evaluation/reference/README.md).

Run the test suite:

```console
uv run pytest
```

## Documentation

The [documentation index](docs/README.md) routes readers by task:

- [operator workflow](docs/operator-workflow.md) — cluster review, source setup,
  span-based mapping in Potato, compilation, and manual LLM critique;
- [architecture](docs/architecture.md) — current boundaries and package ownership;
- [evaluation](docs/evaluation.md) — approaches, metrics, controlled robustness,
  corpora, and release reports;
- [dataset curation](docs/dataset-curation.md) — fixtures, blinded annotation,
  promotion, grouping, and leakage-resistant splits;
- [reference workflow](docs/reference-pilot.md) — public samples, native output
  comparisons, and a first session in the existing Potato workflow;
- [reference fixtures](evaluation/reference/README.md) — OpenSSH, Meraki, Barracuda,
  Carbon Black Cloud, and Cisco ASA coverage and source-family splits;
- [Jev schema experiment](docs/jev-schema-experiment.md) — offline request previews,
  explicit live trials, cached replay, and current evidence limits;
- [roadmap](ROADMAP.md) — current milestone and future work; and
- [contributing](CONTRIBUTING.md) — development checks and contribution guidance.

Dated research and completed implementation records are retained under
`docs/research/` and `docs/archive/`; they explain decisions but are not the source
of truth for current behavior.

## Evaluation boundary

Every substantive mapping approach receives the same provider-neutral
`MappingRequest` and returns the same typed `SemanticMappingPrediction`. Expected
labels never enter an ordinary request, and predictions remain separate from gold
cases.

The repository contains two deliberately different kinds of checked semantic data:

- a synthetic smoke fixture that exercises the contracts; and
- an unadjudicated development set used for error discovery.

Native parser samples provide a separate `asim-parser-silver` track for functional
checks and reference-schema agreement. Jev currently considers Authentication,
NetworkSession, AuditEvent, and Unsupported; its clarified v3 definitions and
staging are opt-in, and its suggestions do not enter mapping approval automatically.

These checked data sets and parser comparisons do not support production-quality
claims. Adjudicated `semantic-gold` evidence, with source-family grouping and locked
held-out splits, is still required before comparing approach quality. The
[evaluation guide](docs/evaluation.md) defines the track and metric boundaries.

## Design guarantees

- **Suggestions are not approvals.** Rankings, scores, evidence, and warnings remain
  distinct from human decisions.
- **Source semantics are target-neutral.** Original events remain inspectable while
  normalized source concepts can be projected into a pinned target catalogue.
- **Schema ranking is not field mapping.** Schema candidates and abstention are
  evaluated before source roles and target fields.
- **Provenance is part of the output.** Inputs, DeepParse, catalogue, approach, case,
  decision, and output revisions are recorded at their respective boundaries.
- **Compilation is deterministic.** The same complete cluster and review contracts
  produce the same parser specification and KQL candidate.
- **Deployment is out of scope.** LogLathe does not connect to a Sentinel workspace
  or promote generated KQL automatically.

The clustering adapter uses
[DeepParse v1.0.0](https://github.com/NightBaRron1412/DeepParse/tree/v1.0.0), pinned
to commit `b53c29b379be5ab834ff990154297ef8fea8d98a`. Runtime clustering uses its
deterministic offline mask bundle; the current build workflow does not download or
invoke a language model.

## Data handling

Generated artifacts, raw operational logs, and Potato annotation state are ignored
by Git because they may contain sensitive security data. Commit synthetic or
suitably sanitized examples, or explicitly redistributable public fixtures with
pinned provenance and attribution. Normal tests and automatic corpus checks use
local fixtures and the pinned catalogue; they require no provider key or sample
downloads. Initial dependency installation and explicit full corpus runs can
access external resources. Live Jev calls require explicit opt-in; trial caches,
review state, and manually preserved replay archives stay local unless separately
reviewed for publication.

## Licence

LogLathe is licensed under GPL-3.0-or-later. DeepParse remains Apache-2.0 and is not
vendored. Potato is an optional, separately installed GPL dependency. See
[`LICENSE`](LICENSE) and [`THIRD_PARTY_NOTICES`](THIRD_PARTY_NOTICES).
