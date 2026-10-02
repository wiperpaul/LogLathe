# Jev Barracuda WAF test assessment

Status: exploratory; official-parser agreement measured, not independent semantic accuracy
Audience: reviewers of the Jev schema experiment
Canonical for: the frozen Barracuda test trial
Last verified: 2026-10-02

## Assessment

Template-only Jev agreed with the pinned official parser on all ten templates.
Enriched Jev agreed on nine, changing one administrative session-timeout template
from AuditEvent to NetworkSession. The lexical baseline agreed on three. Both
views recognized the five configuration templates as AuditEvent and the three
login/logout templates as Authentication. This supports continued advisory
evaluation, but ten unreviewed templates from one public source are insufficient
for automatic approval or a general accuracy claim.

The model and decision definitions were identical to the preceding
[Meraki validation trial](jev-meraki-assessment-2026-10-02.md). No changes were
made in response to its disagreements. The user requested this reserved test
with those definitions frozen. All 20 distinct live requests completed and
validated, with zero cache hits. Clean offline replay reproduced all results
with network access blocked. There were no production-code changes.

## Frozen setup and denominators

| Item | Value |
| --- | --- |
| Date / implementation | 2026-10-02 / `80b166c` |
| Model / decision specification | `jev-1.13.0` / `asim-primary-event-v1` |
| Catalogue revision | `027a0f9338bfabcb27b784571b771c54572ebf01` |
| Split / partition / source group | `mixed-asim-reference-v2` / test / `barracuda-waf` |
| Available choices | Authentication, NetworkSession, AuditEvent, Unsupported |
| Runs | Template-only Choice and enriched Choice; no Noul probes |
| Public inputs | 45 events: 15 per reference schema |
| Cluster output | Ten templates, jointly clustered across the three fixture files |
| Compared representatives | 26 events: seven Authentication, 14 AuditEvent, five NetworkSession |
| Cluster review / independent schema labels | Unreviewed / none |

Every source event has a verified native output. Only 26 of the 45 inputs appear
in the frozen cluster representatives; the other 19 are excluded from event
agreement. Template counts are three Authentication, six AuditEvent, and one
NetworkSession. The balanced original fixture rows do not make the template or
representative-event denominators balanced.

The line-to-fixture sidecar verifies exact source-row identity and message text.
No cluster spans fixture schemas among its representatives, and every selected
native event yields exactly one EventSchema. The existing reference scorer was
applied per capture before aggregating disjoint comparisons. Reference schemas
come from verified native outputs joined after prediction, not fixture filenames.

Enriched inputs use Barracuda/WAF vendor and product, `barracuda_CL.Message`, up to
three example messages per template, and existing slot/type evidence. They match
the prior offline preview. No native outputs, fixture IDs, or expected answers
enter request bodies. Raw messages themselves contain structured product fields
and category/event names; those remain source evidence. In particular, `cat=AUDIT`
appears in both configuration and authentication templates, so it is not a unique
reference-schema label. No vendor-specific demonstrations were added.

## Observed agreement

| Approach | Templates | Representative events |
| --- | ---: | ---: |
| Lexical source-concept baseline | 3/10 (30.0%) | 11/26 (42.3%) |
| Jev template-only Choice | 10/10 (100%) | 26/26 (100%) |
| Jev enriched Choice | 9/10 (90.0%) | 25/26 (96.2%) |

Event agreement applies each template prediction to its retained representative
events. It is not 26 independent model decisions or a measurement on all 45 rows.
The lexical baseline abstained on two templates representing two events; those
remain in the denominators. The best constant-schema predictor, AuditEvent,
would agree on 6/10 templates (60.0%) and 14/26 events (53.8%). Both Jev views
exceed that sanity baseline on this source.

| Reference schema | Template-only templates / events | Enriched templates / events |
| --- | ---: | ---: |
| Authentication | 3/3 / 7/7 | 3/3 / 7/7 |
| AuditEvent | 6/6 / 14/14 | 5/6 / 13/14 |
| NetworkSession | 1/1 / 5/5 | 1/1 / 5/5 |

Neither Jev view selected Unsupported. The single NetworkSession template and
closely related configuration templates particularly limit claims of diversity.
No mapping extraction, field assignments, or generated parser quality was scored.

## Changed selection and interpretation

The only changed selection is `cluster-8d9b905cced8d14d`: a `SESSION_TIMEOUT`
record with `cat=AUDIT`, `duser=admin`, `requestClientApplication=GUI`, and
`deviceProcessName=session_timeout`. The native parser assigns AuditEvent.
Template-only Jev chose AuditEvent with probability 0.56 and confidence 0.42;
enriched Jev chose NetworkSession with probability 0.70 and confidence 0.61,
ranking AuditEvent third at 0.05. This is a useful semantic-review case, not proof
that either returned confidence measures correctness.

Recognizing AuditEvent here shows that Meraki's zero AuditEvent agreement was
not a universal inability to select that choice. Barracuda configuration messages
contain explicit configuration and old/new-value evidence, while the Meraki
reference included operational state changes. This suggests source semantics and
the schema boundary deserve review. It does not isolate a causal explanation:
formats, clustering, source metadata, and event distributions also differ.

Enrichment did not improve either tested source's top-one agreement and made one
Barracuda result worse while more than doubling input tokens. That is evidence
to investigate its value, not a reason to select the winning view retrospectively
and present its score as performance of a tuned system. Report both frozen views.

Barracuda has now been inspected. If these results influence definitions, input
construction, or configuration selection, use a new source for the next untouched
test. The next useful human contribution remains adjudicating schema-boundary
examples, with independent decisions kept separate from native-parser agreement.

## Usage and reproducibility

| View | Input tokens | Output tokens | Median live client round trip |
| --- | ---: | ---: | ---: |
| Template-only | 7,219 | 542 | 362 ms |
| Enriched | 15,998 | 543 | 364 ms |

Total provider-reported usage: 23,217 input and 1,085 output tokens. These are not
monetary costs. Timings include local validation/cache persistence, and these
small sequential runs are not a server-latency or throughput benchmark.

`artifacts/jev-barracuda-2026-10-02-live/` retains frozen inputs and file hashes,
both request/report pairs, per-fixture and aggregate agreements, and live usage.
`artifacts/jev-barracuda-reproduction-2026-10-02.zip` preserves the exact one-off
driver, family build/preview, live reports, and only the 20 required cache entries.
The archive's SHA-256 sidecar and reproduction manifest identify the evidence and
repository dependency bytes. Public fixtures, upstream licences, and catalogue
remain in the recorded source checkout; no maintained publishing framework was
added. The archive remains local for review, not publicly available with this note.

Extract it at the recorded source commit's root, verify the inventory, install the
locked dependencies, and use a new output directory:

```powershell
uv run --no-sync python artifacts/reference-spike/jev-barracuda-trial.py `
  --mode replay --output artifacts/jev-barracuda-reproduced
```

Replay explicitly blocks provider calls and fails on missing cache entries; it
requires no key, Docker, or downloads after dependency installation. A clean
source snapshot plus the archive reproduced all 20 cached results with socket
access blocked, and all inventoried files matched their hashes. Compare request
bodies, parsed responses, baseline results, and reference-agreement reports with
the originals. Replay timings, cache flags, and local path provenance differ
legitimately; preserve live reports for usage measurements.

This remains a small public-source, three-schema comparison with no independent
semantic labels or reviewed clusters. Provider training exposure is unknown, and
no out-of-scope schema examples were tested. Offline replay reproduces this
analysis; it does not establish hosted-model repeatability or generalization.
