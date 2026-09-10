# ASIM field-mapping baseline plan

Status: archived implementation and experiment record; not authoritative for current behavior
Plan reviewed: 2026-09-02
Archived: 2026-09-10

This record preserves the baseline sequence, decisions, and measurements made while
the work was implemented. Some "current" defects and future-tense steps below have
since been resolved. See the [evaluation guide](../evaluation.md),
[architecture](../architecture.md), and [roadmap](../../ROADMAP.md) for current
behavior and remaining work.

## Purpose

[The non-LLM corpus plan](../research/baseline-strategy.md) decides *which evidence* may
support which claim. This note decides *what to build, in what order*, so the field
mapping stage acquires an interpretable baseline. It is a sequencing and
implementation document, not a new evidence contract.

## Problem statement

The ignored local snapshot `artifacts/evaluation-baseline.json` reported
`direct-lexical` at `field_micro_f1 = 0.857` on one synthetic case. That
number has no floor, no ceiling, and no error decomposition, so it cannot support
any statement about approach quality — including the statement that the approach
works at all.

A baseline for this stage is therefore not one score. It is four reference points
reported with the same metric engine, each carrying a different permitted claim.

| Reference point | Definition | Permitted claim |
| --- | --- | --- |
| **B-floor** | Trivial priors that use no source evidence | Whether an approach beats class imbalance |
| **B-frozen** | `direct-lexical` v3, `semantic-frame` v3, `case-retrieval` v2, never tuned on harvested evidence | What existed before evidence was acquired |
| **B-oracle** | The same approaches given gold schema, and later gold source frame | Where the error actually is |
| **B-ceiling** | Signature-collision ceiling per context view; later, inter-annotator agreement | What is identifiable from the available context |

