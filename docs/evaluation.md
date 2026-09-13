# Evaluation

Status: current
Audience: evaluators, approach developers, and release operators
Canonical for: approaches, metrics, controlled conditions, corpora, and evaluation reports
Last verified: 2026-09-10

This is the canonical operator and metric reference for LogLathe evaluation. It
covers semantic-mapping comparisons, controlled robustness, and the registered
corpus benchmark. Dataset acquisition, annotation, promotion, and frozen split
creation belong in [Dataset curation](dataset-curation.md).

The evaluation boundary is deliberately provider-neutral. A
`SemanticMappingCase` contains the input and expected answer; a
`SemanticMappingPrediction` contains the approach identity, rankings, scores,
evidence, warnings, and disposition. Expected labels are never included in the
ordinary `MappingRequest`. Every comparison also pins one exact ASIM catalogue
revision and rejects cases or predictions from another revision.

```text
cases + pinned ASIM catalogue
              |
       context-view mask
              |
       optional oracle hint
              |
      registered approaches
              |
 provider-neutral predictions
              |
 metrics + grouped uncertainty + warnings
```

## Comparison approaches

Running `evaluation compare` without `--approach` evaluates all seven registered
approaches in the order below. The three priors appear first because every
substantive result should be read against a class-imbalance floor, not against
zero.

| Category | Approach | Version | Purpose |
| --- | --- | --- | --- |
| Prior | `null-prior` | 1 | Predicts `unresolved` and nothing else. This is the absolute floor, not a mapper. |
| Prior | `majority-schema-prior` | 1 | Ranks schemas only by their frequency in labelled reference cases and maps no fields. |
| Prior | `field-frequency-prior` | 1 | Selects the majority schema, then assigns its most frequently labelled valid field to every slot. It uses no source-event evidence. |
| Substantive | `direct-lexical` | 3 | Ranks schemas and maps fields directly from local slot context. It does not emit source roles or infer static constants. |
| Substantive | `semantic-frame` | 3 | Infers target-neutral source roles and a small set of static meanings, then projects them into the selected schema. Its normalizers are evaluation baselines, not production vendor rules. |
| Substantive | `matcher-ensemble` | 1 | Combines lexical, character n-gram, value-enumeration, type-affinity, and range signals. It rejects incompatible types and candidates below a declared, untuned acceptance floor. |
| Substantive | `case-retrieval` | 2 | Retrieves up to three similar labelled cases and transfers their schema, source roles, constants, and field mappings. |

The priors and case retrieval depend on a labelled reference set. With a grouped
split, they see only the split's declared reference partitions. Without a split,
the runner uses the evaluated cases as references but excludes the current case by
stable case ID. That leave-one-out behavior prevents direct same-case leakage, but
it does not prevent near-duplicate source or template families from leaking labels.
An unsplit retrieval score is therefore not comparison evidence, and the report
warns accordingly.

The direct lexical and semantic-frame approaches respect schema-ranking
abstention before attempting fields. Each implementation owns its own package
under `semantic_mapping/approaches/`; shared target-neutral normalization, schema
ranking, context extraction, candidate generation, prediction contracts, and
metrics remain outside the providers.

## Run a comparison

Synchronize the exact catalogue revision named by the cases, then run the
comparison:

```powershell
uv run asim-forge catalog sync `
  --output artifacts/asim-catalog `
  --revision 027a0f9338bfabcb27b784571b771c54572ebf01

uv run asim-forge evaluation compare `
  examples/evaluation/semantic-mapping-cases.jsonl `
  --catalog artifacts/asim-catalog `
  --output artifacts/semantic-comparison.json
```

Repeat `--approach` to select a subset. The terminal summary shows the main point
estimates, intervals, paired tests, sample-resolution statement, and warnings. The
optional JSON report also retains every prediction and its evidence for inspection.

For a real comparison, evaluate a held-out partition with the pre-label grouping
and promotion evidence that authenticates it:

```powershell
uv run asim-forge evaluation compare path/to/cases.jsonl `
  --catalog artifacts/asim-catalog `
  --split path/to/split.json `
  --partition test `
  --case-groups path/to/case-groups.jsonl `
  --promotion-manifest path/to/promotion-manifest.json `
  --output artifacts/semantic-comparison.json
