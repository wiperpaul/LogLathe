# Jev schema-ranking experiment

Status: experimental; initial live interoperability verified, general quality unestablished
Audience: schema-ranking contributors and reviewers
Canonical for: Jev request construction, execution, replay, and evaluation limits
Last verified: 2026-10-02

## Decision and scope

The [first live OpenSSH assessment](research/jev-schema-assessment-2026-09-30.md)
completed 72 distinct requests and verified offline replay. It found useful
semantic suggestions and unresolved diagnostic cases; the queue has no independent
schema labels, so these observations are not an accuracy benchmark.

The [Meraki validation assessment](research/jev-meraki-assessment-2026-10-02.md)
completed 64 live requests and verified offline replay. Both Choice input views
agreed with native references on 13/32 templates; neither selected AuditEvent.
Enrichment did not improve agreement, and a constant schema prediction exceeded
Jev's template score. The assessment records the schema-boundary review needed
before changing definitions or examining Barracuda test predictions.

The subsequent [Barracuda test assessment](research/jev-barracuda-assessment-2026-10-02.md)
used those definitions unchanged. Template-only Choice agreed on 10/10 templates;
enriched Choice agreed on 9/10, compared with the lexical baseline's 3/10. All 20
live requests replayed offline. This small public-source test is now inspected;
its results should not be used to tune a system and claim untouched test quality.

The [AuditEvent boundary experiment](research/jev-audit-boundaries-2026-10-02.md)
tested an opt-in structured rubric with 52 new requests. Synthetic agreement rose
from 9/10 to 10/10, but Meraki template agreement fell from 13/32 to 11/32 and
AuditEvent agreement stayed at 0/15. The original rubric remains the default;
semantic adjudication is needed before tuning toward the disputed parser labels.

Jev is a plausible decision engine at the existing schema-ranking boundary:
source template evidence goes in, a schema suggestion comes out. LogLathe owns
the ASIM definitions. Jev does not replace the catalogue, field mapper, Potato
review, or deterministic compiler.

The first experiment uses Authentication, NetworkSession, and AuditEvent, matching
the current source-concept ranker's candidate set. The pinned catalogue contains
more schemas; this is deliberately not a test of the whole ASIM taxonomy.
`Unsupported` means none of these **available** definitions fits. It does not mean
the event is impossible to represent in another ASIM schema.

The repository currently has deterministic mapping baselines, not an existing
hosted LLM classifier. This experiment compares Jev's schema decision with the
existing `SourceConceptSchemaRanker`. Its lexical evidence counts remain separate
from provider probabilities; neither is silently converted into the other.
The experiment is exposed through `evaluation schema-rank`, rather than changing
the default build or adding a second annotation workflow.

## Definitions and evidence

The versioned decision specification, `asim-primary-event-v1`, supplies owned
summaries of Microsoft's [Authentication definition](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-authentication),
[Network Session definition](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-network),
and [Audit Event definition](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-audit).
It asks about the event's primary action, rather than assigning NetworkSession
because an event happens to contain an address or port. The definitions contain
no vendor-specific demonstrations, SSH rules, field mappings, or gold examples.

The opt-in `--decision-spec asim-boundaries-v2` uses structured coverage,
exclusions, and generic constructed examples following Jev's documented guidance.
It clarifies administrative resource operations and runtime-state boundaries;
it is a development hypothesis, not a promoted default. See the
[AuditEvent investigation](research/jev-audit-boundaries-2026-10-02.md).
`asim-primary-event-v1` remains the default, and the specification is included in
cache identity and report provenance so prior trials retain their exact requests.

The opt-in `--decision-spec asim-auth-lifecycle-v3` isolates a different hypothesis:
explicit authentication lifecycle boundaries, including wireless deauthentication
and authenticated-session expiry. It retains v1's string format, instructions,
AuditEvent definition, and source projection. With `--nouls`, it also adds two
diagnostic questions about authentication relationships and communication
lifecycle. These do not override the Choice. See the
[authentication experiment plan](research/jev-auth-lifecycle-experiment.md) for
offline preparation, controls, and evidence limits. Its enriched Meraki trial
improved Authentication agreement from 11/15 to 15/15 templates, but adding
probes or a second staged Choice gave no additional top-choice gain. The
synthetic alert control exposed a confident Authentication mistake. Both v3
and staging remain opt-in development experiments.

