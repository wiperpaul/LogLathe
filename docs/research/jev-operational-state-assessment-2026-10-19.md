# Jev operational-state comparison

Status: completed development comparison; parser agreement, not independent semantic accuracy
Last verified: 2026-10-19

## Finding

The operational-state definitions improved agreement with the pinned official
parsers from **85/102 to 93/102 eligible templates** (83.3% to 91.2%). Eight Meraki
choices changed to AuditEvent; all eight now match the reference. No other public
template changed its top choice. This supports the usefulness of the
[maintainer-informed boundary](jev-operational-state-guidance.md), while leaving
VPN communication/state distinctions and the existing alert error unresolved.

| Source family | Templates presented / eligible | Previous definitions | Operational-state definitions | Representative-event agreement, previous → revised |
| --- | ---: | ---: | ---: | ---: |
| Meraki | 32 / 32 | 17/32 | 25/32 | 28/49 → 40/49 |
| Barracuda WAF | 10 / 10 | 9/10 | 9/10 | 25/26 → 25/26 |
| Carbon Black Cloud | 44 / 44 | 44/44 | 44/44 | 95/95 → 95/95 |
| Cisco ASA | 19 / 16 | 15/16 | 15/16 | 81/86 → 81/86 |
| Total | 105 / 102 | 85/102 | 93/102 | 229/256 → 241/256 |

All 105 public templates completed in both configurations. There are 259 retained
representative events; three ASA events have no native output, making three
templates incomplete references. Those templates remain unscored at template
level. Their other selected events still contribute to event agreement. These
counts do not represent every event in the public source files.

| Reference schema | Previous template agreement | Revised template agreement |
| --- | ---: | ---: |
| Authentication | 37/38 | 37/38 |
| NetworkSession | 9/9 | 9/9 |
| AuditEvent | 39/55 | 47/55 |

## Fixed experiment

The comparison uses `asim-auth-lifecycle-v3` and `asim-operational-state-v4`, each
with a single Choice, `context=enriched`, and model `jev-1.13.0`. V4 changes only
the AuditEvent and NetworkSession descriptions; it retains the Authentication
definition, Unsupported choice, source projection, and primary-event instruction.
No extra questions, second stage, vendor override, or example demonstrations were
added. Definitions were fixed before calls and unchanged after seeing results.

The inputs reuse the frozen Meraki, Barracuda, and expanded-trial builds. Original
request bodies/hashes, cluster evidence, source-row joins, fixture checksums, and
native captures were verified before execution. Provider requests contain no
reference labels or parser output. Meraki, Carbon Black, and ASA retain their
vendor/product metadata; Barracuda retains its original generic
`barracuda_CL.Message` metadata. Both configurations receive identical evidence.

The catalogue remains pinned to `027a0f9338bfabcb27b784571b771c54572ebf01` and the
parser/sample resources to `4fc25fd4c008337dfd6d9c5663be833e66575d53`. Existing
native captures were verified and reused; Kusto was not rerun. The combined
capture identity is a hash of the eleven constituent capture identities.

All four families were inspected before this experiment. They are development
and regression evidence now, regardless of their original split assignments.
The revised descriptions also draw on Meraki findings and maintainer feedback.
This is not a new held-out test or a measurement of broad ASIM accuracy.

## Changed choices

All eight changes occurred among the fifteen Meraki AuditEvent templates,
increasing that subset's agreement from 0/15 to 8/15:

| Event meaning | Templates | Previous choice | Revised choice |
| --- | ---: | --- | --- |
| Port link/speed state changes | 2 | Unsupported | AuditEvent |
| Spanning-tree port-role changes | 2 | Unsupported | AuditEvent |
| VRRP passive-to-active transition | 1 | Unsupported | AuditEvent |
| Purging an ISAKMP security association | 1 | NetworkSession | AuditEvent |
| Deleting an ISAKMP security association | 1 | NetworkSession | AuditEvent |
| Establishing an IPsec security association | 1 | NetworkSession | AuditEvent |