```

The split must cover the cases exactly, keep every group in one partition, use the
same catalogue revision, and preserve the group IDs frozen before labelling. The
evaluation partition cannot also be a reference partition. These checks make a
post-label regrouping or a label-visible retrieval reference fail closed.

### Controlled conditions

Oracles and context views are harness conditions, not new approaches. They are
recorded in the report and generate explicit warnings so their scores are not
presented as deployable accuracy.

Use `--oracle` to isolate stages:

| Value | Gold information supplied | Interpretation |
| --- | --- | --- |
| `none` | None | Ordinary approach behavior. |
| `schema` | Expected schema | Removes schema-selection error from approaches that consume the schema hint. |
| `source-frame` | Expected source roles | Isolates role-to-field projection in `semantic-frame`; other current approaches do not consume this hint. |
| `schema-and-source-frame` | Expected schema and source roles | Combines both diagnostics. It is an error-decomposition ceiling, not approach accuracy. |

For example:

```powershell
uv run asim-forge evaluation compare path/to/cases.jsonl `
  --catalog artifacts/asim-catalog `
  --oracle schema-and-source-frame
```

Use `--context-view` to physically remove evidence before an approach receives the
request. This is stronger than asking an implementation to ignore a populated
field.

| View | Available evidence |
| --- | --- |
| `v0` | Representative slot values only; labels, template text, and source metadata are withheld. |
| `v1` | V0 plus slot labels and the immediate token window around each placeholder. |
| `v2` | V1 plus the full template, constants, and sibling slots; source metadata remains withheld. |
| `v3` | V2 plus system, vendor, product, and source-table metadata. |
| `full` | The unmodified request. It is currently equivalent to V3 and leaves room for future input fields. |

```powershell
uv run asim-forge evaluation compare path/to/cases.jsonl `
  --catalog artifacts/asim-catalog `
  --context-view v1
```

V4 and V5, envisioned for external documentation and repository evidence, are not
implemented because no current provider consumes those inputs.

### Uncertainty, significance, and selective risk

Point estimates are accompanied by group-aware statistics. When a frozen split is
present, its pre-label group is authoritative. Otherwise the runner falls back to
the source family formed from system, vendor, and product metadata. It never treats
templates from one family as independent observations.

By default the runner performs 1,000 deterministic resamples; change that with
`--resamples`. It reports 95% percentile cluster-bootstrap intervals for schema
top-1 accuracy, field micro-F1, field macro-F1, candidate recall at 5, source-role
micro-F1, and mapping exact match. It also reports the number of groups and a
conservative minimum detectable effect so differences smaller than the sample can
resolve are visible as such.

A paired, two-sided group permutation test compares every selected approach with
one baseline on field micro-F1. The default baseline is the first selected prior,
or the first selected approach when no prior is present. Use
`--baseline-approach` to choose another selected approach. The runner intentionally
does not run all pairwise tests over a small sample.

Each approach also receives a selective risk-coverage curve. Cases are ordered by
the product of the approach's mean selected-field score and leading schema score;
abstentions rank last. Risk is one minus case-level field F1. Lower risk and lower
area under the curve are better. These are approach-reported ranking scores, not
calibrated probabilities, so the curve must not be described as calibration.

Report-level warnings identify approaches at or below the best prior's field
micro-F1, samples with fewer than two independent groups, statistically
indistinguishable differences, and other limits such as small, single-system, or
synthetic case sets.

## Metric reference

The metric engine evaluates predictions from exactly one approach name and version
at a time. Predictions must cover the evaluated case IDs exactly and must carry
the same immutable catalogue revision as their cases.

| Metric | Definition |
| --- | --- |
| `case_count` | Number of evaluated cases. |
| `disposition_accuracy` | Fraction whose `mapped`, `unresolved`, or `not_applicable` disposition exactly matches gold. |
| `coverage` | Fraction for which the approach emitted the `mapped` disposition. This exposes abstention rather than dropping hard cases. |
| `schema_top1_accuracy` | Expected schema is ranked first, over gold cases whose expected disposition is mapped. |
| `schema_top3_hit_rate` | Expected schema appears among the first three candidates, over mapped gold cases. |
| `schema_mrr` | Mean reciprocal rank of the expected schema over mapped gold cases. |
| `source_micro_precision`, `source_micro_recall`, `source_micro_f1` | Micro scores for exact source-kind, case-insensitive locator, and case-insensitive semantic-role triples. |
| `source_macro_f1` | Mean case-level F1 for those exact source-semantic triples. |
| `source_facet_micro_f1` | Mean of the aggregate domain, relation, entity, and property facet F1 scores; a partial role match is visible without replacing exact-role F1. |
| `source_domain_f1`, `source_relation_f1`, `source_entity_f1`, `source_property_f1` | Aggregate F1 for each parsed source-role facet. |
| `unregistered_role_rate` | Fraction of predicted source roles not covered by the versioned role registry. |
| `field_micro_precision`, `field_micro_recall`, `field_micro_f1` | Micro scores for exact source-kind, case-insensitive locator, ASIM field, and normalized constant-value tuples. |
| `field_macro_f1` | Mean case-level F1 for the exact field tuples. |
| `field_mrr` | Mean reciprocal rank of each expected ASIM field in the candidates emitted for its source locator. Missing candidates score zero. |
| `field_recall_at_gold` | For each case, globally ranks candidate source-field pairs and measures recall within the first as many pairs as there are expected mappings, then averages cases. |
| `candidate_recall_at_1`, `candidate_recall_at_3`, `candidate_recall_at_5` | Share of expected mappings retained in the first 1, 3, or 5 generated candidates for the correct source locator. This separates candidate-generation loss from final-ranking loss. |
| `candidate_reduction_ratio` | Mean fraction of considered schema fields removed from reported candidate pools. Predictions without pool-size metadata are omitted. |
| `field_top1_tie_rate` | Fraction of predicted fields whose leading candidate is tied with at least one other candidate before the cutoff. |
| `mapping_exact_match` | Fraction with the complete expected schema and field set; non-mapped gold cases also require the correct disposition. |
| `full_exact_match` | Mapping exact match plus the complete source-semantic frame. |
| `mean_mapping_edits` | Mean of one schema correction when needed plus missing and extra exact field tuples. It is a rough reviewer-work proxy, not measured review time. |

For macro scores, a correctly empty expected and predicted set receives full
case-level F1; adding a false positive to an empty case receives zero. Micro and
macro scores answer different questions and should be reported together. Schema
ranking, source-role inference, ASIM projection, exact completion, abstention, and
reviewer effort are deliberately separate stages. Parser execution and downstream
security-query precision and recall are outside this metric layer.

The ranking measures follow the evaluation shape used by
[Magneto](https://www.vldb.org/pvldb/vol18/p2681-freire.pdf). The stage-wise
separation is also consistent with
[Matryoshka](https://arxiv.org/abs/2506.17512); this repository does not reuse that
artifact's task labels as ASIM labels.

## Controlled robustness

The robustness runner generates variants from pinned seed cases and reports them
as separate slices:

```powershell
uv run asim-forge evaluation robustness path/to/cases.jsonl `
  --catalog artifacts/asim-catalog `
  --output artifacts/semantic-robustness.json