`--context template` sends just the template. The default, `enriched`, adds source
vendor/product/table/message-column metadata when present, the first three event
texts, and each parameter's first three values with a deterministic physical-type
profile. It reuses the existing semantic input and slot profiler. A type such as
IPv4 is evidence, not a decision about the address's role.

Case IDs, cluster IDs, system identifiers, file paths, slot labels, expected
answers, review notes, and reference-parser output do not enter the request body.
Original template placeholders and event text remain source evidence and may
themselves be revealing. This projection is not anonymization or a guarantee that
the provider has never seen the underlying public logs. Inspect `requests.jsonl`
before sending sensitive source data. Only its `body` is sent to TypeSafe.

The adapter uses TypeSafe's documented [REST API](https://docs.typesafe.ai/api)
without another runtime dependency. It pins `jev-1.13.0`; moving model aliases are
rejected. One Choice asks for the schema. Optional `--nouls` adds a yes/no probe
for each definition, phrased around the **primary** event. These probes do not
consume the Choice answer or each other's answers.
The v3 specification additionally asks its two lifecycle diagnostic questions.
With v3, `--staged` implies `--nouls` and makes a second schema request using only
the two lifecycle estimates alongside the original source evidence. It excludes
the first Choice and primary-schema probe answers from that second request.
Both stages use the existing request-addressed cache; a case completes only when
the final Choice succeeds. Reports retain the first-stage response and the final
request hash. `second-stage-requests.jsonl` records each dependent request before
it is sent. Prepare mode cannot construct these dependent bodies without actual
probe answers and explicitly reports that requirement.