The gap B-frozen to B-oracle is engineering headroom. The gap B-oracle to B-ceiling
is information headroom. Anything above B-ceiling requires the human or
repository-agent inputs described in
[the corpus plan](../research/baseline-strategy.md#later-human-and-repository-agent-assistance).

## Baseline defects recorded at the start

These are recorded so the frozen baseline is understood rather than mistaken for a
tuned system. None should be fixed before B-frozen is captured.

| Defect | Location | Consequence |
| --- | --- | --- |
| Candidate generation and ranking are one function | [field_ranking.py](../../src/asim_forge/semantic_mapping/field_ranking.py) | Fields with zero token overlap are discarded before scoring, creating an unmeasured recall ceiling |
| `Alias` fields are excluded from ranking | `rank_fields` | Legitimate ASIM alias targets can never be predicted; the cost is unmeasured |
| Only `name` and `logical_type` are used | `rank_fields` | Catalogue descriptions, requirement level, and `allowed_values` enumerations are unused |
| `0.35`/`0.65` precision and coverage weights | `rank_fields` | Arbitrary constants with no train partition on which they could be tuned legitimately |
| Alphabetical tie-breaking | `rank_fields` sort key | Systematic bias toward `ActorUsername` over `TargetUsername` and similar pairs |
| Schema concept overlap is an unnormalized count | [source_concept.py](../../src/asim_forge/schema_ranking/approaches/source_concept.py) | Schemas with larger concept sets are structurally favoured; invisible at three schemas |
| Slots are mapped independently | all approaches | Two slots may claim one target that the compiler will reject; no assignment step exists |

The catalogue `allowed_values` enumerations (`EventResult`, `DvcAction`, and
similar) are the largest cheap win available: matching template *constants* against
target enumerations is high precision and needs no corpus. It is deliberately
deferred until after B-frozen so the improvement is measurable.

## Sequenced work

### PR 1 — floor, decomposition, and visible ties

No new corpus. Establishes the interpretation machinery for every later number.

1. Register prior approaches so the metric engine scores them identically to real
   approaches: `null-prior`, `majority-schema-prior`, `field-frequency-prior`.
   Priors fit on reference cases only and exclude the case under evaluation, using
   the same leave-one-out discipline as `case-retrieval`.
2. Add the schema oracle as an explicit, recorded harness condition rather than an
   approach, so oracle results can never be reported as approach results.
3. Separate candidate generation from candidate scoring in `field_ranking`, and
   emit `candidate_recall_at_1/3/5` and `candidate_reduction_ratio` so retrieval
   failure is distinguishable from ranking failure.
4. Make ties machine-readable and report `field_top1_tie_rate`. Do not change the
   deterministic tie-break order: B-frozen must stay frozen.
5. Warn at report level when a registered approach fails to beat the best prior.

The source-frame oracle is intentionally **not** in PR 1. It requires the source
frame and target projection to be separable stages, which only `semantic-frame`
currently is. It lands with the v2 contracts in PR 3.

### PR 2 — statistical honesty

1. Source-family cluster bootstrap confidence intervals on selected headline metrics.
2. Paired permutation tests for approach differences.
3. Report the minimum detectable effect in the report header. At the planned 30–50
   calibration cases this is roughly 15 F1 points, which bounds what that set can
   decide and should be stated rather than discovered later.
4. Replace bare `coverage` with a selective risk–coverage curve and its AUC.

Implemented in [statistics.py](../../src/asim_forge/semantic_mapping/statistics.py).
Two decisions are worth recording because they constrain later work.

**The exchangeable unit is the source family, not the case.** Templates from one
product are not independent observations, so the bootstrap resamples whole groups
and the permutation test swaps whole groups. The pre-label split group is used when
a split is supplied; source metadata is a fallback, and the report names which was
used. Case-level resampling would report differences a new source family would not
reproduce.

**Approaches are tested against one baseline, not all pairs.** All-pairs testing
across six registered approaches multiplies the false-positive rate on a sample far
too small to absorb it. The default baseline is the first registered prior.

The risk–coverage curve orders cases by the approach's own scores. Those scores are
normalized lexical overlap, so the curve measures ranking quality only and must not
be read as calibration. Brier score and reliability diagrams stay out of scope until
a provider emits probabilities, as
[the corpus plan](../research/baseline-strategy.md#metrics-and-reports) requires.

### PR 3 — version 2 contracts

1. Freeze the source-frame facet registry (`domain` / `relation` / `entity` /
   `property`) and report facet accuracy beside exact-role accuracy, so vocabulary
   growth does not read as regression.
2. Version 2 `MappingRequest` supporting genuinely absent fields, so the V0–V5
   context-mask adapter physically removes information instead of asking approaches
   not to read it.
3. Add the source-frame oracle once projection is a separable stage.

Implemented in [source_frame.py](../../src/asim_forge/source_semantics/source_frame.py)
and [context_views.py](../../src/asim_forge/semantic_mapping/context_views.py).

**Registry revision `source-frame.v1`.** A role decomposes into four facets, and
`custom` is reserved for roles the vocabulary cannot anchor at all rather than roles
that merely leave a facet unstated. `identity.actor.user` names no property and is
still a registered role; `vendor-specific-thing` is custom. Facet metrics are
reported beside exact-role F1, and `unregistered_role_rate` exposes drift between
what approaches emit and what the registry covers. A registry revision requires an
explicit fixture migration.

**Context views V0–V3 only.** V4 and V5 add external documentation and repository
evidence that no current approach can consume, so registering them would imply a
capability that does not exist. V1 preserves the same local token window
`direct-lexical` is defined to read, so the view measures that assumption rather
than starving it.

**Measured on the current fixture.** V0 scores zero and V1 scores full; V2, V3, and
the unmasked input add nothing. Every bit of signal both approaches use comes from
the local token window. Representative values, the wider template, and all source
metadata are currently worth nothing, which is a concrete statement of where the
next signal has to come from.

### PR 4 — controlled track and the N2/N3 rungs

Build the controlled and metamorphic track from the pinned catalogue before any
external acquisition. It has zero acquisition cost and zero licence risk, its
answers are known by construction, and it exercises the version 2 record format
end-to-end while defects are still cheap to fix. Then add the deterministic slot
profiler (N2) and the classical matcher ensemble (N3).

The controlled track is implemented in
[controlled/](../../src/asim_forge/controlled/); N2 and N3 follow separately so the
new methods are measured on a track that was frozen before they existed.

**Six perturbations in two families.** A label-preserving variant must not change
the expected mapping; a label-changing variant must change it. An approach scoring
identically on both is reading position rather than meaning. Results are reported
as robustness slices and never averaged into real-case results.

**Priors and retrieval are excluded, not merely unreported.** Priors read no source
evidence, so surface robustness is undefined for them. Retrieval would match a
perturbed variant to its own unperturbed seed, which is exactly the leakage
[the split contract](../dataset-curation.md#4-author-and-lock-grouped-splits) forbids. Requesting either raises
rather than producing a number that looks like evidence.

**Measured on the current fixture.** Abbreviation, compound identifiers, case
changes, and direction swaps all behave correctly, so the tokenizer and the
direction evidence are sound. Two failures are real:

| Perturbation | `direct-lexical` | `semantic-frame` | Reading |
| --- | --- | --- | --- |
| `opaque-labels` | 0.857 to 0.286 | 1.000 to 0.667 | Slot labels carry a large share of the signal |
| `decoy-slot` | 0.857 to 0.750 | no change | Direct lexical assigns a target to a slot that maps to nothing |

Taken with the V1 result above, the frozen baseline reads the local token window
and the slot label, and `direct-lexical` over-predicts on an unmapped slot of a
familiar type. Those are the specific weaknesses N2 and N3 have to beat.

**N2 and N3.** [profiling.py](../../src/asim_forge/semantic_mapping/profiling.py)
derives a deterministic value profile per slot; the `matcher-ensemble` approach
combines lexical overlap, character n-grams, catalogue type compatibility, value
enumeration, and value-shape affinity, each retained separately so the ensemble can
be ablated signal by signal. Weights are declared constants, not fitted values,
because no train partition exists.

The signal that mattered was not any of the string matchers. It was **type
affinity**: reading the profiled value shape against the target field's own naming.
Catalogue types are too coarse to separate `SrcIpAddr` from a bare `Src` when both
are strings, so with a meaningless slot label the lexical approaches fall back to
the generic field. Value shape resolves it.

| Approach | Field micro-F1 | Stability |
| --- | --- | --- |
| `direct-lexical` | 0.857 to 0.286 | 0.000 |
| `semantic-frame` | 1.000 to 0.667 | 0.000 |
| `matcher-ensemble` | 0.857 to 0.857 | 1.000 |

**`decoy-slot` remains unsolved, and is believed unsolvable at V3.** A slot labelled
`correlation id` carrying plausible integers maps to `DvcId` under every approach.
Nothing inside the event distinguishes this source's correlation identifier from
the device identifier; the distinguishing evidence is source documentation or
repository code. It is recorded as an identifiability boundary rather than an
algorithmic defect, and it is the first concrete candidate for the V5 repository
agent and the V6 owner question.

### PR 5 — bounded ASIM parser silver

The B1 release described in [the corpus plan](../research/baseline-strategy.md#baseline-release-b1-automated-asim-silver),
bounded to Authentication, NetworkSession, and AuditEvent, CEF parser families
first, split by parser family before anything is tuned.

## Publishing results under restrictive licences

The most useful remaining evidence cannot be republished. The Elastic integrations
are Elastic Licence 2.0, LogHub 2.0 restricts use to research, AIT-LDS is
CC BY-NC-SA, and several community repositories carry no root licence at all. A
prose `terms` note does not stop a release from publishing derived content, so
every corpus declares a machine-actionable class that the report writers enforce.

| Class | Permits |
| --- | --- |
| `content` | Source content, derived artefacts, and metrics |
| `derived` | Derived artefacts and metrics, but not the source content |
| `metrics` | Aggregate metrics and the acquisition manifest only |
| `none` | Nothing; the corpus is a local gate and cannot appear in a release |

An undeclared corpus defaults to `metrics`, so forgetting to classify one fails
closed. The strictest class among the included corpora governs the whole report.

**Prediction evidence is derived content.** Evidence strings quote template text,
so a `metrics` corpus has its per-case predictions withheld and the count recorded.
Aggregate metrics, intervals, and risk-coverage curves survive, because they carry
no source text.

**Reproducibility does not require redistribution.** The manifest already pins a
source URL, an immutable revision, and a SHA-256 per resource, plus a corpus
fingerprint. A third party holding their own licensed copy can verify byte
equality and reproduce the run without the project shipping the bytes.

## Release report structure

The release report renders each evidence track as its own section, headed by a
provenance table naming every corpus's track, redistribution class, and permitted
claim. Results are never combined across tracks.

Controlled robustness appears as clearly separated slices, and sample resolution is
published beside the numbers so a reader sees the minimum detectable effect before
reading a difference. Required attributions are emitted at the end of the report.

## Corpus selection


The corpus decision in [the corpus plan](../research/baseline-strategy.md) stands. This
note records only the sequencing change and the documented-silver sources that plan
did not itemise.

**Sequencing change.** Build the controlled/metamorphic track *before* ASIM parser
lineage extraction, for the reasons in PR 4.

**Additional documented-silver sources**, all inexpensive and licence-clean:

| Source | Contribution | Note |
| --- | --- | --- |
| ArcSight CEF and IBM LEEF field dictionaries | The canonical abbreviation table (`src`, `dst`, `spt`, `dpt`, `suser`, `duser`, `deviceAction`) with documented meanings | A subset is already hand-coded in `_ALIASES` in [normalization.py](../../src/asim_forge/source_semantics/normalization.py). Adopting the published dictionary grows source vocabulary without label leakage |
| Windows EventLog manifests (`wevtutil gp`, `.man` files) | Machine-readable EventData field names and types per event ID, generated locally | No redistribution question; complements OSSEM-DD's uneven coverage |
| Zeek log documentation | Documented column names, types, and descriptions per log type | Strong NetworkSession source dictionary; BSD-licensed documentation |
| RFC 5424 structured data and RFC 3164 | Normative syslog element semantics | `normative-schema` evidence class |

## Techniques from adjacent fields

[The research note](../research/semantic-mapping.md) covers semantic typing, schema
matching, and log-specific systems. These adjacent fields fill gaps that note does
not address. None is a near-term dependency; each is recorded so the relevant PR
can adopt an established protocol instead of inventing one.

| Field or work | What it contributes here |
| --- | --- |
| **Semantic role labelling** (PropBank/FrameNet; CoNLL-2005/2012 protocol) | The source frame is an SRL task. Borrow its evaluation protocol directly: report **argument identification** separately from **argument classification**, and report both *given gold structure* and end-to-end. This is a mature precedent for the decomposition the corpus plan requires, including partial-credit conventions for role granularity |
| **Schema-Guided Dialogue** (Rastogi et al., AAAI 2020) and zero-shot slot filling from slot descriptions | The closest task analogue. Its premise is generalising to **unseen services** using natural-language schema descriptions — that is, unseen source families using ASIM field descriptions. Direct evidence that the currently unused catalogue descriptions carry the transfer signal, and a better template for the unseen-family split than a generic grouped split |
| **SemTab** (ISWC challenge; CTA/CEA/CPA) | Established hierarchy-aware approximate scoring. Predicting `SrcIpAddr` when gold is `SrcDvcIpAddr` should not score identically to predicting `EventResult`. Gives partial credit a published basis |
| **V-usable information** (Xu et al.); **pointwise V-information / dataset difficulty** (Ethayarajh et al.); **MDL probing** (Voita and Titov) | A principled quantity for the V0–V6 context ladder. Delta-accuracy between views confounds "more information available" with "this method can exploit it"; V-information separates them. The pointwise variant yields a per-case difficulty score that populates the irresolvable bucket directly |
| **Selective prediction and learning with rejection** (Chow's rule; Geifman and El-Yaniv; Cortes et al.; Mozannar and Sontag on learning to defer) | Turns `coverage` into a risk–coverage curve and supplies a principled deferral policy. "Learning to defer" is the formalism for the detection-engineer-in-the-loop design |
| **Conformal prediction and conformal risk control** | Produces a candidate *set* with a distribution-free coverage guarantee: "the correct field is in this set 90% of the time." Split conformal needs only a small calibration set, so it is viable at this scale, and it maps onto the existing ranked-candidate reviewer surface. This is how abstention becomes defensible before scores have a probabilistic interpretation |
| **Schema matching without a reference alignment** (Gal, *Uncertain Schema Matching*; matching predictors; top-K matching) | Estimates matcher quality and ensemble weights **without gold**. Given that the central constraint is the absence of row-level gold, this literature is unusually on-point |
| **COMA/COMA++ matcher composition** (Do and Rahm); **Rahm and Bernstein's 2001 taxonomy** | The canonical design vocabulary for the N3 ensemble rung: aggregation strategies, thresholding, and reuse of prior match results |
| **Clinical terminology mapping** — OHDSI Usagi and OMOP CDM; LOINC RELMA | The closest *operational* analogue: domain experts mapping local codes into a fixed target vocabulary at scale, with a ranked-suggestion tool, dual review, and published inter-mapper agreement studies. Precedent for the annotation workflow, the agreement ceiling, and reviewer-effort metrics |
| **Active learning** — uncertainty sampling, core-set diversity, query-by-committee | The three registered approaches already form a committee. Mining disagreement maximises information per adjudication hour. Critical caveat: this biases the sample, so the calibration budget must split into a stratified random **test** portion and a separately reported disagreement-mined **diagnostic** portion |
| **Record linkage blocking metrics** (Christen) — pair completeness, reduction ratio | The standard names and definitions for the candidate-generation metrics added in PR 1 |
| **Ontology alignment semantic precision and recall** (Ehrig and Euzenat) | Hierarchy-aware partial credit; complements SemTab scoring |
| **Auto-Suggest and Auto-Pipeline** (Microsoft) | Companion precedent to the Auto-Type citation already recorded, for the future repository-agent context provider |

## Deferral and hand-off, recorded for later

These are noted so the version 2 prediction contract does not have to be reopened
to support them. They are not in scope before B-ceiling exists.

- **Route by expected reuse, not by uncertainty alone.** A question about a vendor
  enumeration appearing across forty templates in a product family is worth an
  owner's time; a one-off is not. Rank the deferral queue by collision-ceiling
  contribution multiplied by source-family frequency.
- **Give the deferral a guarantee.** With conformal candidate sets the question
  becomes "the answer is one of these three, which is it?" rather than "what does
  this field mean?" That is a materially cheaper question for a detection engineer,
  and the same interface is usable by a read-only repository agent without changing
  the provider contract.
- **Design conformal calibration into the version 2 prediction record now.**
  Retrofitting it later means re-running every evaluation.

Raw logs remain untrusted data throughout. Any future agent keeps read-only scoped
access, no deployment authority, a strict output contract, full provenance, and a
deterministic validation and review boundary.