```

By default it runs the three event-reading approaches for which this test is
meaningful: `direct-lexical`, `semantic-frame`, and `matcher-ensemble`. Priors read
no source evidence, while retrieval could match a variant to its own seed, so the
runner rejects those approaches for robustness.

| Family | Perturbations | Passing behavior |
| --- | --- | --- |
| Label-preserving | Common abbreviations, compound identifiers, uppercase text, opaque slot labels, and an unmapped decoy slot | The predicted field set stays unchanged for every applicable case and field micro-F1 does not fall. |
| Label-changing | Swapped source and destination direction | The prediction moves with the changed labels, field micro-F1 does not fall, and stability is below one. |

Rows report seed and perturbed field micro-F1, delta, prediction stability, and a
pass/fail verdict. A transformation that applies to no seed case is unmeasured, not
a pass. Generated variants are synthetic by construction and must never be
averaged into real-case headline metrics. The corpus benchmark runs these slices
automatically for each semantic corpus and keeps them in a separate section.

## Benchmark tracks and corpora

Native parser functional regression is available separately through
`asim-forge reference`; see the [reference pilot](reference-pilot.md). Its
`asim-parser-silver` captures compare values, physical types, and event retention
against a pinned executable parser. They are not registered in the corpus benchmark
below or combined with semantic-development/gold metrics. Public samples enter the
existing build and Potato workflow; semantic annotation and candidate compilation
continue through the established review boundaries.

The release-style benchmark answers a narrow question: did code or evaluation data
change behavior on fixed, inspectable corpora? Every track has a distinct evidence
claim, and scores are never combined across tracks.

| Track | What is measured | Permitted interpretation |
| --- | --- | --- |
| `parsing-gold` | Pairwise template-clustering precision, recall, F1, and cluster purity against upstream template IDs. | Parsing and grouping quality only; not incident detection or ASIM correctness. |
| `format-diagnostic` | Event and cluster counts, clusters with slots, and the event-weighted rate receiving a provisional non-`NoFit` source-concept suggestion. | Operational coverage only. The corpora contain no adjudicated ASIM answer key, so the rate is not accuracy. |
| `schema-hint` | Event- and cluster-weighted agreement with an upstream file-level ASIM placement hint. | Agreement with weak upstream supervision, not independent schema correctness or field-mapping correctness. |
| `semantic-development` | The same semantic rankings, F1 scores, completion, uncertainty, and robustness outputs as semantic gold, against checked but unadjudicated development labels. | Error discovery and diagnostic approach comparison only; no ASIM correctness claim. |
| `semantic-gold` | Schema ranking, source-role and facet F1, field selection and ranking, completion, coverage, edits, uncertainty, and robustness against gold-format repository cases. | ASIM correctness only when labels are independently adjudicated; contract fixtures and small or unrepresentative sets do not establish production quality. |

The currently registered small corpora are:

| Track | Corpora |
| --- | --- |
| `parsing-gold` | LogHub OpenSSH 2k, Linux 2k, and Apache 2k. |
| `format-diagnostic` | Matryoshka SSH example, LogInject benign SSH 500, and LogInject benign Apache 500. |
| `schema-hint` | Microsoft ASIM samples for Cisco Firepower Network Session, CrowdStrike Falcon Authentication, and CrowdStrike FalconHost Audit Event. |
| `semantic-development` | `asim-cef-dev`: 16 CEF and syslog development cases covering four source families. They have checked, documented labels but no independent adjudication or frozen grouped split. In particular, its retrieval score reflects near-sibling leakage rather than quality. |
| `semantic-gold` | `asim-semantic-smoke`: the repository's one synthetic semantic smoke case. It exercises the contract and does not measure approach quality. |

The provisional ASIM-fit diagnostic is named `source-concept-v1` in
format-diagnostic and schema-hint results. Parsing-gold rows use
`deepparse-default`. ASIM sample placement identifies the schema for which parser
samples were collected, but paired ingestion files and aggregate tester results do
not provide row-level mapping gold.

Large sources remain outside the default release run when their cost would make the
change gate unreliable. Security datasets may still be useful inputs without their
threat, injection, or parser-development labels becoming ASIM ground truth.

Run the complete registry locally:

```powershell
uv run asim-forge catalog sync `
  --output artifacts/asim-catalog `
  --revision 027a0f9338bfabcb27b784571b771c54572ebf01

