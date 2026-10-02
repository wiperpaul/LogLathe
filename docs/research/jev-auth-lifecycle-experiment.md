# Jev authentication lifecycle experiment

Status: live development assessment; clearer definitions helped, staging added no top-choice gain
Last verified: 2026-10-02

## Live finding

The clarified v3 Choice corrected all four known Meraki Authentication errors
without changing another top-choice prediction in that cohort. Adding parallel
probes gave the same selections. Feeding two probes into a second Choice added
no correct classifications and changed two operational-state selections without
establishing a semantic correction. The extra stage is not justified as a default
by this trial. The v1 default and previous assessment artifacts remain unchanged.

All variants used the same enriched inputs for 32 templates representing 49
source events. The v1 row is the frozen original trial, not a new provider run.
These are agreements with emitted parser schemas, not independently measured
semantic accuracy. All 15 native AuditEvent templates remain disputed compatibility
cases and stay in the full-cohort denominators.

| Variant | Authentication templates | NetworkSession templates | All templates | Represented events |
| --- | ---: | ---: | ---: | ---: |
| Frozen v1 Choice | 11/15 | 2/2 | 13/32 | 23/49 |
| v3 Choice | 15/15 | 2/2 | 17/32 | 28/49 |
| v3 Choice with Nouls | 15/15 | 2/2 | 17/32 | 28/49 |
| v3 staged Choice | 15/15 | 2/2 | 17/32 | 28/49 |

AuditEvent agreement was 0/15 templates and 0/21 events for every variant.
Authentication event agreement rose from 13/18 to 18/18; NetworkSession remained
10/10. The improvements cover the two WPA-deauthentication templates and two
failed-negotiation disassociation templates. The two 802.1X deauthentication
templates that regressed in v2 both remained Authentication in v3.

Staging changed `cluster-142f48b13b8dea4c` and `cluster-bbdaf637ed1732f2` from
Unsupported to NetworkSession. Their examples report switch-port speed/link
state changes, not explicit identity authentication or an endpoint flow.
Their communication probes were only 0.27 and 0.32. Neither change agreed with
the AuditEvent reference; these uncertain changes are not counted as corrections.

## Synthetic controls and cost

Every variant scored 6/7 labelled synthetic cases. The eighth, generic
disassociation case remained unscored. All classified ordinary TCP idle expiry
and tunnel teardown as NetworkSession. However, every variant missed the
deauthentication-packet-flood alert, which is labelled Unsupported relative to
this experiment's three available schemas. v1 selected NetworkSession; v3
selected Authentication with probability 0.98, and staged v3 raised that to 1.0.
The authentication-relationship probe was itself 0.93 on this alert, so the
extra question did not independently correct the semantic mistake.

Staging also changed the ambiguous disassociation from NetworkSession to
Authentication with probability 0.80 using a 0.58 authentication probe.
Its correct schema remains unresolved; increased certainty is not a demonstrated
improvement. These results motivate testing the distinction between an action
and an alert about that action before promoting broader authentication wording.

| Meraki configuration | Input tokens | Output tokens | Combined token volume |
| --- | ---: | ---: | ---: |
| v3 Choice | 44,537 | 1,712 | 46,249 |
| v3 Choice with five Nouls | 67,417 | 4,976 | 72,393 |
| v3 staged, both stages | 119,335 | 6,696 | 126,031 |

The probe configuration used about 1.57 times the Choice token volume; staging
used about 2.73 times, with no additional top-choice agreement. These are token
counts, not monetary prices. Staged trials reused first-stage responses already
cached by the diagnostic runs, so those tokens were not charged twice. Across
the Meraki and synthetic configurations there were exactly 128 distinct new
requests, totaling 196,824 input and 10,936 output tokens. All cases completed.
Staged wall-clock timings include cached first stages and should not be presented
as end-to-end latency for two fresh provider calls.

## Evidence and decision

Local artifacts are in `artifacts/jev-auth-lifecycle-trial-2026-10-02-live/`:
the trial manifest, original inputs, per-configuration requests and reports,
dependent second-stage bodies, native-reference comparisons, and `comparison.json`.
The one-off driver is `artifacts/reference-spike/jev-auth-lifecycle-trial.py`;
it reuses the existing experiment and reference scorers and verifies the fixture
captures and source-row joins before any call. It introduces no new publisher
or runtime evaluation dependency. These local files are not a published
reproduction package.

