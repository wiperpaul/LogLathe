# Documentation

Status: current
Canonical for: documentation navigation
Last verified: 2026-09-10

Choose the guide that matches the work you are doing. The root
[README](../README.md) provides the project overview and shortest runnable example.

| Audience or task | Start here | Canonical content |
| --- | --- | --- |
| First-time user | [Operator workflow](operator-workflow.md) | Build, cluster review, catalogue sync, compilation, and artifacts |
| First pilot reviewer | [Reference pilot](reference-pilot.md) | Public SSH samples, native output replay, and the existing Potato review workflow |
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
- [Field-mapping baseline record](archive/field-mapping-baseline.md) records the
  completed and partially completed baseline implementation sequence.

Current behavior belongs in the task guides above. Remaining work belongs in the
roadmap. When a dated plan is completed or superseded, retain it under `archive/`
rather than leaving future-tense instructions in the active documentation set.