uv run asim-forge evaluation benchmark evaluation/corpora `
  --catalog artifacts/asim-catalog `
  --cache artifacts/corpus-cache `
  --output artifacts/evaluation `
  --revision (git rev-parse HEAD)
```

The runner writes `benchmark-report.json` for machines and
`benchmark-report.md` for review and release notes. Supply a prior JSON report with
`--baseline` to show deltas. A delta is emitted only when the corpus ID, approach,
primary metric, and corpus fingerprint remain comparable. Editing a manifest,
local cases, or frozen split evidence changes the fingerprint and suppresses a
misleading before-and-after value.

## Reproducibility and redistribution

Every registered corpus has an `evaluation/corpora/<id>/manifest.json`. Remote
resources use immutable revisions where possible and declare a SHA-256 digest. The
runner verifies bytes before use and selects exact archive members where declared.
Local semantic cases, split files, frozen case groups, and promotion manifests
contribute to the corpus fingerprint.

The two LogInject benign diagnostic corpora pin the first 500 `raw_log` values from
the SHA-256-verified Zenodo archive in small, attributed `input.log` fixtures. Their
resource hashes are checked on each run. The originating archive is not required
for CI, and no injection or security labels are treated as ASIM ground truth.

The machine-actionable redistribution class controls what may leave the local
machine:

| Class | Publication boundary |
| --- | --- |
| `content` | Source content, derived artifacts, and metrics may be published. |
| `derived` | Derived artifacts, predictions, and metrics may be published, but source content may not. |
| `metrics` | Only aggregate metrics and the acquisition manifest may be published; source content and per-case output may not. |
| `none` | Nothing derived from the corpus may be published, including metrics. The release-report writer refuses it. |

A combined benchmark report inherits the least permissive class among its corpora
and records required attributions. Prediction evidence can quote source templates,
so it counts as derived corpus content. Keep a standalone comparison JSON local
unless the input's terms permit publishing its per-case predictions and evidence.
Downloads and generated results remain under ignored `artifacts/`; downstream
users remain responsible for the terms recorded in each manifest.
The benchmark reuses local downloads by SHA-256 when its `--cache` directory is
retained. GitHub-hosted CI starts with a fresh runner; its workflow restores and
saves only remote blobs whose corpus manifests permit `content` redistribution.
Other remote resources are fetched on each run. All restored bytes are verified
against their manifest digests, and transient HTTP failures receive bounded retries.

## Automation and release policy

`.github/workflows/evaluation.yml` runs when evaluation definitions, fixtures,
implementation code, the workflow, or dependency locks change. Pull requests
retain the generated reports as a workflow artifact but do not publish a release.
Relevant successful pushes to `main` create an `eval-<run>-<sha>` prerelease; a
`v*` tag creates its release or attaches the reports to an existing release.

Before evaluation, the workflow tries to download the newest prior `eval-*` JSON
report for comparable deltas. Failed or partial runs do not reach the release job.
Keep any report cited by a paper, thesis, or release under a stable tag before
applying a future prerelease-retention policy.
