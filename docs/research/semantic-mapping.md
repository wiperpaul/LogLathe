# Semantic typing and schema matching research note

Status: dated research record; not authoritative for current implementation status
Research reviewed: 2026-08-29
Archived: 2026-09-10

This document preserves the research and decision context that shaped the current
architecture. Statements about the "next" experiment reflect the project state at
the review date. See [the current architecture](../architecture.md),
[evaluation guide](../evaluation.md), and [roadmap](../../ROADMAP.md) for present
behavior and remaining work.

## Question

Should LogLathe maintain both a semantic rule engine and a model-based matcher,
or can one approach provide useful ASIM suggestions without creating two feedback
and release cycles?

## Finding

The strongest results do **not** support a large rules-only semantic mapper. They
also do not support sending the entire problem to one unconstrained model.

Current high-performing systems combine signals or stages, but their "hybrid"
parts are usually learned components plus small deterministic constraints:

- semantic typing combines a value representation with surrounding table
  structure;
- schema matching retrieves a small candidate set before semantic reranking and
  often applies an assignment or validation step;
- hand-written rules remain useful for closed-form types, output constraints,
  and validation, but are not normally the main semantic classifier.

This distinction means LogLathe does not need two full semantic engines. It can
have one versioned suggestion-provider boundary surrounded by catalogue-derived
candidate filtering and validation that are required regardless of the provider.

A broader reading of security-log research suggests one refinement: the first
semantic output should describe the source event in source terms, not immediately
force every slot into ASIM. ASIM projection can then be a second logical step in
the same provider call and the same reviewer screen. This is a data boundary, not
a proposal for another human gate or AI framework.

## Evidence from semantic typing

