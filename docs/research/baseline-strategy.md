# Non-LLM semantic-mapping baseline and corpus plan

Status: dated research and acquisition record; not authoritative for current implementation status
Research reviewed: 2026-09-01
Archived: 2026-09-10

This document preserves the evidence-acquisition strategy and proposed approach
ladder at the review date. Several early rungs have since been implemented. See the
[current evaluation guide](../evaluation.md), [current architecture](../architecture.md),
and [roadmap](../../ROADMAP.md) for present behavior and remaining work.

## Decision

There is no public corpus that can be treated as independent, row-level gold for
mapping heterogeneous raw security events into ASIM or OCSF. The useful public
assets either label a different task, encode an existing implementation, or are
explicitly described by their maintainers as examples rather than gold.

The baseline should therefore be a **versioned portfolio of evidence tracks**, not
one blended dataset:

1. **Controlled synthetic and metamorphic cases** test mechanics and robustness
   where the answer is known by construction.
2. **Executable upstream silver** extracted from pinned ASIM and OCSF mappings
   tests whether a method can reproduce established normalization work.
3. **Cross-schema source-semantic silver** from ECS and OSSEM grows coverage of
   field names, values, event shapes, and source roles without pretending those
   labels are ASIM truth.
4. **Unlabelled security logs** continue to test parsing, coverage, scale, and
   outlier retention only.
5. **Independently adjudicated semantic gold** remains necessary for claims about
   ASIM correctness and safe automation, but is not sufficient without deployment
   and downstream evidence.

