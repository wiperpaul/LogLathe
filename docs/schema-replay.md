# Classifier replay dashboard

Status: implemented; recorded replay only
Audience: schema-ranking developers and reviewers preparing a demonstration
Canonical for: exporting and using the classifier replay page
Last verified: 2026-10-03

`evaluation schema-view` turns existing classifier reports into a self-contained
HTML page. It shows the frozen template, representative messages, classifier
choices, scores, recorded Jev semantic questions, and reference agreement while
stepping through cases. The interface uses HTML, CSS, and JavaScript without a
separate frontend build or runtime dependency.

## Replay the existing expanded Jev trial

The local trial described in the
[expanded assessment](research/jev-expanded-assessment-2026-10-02.md) supplies all
four Jev configurations and the recorded source-concept baseline:

```powershell
uv run asim-forge evaluation schema-view artifacts/jev-expanded-2026-10-02/inputs.jsonl `
  --input-kind experiment `
  --run "Jev v1=artifacts/jev-expanded-2026-10-02/live/v1-choice" `
  --run "Jev v3=artifacts/jev-expanded-2026-10-02/live/v3-choice" `
  --run "v3 + probes=artifacts/jev-expanded-2026-10-02/live/v3-nouls" `
  --run "v3 staged=artifacts/jev-expanded-2026-10-02/live/v3-staged" `
  --output artifacts/schema-replay/index.html
```

Open the generated file directly in a browser. These recorded trial files remain
local and are not included in a public checkout. A checkout can use its own
`schema-rank` outputs instead, following the [Jev guide](jev-schema-experiment.md).
For canonical cases, omit `--input-kind`; for an existing frozen annotation queue,
use `--input-kind queue`. `experiment` reads the existing `SchemaExperimentInput`
JSONL contract used by the local mixed-source trial.

Each `--run` names a directory containing `report.json`. If it contains
`reference-agreement.json`, the viewer also presents the saved official-parser
assessment. Runs must cover exactly the same case IDs, use the same catalogue,
and match request hashes recomputed from the supplied model-visible source evidence.
Those hashes do not authenticate examples outside Jev's bounded input view. Staged
responses are checked against their probe-derived final request. Reference
assessments must agree on captures, labels, and retained example coverage.
The exporter records input/report hashes; it does not rerun native Kusto or
independently re-adjudicate an imported reference assessment.

## Add the registered mapping approaches

`--comparison` imports every approach and its saved predictions from an existing
`evaluation compare` report. This includes priors, lexical and semantic-frame
approaches, matcher ensemble, and case retrieval. It may be repeated alongside
Jev runs when all reports describe the same frozen cases and catalogue.
Oracle conditions and reduced context views are rejected. Comparison reports do
not carry source-input fingerprints, so the operator must supply the exact case
evidence used to generate them; matching IDs alone cannot prove source identity.

A runnable contract demonstration uses the checked synthetic smoke case:

```powershell
uv run asim-forge evaluation compare examples/evaluation/semantic-mapping-cases.jsonl `
  --catalog evaluation/ci-catalog --output artifacts/schema-comparison.json
uv run asim-forge evaluation schema-view examples/evaluation/semantic-mapping-cases.jsonl `
  --comparison artifacts/schema-comparison.json --output artifacts/all-classifiers.html
```

This demonstrates all seven registered mapping approaches plus source-concept
ranking. It is a one-case interface/contract check, not a performance comparison.
Label-dependent approaches still require appropriate reference partitions for a
meaningful assessment; the viewer does not create training labels from parser
answers. Field-mapping suggestions and warnings appear under decision details.

## Reading the replay

- **Play / pause**, speed, previous/next, and the timeline control playback.
  Arrow keys step cases; Space toggles playback outside form controls.
- Select a classifier lane to see its full score distribution, recorded semantic
  questions, schema definitions, and staged initial/final choices where present.
- Filters isolate differing classifier choices, reference disagreements, or
  incomplete references. Counts cover the filtered prefix reached in playback;
  revisiting earlier cases preserves that prefix until playback restarts or the
  filter changes.
- Jev scores are probabilities; the source-concept baseline displays evidence
  counts. Imported mapping approaches retain their own scores. Only the Jev
  distribution is presented as a probability distribution.
- **Matches reference** means agreement with the displayed reference or case
  label. Missing or mixed native outputs remain unscored at template level;
  individual retained example labels remain visible. Case-label provenance is
  displayed separately from official-parser references.

Playback advances through recorded decisions; it is neither live inference nor
model training. The page performs no API requests and needs no provider key.
It embeds source messages and predictions, so keep exports under ignored
`artifacts/` and follow the existing redistribution rules before publication.