| Work | Main design | Relevant result | Implication |
| --- | --- | --- | --- |
| [Sherlock (KDD 2019)](https://arxiv.org/abs/1905.10688) | Statistical, character, embedding, and paragraph-vector features feeding a neural classifier | Weighted F1 0.89 over 78 types; exceeded dictionary and regex baselines | Rules recognize strict formats but do not cover broad, dirty semantic types well. |
| [Sato (VLDB 2020)](https://arxiv.org/abs/1911.06311) | Sherlock-style column model plus table topics and structured prediction | Context improved value-only semantic typing | A slot should be interpreted with its template and peer slots, not independently. |
| [Doduo (SIGMOD 2022)](https://arxiv.org/abs/2104.01785) | A pretrained language model jointly annotates all columns | Stronger contextual results without a hand-written semantic rule engine | Learned joint context can replace semantic rules when enough representative training data exists. |
| [ArcheType (VLDB 2024)](https://www.vldb.org/pvldb/vol17/p2279-freire.pdf) | Zero-shot/fine-tuned LLM with context sampling and label remapping | On SOTAB-91, the reported fine-tuned result rose from 82.9 to 85.97 micro-F1 with rule-based label remapping; a transferred Doduo model also fell from 84.8 to 23.8 under dataset shift | Even a model-first system benefits from constrained output handling, while domain shift makes a fixed trained classifier risky. |
| [TabEmb (ACL 2026)](https://aclanthology.org/2026.acl-long.757/) | Frozen LLM column embeddings plus a trainable graph model for inter-column structure | Across its tasks and datasets, the no-graph variant averaged 88.3 micro-F1 and the graph-attention variant 92.1; it reports the strongest average result among its eight baselines | The current leading direction is hybrid semantic/structural learning, not semantic rules plus a model. Context is a measurable part of the gain. |

The benchmarks are not ASIM benchmarks. Their absolute scores must not be used
as an expected LogLathe accuracy. They nevertheless show a stable pattern:
value shape alone is insufficient, context helps, and closed-set learned models
can fail sharply when the source distribution changes.

## Evidence from schema matching

The older [Valentine evaluation](https://arxiv.org/abs/2010.07386) ran roughly
75,000 experiments across schema-, instance-, embedding-, and combined matchers.
It found no consistently best method and recommended composing matchers. It also
found that the particular hybrid methods it tested could perform poorly. Hybrid
construction by itself is therefore not evidence of quality; each added signal
needs an ablation on the target workload.

A later [LLM schema-matching study](https://vldb.org/workshops/2024/proceedings/TaDA/TaDA.8.pdf)
found that one pair at a time provided too little context, while whole-schema
prompts could overwhelm the model. Combining bounded 1-to-many and many-to-1
views produced better coverage with a manageable candidate-verification set.
Combining LLM results with string matching also improved the string baseline,
showing that the methods made complementary errors.

[Magneto (VLDB 2025)](https://www.vldb.org/pvldb/vol18/p2681-freire.pdf), a
recent high-performing schema matcher, uses a small language model to retrieve
candidates, then applies bipartite assignment or an LLM reranker. Its ablation
found that passing the entire schema directly to an LLM was both less accurate
than the staged variants and much slower. The important caveat is that reranking
cannot recover a correct target omitted by retrieval.

These results favor a ranked-candidate workflow over either hard automatic
mapping or a single unrestricted prompt.

## How ASIM mapping differs

LogLathe is not performing ordinary column typing:

- a typed slot is extracted from a message template rather than a named table
  column;
- multiple ASIM fields share the same physical type but encode different roles,
  such as source, destination, actor, target, or reporting device;
- selecting a schema and selecting fields are related decisions;
- constants, transforms, aliases, mandatory fields, and fields absent from the
  event are part of parser construction;
- the target catalogue is known, versioned, and much smaller than an open-world
  ontology.

This gives LogLathe stronger deterministic constraints than general semantic
typing, but also makes a value-only classifier less applicable. Template text,
slot order, peer slots, representative events, source metadata, schema guidance,
and catalogue field properties should be treated as one mapping context.

## Security-log-specific evidence

Generic table benchmarks only approximate this problem. The closest published
systems suggest that log understanding, target-schema projection, and executable
validation are separable concerns.

| Work | What it adds | Implication for LogLathe |
| --- | --- | --- |
| [Matryoshka (2025 preprint; also a 2026 Berkeley dissertation chapter)](https://arxiv.org/abs/2506.17512) | Generates a syntactic parser, meaningful source-field names, and optional OCSF or UDM mappings, then runs static regex rather than an LLM on live logs | Preserve a source-semantic representation before ASIM projection. The paper's normalized-taxonomy mapping was markedly harder than its source-semantic extraction, so the two errors should be measurable separately. |
| [LogNER (Journal of Systems and Software, 2026)](https://www.sciencedirect.com/science/article/pii/S0164121226001251) | Frames log understanding as template-assisted entity recognition rather than only variable extraction | Semantic entities and important constants may not align one-to-one with parser slots. Evaluate event-level roles as well as slot labels. |
| [OntoLogX (Advanced Intelligent Systems, 2026)](https://advanced.onlinelibrary.wiley.com/doi/10.1002/aisy.202501381) | Builds ontology-grounded event graphs with retrieval and iterative correction before aggregating them into sessions | A compact event frame such as actor-action-target-result is a plausible intermediate representation; a full knowledge-graph platform is not required to test the idea. |
| [MicLog (AAAI 2026)](https://ojs.aaai.org/index.php/AAAI/article/view/37123) | Uses clustered examples, retrieval of demonstrations, and caching with a small model for log parsing | Curated approved cases may be more useful as retrieval memory than as immediately compiled hand-written rules. |
| [DeepParse (2026 preprint)](https://arxiv.org/abs/2604.20553) | Synthesizes reusable regex masks with an LLM and retains deterministic parsing at runtime | Reinforces the generation-time intelligence/runtime determinism boundary already used by the project; it does not by itself solve ASIM semantics. |
| [Elastic Streams experiment (Elastic Observability Labs, 2026)](https://www.elastic.co/observability-labs/blog/automated-log-parsing-ml-streams) | Uses deterministic log-format fingerprints, hierarchical grouping, and diversity-aware sampling before asking an LLM to generate parsing rules | Supports deterministic compression and representative-example selection before model use. Its parsing and source-partitioning results do not establish ASIM schema or field-mapping accuracy. |

### Industry experiment: Elastic Streams

Elastic reports 94% parsing accuracy and 91% log-partitioning accuracy on its
Loghub experiment. The result is useful engineering evidence, but the article is
not a peer-reviewed evaluation and does not define enough of the metric,
prompt, model version, repeated-run variance, or held-out split to make those
numbers a comparable benchmark for LogLathe. A random 20% sample from each
data source may also allow closely related formats to appear in both generation
and evaluation data. Treat the figures as motivation for an experiment, not an
expected performance level.

Four observations are directly relevant:

- **Sample diversity matters more than raw sample count.** Elastic observed
  brittle overspecification with homogeneous examples and confused,
  over-general output with excessively heterogeneous examples. Its stratified
  fingerprint sampling is a concrete alternative to taking the first or most
  frequent events from a cluster.
- **Deterministic structure can compress model context.** Exact fingerprints
  form subclasses and fingerprint prefixes form broader groups before model
  invocation. This is consistent with keeping deterministic clustering and
  candidate preparation outside the semantic provider.
- **Open-ended names drift.** Elastic observed source- and field-name variation
  between runs and normalized the outputs afterward. LogLathe should instead
  constrain schema and field outputs to the pinned catalogue and preserve the
  ranked candidates that led to the choice.
- **Outlier removal has a security cost.** Elastic drops fingerprint subclasses
  below 5% of volume to protect parsing quality. Rare security events may be the
  most important events, so LogLathe must never silently discard them. Low
  coverage should produce an explicit outlier, abstention, or human-review path.

The immediate experiment is therefore not to replace DeepParse or add an LLM
framework. Compare the existing representative-event selection with a small
fingerprint-stratified selector. Measure template/slot coverage, downstream
mapping accuracy, prompt size, reviewer edit rate, and rare-event retention.
Keep the selector behind the same provider-neutral fixture and report results
separately for clustering, source-role inference, and ASIM projection.

There is also a security reason to keep that boundary. A [USENIX Security 2026
study of prompt injection through log data](https://www.usenix.org/conference/usenixsecurity26/presentation/karanjai)
reports high attack success against LLM log-analysis pipelines and residual risk
after layered defenses. Raw event text must therefore remain untrusted data. Any
model provider should have no operational tools or authority, use a strict output
schema, preserve provenance, and feed deterministic validation and human review.

## Alternative designs worth testing

These are alternatives to a growing semantic rule engine, not requirements to
implement together.

| Design | Research basis | Small LogLathe version |
| --- | --- | --- |
| Source semantic frame | Matryoshka, LogNER, OntoLogX | Infer `actor`, `action`, `target`, `result`, `network_source`, and similar source roles, then project those roles to ASIM. Keep the vocabulary deliberately small and allow unknown/custom roles. |
| Retrieval from approved cases | MicLog; [REVEAL, accepted at SIGMOD 2026](https://arxiv.org/abs/2508.17203) | Retrieve a few diverse, relevant reviewed clusters and ASIM concept cards. Begin with lexical/BM25 retrieval; embeddings are an optional provider detail. |
| Uncertainty-only model use | [LLMs as Oracles for Ontology Alignment (EACL 2026)](https://aclanthology.org/2026.eacl-long.110/) | Let catalogue constraints and retrieval settle obvious cases; ask a model only to rank unresolved candidate pairs. This sharply limits the API dependency and review surface. |
| Synthetic domain adaptation | [ZTab (ICDE 2026)](https://arxiv.org/abs/2603.11436) | Generate pseudo examples from ASIM labels, descriptions, and example schema structures, then evaluate a small local model without training on customer logs. This is a later research track, not the next PR. |
| Verification-guided synthesis | Matryoshka and executable program-synthesis practice | Propose roles, mappings, and transforms; run them over every representative event and the pinned ASIM tests; reject or repair inconsistent drafts. Confidence is supporting evidence, not the correctness mechanism. |
| Learned interpretable rules | [SymCA (July 2026 preprint)](https://arxiv.org/abs/2607.25228) | If enough reviewed data accumulates, learn compact trees or labeling functions offline instead of manually adding vendor rules. The evidence is too new to make this a near-term dependency. |

The source frame is the most promising departure from the current direct mapping
assumption. It prevents an ASIM catalogue revision from erasing what was learned
about a source, and it lets reviewers distinguish two corrections: “we
misunderstood the event” from “we chose the wrong ASIM representation.” It need
not add reviewer work: show the inferred source role and ASIM destination in the
same editable mapping row, with details collapsed by default.

## Selected 2026 reviews and broader reading

Publication status matters because several attractive 2026 systems are still
preprints. As of the review date, the following provide the most useful map of
the surrounding fields:

| Publication | Scope | Use here |
| --- | --- | --- |
| [Large Language Models for Ontology Engineering: A Systematic Literature Review (Semantic Web, 2026)](https://www.semantic-web-journal.net/system/files/swj4039.pdf) | Reviews 36 studies of LLMs used as ontology engineers, domain experts, and evaluators; highlights weak standardization, benchmarking, and reproducibility | Supports a modular, evaluated human-in-the-loop component rather than treating an LLM as catalogue authority. |
| [Survey on Embedding Methods Applied to Ontology Matching (ACM Computing Surveys, 2026)](https://dl.acm.org/doi/10.1145/3805799) | Reviews 81 studies across embeddings, context, training, and simple versus complex correspondences | Useful design catalogue for later candidate retrieval; also warns that complex mappings remain less well covered than simple equivalence. |
| [Systematic review of data integration methods in enterprise information systems (Procedia Computer Science, 2026)](https://www.sciencedirect.com/science/article/pii/S1877050926005867) | Broad taxonomy spanning schema matching, mapping, entity matching, and fusion | Useful orientation, but too broad to choose the ASIM provider architecture on its own. |
| [Heterogeneity in Entity Matching: A Survey and Experimental Analysis (Data & Knowledge Engineering, 2026)](https://www.sciencedirect.com/science/article/pii/S0169023X26000224) | Separates representation and semantic heterogeneity and experimentally studies distribution shifts | Adjacent rather than identical to field mapping, but directly relevant to holding out source families and testing catalogue/source drift. |
| [Representation Learning for Tabular Data: A Comprehensive Survey (IEEE TPAMI, 2026)](https://doi.org/10.1109/TPAMI.2026.3657217) | Organizes specialized, transferable, and general tabular representation learning | Background for a future local model; less directly actionable than log and ontology-matching work. |

Two further 2026 ontology-matching results are especially relevant even though
they are not reviews. [GenOM](https://link.springer.com/article/10.1007/s11280-026-01413-y)
generates or enriches concept definitions, retrieves candidates with embeddings,
and then judges equivalence. This suggests compiling versioned ASIM “concept
cards” from official documentation and catalogue metadata. Generated descriptions
may aid retrieval but must not become normative. The EACL oracle study above
instead inserts an LLM only where an established matcher is uncertain; that is
the lower-complexity experiment and the better first API-model shape.

Recent preprints such as [RACT](https://arxiv.org/abs/2606.07843), which retrieves
related tables before multi-table schema matching, and SymCA are useful horizon
signals, not mature dependencies. A broad [table-intelligence survey currently
listed for November 2026](https://www.sciencedirect.com/science/article/pii/S1574013726001048)
is forthcoming relative to this note's review date and should not yet be treated
as completed evidence.

## Recommended boundary

Use a single provider contract with two explicit semantic artefacts:

```text
cluster + source metadata + pinned catalogue
                    |
         suggestion provider (versioned)
          1. infer source semantic frame
          2. retrieve and rank catalogue candidates
                    |
      source semantic frame + ranked ASIM mappings
                    |
       deterministic ASIM validation
                    |
              human decision
```

Catalogue-derived filtering and validation are not a competing semantic rule
engine. They enforce facts such as schema membership, catalogue type
compatibility, and valid output structure. The provider owns semantic ranking
and may initially be a small lexical baseline, later an API model, a local model,
or an MCP-backed service. The source frame and ASIM mapping may be produced in
one invocation; the separation is for evaluation, feedback, and future remapping
rather than an instruction to deploy two services.

Do not initially build an accumulating set of vendor-specific semantic rules.
Only promote a pattern to a deterministic rule when it is unambiguous, common,
covered by fixtures, and valuable without source-specific exceptions. Examples
include a labelled `destination port` integer or a syntactically valid timestamp;
bare IP addresses, users, and integers should remain ranked candidates.

## Feedback policy

Feedback should be provider-neutral evidence, not an immediate mutation of
production behavior. Store:

- the cluster and catalogue revisions;
- provider name and version;
- inferred source roles and reviewer corrections;
- candidates, ranks, scores, and evidence shown;
- accepted mapping, reviewer edits, rejection, or deferral;
- validation outcome.

The same decision data can evaluate a rule baseline, a prompt, or a trained
model. Rules change only through a reviewed versioned release. Models and prompts
also change only after offline evaluation. This avoids an opaque online feedback
loop and gives every approach the same change discipline.

## Smallest useful next experiment

Before choosing a production semantic implementation:

1. Create a provider-neutral gold fixture format from reviewed clusters, storing
   source-semantic roles separately from target ASIM fields.
2. Add a cheap catalogue/type/lexical direct-mapping baseline only as a benchmark.
3. Compare direct mapping with a minimal source-frame-then-ASIM pipeline and
   retrieval from approved cases. These can all be deterministic initially.
4. Compare the existing representative-event selection with deterministic
   fingerprint-stratified selection; retain rare subclasses as outliers rather
   than dropping them.
5. Measure source-role accuracy, schema top-1/top-3, field precision@1,
   reciprocal rank, abstention
   coverage, exact cluster completion, reviewer edit rate, review time, prompt
   size, and rare-event retention.
6. Split fixtures by source or template family to expose distribution shift and
   prevent near-duplicate formats from leaking across the evaluation boundary.
7. Later replay the identical fixtures through one model provider, first only on
   uncertain candidate pairs.
8. Add a signal or second stage only when its ablation improves the intended
   precision/coverage or reviewer-time metric.

This postpones the expensive semantic-rule feedback cycle without blocking the
data contracts, review UI, catalogue constraints, or eventual model integration.

The companion [non-LLM baseline and corpus plan](baseline-strategy.md)
turns this experiment into an acquisition design. It keeps parser-derived ASIM,
paired OCSF, cross-schema source semantics, synthetic stress cases, and future
adjudicated gold in separate evidence tracks, and measures the additional value of
each available context layer.