The first four tracks can be built and frozen before human-in-the-loop mapping.
The controlled and silver tracks support component comparisons, robustness tests,
and agreement with existing implementations; unlabelled logs add operational
diagnostics only. Together they expose leakage and measure which context changes
an answer, but they are not sufficient to select an unattended production mapper.
This preserves the evidence boundary already established in [the benchmark
design](../evaluation.md#benchmark-tracks-and-corpora), [the fixture
contract](../dataset-curation.md#semantic-mapping-case-contract), and the
[semantic-mapping research note](semantic-mapping.md).

## What the current baseline really is

The repository has good comparison, provenance, split, and metric contracts, but
only one synthetic semantic case. Its three mapping approaches are intentionally
small:

| Approach | What it currently uses | Important boundary |
| --- | --- | --- |
| `direct-lexical` v3 | The leading lexical schema and the slot label plus up to three preceding and two following tokens; target field name and logical type | Maps slots independently; emits no source frame or constants. |
| `semantic-frame` v3 | The same lexical schema plus small patterns for addresses, ports, users, and four result phrases | Its role vocabulary is a few hand-written cases, not a general semantic typer. |
| `case-retrieval` v2 | Jaccard overlap over templates, slot labels, and system/vendor/product metadata; three labelled neighbours | Representative values/events, source table, and message field are absent from its retrieval signature; it learns only from `mapped` cases. |

Consequently, the checked semantic scores are harness tests, not a quality
baseline. In particular:

- no approach predicts `not_applicable`;
- field scores are relative lexical scores, not calibrated probabilities;
- representative values, value distributions, much of the source metadata, ASIM
  field constraints and allowed values are unused, while aliases are explicitly
  excluded from ranking;
- independent slot choices can select the same target even though the compiler
  rejects duplicate targets;
- only Authentication, NetworkSession, and AuditEvent have substantial schema
  concept vocabularies; and
- source-role F1 and full exact match compare open free-text role strings exactly,
  so synonyms or different levels of granularity can score as errors.

These limitations make the proposed corpus useful immediately: the first goal is
to measure each missing signal rather than add rules without an ablation.

## The task must remain decomposed

"Semantic mapping" hides several different problems. A corpus record should say
which of these it can actually score:

1. **Structure extraction**: raw event to fields, keys, slots, and constants.
2. **Physical or shape typing**: IP address, port-shaped integer, timestamp, SID,
   hash, URI, path, identifier, free text, and so on.
3. **Source-semantic typing**: actor, target, source, destination, observer,
   action, result, object, or time in the source event.
4. **Target class selection**: ASIM schema or OCSF event class/activity.
5. **Target projection**: source concept to ASIM field or OCSF attribute path.
6. **Transformation**: parsing, casting, enum normalization, conditionals,
   constants, derivation, one-to-many mapping, and deliberate non-mapping.
7. **Disposition**: mapped, ambiguous/unresolved, or not applicable.

ASIM parser source directly encodes implementation-derived silver evidence for
stages 4-6 and can supply indirect silver evidence for stage 3, but it does not
independently label source meaning and may not provide executable local
raw-to-output pairs. LogHub labels stage 1 only. OSSEM
dictionaries mainly support stages 2-3. Treating any of them as end-to-end truth
would erase the very error boundary the project is trying to understand.

## Evidence classes

Track and label provenance are separate properties. A source can be high-quality
for one task and unusable for another.

| Evidence class | Meaning | Permitted claim |
| --- | --- | --- |
| `normative-schema` | A pinned ASIM/OCSF definition supplies valid fields, paths, types, enumerations, and requirements. | Candidate validity and conformance, never source correspondence. |
| `controlled-gold` | The case and its perturbations were generated from an explicit known mapping. | Correctness on that controlled transformation family only. |
| `executable-silver` | A pinned parser, pipeline, or mapping program supplies the expected correspondence/output. | Agreement with that implementation. |
| `documented-silver` | A source dictionary or official example supplies field meaning or a proposed mapping. | Agreement with that documented interpretation. |
| `schema-hint` | File placement, parser family, or source metadata suggests only a target class. | Class-hint agreement only. |
| `parsing-gold` | A dataset supplies templates or event groups. | Structure/template metrics only. |
| `format-diagnostic` | Events have no adjudicated mapping answer key. | Coverage, scale, outliers, and operational behaviour only. |
| `adjudicated-gold` | Independent reviewers resolved source meaning and target projection under a pinned catalogue. | Correctness against adjudicated labels for the sampled cases. |

Do not promote evidence by changing a manifest label. In particular,
`executable-silver` remains silver even when its parser passes all schema and data
tests: validation proves consistency with a target contract, not that the source
was interpreted correctly.

## Security-specific corpus candidates

Track names introduced below are proposed names, not claims that the current
benchmark runner already implements them. The v1 manifest accepts only
`parsing-gold`, `format-diagnostic`, `schema-hint`, and `semantic-gold`; its
semantic case/prediction contracts are ASIM-specific and cannot represent OCSF
paths, transforms, mapping kinds, or the full outcome taxonomy below. Introduce a
versioned benchmark/manifest/record/prediction v2 with explicit handlers and
report boundaries before registering any silver corpus. Do not encode silver as
`semantic-gold` merely to reuse the v1 runner.

### Priority 1: direct ASIM and OCSF evidence

| Source | What can be harvested | Best track | Caveat and handling |
| --- | --- | --- | --- |
| [Azure-Sentinel ASIM parsers](https://github.com/Azure/Azure-Sentinel/tree/master/Parsers), [schemas](https://github.com/Azure/Azure-Sentinel/tree/master/ASIM/schemas), and [sample data](https://github.com/Azure/Azure-Sentinel/tree/master/Sample%20Data/ASIM) | Source fields and parse expressions to ASIM fields; constants, casts, branches, enum maps, and target schemas; raw and post-ingestion examples for some products | `asim-upstream-silver` plus the existing `schema-hint` track | `*_IngestedLogs` is post-ingestion source-table data, not normalized ASIM output, and many folders lack a complete raw/ingested/schema triplet. Manifest actual artifact availability. Pin one commit, extract KQL lineage, execute where possible, and never expose the held-out parser to the matcher. Azure-Sentinel is MIT licensed. |
| [AWS Security Lake OCSF validation samples](https://github.com/aws-samples/amazon-security-lake-ocsf-validation/tree/main/samples/1.0.0-rc.2) | Same-named original source records and transformed OCSF records for CloudTrail, Route 53, VPC Flow, and Security Hub families | `ocsf-paired-silver` and value-level regression | The target is the legacy pre-release `1.0.0-rc.2`. Preserve that version rather than silently translating it. Verify same-name and row-level pairing; schema validity is not semantic correctness. MIT-0. |
| [AWS Security Lake transformation library](https://github.com/aws-samples/amazon-security-lake-transformation-library) | Executable JSONPath-to-OCSF mappings for Windows Sysmon, AWS Network Firewall, and ALB; static, derived, and enum mappings plus `unmapped` | `ocsf-executable-silver` | Mapping metadata currently declares mixed target versions (1.0.0 and 1.1.0). Pin and report version per mapping and fixture rather than treating the repository as one OCSF-version corpus. MIT-0. |
| [Bedrock Guardrails to OCSF](https://github.com/aws-samples/sample-bedrock-guardrails-security-lake) | Mapping table, transformer, tests, and samples for OCSF 1.3 Detection Finding | `ocsf-executable-silver` | A small single-product synthetic/sample fixture set, useful specifically for derived fields and modern cloud/AI-security telemetry, but not independent gold. MIT-0. |
| [OCSF community examples](https://github.com/ocsf/examples) | Contributed `*.raw` and corresponding `*.ocsf` examples for Windows, Zeek, AWS, Okta, Falco, flow data, and other sources | `ocsf-documented-silver` | The project explicitly says it is not a gold standard. The examples repository currently has no root licence; index remotely but do not redistribute until terms are clarified. Pin the separately Apache-2.0 [OCSF schema](https://github.com/ocsf/ocsf-schema). |

The validation repository also contains a narrow `samples/1.1.0/EKS` fixture.
Inventory it separately, but do not classify it as paired input/output evidence
until original-record provenance and row linkage are confirmed.

The ASIM source is the best first fit for the code already implemented. Its KQL
should be converted into explicit lineage records such as:

```text
source path -> target field
mapping kind: direct | parsed | conditional | enum | derived | constant | dropped
branch predicate and transform
source/target physical types
expected value when executable
parser URI, commit, and line/AST node
```

Only a strict, explainable KQL subset should be accepted initially. Opaque or
dynamic expressions should be retained as acquisition failures, not guessed. An
upstream parser defect is a silver-label defect; public parser fixes demonstrate
why this distinction matters.

### Priority 2: source-semantic breadth

| Source | What it contributes | Use | Caveat |
| --- | --- | --- | --- |
| [Elastic integrations](https://github.com/elastic/integrations) and its [pipeline-test format](https://www.elastic.co/docs/extend/integrations/pipeline-testing) | Large numbers of raw `.log`/JSON inputs paired with expected ECS documents, including parsing and type conversion | Learn and test source field profiles; derive high-confidence source roles through a separately versioned ECS-to-source-frame crosswalk | Expected files are generated by the pipeline under test and require manual verification. Integrations default to Elastic License 2.0 while ECS is Apache-2.0; package, fixture, and third-party terms may differ, so record them per artifact and review redistribution. |
| [OSSEM Data Dictionaries](https://github.com/OTRF/OSSEM-DD) and [Detection Model](https://github.com/OTRF/OSSEM-DM) | Event-specific names, types, descriptions, samples, occasional standard names, and actor-action-target relationships across Windows, Linux, macOS, AWS, Azure, Cowrie, and Zeek | Source-frame dictionary silver, abbreviations, hard negatives, event-class context | Documentation is incomplete and uneven; it is not raw-event-to-ASIM/OCSF gold. MIT licensed. |
| [OpenTelemetry semantic conventions](https://github.com/open-telemetry/semantic-conventions) | Modelled names, types, and semantics for network, HTTP, TLS, host, process, cloud, database, and resources | Auxiliary vocabulary and relation tests | Observability semantics are not a target mapping answer key. Apache-2.0. |
| [Sigma log sources](https://sigmahq.io/docs/basics/log-sources.html) and [pySigma community pipelines](https://github.com/SigmaHQ/pySigma-community-pipelines) | Consumer-side source classes, field expectations, and platform translations | Later competency-query and detection-utility tests | Detection fields are not proof of raw-source meaning. Do not use the same pipeline both to generate a mapping and to score it. The pipeline repository currently has no root licence; keep its YAML reference/index-only pending permission. The Detection Rule Licence applies to covered Sigma rule content, not automatically to this separate repository. |

For ECS and paired OCSF output, derive an automatic bounded silver label only when
one-to-one lineage is proven by executable mapping or a unique controlled pairing.
Exact or normalized value equality alone is candidate evidence: repeated IPs,
users, times, and identifiers can create false correspondences. Colliding values,
absent values, derived values, and lossy transforms must be marked ambiguous unless
executable mapping code resolves them.

### Priority 3: robustness and downstream utility, not mapping labels

[Matryoshka's SecurityLogs](https://github.com/julien-piet/matryoshka),
[LogHub 2.0](https://github.com/logpai/loghub-2.0),
[Splunk Attack Data](https://github.com/splunk/attack_data),
[SOCBED](https://github.com/fkie-cad/socbed-eval-acsac-2021),
[AIT-LDS v2](https://zenodo.org/records/5789064), and the
[LANL Unified Host and Network dataset](https://csr.lanl.gov/data/2017/) add useful
formats, source diversity, rare events, and detection scenarios. None supplies
field-semantic ASIM/OCSF gold. Attack, anomaly, template, and scenario labels must
remain attached to their original objectives.

Large datasets belong in an extended robustness run rather than the default change
gate. Record each artifact/file/subtree's licence, provenance, and redistribution
terms independently. In particular, Matryoshka's GPL-3.0 code licence does not
clearly license its external SecurityLogs download; LogHub 2.0 restricts datasets
to research/academic use; and SOCBED's evaluation, testbed, bundled rules, and
executables use different terms. Splunk Attack Data is Apache-2.0 but large and
Git-LFS based; AIT-LDS v2 is CC BY-NC-SA 4.0; and the LANL page states a
copyright/related-rights waiver to the extent possible. None belongs in an
ordinary product CI corpus without artifact-level review.

## Adjacent benchmarks and what to borrow

The adjacent fields are valuable because they isolate capabilities that the
security corpora entangle. Their scores must not be presented as expected ASIM
accuracy.

| Benchmark | Capability | Transfer to this project |
| --- | --- | --- |
| [WDC Schema Matching Benchmark](https://webdatacommons.org/structureddata/smb/) | Header/instance correspondence, hard negatives, table-disjoint splits | Qualify lexical, value-profile, and candidate-generation components. |
| [Valentine](https://arxiv.org/abs/2010.07386) and [implementation suite](https://github.com/delftdata/valentine) | Schema-, instance-, distribution-, embedding-, and combined matchers under controlled schema changes | Reuse matcher ensembles and perturbations: abbreviations, prefixes, missing headers, and value noise. Its broad evaluation also cautions against assuming one matcher dominates. |
| [SERENE/DINT benchmark](https://arxiv.org/abs/1801.09788) and [artifact](https://github.com/NICTA/serene-benchmark) | Assignment of source columns to ontology class-property labels, an `unknown` class, and leave-one-source-out tests | The closest general analogue to target-field ranking plus abstention. |
| [SOTAB v2](https://webdatacommons.org/structureddata/sotab/v2/) | Column type annotation and contextual column-pair property annotation | Test the difference between value type and relational role; borrow same-values/different-meaning cases. |
| [Sherlock](https://vis.csail.mit.edu/pubs/sherlock/) and [Sato](https://www.vldb.org/pvldb/vol13/p1835-zhang.pdf) | Statistical/character value profiles, then table-context features | Evidence for value profiling and explicit context ablations. Their labels and domains are not security gold. |
| [OAEI](https://oaei.ontologymatching.org/) | Lexical, structural, and logically coherent ontology alignment | Qualify hierarchy, synonym, enum, and future ASIM-to-OCSF alignment logic. |
| [RODI](https://www.cs.ox.ac.uk/isg/tools/RODI/) | Database-to-ontology mapping assessed by preserved query answers | Precedent for later detection/query competency tests rather than field scores alone. |

The first external component lab should be WDC SMB, SERENE/DINT, and the contextual
SOTAB tasks, with Valentine used as an implementation and perturbation reference.
Do not make these large external corpora part of the default product release gate.

## Proposed non-LLM approach ladder

Every rung should implement a common logical evaluation interface for the tasks it
supports, preserve its evidence, and be ablated against the rung below it. That
interface is a v2 requirement: the existing ASIM-flat prediction contract can
adapt N0-N1 for direct mappings, but it cannot express OCSF paths, transforms,
mapping kinds, one-to-many mappings, or explicit out-of-schema outcomes. This
extends the repository's [ASIM-first, target-neutral architecture](../architecture.md#source-and-target-seams)
rather than pretending the current provider boundary is already target-neutral.

| ID | Approach | Purpose |
| --- | --- | --- |
| N0 | Null, majority-schema, and target-frequency priors | Detect whether a proposed method beats trivial class imbalance. |
| N1 | Current direct lexical, semantic-frame, and case-retrieval versions | Freeze the actual pre-corpus baseline. Do not tune them on the harvested test families. |
| N2 | Deterministic physical-type and column/slot profiler | Add parse success, lengths, character classes, null/uniqueness/cardinality, entropy, numeric range, categorical vocabulary, and stability across representatives. [ptype](https://arxiv.org/abs/1911.10081) is a useful dirty-data model. |
| N3 | Classical schema-matcher ensemble | Combine token/character n-grams, abbreviation dictionaries, edit/Jaro similarity, BM25 or TF-IDF, type compatibility, instance containment, and distribution distance. Keep every signal separately inspectable. |
| N4 | Contextual source-frame and global constraints | Add peer fields, slot order, phrases such as `from ... to ...`, event ID/class, producer metadata, target requirements, and optional bipartite/min-cost assignment. Permit no-map, one-to-many, and derived mappings rather than enforcing universal one-to-one correspondence. |
| N5 | Family-isolated case-based retrieval | Compare Jaccard, BM25, TF-IDF k-nearest neighbours, source-role transfer, and diversity-aware retrieval without same-family leakage. |
| N6 | Weak supervision plus a classical classifier | Combine parser lineage, OSSEM, ECS, type rules, and dictionaries as noisy labelling functions; train logistic regression, linear SVM, gradient boosting, or a CRF/factor graph. [Snorkel](https://www.vldb.org/pvldb/vol11/p269-ratner.pdf) is precedent for modelling correlated weak sources without hand-labelled training data. Never score on a family whose mapping supplied its training labels. |
| N7 | Restricted transform synthesis | Infer casts, substring/regex extraction, concatenation, timestamp conversion, and enum tables from paired values in a small safe DSL. [FlashFill](https://www.microsoft.com/en-us/research/publication/automating-string-processing-spreadsheets-using-input-output-examples/) and [Foofah](https://web.eecs.umich.edu/~michjc/papers/jin_foofah_sigmod17.pdf) provide the relevant programming-by-example pattern. |

Neural column classifiers and embeddings can be added later as explicitly named
non-LLM approaches, but they are not needed to establish this baseline. First
measure how far transparent lexical, profile, structural, retrieval, and constraint
signals travel.

## A controlled source-semantic vocabulary

The current fixture format accepts any nonblank role string but scores it by exact
match. That will make slow vocabulary growth look like approach regression. Before
building a larger silver corpus, freeze a small versioned source-frame registry.

A role should be decomposable into facets rather than copied from ASIM or OCSF:

```text
domain:   network | identity | process | file | application | resource | event | other
relation: actor | target | source | destination | observer | event | other | unknown
entity:   user | endpoint | process | file | application | resource | rule | other
property: id | name | address | port | time | action | result | protocol | other
```

The existing `network.source.address` style can remain a compact alias: here
`network` is the domain, `source` the relation, and `address` the property, with
the entity inferred as endpoint. The canonical structured record should retain
all applicable facets rather than require every role to fit a three-token string.
The registry should add definitions, aliases, parent relations, examples, and a
`custom` escape hatch. Report both exact-role accuracy and separate facet accuracy;
only exact target fields should remain strictly exact. A vocabulary revision needs
an explicit fixture migration rather than silently reinterpreting old labels.

OSSEM relationships and the common role patterns in ASIM/OCSF can seed the
registry, but neither target should define the source semantics. This is what makes
later ASIM-to-OCSF remapping possible without relearning the source event.

## Corpus record additions

Keep predictions and confidence outside expected records, as the current fixture
contract already does. Silver and future format-v2 records additionally need:

- source vendor, product, version, event subtype/ID, encoding, and collection
  vantage point;
- original record, decoded structure, template, source key/path, sibling fields,
  representative values, and value profile;
- source-family and template-family group IDs assigned before labels are visible;
- physical type and source-semantic facets;
- target namespace, version, class/schema, field/path, target type, requirement
  level, and acceptable alternative set where the target permits more than one
  defensible representation;
- mapping cardinality and kind: direct, parsed, conditional, enum, derived,
  constant, one-to-many, many-to-one, or deliberately unmapped;
- predicate, transform expression/DSL, expected normalized values, and information
  loss;
- evidence class, source URI, immutable commit/version, source lines or AST nodes,
  content digest, acquisition tool/version, and terms;
- `context_requirement`: `value`, `local`, `event`, `producer`, `documentation`,
  `runtime_configuration`, or `not_identifiable`; and
- the v1 top-level dispositions `mapped`, `unresolved`, and `not_applicable`, plus
  structured reason codes such as `ambiguous` and `out_of_target_schema` so those
  concepts are not overloaded as new, undefined dispositions.

The existing `imported` label source is too broad to distinguish parser-derived,
documented, and paired-output evidence. Preserve those distinctions in the corpus
track/manifest even if a compatibility reader temporarily serializes them as
`imported`.

## The context ladder experiment

Run each applicable method under deliberately masked input views. This measures
the value of context instead of merely observing that a more complex method won.

| View | Information available | Question answered |
| --- | --- | --- |
| V0 | Representative values only | What can physical shape and distribution determine? |
| V1 | V0 plus source key/slot label and immediate token window | How far can the current local lexical assumption go? |
| V2 | V1 plus full event/template, constants, sibling fields, slot order, and multiple representatives | Which role distinctions are resolved inside the event? |
| V3 | V2 plus source system/vendor/product, event ID, format, and collection vantage | What does onboarding metadata buy? |
| V4 | V3 plus pinned public source dictionaries and vendor documentation | Which cases are specification-dependent rather than statistically learnable? |
| V5 | V4 plus read-only source repository, serializers, enums, parser configuration, tests, and detection consumers | What could a repository-aware automated agent settle? |
| V6 | V5 plus a narrowly scoped owner/reviewer answer | What genuinely requires external judgement? |

A context-mask adapter should physically remove unavailable fields before calling
an approach. Because the v1 input contract requires nonempty template,
representative-event, and system metadata fields, V0-V1 need a view-specific v2
request contract or deterministic placeholders proven not to leak information.
Merely asking an implementation not to read hidden fields is not an ablation.

## Finding the edge of what is identifiable

Several mappings are impossible from values alone. An IP address does not reveal
whether it is source, destination, reporting device, or NAT address. A username
does not reveal actor versus target. An integer may be a port, PID, status, event
ID, byte count, or vendor enum. Similar ambiguity applies to event time versus
ingestion time, process versus parent process, action versus result, and local
versus remote endpoint.

Measure this boundary rather than calling every case an algorithm failure:

1. **Exact-signature collision audit.** For each context view, group cases with
   the same observable signature but different expected labels. For deterministic
   systems restricted to that view, the empirical collision ceiling is
   `sum_x max_y count(x,y) / N`. Report the conflicting groups, not only the number.
2. **Counterfactual contrast sets.** Swap source/destination, actor/target,
   parent/child, start/end, or success/failure while holding physical values
   constant. Predictions should change only when the distinguishing evidence is
   visible.
3. **Progressive context deltas.** Measure the paired improvement V0 -> V1 -> ...
   -> V5 for every role and source family. The first view that resolves a case is
   its observed minimum context, not a universal truth.
4. **Irresolvable bucket.** Vendor enum semantics, sensor vantage, deployment
   configuration, overloaded fields, missing source documentation, and target
   modelling policy may remain non-identifiable. A correct abstention is the
   desired output.

The collision ceiling is a diagnostic for this sample, not a proof of the true
Bayes limit. Contrast cases are needed because unique values can hide semantic
collisions in an ordinary dataset.

## Perturbation and stress corpus

Generate label-preserving and label-changing variants in their own controlled
track. Borrow the perturbation families from Valentine, WDC SMB, SOTAB, and data
integration benchmarks:

- case, separators, compound identifiers, prefixes/suffixes, abbreviations,
  vowel deletion, and opaque key names;
- reordered fields/slots, missing siblings, duplicated values, decoy fields, and
  same-shape values with different roles;
- nulls, malformed values, IPv4/IPv6, time zones, locale/date ambiguity, units,
  categorical drift, unseen enum values, and varying sample counts;
- template-frequency skew, rare event variants, and closely related products;
- source/destination and actor/target swaps that must change the answer;
- target catalogue version changes, renamed/deprecated fields, and new optional
  fields, represented as distinct revision-specific cases; and
- out-of-schema and deliberately irrelevant application messages.

Never mix thousands of generated variants into the real-case micro average. Report
them as robustness slices so synthetic volume cannot dominate the headline.

## Split and leakage rules

Use the existing [grouped split contract](../dataset-curation.md#4-author-and-lock-grouped-splits), extended to
silver tracks where necessary:

- group by vendor/product/parser or integration package and template family;
- prefer time/commit holdouts when version history exists;
- put sanitized copies and near-duplicate templates in the same partition;
- for small numbers of source families, report leave-one-family-out folds rather
  than a random row split;
- exclude the held-out parser, mapping file, expected normalized record, and schema
  hint encoded in filenames/paths from approach input;
- let retrieval see only declared reference partitions;
- if OSSEM, ECS, OTel, or a crosswalk generated a label, do not use the same source
  as independent test evidence; and
- score ASIM and each OCSF version separately. Raw numbers across targets are not
  directly comparable because their granularity and constraints differ.

Publish both an **open-book** view, where pinned target descriptions and source
documentation are allowed, and a **closed-book** view containing only names, types,
values, event context, and source metadata. This makes documentation value visible
without confusing it with algorithmic inference.

## Metrics and reports

Retain the current metrics in [the metric contract](../evaluation.md#metric-reference) and add
the missing diagnostics by task and evidence track:

- candidate recall@k, MRR/MAP, and candidate reduction ratio before final ranking;
  v2 must retain candidates even when the final decision abstains;
- schema/class top-k, exact and hierarchy-aware class accuracy reported separately;
- exact source-role and target-field micro precision, recall, and F1; preserve the
  current case-macro F1, and add separately named per-role/per-field-class macro
  results and confusion matrices;
- transform-kind accuracy and value-level normalized-output equality;
- mandatory/recommended target coverage, type/enum conformance, information
  retention, and correct deliberate-unmapped rate;
- `not_applicable`, `unresolved`, out-of-schema, and false-mapping rates;
- selective risk: precision/error versus coverage as the method abstains;
- calibration only after scores have a probabilistic interpretation, using Brier
  score/reliability diagrams rather than treating normalized lexical overlap as
  confidence;
- template/type-macro as well as event-weighted results so common events cannot
  hide rare-family failures;
- slices by context view, header quality, physical type, role, source family,
  target schema, mapping kind, sample count, and ambiguity class;
- paired differences with source-family bootstrap confidence intervals; and
- runtime, memory, corpus/sample-size curves, and the existing approximate edit
  count.

Silver agreement and controlled-gold robustness should never be averaged with
adjudicated correctness. A release summary may show them next to one another with
their evidence class plainly named.

Two diagnostic oracles are especially valuable even though they are not deployable:

- give the field mapper the expected schema to separate schema-selection error
  from field-ranking error; and
- give the target projector the expected source frame to separate source
  understanding from target-projection error.

These expose where the next improvement should be made.

## Recommended acquisition sequence

### Baseline release B0: freeze what exists

1. Re-run `direct-lexical` v3, `semantic-frame` v3, and `case-retrieval` v2 on the
   checked synthetic case. Re-run the existing parser and schema-ranking phases on
   their current diagnostic corpora.
2. Record these objective-separated results as a mechanics snapshot with the
   existing warnings. Do not quote them as semantic quality.

### Baseline infrastructure B0.5: make silver representable

1. Freeze source-frame registry v1, including domain/event-family information,
   definitions, aliases, and unknown/custom handling.
2. Define version-2 manifest, case, prediction, and report contracts that preserve
   evidence class independently of evaluation task and can represent ASIM or OCSF
   targets, structured source paths, transforms, mapping kinds, cardinality,
   provenance, context requirements, and explicit no-map outcomes.
3. Add track-specific handlers and metrics without weakening the v1 gold boundary;
   provide a one-way adapter for applicable v1 ASIM cases and predictions.
4. Define the restricted structured-lineage representation and validate it on a
   few hand-inspected ASIM parser fragments before corpus-scale extraction. This
   also resolves the structured-input work identified in the
   [deferred input-adapter work](../../ROADMAP.md#deliberately-deferred).

### Baseline release B1: automated ASIM silver

1. Inventory parser metadata broadly and write a manifest before downloading
   content, but bound extraction and evaluation to a multi-family pilot.
2. Start with the implemented Authentication, NetworkSession, and AuditEvent
   boundary and select independent vendor/product families within each.
3. Extract only high-confidence KQL lineage and mapping kinds. Link raw to ingested
   records by stable identifiers or value fingerprints, never assumed row order.
4. Execute parsers and ASIM schema/data tests where an authorized Sentinel test
   environment is available; otherwise report static-lineage coverage separately.
5. Split by parser/source family before tuning any approach.

### Baseline release B2: target-neutral and OCSF silver

1. Import OSSEM dictionary records into the source-frame track.
2. Curate security-focused Elastic pipeline fixtures by package/data stream and
   retain their ECS output only as silver.
3. Add the AWS OCSF paired legacy track, version-pinned transformation tracks, and
   small 1.3 Bedrock track, each pinned and scored separately.
4. Index only URLs and metadata for OCSF community examples; until licence terms
   are clarified, do not copy, tokenize, cache, or derive a stored corpus from
   their contents.

### Baseline release B3: method and context comparison

1. Add N0 priors, deterministic profiles, the matcher ensemble, optional global
   assignment, and family-isolated retrieval.
2. Run V0-V5 masks and every signal ablation on identical partitions.
3. Add controlled contrast/perturbation suites and selective-risk reporting.
4. Freeze the results and error taxonomy before the first semantic annotation
   calibration set is used for tuning.

The existing 30-50 case plan in
[the semantic pilot](../dataset-curation.md#pilot-composition) remains the next
independent calibration step. The number of final held-out cases should be driven
by source-family coverage and bootstrap interval width, not by event count alone.

## What this baseline can and cannot establish

After B3, the project can credibly answer:

- which non-LLM signals reproduce established parsers and paired mappings;
- whether value profiles, event context, constraints, global assignment, or
  retrieval add measurable value;
- how performance changes on unseen source families and noisy/opaque fields;
- which source roles are routinely identifiable at V0-V3;
- which mappings first become identifiable with documentation or repository code;
  and
- the maximum silver precision/coverage achieved under abstention.

It still cannot establish:

- independent ASIM/OCSF correctness;
- a safe auto-approval threshold in customer environments;
- the prevalence of ambiguity or out-of-schema concepts in real deployments;
- reviewer effort saved; or
- preservation of real detection behaviour.

Those claims require locked adjudicated gold and, later, executable downstream
competency queries.

## Later human and repository-agent assistance

The context-ladder results should drive narrowly scoped questions rather than ask
a person to map an event from scratch.

Questions for an application/source owner usually concern source facts:

- What does this event ID or enum value mean?
- Is this endpoint local, remote, original, translated, or the reporting sensor?
- Is this user the initiator, authenticated subject, or affected account?
- Is this timestamp creation, observation, receipt, or ingestion time?
- Is a field conditional on configuration or event version?

Questions for a detection engineer usually concern target policy and utility:

- Which ASIM schema or OCSF class preserves the event's detection meaning?
- Is the entity actor, target, source, destination, or observer for this analytic?
- Is information loss acceptable, should the field remain unmapped, or is an
  extension/derived field required?
- Which of two semantically valid target representations matches local content?

Present one ambiguity at a time with representative redacted events, two or three
ranked choices, the conflicting evidence, and an explicit `unknown/defer` option.
Choose questions by expected reuse across a source family, not by raw event volume.

A later read-only repository agent can gather V5 evidence from source models,
serializers, enum declarations, parser configuration, tests, documentation, and
detection queries; cite exact files; propose a mapping/transform; and run
deterministic validation. [Auto-Type](https://www.microsoft.com/en-us/research/publication/auto-type-synthesizing-type-detection-logic-for-rich-semantic-data-types-using-open-source-code/)
is useful precedent for mining repository code for semantic validators. Microsoft's
current [ASIM parser-agent workflow](https://learn.microsoft.com/en-us/azure/sentinel/normalization-create-parsers-ai-agent)
also requests source documentation, table/schema, samples, and target schema before
iterating through schema/data validation. These are future context providers, not
substitutes for the non-LLM baseline or independent gold.

Raw logs must remain untrusted data. Any future agent should have read-only scoped
access, no deployment authority, a strict output contract, full provenance, and a
deterministic validation/review boundary.

## Immediate recommendation

Implement B0.5 and then a bounded B1 before adding another semantic approach. That
is the shortest path from the current one-case smoke test to a meaningful,
source-family-held-out comparison. It requires new lineage adapters and silver
track support before adapted versions of the existing approaches can be evaluated.
In parallel, inventory the AWS OCSF pairs and OSSEM dictionaries so the record
format does not accidentally become ASIM-only.

The first research question is not "which mapper is best?" It is:

> Under each explicitly available context view, how much established mapping can a
> transparent non-LLM method recover, when does it abstain, and which remaining
> disagreements are algorithmic versus missing-information failures?

That answer creates the honest baseline from which human review, repository-aware
agents, local models, or LLM providers can later demonstrate an actual improvement.

The sequenced implementation of that answer, including the floor, oracle, and
identifiability reference points it depends on, is in
[the field-mapping baseline record](../archive/field-mapping-baseline.md).