TypeSafe describes Choice [confidence](https://docs.typesafe.ai/confidence) as
derived from the returned distribution. It is not an independent corroborating
signal or measured ASIM accuracy. The Noul outputs are diagnostics, not
statistically independent evidence to multiply together. No probability threshold
approves a schema, mapping, or parser in this implementation.

## Prepare without a key or network access

Use an existing frozen annotation queue and its matching local catalogue:

```powershell
uv run asim-forge evaluation schema-rank `
  artifacts/reference-pilot/openssh-potato/annotation `
  --input-kind queue `
  --catalog artifacts/asim-catalog `
  --output artifacts/jev-openssh-prepare `
  --limit 5
```

Alternatively, use the checked-in smoke-test case and catalogue. This single
example checks the plumbing; it is not a quality benchmark:

```powershell
uv run asim-forge evaluation schema-rank `
  examples/evaluation/semantic-mapping-cases.jsonl `
  --catalog evaluation/ci-catalog `
  --output artifacts/jev-example-prepare
```

Neither command downloads a catalogue, invokes Jev, nor requires credentials.
The command writes `requests.jsonl` with exact outbound bodies and hashes, and
`report.json` with baseline predictions, settings, and definition provenance.
Use a new output directory for each run to preserve previous reports.

## Run and replay

Set `TYPESAFE_API_KEY` in the environment of the shell running the command, using
your normal local secret-management process. Do not put it in repository config,
request artifacts, or a command argument. Then explicitly opt into a hosted call:

```powershell
uv run asim-forge evaluation schema-rank `
  artifacts/reference-pilot/openssh-potato/annotation `
  --input-kind queue `
  --catalog artifacts/asim-catalog `
  --output artifacts/jev-openssh-live `
  --cache artifacts/jev-cache `
  --limit 5 --live
```

This sends source evidence to TypeSafe and may incur provider charges. Reuse the
same arguments with `--replay` instead of `--live` and a new output directory to
read cached results without a key or network. Live mode also reuses matching cache
entries. Changing samples, definitions, model, catalogue revision, or questions
changes the request identity and can require new calls. Cache directories contain
provider results; request/report directories contain source evidence.

Responses retain the model version, full Choice distribution, confidence,
optional Noul values, and token usage. Reports distinguish cache hits and record
elapsed time; cache-read latency is not provider latency. The client rejects
missing answers, wrong models, unknown choices, invalid probabilities, and a
selection that is not a maximum. A tied maximum is reported as `tied_top`, with
no selected schema. `Unsupported` remains a semantic choice. Authentication,
timeout, malformed-response, and cache errors are operational failures instead.

HTTP 429 and 529 receive at most two retries with backoff; individual requests
have a 45-second timeout. Other errors stop the batch, save a partial report, and
return a nonzero exit. Successfully cached requests can be reused on the next
run. Provider error bodies and credentials are excluded from artifacts. Tests
mock transport: normal CI needs no Jev key, API calls, or external downloads.

## Evaluate before integrating into review

### Compare with official parser output offline

The existing native capture can supply implementation-derived schema labels for
this queue. Attach it to a cached replay to score both Jev and the lexical baseline:

```powershell
uv run asim-forge evaluation schema-rank `
  artifacts/reference-pilot/openssh-potato/annotation `
  --input-kind queue --catalog artifacts/asim-catalog `
  --output artifacts/jev-reference-check --cache artifacts/jev-cache `
  --context enriched --replay `
  --reference-fixture evaluation/reference/openssh `
  --reference-bundle artifacts/reference-pilot/openssh-potato `
  --reference-capture evaluation/reference/openssh/reference-output.json
```

All three reference options are required together and are available for queue
inputs in live or replay mode. Reference fixture, capture, source-row identity,
message text, and queue/build hashes are verified using the existing review
evidence join before any paid call. Reference evidence never enters Jev's request.

The additional `reference-agreement.json` reports top-choice agreement and the
reference schema's rank, separately per template and represented source event.
Labels come from `EventSchema` in actual emitted rows, not the parser's declared
schema or directory name. A dropped event is unscorable for schema agreement; it
does not become Unsupported. Missing schemas, partial coverage, and mixed-schema
templates are reported explicitly rather than receiving an invented majority
label. Multiple output rows with the same schema receive one source-event vote.
The comparison covers representative events in reviewed templates, not every
event in an arbitrary corpus or the fixture's additional controlled variants.

Tied scores produce a minimum/maximum rank and corresponding mean reciprocal
rank bounds. No alphabetical tie-breaking improves a rank. Baseline abstentions
still have rankings but cannot count as top-choice agreement. A target outside
the candidate set is a miss with reciprocal rank zero. This is
`asim-parser-silver`: agreement with a pinned implementation, not independent
semantic accuracy. A single-schema fixture also admits a trivial constant-schema
answer; multi-schema fixtures are needed for a useful broader comparison.

### Preserve and replay a reported trial

Reuse the [existing release policy](evaluation.md#automation-and-release-policy).
Its workflow publishes corpus benchmark reports; it does not currently package
Jev caches or annotation queues. For an occasional Jev assessment, keep a manually
reviewed ZIP beside those reports as an additional release asset. No new publisher,
corpus registration, or CI download is needed. A local ZIP is a preservation copy,
not a publicly reproducible result until the accompanying files are available.

For the first OpenSSH trial, preserve only this allowlist, retaining repository-relative
paths so extraction into a separate checkout restores the documented commands:

| Evidence | Files to preserve |
| --- | --- |
| Frozen reference build | `artifacts/reference-pilot/openssh-potato/prepared-manifest.json` and exactly the files in its `files` map |
| Reviewed queue | Its `annotation/queue-manifest.json`, `tasks.jsonl`, `submission-schema.json`, and `cluster-review.user_state.json` |
| Provider answers | The 72 `artifacts/jev-cache/<request_hash>.json` files referenced by the four trial reports |
| Original experiment | `requests.jsonl` and `report.json` for `template-choice`, `enriched-choice`, `template-nouls`, `enriched-nouls`, and `smoke-enriched-nouls` under `artifacts/jev-assessment-2026-09-30/` |
| Measurements | `summary.json`, its small aggregation script, and the four `reference-comparison/<configuration>/reference-agreement.json` files |

The smoke report retains the original live timings for five reused requests; those
are not five additional cases. Preserve the exact queue rather than asking another
reviewer to recreate the same approvals. Original local path strings in build
metadata are provenance, not required execution paths; do not rewrite hashed files.

Include a plain JSON inventory with the reproduction code commit, original live
implementation commit, dependency-lock hash, per-file SHA-256 digests, and hashes
of the repository files on which replay depends. Keep the OpenSSH fixture, native
capture, upstream licence, and catalogue in their existing checked-in locations.
`evaluation/ci-catalog` is byte-identical to the catalogue used for this trial, so
the ZIP does not need another catalogue or upstream-data copy. Archive only the
allowlisted files, not an entire artifacts directory or machine environment.

On a checkout of the recorded reproduction commit, install the locked environment
with `uv sync --locked`, verify the inventory hashes, then replay all four runs:

```powershell
foreach ($context in 'template', 'enriched') {
    foreach ($questions in 'choice', 'nouls') {
        $extra = @()
        if ($questions -eq 'nouls') { $extra += '--nouls' }
        uv run --no-sync asim-forge evaluation schema-rank `
          artifacts/reference-pilot/openssh-potato/annotation `
          --input-kind queue --catalog evaluation/ci-catalog `
          --output "artifacts/jev-reproduced/$context-$questions" `
          --cache artifacts/jev-cache --context $context --replay @extra `
          --reference-fixture evaluation/reference/openssh `
          --reference-bundle artifacts/reference-pilot/openssh-potato `
          --reference-capture evaluation/reference/openssh/reference-output.json
        if ($LASTEXITCODE -ne 0) { throw 'Replay failed' }
    }
}
```

Each run must complete with 18 cache hits. Compare the generated request bodies,
parsed provider responses, and `reference-agreement.json` with the originals.
Elapsed times, live/replay mode, and cache-hit flags legitimately differ. Recompute
the original latency/token summary from the saved live reports, not replay timings.
After dependency installation this requires no provider key, Docker, catalogue
sync, or network access. Missing cache entries fail instead of calling the API.
This reproduces the reported analysis; a new hosted-model run is a separate trial.

Keep the ZIP and its checksum local until the source content, review metadata, and
credential exposure have been reviewed. For a publication, attach the approved ZIP
to a stable release under the existing policy and cite that exact version. The
default 30-day workflow artifact retention is not a publication archive. Preserve
the evidence limits in the [assessment](research/jev-schema-assessment-2026-09-30.md):
official-parser agreement, a single source family and reference schema, and no
independent semantic labels. Reproducibility does not turn this inspected queue
into an untouched test set.

### Independently labelled evaluation

For labelled data, use existing canonical case JSONL. `mapped` cases score their
schema; `not_applicable` cases score Unsupported; `unresolved` cases are excluded
from accuracy. A mapped label outside the three available schemas is rejected,
not relabelled as Unsupported. Queue-only trials have no ground truth and produce
no accuracy claim. Reports retain label provenance so synthetic smoke tests can
be distinguished from human-reviewed evaluation.

For held-out evaluation, add `--split`, `--partition test`, `--case-groups`, and
`--promotion-manifest` using artifacts from the existing
[dataset curation workflow](dataset-curation.md). The command verifies frozen
pre-label groups and selects only the evaluation partition before building
requests. Labels and reference partitions never become demonstrations. An
unsplit or limited run is exploratory, not evidence of held-out generalization.

Compare four runs on the same frozen cases: template-only Choice, enriched Choice,
template-only Choice plus Nouls, and enriched Choice plus Nouls. Keep definitions
fixed while examining a test set; refine them on a separate development split.
The lexical baseline always sees only the template. Consequently, an enriched
Jev improvement alone cannot separate a model improvement from added context.

Report both accuracy on completed labelled cases and completion rate. Failures
and unrun cases must not disappear from the denominator unnoticed. A baseline
abstention is not an Unsupported prediction. Inspect confusion between schemas,
unsupported events, high-confidence mistakes, and disagreements with the probes.
The current report has per-case outputs and simple schema accuracy; grouped
uncertainty, calibration curves, cost comparisons, and field accuracy remain
future evaluation work.

The immediate human contribution is a small blind schema review spanning several
source families, including ambiguous and out-of-scope events. Compare those
decisions with Jev only after recording them. If it adds useful evidence, the
next integration is an advisory schema suggestion in the existing Potato stage,
followed by the existing schema-scoped mapper. Mapping critique by an LLM remains
a separate opportunity, described in the [operator guide](operator-workflow.md).
