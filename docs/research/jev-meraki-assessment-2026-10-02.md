# Jev Cisco Meraki validation assessment

Status: exploratory; official-parser agreement measured, not independent semantic accuracy
Audience: reviewers of the Jev schema experiment
Canonical for: the frozen Meraki validation trial
Last verified: 2026-10-02

## Assessment

Both Jev input views agreed with the pinned official parser on 13/32 templates
(40.6%) and 23/49 representative events (46.9%). The lexical baseline agreed on
8/32 templates (25.0%) and 18/49 events (36.7%). Enrichment did not improve top-one
agreement. Jev selected no AuditEvent templates, despite 15 reference templates
with that schema. These results do not justify automatic schema approval.

A constant Authentication or AuditEvent prediction would agree on 15/32 templates
(46.9%), exceeding Jev's template score. At event level, the best constant
prediction is AuditEvent: 21/49 (42.9%), below Jev's 23/49. Improvement over the
lexical baseline alone therefore does not establish a useful overall classifier.
The template distribution is 15 Authentication, 15 AuditEvent, and only two
NetworkSession templates.

All 64 distinct live requests succeeded and validated, with zero cache hits.
Offline replay reproduced request bodies, parsed provider responses, baseline
results, and all reference-agreement reports with provider calls blocked. No
production code, model version, or decision definition changed during the trial.
Barracuda test predictions remained unexamined at this point. The subsequent
[Barracuda assessment](jev-barracuda-assessment-2026-10-02.md) ran with these
definitions unchanged at the user's request.

## Frozen setup and denominators

| Item | Value |
| --- | --- |
| Date / implementation | 2026-10-02 / `3dd6e60` |
| Model / decision specification | `jev-1.13.0` / `asim-primary-event-v1` |
| Catalogue revision | `027a0f9338bfabcb27b784571b771c54572ebf01` |
| Split / partition / source group | `mixed-asim-reference-v2` / validation / `cisco-meraki` |
| Available choices | Authentication, NetworkSession, AuditEvent, Unsupported |
| Runs | Template-only Choice and enriched Choice; no Noul probes |
| Public inputs | 52 events: 18 Authentication, 21 AuditEvent, 13 NetworkSession |
| Cluster output | 32 templates, jointly clustered across the three fixture files |
| Compared representatives | 49 events: 18 Authentication, 21 AuditEvent, 10 NetworkSession |
| Cluster review / independent schema labels | Unreviewed / none |

The public messages were combined before DeepParse clustering. A line-to-fixture
sidecar retains exact source-row identity. All 52 source events have verified
native outputs, but only 49 appear in the frozen cluster representatives. The
three remaining NetworkSession inputs are excluded from the event agreement
denominator; the result does not measure predictions for all 52 inputs.

No cluster spans fixture schemas among its representatives. Each selected native
event yields exactly one EventSchema. The existing reference scorer was applied
separately to each verified fixture capture, then its disjoint template and event
comparisons were aggregated. Labels are actual native output values, joined after
prediction; no filename-derived labels or synthetic majority labels were used.

Enriched requests include Cisco/Meraki vendor and product metadata, representative
messages, and slot-value/type evidence. Table and message-column metadata are
omitted because Authentication/AuditEvent use `meraki_CL.Message` while
NetworkSession uses `Syslog.SyslogMessage`. Reference output, fixture identities,
and expected schemas are excluded from both request views. The enriched requests
match the preceding offline preview exactly. No vendor-specific demonstrations
were added, and no cluster approvals were manufactured to run this experiment.

## Observed agreement

| Approach | Templates | Representative events |
| --- | ---: | ---: |
| Lexical source-concept baseline | 8/32 (25.0%) | 18/49 (36.7%) |
| Jev template-only Choice | 13/32 (40.6%) | 23/49 (46.9%) |
| Jev enriched Choice | 13/32 (40.6%) | 23/49 (46.9%) |

Both Jev views have the same per-schema counts:

| Reference schema | Jev templates | Jev events | Baseline templates | Baseline events |
| --- | ---: | ---: | ---: | ---: |
| Authentication | 11/15 | 13/18 | 6/15 | 8/18 |
| AuditEvent | 0/15 | 0/21 | 0/15 | 0/21 |
| NetworkSession | 2/2 | 10/10 | 2/2 | 10/10 |

