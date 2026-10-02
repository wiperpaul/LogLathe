# Jev AuditEvent boundaries and structured-rubric experiment

Status: development investigation; disputed parser labels are not semantic gold
Audience: schema-ranking contributors and reviewers
Canonical for: AuditEvent boundary research and the opt-in v2 rubric
Last verified: 2026-10-02

## Finding and scope

The Meraki disagreement is partly a definition/reference mismatch. Its parser
deliberately classifies operational VPN, interface, and router-state events as
AuditEvent. Our v1 rubric describes configuration and policy operations. Improving
agreement by treating every state change as an audit event would change the target
semantics, not simply repair model recognition. The parser's assignments remain
valuable compatibility evidence; they do not establish independent correctness.

This is a reason to clarify boundaries and obtain human judgments, not to declare
the parser creator wrong. Configuration, managed-resource operations, network
protocol state, and device health can overlap. No upstream issue is opened, and
the public source messages, reference outputs, and previous scores stay frozen.

## What Jev's own guidance says

[TypeSafe's Jev 1.13 failure-mode guide](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
documents literal interpretation, indirect questions, distracting context, and
conflicting instructions/criteria. It also shows that a Choice and matching Nouls
need not return equivalent probabilities. Adding probes is useful diagnostically;
it does not supply independent corroboration or automatically fix a boundary.
These are provider-documented examples, not independent ASIM failure studies.
The search did not identify a reliable firsthand public Jev/ASIM case study.

[Choice guidance](https://docs.typesafe.ai/primitives/choice) specifically recommends
structured option descriptions when adjacent options are confused, with coverage,
exclusions, and examples. [Structured entries](https://docs.typesafe.ai/primitives/advanced)
are supported for instructions and criteria. Our original adapter constrained
these fields to strings; this investigation adds the documented JSON shapes.

The provider's [borderline moderation cookbook](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook)
shows selections changing across repeated requests and separates automatic
coverage from agreement. It uses a moving model alias and another task; its
threshold is not transferable evidence for ASIM. Replay stability in our trials
likewise does not establish hosted-model stability.

## ASIM and other open-source taxonomies

[Microsoft's AuditEvent guidance](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-audit)
emphasizes configuration/policy activity, but its object/operation model also
covers managed resources, services, scheduled tasks, and event logs. Allowed
operations include Read, Create, Delete, Start, Stop, and Clear. A named actor is
recommended, not mandatory; its absence alone should not reject an audit event.

[ASIM NetworkSession](https://learn.microsoft.com/en-us/azure/sentinel/normalization-schema-network)
describes communication, endpoint/intermediate-device sessions, and flows, with
start/end subtypes and tunnel-related result/action values. A transition word
does not by itself distinguish configuration from communication. Conversely,
an interface carrier-loss notification is not necessarily a session record.

| Source | Relevant distinction | Implication for this investigation |
| --- | --- | --- |
| [OCSF 1.9 Tunnel Activity](https://github.com/ocsf/ocsf-schema/blob/v1.9.0/events/network/tunnel_activity.json) | Secure-tunnel establishment, teardown, and renewal belong to networking | VPN lifecycle has a natural network interpretation; this is not an automatic OCSF-to-ASIM mapping |
| [OCSF 1.9 Device Config State Change](https://github.com/ocsf/ocsf-schema/blob/v1.9.0/events/discovery/device_config_state_change.json) | Device security-state changes have a separate class and optional actor | Audit, device state, and network activity are not one universal bucket |
| [Elastic ECS categories](https://www.elastic.co/docs/reference/ecs/ecs-allowed-values-event-category) | Configuration concerns settings/parameters; networking and logical sessions have their own categories | ECS permits multiple categories, unlike our current single-ASIM-schema decision |
| [Kubernetes auditing](https://kubernetes.io/docs/tasks/debug/debug-cluster/audit/) | Audit records capture API activity by users, applications, and control-plane components | Administrative operations can be automated; a human actor is not required |

These sources inform the boundary review. ASIM remains our target ontology; their
classes and category arrays are not substituted for its schema definitions.

## Trace of the Meraki reference

[Cisco's source descriptions](https://documentation.meraki.com/Platform_Management/Dashboard_Administration/Operate_and_Maintain/Monitoring_and_Reporting/Syslog_Event_Types_and_Log_Samples)
identify VPN negotiations/tunnel establishment, port status, spanning-tree role
changes, and VRRP transitions. They do not describe those sample records as
administrator configuration edits. This supports a scope-mismatch hypothesis,
but does not determine the intended ASIM schema on its own.

The [official parser at the checked upstream revision](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/Parsers/ASimAuditEvent/Parsers/ASimAuditEventCiscoMeraki.yaml)
was byte-identical to our pinned parser on 2026-10-02:
`d708665db9d9822e2d325fc7361edfbb9fb804d9922f566897aa76d5afb3db08`.
It is parser version 0.2.1 and targets AuditEvent schema 0.1. Its prefilter explicitly
selects these operations and assigns AuditEvent. Its lookup normalizes VPN
connectivity to Set, associations to Create/Delete, and interface status to
Enable/Disable. This is deliberate implemented coverage, not a filename-derived
label, accidental join, or a demonstrated recent regression. Code establishes
what it does, not the creator's unstated rationale.

| Frozen Meraki AuditEvent family | Templates / represented events | v1 enriched selections |
| --- | ---: | --- |
| VPN negotiations, associations, and connectivity | 10 / 14 | NetworkSession |
| Port status, STP roles, VRRP transition | 5 / 7 | Unsupported |

All 21 native AuditEvent outputs omit Object, ActorUsername, OldValue, and NewValue.
Our pinned catalogue already requires Object, independently of the current web
documentation's newer schema version. The existing conformance checker exposes
that omission; the other fields are not all mandatory. Missing Object is a
separate conformance issue and does not prove every EventSchema assignment wrong.

The strongest justified conclusion is that generic guidance and this parser's
operational coverage diverge enough to need explicit adjudication. Port/STP/VRRP
records are especially ambiguous within the three available choices. A blanket
rule assigning all network-device changes to AuditEvent is not supported here.

## Implementation and experiment

`asim-primary-event-v1` remains the default. The opt-in `asim-boundaries-v2` adds
structured coverage, exclusions, and generic constructed examples; it represents
configuration and administrative resource operations more explicitly, allows
missing actor context, and separates runtime state from administrative actions.
This is a new hypothesis, not a silently corrected ontology or a zero-example run.
It changes both representation and some wording; an improvement cannot be
attributed to JSON formatting alone.

The existing `evaluation schema-rank` command accepts
`--decision-spec asim-boundaries-v2`. Request hashes, cache lookup, and report
provenance include the selected specification. The model, source projection,
candidate schemas, lexical baseline, and advisory review policy are unchanged.
All 84 frozen Meraki/Barracuda v1 request bodies and hashes were checked identical.
No new framework, provider dependency, or CI call is introduced.

The existing canonical case format holds ten
[synthetic boundary diagnostics](../../examples/evaluation/audit-boundary-cases.jsonl).
They contrast administrative and operational activity with configuration reads,
automated service operations, log clearing, authentication, and network controls.
Their labels were developer-authored after inspecting results and are neither
independent human labels nor held-out performance evidence. Their minimal EventType
projection satisfies the existing case contract; they are not complete mapping
truth for generating a parser. No expected answer enters Jev's outbound state.

Prepare them offline using a new output directory:

```powershell
uv run --no-sync asim-forge evaluation schema-rank `
  examples/evaluation/audit-boundary-cases.jsonl `
  --catalog evaluation/ci-catalog --context template `
  --decision-spec asim-boundaries-v2 --output artifacts/jev-audit-boundaries-prepare
```

The bounded development comparison uses the same 32 frozen Meraki inputs with
template-only v2 and the existing v1 cache, plus both rubric versions on the ten
synthetic diagnostics. The 52 possible new requests keep the model/input views
fixed and avoid tuning on new Barracuda results. Meraki reference agreement and
synthetic expected-label agreement must be reported separately. Neither supplies
an independently adjudicated semantic improvement claim.
