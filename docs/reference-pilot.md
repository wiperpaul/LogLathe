# ASIM reference workflow

Status: current
Audience: mapping reviewers and parser developers
Canonical for: the worked OpenSSH review and native parser execution workflow
Last verified: 2026-10-03

The worked example uses 20 public OpenSSH source-table rows and ten explicit
controlled variants. The upstream parser and its three helpers are pinned to Azure-Sentinel
commit `027a0f9338bfabcb27b784571b771c54572ebf01`. Checked native Kusto output is
available in [the fixture directory](../evaluation/reference/openssh).

The [mixed fixture inventory](../evaluation/reference/README.md) also covers Cisco
Meraki, Barracuda WAF, Carbon Black Cloud, and Cisco ASA, with source-family
partitions and checked captures.
It records native selection gaps explicitly; sample filenames are not labels.

This is `asim-parser-silver`: functional regression evidence against a particular
implementation. It is separate from the corpus benchmark and from semantic gold.
Public examples, generated variants, and independently reviewed labels remain
distinct. All events and variants in this worked example belong to the same
`openbsd-openssh` source family; this fixture cannot establish cross-vendor
generalization.

## Your first session

Prepare the public samples in an empty output directory. This calls the existing
build pipeline and produces its normal Potato task under `build/potato/`:

```console
uv run asim-forge reference prepare evaluation/reference/openssh --output artifacts/reference-pilot/openssh
```

Start the same Potato interface used in the [operator workflow](operator-workflow.md):

```console
uv sync --locked --extra review
uv run potato start artifacts/reference-pilot/openssh/build/potato/config.yaml -p 8000
```

Visit `http://localhost:8000` and enter your reviewer name. The task shows source
templates, representative messages, and extracted slot values. Mapping suggestions
and normalized reference outputs stay outside the cluster review.

Prioritize suspicious examples: malformed or truncated messages, multiline records
split into separate events, mixed layouts, and captures that lose important values.
Routine approval of already coherent clusters provides little corrective evidence.
For each inspected cluster, choose an outcome:

- **Approve:** the examples form one coherent event pattern.
- **Needs a split:** the cluster mixes distinct meanings or layouts.
- **Insufficient evidence:** more examples or source context are needed.
- **Reject:** this cluster is unsuitable for continued parser development.

Use notes to identify lost fields, over-specific templates, distinctions between
events, or additional context you need. A single-event cluster is allowed, but it
provides little evidence about variation. A coherent cluster may still have an
extraction problem; record that in the notes.

Potato stores decisions in
`build/potato/annotation_output/<reviewer>/user_state.json`. The existing review
reader accepts that file directly; partial reviews are supported. Approved clusters
still await mapping.

After review, use the existing annotation queue with the committed
[catalogue snapshot](../evaluation/ci-catalog/README.md). It matches this fixture's
pinned Azure-Sentinel revision and requires no catalogue download:

```console
uv run asim-forge evaluation queue artifacts/reference-pilot/openssh/build artifacts/reference-pilot/openssh/build/potato/annotation_output/<reviewer>/user_state.json --catalog evaluation/ci-catalog --group-id openbsd-openssh --group-strategy source-family --vendor OpenBSD --product OpenSSH --source-table Syslog --message-field SyslogMessage --output artifacts/reference-pilot/openssh/annotation
```

The queue preserves the approved source evidence. Its tasks remain blinded to
predictions and reference answers. The [dataset-curation workflow](dataset-curation.md)
owns independent semantic decisions, promotion, and grouped evaluation.

## Review mappings against native reference output

Use the same queue for assisted engineering review in Potato. Attach the checked
native output and the prepared public fixture to make the before/after values
available beside the mapping controls:

```console
uv run asim-forge review prepare artifacts/reference-pilot/openssh/annotation --catalog evaluation/ci-catalog --setup examples/review-setup/openssh.json --cluster-reviews artifacts/reference-pilot/openssh/build/potato/annotation_output/<reviewer>/user_state.json --reference-fixture evaluation/reference/openssh --reference-bundle artifacts/reference-pilot/openssh --reference-capture evaluation/reference/openssh/reference-output.json --output artifacts/reference-pilot/openssh/mapping
uv run potato start artifacts/reference-pilot/openssh/mapping/potato/config.yaml -p 8000
```

