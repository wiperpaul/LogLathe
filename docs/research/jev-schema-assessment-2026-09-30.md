# First live Jev schema assessment

Status: exploratory; local review draft, not an accuracy benchmark
Audience: reviewers of the Jev schema experiment
Canonical for: observations from the first frozen OpenSSH trial
Last verified: 2026-09-30

## Assessment

Jev is worth continuing as an advisory schema selector. It distinguished explicit
login events from network-session evidence more usefully than the existing lexical
baseline in this small sample. Enrichment also introduced a questionable schema
change for a reverse-DNS diagnostic. Noul probes exposed uncertainty but changed
none of the selected schemas and increased token usage. These observations do not
justify automatic approval, calibrated thresholds, or a general accuracy claim.

The hosted API worked with the existing adapter without a production-code change.
All 72 distinct requests completed and validated. A subsequent offline replay
returned the same 72 responses with provider calls explicitly blocked.

## Frozen setup

| Item | Value |
| --- | --- |
| Date | 2026-09-30 |
| Implementation | `e857d90` |
| Model | `jev-1.13.0` |
| Decision specification | `asim-primary-event-v1` |
| ASIM catalogue revision | `027a0f9338bfabcb27b784571b771c54572ebf01` |
| Source | Existing frozen OpenBSD/OpenSSH annotation queue |
| Queue tasks SHA-256 | `e09dbe8eeb143cd94d4553c4a56b0ec0fd7259687d9a8fb060af266c2bcad69b` |
| Size | 18 templates from one source family |
| Independent schema labels supplied to evaluation | 0 |
| Available choices | Authentication, NetworkSession, AuditEvent, Unsupported |
| Runs | Template-only/enriched, each with Choice only or Choice plus three Noul probes |

Definitions, samples, and model version stayed fixed throughout the trial. No
gold labels, reference-parser outputs, or demonstrations were sent. The source
queue had cluster approvals; those are not schema gold labels. Several templates
differ mostly by fixed usernames, so the 18 templates are not 18 independent
semantic event families. Only three of the catalogue's 13 schemas were candidates.

The five-request smoke test used enriched inputs with Nouls. The full run reused
those five cached responses and made 13 more calls for that configuration. Each
other configuration made 18 calls: 72 distinct successful live requests in total.
The five smoke-test observations are not counted as additional evaluation cases.

## Observed results

Counts below are predictions, not correctness scores. No run selected AuditEvent.

| Configuration | Authentication | NetworkSession | Unsupported | Median confidence | Median live time | Input tokens | Output tokens |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Template, Choice | 14 | 2 | 2 | 0.990 | 356 ms | 10,849 | 944 |
| Enriched, Choice | 15 | 2 | 1 | 1.000 | 362 ms | 15,294 | 944 |
| Template, Choice + Nouls | 14 | 2 | 2 | 0.985 | 339 ms | 16,699 | 2,096 |
| Enriched, Choice + Nouls | 15 | 2 | 1 | 1.000 | 358 ms | 21,144 | 2,096 |

Total provider-reported usage was **63,986 input tokens and 6,080 output tokens**.
This is token usage, not an invoice or estimated monetary cost. Timing measures
the local client round trip, including response validation and cache persistence;
it is not server inference latency. For the five reused smoke-test entries, the
table uses their original live timings, not their later cache-read timings.
These small, sequential runs are not a throughput or latency benchmark.

Template-only and enriched selections agreed on 17 of 18 cases, both with and
without Nouls. Choice-only and Choice-plus-Nouls selections agreed on all 18 cases
within each context view. Distributions and confidence sometimes differed, so
unchanged selections do not establish identical internal computation or stability
over repeated live trials. Cache replay verifies artifact reuse, not model
repeatability. The lexical baseline selected NetworkSession 12 times and
abstained six times; it selected Authentication zero times.

## Semantic review of the differences

The following interpretation is an **LLM-assisted post-hoc review**, not independent
human adjudication. Microsoft describes Authentication around identity sign-in and
sign-out, and Network Session around network connections and sessions. That supports
distinguishing the action in a login message from its accompanying IP/port evidence.
See the [Authentication schema](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-authentication)
and [Network Session schema](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-network).

