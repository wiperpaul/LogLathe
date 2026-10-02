# Documentation

Status: current
Canonical for: documentation navigation
Last verified: 2026-10-03

Choose the guide that matches the work you are doing. The root
[README](../README.md) provides the project overview and shortest runnable example.

| Audience or task | Start here | Canonical content |
| --- | --- | --- |
| First-time user or mapping reviewer | [Operator workflow](operator-workflow.md) | Build, staged Potato review, span mappings, setup defaults, compilation, and artifacts |
| Native-reference reviewer | [Reference workflow](reference-pilot.md) | Worked OpenSSH example, native output replay, and mapping review against reference output |
| Public-fixture contributor | [Reference fixture inventory](../evaluation/reference/README.md) | Five source families, pinned inputs/parsers, coverage limitations, and source-family splits |
| Jev experimenter | [Jev schema experiment](jev-schema-experiment.md) | Offline request previews, explicit live runs, cached replay, decision definitions, and assessment records |
| Evaluator or approach developer | [Evaluation](evaluation.md) | Registered approaches, metrics, robustness, corpora, and reports |
| Dataset curator or annotator | [Dataset curation](dataset-curation.md) | Fixture contract, blinded annotation, promotion, grouping, and splits |
| Contributor or integrator | [Architecture](architecture.md) | Current boundaries, package ownership, and target-neutral seams |
| Project contributor | [Contributing](../CONTRIBUTING.md) | Development checks, contribution scope, and data-handling rules |
| Planning work | [Roadmap](../ROADMAP.md) | Current milestone, future milestones, and deferred work |

## Research and historical records

These documents preserve rationale and experiment history. They are not
authoritative descriptions of current implementation status.

- [Semantic-mapping research](research/semantic-mapping.md) records the literature
  review and the architectural decision to separate source semantics from target
  projection.
- [Baseline and corpus strategy](research/baseline-strategy.md) records the broader
  evidence-acquisition design and proposed non-LLM ladder.
- [Span-mapping research](research/span-mapping.md) records extraction boundaries
  and the distinction between selecting evidence and creating an extraction rule.
- [Expanded Jev assessment](research/jev-expanded-assessment-2026-10-02.md)
  records the latest frozen Carbon Black Cloud and Cisco ASA comparison. The
  [Jev guide](jev-schema-experiment.md) links earlier trials and their limitations.
- [Field-mapping baseline record](archive/field-mapping-baseline.md) records the
  completed and partially completed baseline implementation sequence.

Current behavior belongs in the task guides above. Remaining work belongs in the
roadmap. When a dated plan is completed or superseded, retain it under `archive/`
rather than leaving future-tense instructions in the active documentation set.
