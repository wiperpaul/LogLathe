# Principles from Microsoft's ASIM parser agent

Status: design guidance for future field classification; no new runtime behavior
Reviewed: 2026-10-03
Canonical for: lessons from the public Microsoft skills and their application here

The comparison examined Azure-Sentinel revision
`4fc25fd4c008337dfd6d9c5663be833e66575d53`. Microsoft's initial
[requirements skill](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/.github/skills/asim-parser-user-prompter/SKILL.md)
selects a schema from source documentation and table name, stopping when the
target is unclear. It does not specify our primary-event boundary questions or
a measured classifier comparison. Its instructions are useful engineering
guidance, not evidence that either classifier is more accurate.

## Principles to carry into field classification

The following Microsoft instructions come from the
[creation skill](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/.github/skills/asim-parser-create-parser/SKILL.md)
and [validator skill](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/.github/skills/asim-parser-validator/SKILL.md).
The applications below are LogLathe's interpretation, not additional Microsoft
requirements.

| Microsoft instruction | Application in LogLathe |
| --- | --- |
| Derive the parser from source data and target guidance; avoid copying existing parsers. | Infer source roles and propose fields without reference answers in the provider input. Join parser outputs afterwards as compatibility evidence, separately from independent semantic labels. |
| Use `ASimTester.csv` for field classes and schema documentation for field meaning. | Restrict candidates and validation to the selected, pinned schema version. Explain the source role as well as the target field; physical type alone cannot distinguish actor, target, source, destination, and reporting device. |
| Sample actual rows and inspect values requiring transformations. | Preserve representative source events alongside templates and parameter profiles. Check a proposed extraction and conversion across examples, including rare variants; a large arbitrary sample need not cover them. |
| Normalize values explicitly; do not force recommended mappings unsupported by source data. | Record evidence, transforms, configured constants, and missing information separately. Never invent a value to satisfy a check. A missing field can limit parser conformance without making the event's schema classification wrong. |
| Read one source table; use unique static lookup keys; avoid event enrichment, fan-out, and aggregation. | Keep future generated parsers within a per-record boundary. Additional context used to understand a source must not silently become runtime event correlation. `EventCount` can describe repeated source activity while one input record still produces at most one normalized record. |

## Validation is complementary to semantic judgment

Microsoft executes schema and data testers and uses a bounded repair loop in its
[workflow](https://learn.microsoft.com/en-us/azure/sentinel/normalization-create-parsers-ai-agent).
The [data tester implementation](https://github.com/Azure/Azure-Sentinel/blob/4fc25fd4c008337dfd6d9c5663be833e66575d53/ASIM/dev/ASimTester/Testers/ASimDataTester.yaml)
checks output types, enumerations, formats, and other catalogue constraints.
It does not independently infer the event's primary action from source text.

Keep three questions separate when field classification resumes:

1. Does the selected schema represent the source event's meaning?
2. Do the proposed fields represent the right participants, action, and result?
3. Does the executable output conform to the pinned schema and preserve source
   record cardinality?

A valid IP address assigned to the wrong participant can pass formatting checks.
A parser's emitted schema establishes implemented behavior, not independent
semantic truth. Preserve disagreements for adjudication rather than changing
labels or manufacturing fields to improve agreement.

For the next Jev experiment, adopt Derrick Lee's subsequent
[operational-state guidance](jev-operational-state-guidance.md) as the working
schema policy. It broadens AuditEvent beyond the original configuration-focused
descriptions. Keep that policy version separate from earlier frozen results and
from field/output conformance checks.

Reuse existing catalogue checks, fixtures, and native execution paths for a
future mapping experiment. Do not introduce another validation framework or
assume the local checks are identical to Microsoft's deployed testers. Any
repair loop should retain the failed proposal and errors, have a finite limit,
and avoid treating validator success as semantic approval.

## Incomplete documentation and continuous classification

Up-to-date message catalogues or machine-readable log contracts may be absent,
especially for internal applications. Templates and actual examples can supply
observed evidence without claiming to reconstruct a complete source contract.
Use available vendor documentation as additional context, preserve its revision,
and retain ambiguity where observations cannot establish a role or action.

Template reuse makes continuous schema classification a plausible future design:
classify new or materially changed evidence, then reuse a versioned decision for
matching events. It does not require hosted inference on each event. Reassessment
must account for changed examples, source context, extraction, model, definitions,
and catalogue; the template string alone is not a sufficient cache identity.
Clusters can conceal semantic differences, so reuse depends on coherence and
drift checks rather than confidence alone.

The existing Jev path is a batch experiment with request-addressed caching, not
an unattended streaming classifier. See the
[Jev guide](../jev-schema-experiment.md#template-level-reuse-and-continuous-operation)
for the current boundary and the
[expanded assessment](jev-expanded-assessment-2026-10-02.md) for measured results.
