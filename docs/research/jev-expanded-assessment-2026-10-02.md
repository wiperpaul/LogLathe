# Jev test on Carbon Black Cloud and Cisco ASA

Status: fixed-definition source-family test completed; reference agreement, not semantic accuracy
Last verified: 2026-10-02

## Finding

The clarified v3 definitions improved agreement on the new sources from **57/60
to 59/60 eligible templates**. Parallel diagnostic questions and a second staged
Choice produced exactly the same top choices as v3 Choice alone. All four Jev
variants recognized all 34 Carbon Black AuditEvent templates. These administrative
events provide evidence that the previous Meraki audit disagreements are not a
general inability to recognize AuditEvent; they do not resolve those disputed
parser/schema boundaries.

| Approach | Eligible templates | Eligible representative events |
| --- | ---: | ---: |
| Existing source-concept baseline | 39/60 (65.0%) | 107/181 (59.1%) |
| Original v1 Choice | 57/60 (95.0%) | 167/181 (92.3%) |
| Clarified v3 Choice | 59/60 (98.3%) | 176/181 (97.2%) |
| v3 Choice with parallel Nouls | 59/60 (98.3%) | 176/181 (97.2%) |
| v3 staged Choice | 59/60 (98.3%) | 176/181 (97.2%) |

Every configuration completed all 63 input cases. Three templates had an
incomplete reference and therefore did not receive a forced template label.
Event agreement uses individual selected reference examples, including selected
examples from those three templates. It is not an assessment of all 345 messages
or all 361 public input rows.

## Frozen design and coverage

Source commit `85235f07259cffb40e9894784e6ecf12e70da34e` preceded all live requests.
The source-family assignment is `expanded-asim-reference-v3`, using
[`expanded-split.json`](../../evaluation/reference/expanded-split.json). Both new
families were assigned to test before inspecting their Jev predictions. The
already inspected Meraki and Barracuda families are validation/development
evidence. The catalogue remains pinned to
`027a0f9338bfabcb27b784571b771c54572ebf01`; the new parser/sample resources are
pinned to `4fc25fd4c008337dfd6d9c5663be833e66575d53` with matching schema versions.

The model was `jev-1.13.0`. All variants used `context=enriched`, identical source
state, and vendor/product metadata only. No schema-specific table or message-field
name, expected schema, parser output, filename, reference note, or demonstration
was sent as metadata. The enriched projection sends the template, the first three
event examples, and parameter profiles. Reference scoring covers all retained
representative examples, up to five per template, rather than only the three
examples visible to Jev. Definitions, input views, and stage policy were unchanged
throughout the trial.

Messages from each product's schema files were combined before the existing
DeepParse build. A local sidecar preserves fixture ID, original event ID and
source row for each combined input line. This avoids clustering each known schema
separately. Clusters remain **unreviewed**; no approval files were generated.

| Family | Public rows | Nonblank messages | Native-selected rows | Templates | Representative events | Eligible templates / events |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Carbon Black Cloud | 114 | 98 | 114 | 44 | 95 | 44 / 95 |
| Cisco ASA | 247 | 247 | 244 | 19 | 89 | 16 / 86 |
| Total | 361 | 345 | 358 | 63 | 184 | 60 / 181 |

Carbon Black's 16 blank NetworkSession descriptions were excluded from text
clustering, while their native output remains captured. Its descriptions are
vendor-provided narratives, including original markup, rather than complete raw
records. The explicit ingestion adapter adds only an empty Log Analytics item-ID
column; original cells and messages are unchanged.

ASA NetworkSession rows 4, 15 and 21 have structured message IDs inconsistent with
their message text and emit no native output. All three appear among retained
examples in three different templates. Those templates are recorded as
`incomplete_or_ambiguous_reference`; the three events are `not_selected`. They are
not relabelled Unsupported or assigned a majority reference schema. Other selected
events in those templates still contribute to event agreement.

The existing reference scorer consumes the actual emitted `EventSchema`. Each
fixture capture was verified against its exact manifest, source events, program
and per-event query hashes before the join. The aggregate comparison uses a
composite hash of those five already verified captures, with each constituent
hash retained in its manifest; it does not claim a newly executed composite parser.

## Results by source and schema

| Family | Baseline templates / events | v1 Choice templates / events | v3 Choice templates / events |
| --- | ---: | ---: | ---: |
| Carbon Black Cloud | 32/44 · 60/95 | 44/44 · 95/95 | 44/44 · 95/95 |
| Cisco ASA | 7/16 · 47/86 | 13/16 · 72/86 | 15/16 · 81/86 |

| Emitted reference schema | Baseline templates / events | v1 Choice templates / events | All v3 variants templates / events |
| --- | ---: | ---: | ---: |
| Authentication | 8/20 · 37/85 | 17/20 · 71/85 | 19/20 · 80/85 |
| NetworkSession | 6/6 · 30/30 | 6/6 · 30/30 | 6/6 · 30/30 |
| AuditEvent | 25/34 · 40/66 | 34/34 · 66/66 | 34/34 · 66/66 |