Fourteen templates read as accepted/failed logins, invalid-user events, or repeated
login failures. All four configurations selected Authentication for these. Within
this post-hoc group, the baseline selected NetworkSession ten times and abstained
four times. This is useful qualitative evidence for semantic classification; it
is not a measured accuracy improvement on a held-out labelled set.

The remaining four templates deserve explicit human review. Descriptions below
are generalized to omit source usernames, hosts, addresses, and key fingerprints.
Values are from the Choice-only runs unless otherwise stated.

| Diagnostic | Template-only result | Enriched result | Review interpretation |
| --- | --- | --- | --- |
| Reverse-name lookup failed; break-in warning | Unsupported, probability 0.52, confidence 0.36 | Authentication, probability 0.55, confidence 0.40 | The only selection change. Source context may be anchoring the decision toward login activity without an explicit authentication outcome. More context is not automatically better evidence. |
| Forward/reverse name mismatch; break-in warning | NetworkSession, probability 0.57, confidence 0.42 | NetworkSession, probability 0.51, confidence 0.35 | Low confidence in both views. A name-validation diagnostic does not clearly assert a network session. Unsupported within this candidate set remains plausible. |
| Suspicious PTR record ignored | Unsupported, probability 0.63, confidence 0.50 | Unsupported, probability 0.56, confidence 0.41 | Plausible outside the three available schemas. The wording alone does not establish that a configuration change occurred. |
| Client stopped responding; session timeout | NetworkSession, probability 0.99, confidence 0.99 | NetworkSession, probability 0.96, confidence 0.95 | A network/session interpretation is plausible, but whether this should represent an authenticated-session sign-out needs source-specific semantics. |

With enriched Nouls, the reverse-lookup warning selected Authentication with
probability 0.50 and confidence 0.33, while its primary-Authentication probe was
only 0.28. The name-mismatch warning selected NetworkSession with probability
0.48 and confidence 0.30, while its primary-NetworkSession probe was 0.22.
Those disagreements are useful review signals rather than decisive veto rules.

The probes also overlap on obvious login events. For an accepted `none` method,
the enriched Authentication and NetworkSession probes were 0.89 and 0.73, while
Choice selected Authentication with probability 0.96. The template-only suspicious
PTR diagnostic produced an AuditEvent probe of 0.63 although Choice preferred
Unsupported. Narrow yes/no questions still need evaluation; they do not provide
independent corroboration just because they return separate numbers.

TypeSafe's [confidence guidance](https://docs.typesafe.ai/confidence) describes
confidence as a property of the choice distribution. Values of 1.00 here are
provider-reported certainty, not proof of correctness. No confidence threshold was
tuned, validated, or used to approve a schema during this assessment.

## Recommended next experiment

Keep Choice-only as the simpler candidate for future advisory integration; retain
Nouls as optional diagnostics. Their extra token usage bought no selection change
in this trial, though it revealed disagreements worth studying. Keep both context
views available until independent labels show whether enrichment helps.

The most useful human contribution now is adjudicating the three name-resolution
diagnostics and the session-timeout boundary. Then collect a small, diverse blind
schema set containing real network activity, configuration changes, authentication,
and events outside the available schemas. The current sample cannot establish
AuditEvent performance, broad rejection quality, or generalization across vendors.
Any future prompt changes motivated by this trial belong on a development split;
this inspected queue should not subsequently be described as untouched test data.

Only after that comparison should a Jev result be considered for the existing
Potato Schema stage. Field mapping, extraction quality, required-field handling,
and parser compilation were not evaluated in this run.

## Local audit trail and publication boundary

Raw requests, responses, caches, timings, and the aggregate JSON remain under the
ignored `artifacts/jev-assessment-2026-09-30` and `artifacts/jev-cache` directories. ( keeping in notes in case repeated I am not commiting artifactss repos atm )
This note contains aggregate results and generalized event descriptions. It does
not contain the credential, authorization headers, raw messages, or fingerprints.
The key was loaded from a Windows-encrypted file outside the repository into the
live subprocess environment and removed from that environment when the run ended.

The four configurations were also replayed with `send_request` patched to raise
if invoked. Every replay completed with 18 cache hits and identical parsed JSON
response content. Local `summary.json` records the original report hashes and
checks input alignment and unique live-request counts. No new live calls are
needed to inspect these results. See the [experiment guide](../jev-schema-experiment.md)
for request construction and replay commands.
