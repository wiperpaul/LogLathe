# Jev operational-state guidance

Status: implemented opt-in specification; live development comparison completed
Last verified: 2026-10-19

The [completed comparison](jev-operational-state-assessment-2026-10-19.md) improved
agreement from 85/102 to 93/102 eligible public templates. Eight Meraki choices
changed; other source families retained their top choices. The record below
describes the frozen policy and its original preparation, not independent labels.

## Decision and attribution

For the next experiment, follow Derrick Lee's guidance in
[Azure-Sentinel issue 15249](https://github.com/Azure/Azure-Sentinel/issues/15249).
His [AuditEvent response](https://github.com/Azure/Azure-Sentinel/issues/15249#issuecomment-6049096628)
includes operational state changes and security-association creation/deletion,
distinguishing acting on a resource from accessing another device. His
[Authentication response](https://github.com/Azure/Azure-Sentinel/issues/15249#issuecomment-6049112240)
prioritizes explicit login/logout meaning over a generic network-session interpretation.
The user chose to adopt this guidance as the working policy for now. This work
does not propose changes to Microsoft Sentinel or post an upstream follow-up.

`asim-operational-state-v4` implements that policy as a new decision specification.
It preserves the three available schemas plus Unsupported, source projection,
model, and primary-event instructions. The earlier specifications and cached
results remain unchanged. This is a maintainer-informed interpretation for our
experiment, not a claim that Microsoft's schema documentation has been revised.

## Boundaries supplied to Jev

- **AuditEvent:** operations and operational transitions on managed resources,
  including creation/deletion of security associations, interface link-state
  changes, and protocol-role changes. Automatic changes are included; neither a
  named administrator nor an explicit configuration edit is required.
- **NetworkSession:** communication with a destination peer, including connections,
  flows, permitted/denied traffic, and communication establishment or teardown.
  Distinguish the remote communication participant from a device whose resource
  is being changed. A contextual peer address alone does not change a resource
  operation into a communication event.
- **Authentication:** explicit identity authentication or termination of an
  authenticated relationship. Retain v3's authentication definition, including
  attempts, failures, sign-in/out, deauthentication, and authenticated-session
  expiry. Interpret login/logout semantically, rather than requiring those
  exact strings. A generic network disconnect does not establish sign-out.
- **Unsupported:** none of these available schemas fits. Missing mapping values
  or a missing actor do not, on their own, establish Unsupported.

The resource-versus-communication wording operationalizes the destination-device
distinction in Derrick's response. These descriptions contain no vendor override,
reference answer, or fixture-specific demonstration. They are informed by already
inspected Meraki outcomes and maintainer feedback, so the Meraki cohort is
development evidence rather than an untouched test set.

`--nouls` adds the three primary-schema diagnostic questions using these exact
definitions. V3's two extra lifecycle probes and staged policy remain specific to
v3: their communication question would not express the new resource boundary.
Do not reuse them as though they were a validated operational-state policy.

## Barracuda session timeout

Derrick's [Barracuda response](https://github.com/Azure/Azure-Sentinel/issues/15250#issuecomment-6048421972)
conditionally supports including `SESSION_TIMEOUT` in Authentication when the
event name is confirmed in real data. It does not direct all timeout events into
Authentication or confirm the exact meaning of the public sample. See the
[Barracuda follow-up](jev-barracuda-assessment-2026-10-02.md#maintainer-feedback-2026-10-10)
for the source evidence and its inconsistencies.

V4 retains v3's authenticated-session expiry wording, which already expresses
this distinction. Use identity-session context to distinguish expiry from a
generic network timeout; a username, GUI label, or event-name token alone is not
a universal schema rule. Preserve native reference labels and earlier predictions
while assessing the revised policy. This follow-up adds attribution and guidance,
not a further wording change to the prepared v4 requests.

## Prepare and assess

Local preparation on 2026-10-10 reused the frozen 32-template Meraki inputs:
32 template-only Choice requests and 32 enriched Choice requests are under
`artifacts/jev-operational-state-2026-10-10/`. No API calls were made. All 64
original Meraki v1 request bodies and hashes were verified unchanged; regression
tests also retain the frozen v1/v2/v3 example hashes. These local previews remain
ignored artifacts, not a published assessment or evidence of classification gains.

Use the existing command and an existing frozen annotation queue:

```powershell
uv run asim-forge evaluation schema-rank <queue-directory> `
  --input-kind queue --catalog <pinned-catalog-directory> `
  --decision-spec asim-operational-state-v4 `
  --output artifacts/jev-operational-state-preview
```

Without `--live`, this writes requests without provider calls or a key. Optional
`--nouls` must be specified before preparing the matching live run. The normal
default remains v1; selecting v4 is explicit and participates in cache identity.

Compare fixed v3 and v4 on the same source evidence and native reference captures.
Inspect resource-state cases and Authentication/NetworkSession controls, retaining
all cohort denominators and separating missing reference outputs from model errors.
Report corrections, regressions, per-schema agreement, and token usage. Do not
rewrite old labels or reports, infer provider predictions from mock tests, or
describe maintainer agreement as independently adjudicated accuracy. Freeze v4
before a live run; any wording changes after inspecting its responses need another
specification version.