Source and schema distributions are uneven. AuditEvent comes entirely from
Carbon Black, and only six network templates have complete references. Equal
source weighting gives template agreement of 90.6% for v1 and 96.9% for v3, compared
with the pooled 95.0% and 98.3%. Neither weighting supplies broad generalization
evidence from only two public source families.

## Changed and remaining cases

V3 changes exactly two top choices, both Cisco ASA Authentication references:

| Template | Example | v1 | All v3 variants |
| --- | --- | --- | --- |
| `cluster-5f7aa5a0dcd33f34` | SSH session for a user terminated normally | NetworkSession | Authentication |
| `cluster-772e0c52f09c3287` | SSH session disconnected by server, reason "Rejected by server" | NetworkSession | Authentication |

The remaining disagreement is `cluster-76bf16bfb7b0afde`, represented by five
events such as:

```text
%ASA-6-315011:  SSH session from 192.168.1.5 on interface inside for user "Unknown" disconnected by SSH server, reason: "Internal error" (0x00)
```

The official parser emits `EventSchema=Authentication`, `EventType=Logoff` and
`EventResult=Success`, preserving `Internal error` as the original result detail.
Jev chooses NetworkSession in every variant. V1 assigns probabilities 0.96 to
NetworkSession and 0.04 to Authentication; v3 Choice narrows this to 0.54 and 0.46.
The provider confidence falls from 0.94 to 0.38. That is a more uncertain decision,
not evidence of calibrated correctness or a resolved semantic label.

The v3 parallel probes also diverge: `primary_Authentication=0.89`, while
`authentication_relationship_change=0.18`. Staging uses only the two lifecycle
probes, not the primary-schema answers, and still chooses NetworkSession. It does
not repair this disagreement. Human review should establish whether the record
reports an identity-session logoff, an SSH transport failure, or insufficient
evidence of either. The parser's Success result is a separate mapping question.
No provider definition or reference record was changed to improve this score.

## Requests, replay and preservation

| Configuration | Observed input / output tokens | Median client round trip |
| --- | ---: | ---: |
| v1 Choice | 60,475 / 3,426 | 0.370 s |
| v3 Choice | 71,941 / 3,418 | 0.353 s |
| v3 with Nouls | 116,986 / 9,844 | 0.363 s |
| v3 staged, both recorded stages | 203,476 / 13,262 | 0.354 s |

There were **252 distinct live requests**, totaling **335,892 input and 20,106
output tokens**. The staged run reused all 63 cached first-stage responses from
the Noul run; its 63 new second-stage calls used 86,490 input and 3,418 output
tokens. The both-stage row above includes reused token usage, so summing the four
rows double-counts the first stage. Its timing includes a cache read and must not
be presented as latency for two fresh provider calls.

The private local trial is `artifacts/jev-expanded-2026-10-02/`: frozen inputs,
family builds, source-row joins, pre-call manifest, four request previews, live
reports, dependent second-stage requests, reference comparisons, and replay
verification. The one-off driver is
`artifacts/reference-spike/jev-expanded-trial.py`, reusing existing production
components. No new runtime harness, publisher, CI download, or paid CI job was
introduced.

All four variants replayed without a key, with the provider sender blocked.
Initial/dependent request bodies, parsed responses, baseline predictions and
native-reference comparison files matched the live artifacts. A clean checkout
at the frozen commit also replayed all 252 case/configuration rows with socket
access blocked. The local package follows the existing ZIP/checksum convention:

```text
artifacts/jev-expanded-reproduction-2026-10-02.zip
SHA256: 102ceab8db5cee61f97b2c30fc02786a65f57c8b1f43d48269b788290bbd71ea
```

It contains the 252 required cached responses and a reproduction inventory.
Recorded source-file line endings are preserved as overlays verified to match
the frozen commit after newline normalization. The replay wrapper normalizes only
the recorded absolute driver pathname in the in-memory manifest comparison;
hashes and archived evidence remain unchanged. The archive is **local review
only**, not a published reproduction package. Follow its `REPRODUCTION.md` at the
recorded commit. Exact-value credential checks found no raw or encoded key in
the trial, caches or fixture files.

## Decision and next human contribution

This new source-family test supports the narrower authentication clarification
from the earlier Meraki development trial. It supplies no additional top-choice
benefit for probes or staging, which add requests and tokens. The production
default remains unchanged; v3 and staging remain opt-in experiments.

The most useful next human review is the remaining SSH internal-error case and
its reference Logoff/Success mapping, followed by cluster-coherence checks.
These public samples may have appeared in model training data, and no independent
semantic labels exist here. Both source families' predictions are now inspected:
any further tuning using these outcomes makes them development evidence rather
than an untouched test cohort.