Template-only selected Authentication 11 times, NetworkSession 17 times, and
Unsupported four times. Enriched selected Authentication 11 times, NetworkSession
16 times, and Unsupported five times. Only one selection changed: an AuditEvent
reference template moved from NetworkSession to Unsupported. Neither answer
agrees with the native reference. The other 31 selections stayed the same.

Saved reports retain complete distributions and reciprocal-rank bounds for ties.
Template mean reciprocal rank is 0.599–0.628 for template-only and 0.609–0.646 for
enriched, compared with the baseline's 0.505–0.625. Event figures are 0.643–0.667,
0.650–0.684, and 0.582–0.684 respectively. These bounds describe tied ranks, not
statistical uncertainty or confidence intervals.

## Disagreements and the next human contribution

The AuditEvent reference examples include VPN connectivity and negotiation,
IPsec/ISAKMP security association creation/deletion, port and STP state changes,
and VRRP role transitions. Enriched Jev assigned ten of those templates to
NetworkSession and five to Unsupported. Several VPN disagreements have reported
confidence of 0.95–0.99, demonstrating why confidence cannot be an approval rule.
This sample does not measure calibration.

The supplied AuditEvent definition emphasizes configuration, settings, and policy
changes in an administrative trail. This emphasis also appears in
[Microsoft's generic AuditEvent guidance](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-audit).
The pinned Meraki parser's assignments include operational state changes. That
is a boundary to adjudicate, rather than evidence that every disagreement is
necessarily a semantic model error or that the prompt should simply imitate the
parser. The reference uses historical parser/schema versions, which remain frozen.

Authentication disagreements are four templates describing `wpa_deauth` or
`disassociation` with `auth_neg_failed`; both views assigned them NetworkSession.
Several templates differ by username or format, so template counts are not counts
of independent semantic event families. The two successful NetworkSession
templates are particularly weak evidence of broad network-event performance.

The most useful next step is a small human review of these two boundaries: whether
the operational state changes should be AuditEvent under the intended ASIM
semantics, and whether wireless deauthentication/disassociation should be
Authentication. Record the reasoning against ASIM guidance before revising the
decision specification. Any revision should get a new specification version and
be evaluated on validation data; keep Barracuda reserved until that choice is
frozen. Independent labels should remain separate from native-parser agreement.

## Usage and reproducibility

| View | Input tokens | Output tokens | Median live client round trip |
| --- | ---: | ---: | ---: |
| Template-only | 20,466 | 1,732 | 388 ms |
| Enriched | 38,713 | 1,728 | 356 ms |

Total provider-reported usage: 59,179 input and 3,460 output tokens. This is not an
invoice or cost estimate. Timing includes local validation and cache persistence;
these sequential requests are not a throughput or server-latency benchmark.

The local evidence directory is `artifacts/jev-meraki-2026-10-02-live/`: exact
inputs, frozen file hashes, both request/report pairs, per-fixture and aggregate
reference agreements, and the live summary. The one-off driver is
`artifacts/reference-spike/jev-meraki-trial.py`; it reuses existing experiment,
capture-verification, and scoring APIs instead of adding a maintained runner.

`artifacts/jev-meraki-reproduction-2026-10-02.zip` preserves the driver, frozen
family build and preview, live reports, and only the 64 required cache entries.
Its SHA-256 sidecar and reproduction manifest identify exact evidence and
repository dependency bytes. Public fixtures, upstream licences, and catalogue
remain in the recorded source checkout. Extract the archive at that checkout's
root, verify the inventory, install the locked dependencies, then run:

```powershell
uv run --no-sync python artifacts/reference-spike/jev-meraki-trial.py `
  --mode replay --output artifacts/jev-meraki-reproduced
```

Use a new output directory. Replay fails on missing cache entries and explicitly
blocks provider calls; it requires no API key, Docker, or downloads after
dependency installation. Compare request bodies, parsed responses, baseline
results, and reference-agreement reports with the originals. Replay timing,
cache-hit flags, and local path provenance legitimately differ. Use saved live
reports for usage and latency figures. The archive remains local for inspection;
a committed note alone does not make these ignored artifacts publicly available.

This is one inspected validation source, public samples potentially present in
provider training data, three available ASIM schemas, and unreviewed clusters.
It is official-parser agreement, not a blind semantic benchmark or a claim of
generalization. Offline replay establishes analysis reproducibility, not repeated
hosted-model stability. A clean source snapshot plus the archive reproduced all
64 cached results with socket access blocked; every inventoried evidence and
repository file matched its recorded hash.