All seven configurations replayed with the provider sender blocked. Request
bodies matched the pre-call previews; dependent bodies, provider answers,
hashes, and reference-agreement files matched the live artifacts. There are
128 unique cached responses, including both stages. All frozen implementation
and input fingerprints matched after the run. Exact-value credential checks
found no raw or encoded key in repository candidates, caches, or trial artifacts.

Keep both v3 and staging opt-in. The evidence supports more explicit definitions
for these inspected Authentication cases, but not automatic use of a second
model decision. Further experiments should use an independently reviewed mix
of actual authentication events, alerts, communication events, and ambiguous
operational records. This cohort has already informed the wording and is a
development diagnostic, not a held-out generalization result. Do not modify
these frozen v3 questions in place after inspecting these results.

## Hypothesis and evidence

The frozen v1 Meraki runs misclassified two WPA-deauthentication templates and
two disassociation templates with explicit authentication-negotiation failure
as NetworkSession. The v2 audit experiment also regressed on two 802.1X
deauthentication templates. This motivates clarifying Authentication separately
from v2's structured format and AuditEvent changes.

[Microsoft's Authentication guidance](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-authentication)
includes sign-in and sign-out, with Logon/Logoff event types and Session expired
result details. It does not provide an exhaustive wireless-event taxonomy.
[Cisco's Meraki examples](https://documentation.meraki.com/Platform_Management/Dashboard_Administration/Operate_and_Maintain/Monitoring_and_Reporting/Syslog_Event_Types_and_Log_Samples)
explicitly describe `wpa_deauth` and `8021x_deauth` as deauthentication, and a
`disassociation` example with `auth_neg_failed=1` as failed authentication.
Our inference is that these records primarily report authentication lifecycle,
even when an automatic condition triggers them. Generic disassociation alone
does not establish the same meaning, nor does deauthentication imply failed login.

[TypeSafe's Jev 1.13 guidance](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
recommends literal conditions, explicit option boundaries, and narrower questions
when interpretation requires several steps. It also warns that Choice and Noul
probabilities need not obey identities across questions. Extra decisions can
diagnose a failure; their presence is not evidence of improved schema selection.

## Isolated specification

`asim-auth-lifecycle-v3` starts from v1 and changes only the Authentication and
NetworkSession descriptions. Authentication explicitly includes attempts,
failed negotiation, deauthentication, and expiry of an authenticated user/device
relationship. NetworkSession describes communication and distinguishes network
flow expiry from authenticated-session expiry. Missing usernames, addresses,
ports, and automatic disconnect triggers do not determine the schema alone.

The instructions, string representation, candidate set, AuditEvent description,
Unsupported description, and source projection remain identical to v1.
No vendor lookup, parser label, or fixture-specific example is added to requests.
The default remains v1. Prior requests, caches, and assessment reports stay frozen.

With `--nouls`, v3 retains the three primary-schema probes and adds:

- `authentication_relationship_change`: does the record explicitly describe
  an authentication attempt, rejection, deauthentication, sign-out, or expiry
  of an authenticated relationship?
- `communication_lifecycle`: does it explicitly describe communication
  establishment, traffic, or teardown as its primary action?

Answers are retained in the existing report and cache. They do not feed the
Choice, vote on its selection, multiply probabilities, or approve a mapping.
A high authentication probe with NetworkSession selected would suggest a
decision-boundary problem; a low authentication probe on explicit deauthentication
would suggest a recognition/context problem. These are diagnostic hypotheses,
not model explanations.

## How additional decisions could improve classification

Questions in one Jev request are evaluated in parallel against the supplied
state, as described in the [Choice documentation](https://docs.typesafe.ai/primitives/choice).
Adding the lifecycle probes does not feed their answers into the schema Choice.
The current experiment therefore separates a possible wording improvement from
the diagnostic value of the additional questions.

| Comparison | What it tests |
| --- | --- |
| Frozen v1 Choice versus v3 Choice, with identical inputs and context | Whether the clearer definitions improve schema selection |
| v3 Choice alone versus v3 Choice plus all five Nouls | Probe recognition, disagreement with Choice, any observed Choice differences, and additional token cost |
| v3 staged decision versus v3 Choice alone | Whether using the probe answers actually improves the final decision |

The opt-in `--staged` variant uses the existing v3 Choice-with-Nouls request as
its first stage, retaining that Choice as a comparison. It then sends a second
Choice with the original source projection plus only the two literal lifecycle
questions and their returned probabilities. The first Choice, its confidence,
and the three primary-schema probe answers do not enter the second request.
This uses policy `auth-probes-to-choice-v1`. Probe estimates are explicitly
described as model judgments, not verified facts or independent evidence;
source evidence takes precedence when they conflict. No threshold or product
of probabilities forces a schema.

Both stages are cached separately. The second request identity includes the
exact probe estimates; changing them changes its hash. A failure in the second
stage preserves the first response, final request body, and partial report.
Replay requires both cached responses, and a new live run can reuse the first
stage after a partial failure. A prepared staged run contains only the first
requests and reports that actual probe answers are required for stage two.

For each comparison, inspect corrections and new errors, results by schema,
completion, and token usage. Keep template-only and enriched inputs separate.
Do not attribute a wording improvement to probes, or treat diagnostic agreement
as improvement in the final classification. Freeze the questions before calling
the provider; changes made after inspecting answers define another experiment.

Further distinctions should follow observed errors and independently reviewed
source semantics. Useful hypotheses include an administrative operation versus
automatic operational state, authenticating an identity versus managing an
account or its policy, and an action occurring versus an alert about that action.
Some require expanding the current three-schema candidate set. Disputed parser
assignments alone should not determine the expected semantic answers.
Retain new questions only when their measured benefit justifies their complexity
and cost; the aim is a small reusable set of distinctions, not a growing list
of vendor-specific exceptions.

## Prepare offline

The eight cases in `examples/evaluation/auth-lifecycle-cases.jsonl` are synthetic
development diagnostics written after examining errors. They cover deauthentication,
failed negotiation, authenticated-session expiry, sign-out, ordinary TCP expiry,
tunnel teardown, an alert merely mentioning deauthentication, and ambiguous
disassociation. The ambiguous case has no forced schema label and is unscored.
Mapped cases include only a minimal EventType projection, not complete mapping truth.
These expected outcomes never enter the outbound request.

Prepare a new v3 preview using the existing command, without a key or API call:

```powershell
uv run --no-sync asim-forge evaluation schema-rank `
  examples/evaluation/auth-lifecycle-cases.jsonl `
  --catalog evaluation/ci-catalog `
  --decision-spec asim-auth-lifecycle-v3 --nouls `
  --output artifacts/jev-auth-lifecycle-preview
```

Use new output directories for v1/v3 and Choice/Choice-with-Nouls comparisons.
The synthetic cases share literal templates and event examples, so template
versus enriched comparisons there do not test parameter abstraction. The frozen
Meraki inputs do contain clustered templates and should be compared in both views.

For a live development trial, compare v1 and v3 on the same frozen Meraki inputs.
Inspect all Authentication templates and NetworkSession controls, including the
two v2 regressions, rather than only the four v1 mistakes. Keep disputed AuditEvent
labels separate from semantic judgments; a changed prediction is not automatically
a correction. Evaluate Choice alone before deciding whether extra probes justify
their token cost. Do not silently promote a probe-based selection rule.

The first live comparison fixes `context=enriched` for all three v3 variants
and uses all 32 frozen Meraki templates, including NetworkSession controls and
the disputed AuditEvent assignments. Original v1 results are replayable cached
comparisons rather than new calls. It also compares v1 Choice and the three v3
variants on the eight synthetic diagnostics. With no matching caches this
requires at most 128 distinct provider requests: 96 Meraki and 32 synthetic.
Staged trials reuse the corresponding v3 diagnostic responses. Report token
volume for both stages; reused first-stage tokens are not billed twice, and
their cache-read timings are not full staged provider latency.

Do not tune on the unresolved Barracuda timeout label as if it were semantic gold.
Its native-parser label and raw/structured sample consistency need adjudication.
Any live scores on these already inspected cases are development results, not
held-out generalization. Existing published/frozen performance reports are not
rewritten by this experiment.