These are improvements in compatibility with the adopted working policy and
reference parser. They are not eight independently adjudicated corrections.

## Remaining review cases

Seven Meraki templates still choose NetworkSession against AuditEvent references:
phase-one negotiation initiation; failure to get `sainfo`; connectivity becoming
true or false; phase-two negotiation timing out while waiting for phase one;
an IPsec-SA request queued without phase one; and phase-two packet preprocessing
failure. Review whether each primarily describes communication with a peer or an
operation on a managed resource. The maintainer guidance need not establish a
unique answer for every terse message; insufficient context remains a possible
review outcome.

Cisco ASA's SSH disconnection with user `Unknown` and reason `Internal error`
still chooses NetworkSession against Authentication/Logoff. Its source meaning
remains the question recorded in the [expanded assessment](jev-expanded-assessment-2026-10-02.md).

Barracuda `SESSION_TIMEOUT` chooses Authentication under **both** v3 and v4.
This agrees with the proposed interpretation in
[PR 15298](https://github.com/Azure/Azure-Sentinel/pull/15298), while the frozen
parser emits AuditEvent. It remains a disagreement in this report; no old
capture, fixture, or score was rewritten. This is not an improvement attributable
to the operational-state wording. Any future capture using the updated parser
must retain its own revision and comparison.

The eight existing synthetic controls also retain all top choices. Both versions
match 6/7 labelled controls, with one ambiguous disassociation left unscored.
Both incorrectly classify the deauthentication-flood **alert** as Authentication,
with probability 0.98. Its synthetic label is Unsupported relative to the three
offered schemas. The remaining failure concerns an alert about an action versus
the action itself; more confidence is not evidence that it is resolved.

## Usage, replay, and preservation

There were 226 completed decisions: 210 public-template decisions and 16 synthetic
decisions. The previous-definition run reused 103 cached responses and made ten
new Barracuda calls. V4 made 113 new calls. The **123 new requests** used 169,785
input tokens and 6,648 output tokens. These are observed token counts, not billed
monetary costs. Recorded prior responses are not charged again by replay.

V4's public-template requests used 150,162 combined tokens versus 139,965 for v3,
about 7.3% more. Median client round trip for new V4 public requests was 0.350 s;
this is an observed local timing, not a controlled latency benchmark. Most v3
answers were recorded in the earlier trial, so this comparison does not measure
repeatability of fresh calls over time, even with the same pinned model.

Local evidence is under `artifacts/jev-operational-state-2026-10-19/`: frozen
inputs, a file-hash manifest, prepared requests, live reports, verified reference
comparisons, `comparison.json`, and a targeted `review-cases.md`. The existing
dashboard exports `live/public-replay.html` and `live/synthetic-replay.html` with
previous and revised definitions alongside the source-concept baseline.

All 226 decisions replayed from cache with the provider sender blocked. Request
bodies, parsed answers, baseline outputs, and metrics matched the live reports;
all manifest file hashes were checked. The initial one-off driver omitted a
required argument when exporting the dashboard after all calls completed; a
separate offline replay/export step produced the dashboards without additional
provider calls. This did not change inputs, definitions, or predictions.

The local driver and replay step reuse the existing experiment, reference scorer,
cache, and dashboard. No new runtime framework, CI downloads, or publishing
workflow was added. Trial artifacts and caches remain ignored local evidence;
this is not yet a publicly reproducible archive. Use the existing
[preservation policy](../jev-schema-experiment.md#preserve-and-replay-a-reported-trial)
before publishing a result package.

## Next decision

Keep v4 opt-in and retain the existing default. Review the remaining boundary
cases and a few changed/agreed controls before adopting a working schema policy
in Potato. Prioritize the alert-versus-action distinction for a separate future
specification, using controls that also test ordinary authentication, audit, and
communication events. Do not modify this frozen specification to fit these
outputs or add another decision stage without a measured benefit.
