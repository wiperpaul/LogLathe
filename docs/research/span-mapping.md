# From selected text to validated ASIM mappings

Status: literal evidence and constant-output increment implemented on the follow-up branch;
value translations and capture proposals remain proposed
Date: 2026-09-14

## Recommendation

Keep DeepParse for structural grouping and Potato for review. Let a reviewer
label any meaningful source span, then resolve a separate executable mapping:
an existing capture, a template-scoped constant, a value translation, or a new
capture proposal. Selecting evidence must not require a wildcard at that position.

The first increment connects literal-span evidence to the constant mapping
capability already present in the compiler. General extraction repair can follow
as a separately measurable extension of the same review workflow.

## What Drain and DeepParse actually support

The original [Drain paper, section III-C](https://pinjiahe.github.io/files/pdf/research/ICWS17.pdf)
partitions messages by token count after preprocessing. Its template updates
compare corresponding positions. A normal wildcard therefore represents a token
position; Drain does not inherently combine an arbitrary varying phrase into one
parameter. The paper explicitly acknowledges that one event type can have
different token counts.

[DeepParse, section 4.6](https://arxiv.org/html/2604.20553v1#S4.SS6), applies regex
masks to the full message before tokenization. A mask that consumes a multiword
value can replace it with one typed placeholder. Different raw word counts can
then yield the same masked token count. This depends on having an appropriate
mask; it is not guaranteed for an unknown free-text phrase.

The pinned implementation at `b53c29b379be5ab834ff990154297ef8fea8d98a` confirms
this order in `deepparse/drain/drain_engine.py`: `add_log()` masks, tokenizes,
and chooses a bucket by token count plus a bounded prefix. Its tokenwise
generalization operates only within that bucket.

Our [adapter](../../src/asim_forge/clustering.py) currently requests
`synth_masks(..., mode="offline")`. The dependency's offline implementation uses
predefined core patterns and optional token-shape patterns. It does not run the
paper's model-based mask synthesis or learn arbitrary phrase boundaries. An
optional model path exists upstream, but enabling it would be a separate evaluated
change, not a prerequisite for fixing semantic review.

## Local experiment

These synthetic probes were run against the installed pinned dependency. They
test mechanism, not accuracy on real-world logs. All examples share the prefix
`service audit record for user` and suffix `; result Failed`.

| Names supplied | Preprocessing | Observed result |
| --- | --- | --- |
| `Alice Smith`, `Bob Jones` | No masks | One cluster, two wildcards: `user <*> <*> ; result Failed` |
| `Alice`, `Bob van Jones` | No masks | Two clusters because token counts differ |
| `Alice`, `Bob van Jones` | Mask the name between `user ` and ` ; result` | One cluster, one wildcard: `user <*> ; result Failed` |

The demonstration mask was `(?<=user ).+?(?= ; result)`. It is a controlled
example, not a proposed general-purpose name regex. Our existing template
capture code also recovered `Alice` and `Bob van Jones` as individual values
from one `<VAR:NAME>` slot.

This means the current representation can capture spaces inside a slot. However,
its general `(.+?)` captures do not prove correct boundaries: adjacent captures,
repeated delimiters, escaped quotes, empty values, and whitespace changes need
explicit checks. Multi-line event framing is a separate ingestion problem.

## Research implications

- [Spell, section III-C](https://www2.cs.utah.edu/~lifeifei/papers/spell.pdf)
  uses longest common subsequences and merges adjacent differing regions into a
  wildcard. It gives an example with one versus two node names sharing a single
  wildcard. Fixed token counts are therefore an algorithm choice. Alignment is
  worth testing for repair suggestions if masks leave fragmented clusters; it
  does not determine the ASIM meaning of a phrase.
- [Documentation based Semantic-Aware Log Parsing, section II-B](https://arxiv.org/pdf/2202.07169)
  extracts variable-length, multiword parameters between stable keyword anchors
  and integrates semantic parameter annotations with customized Drain3. Its
  stated single-parameter-between-anchors constraint matters: a bounded region
  is not enough to disambiguate several adjacent fields. The demonstration uses
  IBM z/OS messages, so general effectiveness remains to be tested here.
- [SemParser](https://arxiv.org/pdf/2112.12636) treats semantics in both parameters
  and template text as useful information. This supports the distinction between
  structural variability and semantic meaning. It is not a ready multiword
  solution: the paper notes single-word semantic-pair granularity and requires
  annotation/fine-tuning for new systems.
- [Drain3's parameter extraction](https://github.com/logpai/Drain3#parameter-extraction)
  incorporates mask regexes when extracting values. That is a useful reference
  for retaining capture constraints rather than reconstructing every placeholder
  with the same unrestricted pattern.

These sources motivate the following design; they do not establish ASIM mapping
accuracy or prove that a replacement clustering engine is needed.

## Proposed behavior in the existing mapping tab

| Reviewer selection | Saved meaning and executable mapping | Review feedback |
| --- | --- | --- |
| `5` for `EventCount` | Selected evidence plus existing slot and integer conversion | Show `EventCount` on the span and output preview `5` |
| Literal `Failed` for `EventResult` | Selected evidence plus confirmed template-scoped constant `Failure` | Show `EventResult` on the span; offer the catalogue's output values |
| Variable `Failed` / `Accepted` for `EventResult` | Selected evidence plus capture and explicit value translation | Preview `Failure` / `Success`; unknown values remain unresolved |
| Multiword value absent from existing slots | Selected evidence plus proposed bounded extraction | Show the target field, extracted examples, and any ambiguous or unmatched cases |

The selected span answers which source text supports a mapping. The executable
binding answers how to produce its value for future matching events. Store both
in the existing Potato draft. Keep the ASIM field visible on the span; show
binding readiness and allowed-value problems separately.

For a constant, verify that the evidence aligns with literal template text and
scope the output to that template's matching rule. A value occurring unchanged
in a few examples is insufficient evidence that it should be constant. Keep the
selected offsets, example identity, template revision, and reviewer confirmation.

This extends existing functionality:

- `MappingRow` already supports slots, source columns, constants, and span evidence.
- The semantic-frame approach already models `template_constant` evidence.
- The compiler already supports constant outputs and template filtering.
- Validation now distinguishes exact-slot evidence from literal evidence for a
  constant. Ambiguous template alignment cannot establish either binding.

General capture proposals should prefer documented/parser-backed delimiters and
typed masks. Validate their boundaries on the available source events, including
nearby nonmatching formats, before freezing an executable rule. Show how many
events were checked and which remain untested. A missing capture can be repaired
within a coherent cluster; a mask change that alters membership must rebuild the
cluster evidence and require review again. Reuse the existing cluster tab for
actual coherence or framing problems.

## Smallest useful next increment

1. Retain literal spans as evidence for existing constant mappings; supply an
   allowed-value selector and show `Failed` -> `Failure` before approval.
2. Keep field labels visible on every selected span. Distinguish an unresolved
   binding, a type conversion, and an invalid normalized value in the editor.
3. Add an explicit value-translation representation for varying result words,
   then bounded multiword capture proposals. Validate Python-side previews and
   emitted KQL together; a successful Python regex alone does not validate KQL.

Use the existing pinned OpenSSH fixtures and reference captures for the first
increment, with a small local set of declared synthetic boundary cases. Include
literal outcomes, translated outcomes, one- versus several-word values, adjacent
parameters, missing/escaped delimiters, and an unrelated event that must not match.
Keep preparation examples separate from held-out variants. Native parser outputs
are evidence and differential checks; disagreements still require assessment.

Measure correct output values, exact capture boundaries, incorrect matches,
unresolved cases, and reviewer corrections. Cluster count alone cannot show that
normalization improved. No new external corpus download or team annotation
exercise is needed to start this experiment.