Stop the earlier Potato task before reusing its port. Continue at
`http://localhost:8000`; the [mapping-review guide](operator-workflow.md#review-asim-mappings-in-potato)
describes editing, saving, deferral, and approval. Start with one or two concrete
disagreements. There is no need to repeat cluster approval or finish every mapping
before compiling a candidate.

The [example setup](../examples/review-setup/openssh.json) uses the Authentication
version `0.1.3` recorded by the pinned OpenSSH parser. It also makes AuditEvent
`0.1.2` and NetworkSession `0.2.7` available for schema assessment, as documented in
the [AuditEvent](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-audit)
and [Network Session](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-network)
references. The source already includes a datetime `TimeGenerated`, so the setup
uses `source`. Versions are frozen into the review task; they are never inferred
from the catalogue commit number or entered by the reviewer. Without `--setup`, an
attached reference enables only its declared schema/version and retains the default
`map` timestamp policy.

Tasks with cluster notes appear first. Reference examples are joined by their
original source row and checked against the exact reviewed message and build.
Reference output stays separate from suggestions, and upstream catalogue findings
remain visible. The reviewer can disagree with either the mapper or the reference.
If a value needs new extraction, record that gap instead of entering one example's
value as a constant intended to generalize.

The checked capture works offline. A new native Kusto execution is needed only
after you compile a candidate and want to compare its behavior. Mapping approval
does not supply independent evaluation labels or certify parser correctness.

## Integration boundaries

| Responsibility | Owner |
| --- | --- |
| Log ingestion, DeepParse clustering, schema ranking, Potato task creation | Existing `build_review_bundle` |
| Cluster decisions | Existing Potato task and `load_review_decisions` |
| Assisted mapping decisions | `review prepare`, a Potato task layout, and existing `compile --mapping-bundle` |
| Semantic annotation, provenance, promotion, grouped splits | Existing `evaluation queue`, `promote`, and `validate` |
| Mapping approaches, metrics, and semantic perturbations | Existing `evaluation compare`, controlled track, and corpus benchmarks |
| Candidate generation | Existing `compile` with a complete approved mapping |
| Typed public source-table fixtures, native Kusto captures, output comparison | `reference prepare`, `capture`, and `compare` |

The fixture's ten input-row probes exercise native parser behavior. The existing
controlled track transforms labelled semantic cases and remains responsible for
mapping robustness tests. Native output captures carry runtime, program, and query
provenance; they do not supply human semantic labels or review decisions.

## What the fixture preserves

The CSV is **post-ingestion Syslog input**, not normalized ASIM output. The fixture
preserves all ten source columns and records their KQL types. Its adapter converts
the declared timestamp format to UTC and `ProcessID` to `int`; it keeps messages,
source IDs, and other string values intact. It does not reconstruct missing raw
transport envelopes or claim to test ingestion.

Controlled cases exercise IPv6, identical source and observer addresses, unknown
methods, missing/invalid ports, empty and escaped usernames, a different process,
preauth-only traffic, and a changed outcome. Each keeps its parent event ID and
source-family group. They are passed forward through the reference parser; no
expected labels are inferred by reversing KQL.

The query runner uses one input row per query to preserve event identity even when
an upstream `project` removes columns. It wraps the exact pinned parser/helper
bodies as query-local functions, supplies a typed `Syslog` datatable, and installs
no parser functions or source tables in a workspace.

The fixture manifest hashes every upstream resource. Captures record fixture,
event-set, complete query, and program hashes, engine version, runtime identity,
capture time, output column types, and all rows, including empty results. A sidecar
checksum detects changed capture bytes. This is a reproducibility chain, not a
cryptographic attestation of who executed the queries.

## Native capture

The checked reference snapshot lets ordinary regression tests run offline.
Executing a new candidate requires a local Kusto engine. Microsoft's
[Kusto emulator documentation](https://learn.microsoft.com/en-us/azure/data-explorer/kusto-emulator-install)
describes local functional testing; its terms restrict benchmark testing, so this
workflow records functional results and does not measure engine performance.

The initial capture used this immutable container image:

```text
mcr.microsoft.com/azuredataexplorer/kustainer-linux@sha256:25189b6240251372158c8897ed2a26ced34fad54734499a88abcfe0bb24edb92
```

If you accept Microsoft's emulator terms, a local test container can be started
with Docker. Select the Docker context appropriate for your machine:

```console
docker run --rm --detach --name loglathe-reference-kusto --memory 4g --cpus 2 --publish 127.0.0.1:18080:8080 --env ACCEPT_EULA=Y mcr.microsoft.com/azuredataexplorer/kustainer-linux@sha256:25189b6240251372158c8897ed2a26ced34fad54734499a88abcfe0bb24edb92
```

Create `LogLatheReference` once inside that disposable engine using the management
endpoint. For example, in PowerShell:

```powershell
$body = @{ csl = '.create database LogLatheReference persist (@"/kustodata/loglathe/md", @"/kustodata/loglathe/data")' } | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType application/json -Body $body -Uri http://127.0.0.1:18080/v1/rest/mgmt
```

Capture the pinned reference or a candidate, supplying the actual runtime identity:

```console
uv run asim-forge reference capture evaluation/reference/openssh artifacts/reference-pilot/openssh --runtime-identity <image-at-sha256-digest> --output artifacts/reference-pilot/openssh/reference-output.json
uv run asim-forge reference capture evaluation/reference/openssh artifacts/reference-pilot/openssh --candidate path/to/candidate.kql --runtime-identity <image-at-sha256-digest> --output artifacts/reference-pilot/openssh/candidate-output.json
```

`--endpoint` and `--database` override the defaults. Execution accepts HTTP loopback
endpoints only. Incomplete responses and native query errors fail the capture;
there is no Python KQL interpreter or simulated success fallback. Existing capture
files are not overwritten. Stop the disposable container when finished:

```console
docker stop loglathe-reference-kusto
```

## Offline output comparison

Compare complete native captures using the committed catalogue for this fixture:

```console
uv run asim-forge reference compare evaluation/reference/openssh artifacts/reference-pilot/openssh --reference evaluation/reference/openssh/reference-output.json --candidate path/to/candidate.kql --candidate-capture path/to/candidate-output.json --catalog evaluation/ci-catalog --output artifacts/reference-pilot/openssh/comparison.json
```

Comparison verifies the exact input, reference program, candidate program, and
event coverage before inspecting outputs. It compares unordered row multisets,
row counts, column types, missing/extra output fields, and values. Source-table
pass-through columns are ignored unless the pinned catalogue defines them as target
fields. Target columns are still compared when both parsers select zero rows, so
event-selection agreement and exact-output agreement are reported separately.

For a fixture pinned to another catalogue revision, use its declared revision;
the [catalogue sync guide](operator-workflow.md#sync-the-asim-catalogue) describes
fetching a different snapshot explicitly.

Catalogue checks cover mandatory columns/values, physical types, enumerations,
and IP addresses. They run on **both** outputs and retain upstream failures.
Conditional requirements, alias equivalence, other logical constraints, the
official ASIM tester functions, production Sentinel behavior, and downstream
detection quality are not certified by these checks.

## Candidate generation and iteration

Use the existing [reviewed compilation workflow](operator-workflow.md#compile-reviewed-mappings)
to produce a candidate, then pass that exact KQL file to `reference capture
--candidate`. The reference tools consume KQL; they do not turn predictions into
parser specifications or approve mappings.

Use native output differences to locate extraction and normalization failures.
Improve the relevant existing clustering, mapping, or compiler component, add a
regression case there, and capture the resulting candidate again. Use the existing
semantic evaluation harness for comparisons against independently reviewed labels.
