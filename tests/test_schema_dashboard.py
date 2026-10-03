import json
from pathlib import Path

import pytest

from asim_forge.catalog import load_catalog
from asim_forge.cli import main
from asim_forge.evaluation import EvaluationError, load_semantic_mapping_cases
from asim_forge.schema_ranking import jev_experiment
from asim_forge.schema_ranking.dashboard import dashboard_data, export_dashboard
from asim_forge.schema_ranking.jev import (
    AUTH_LIFECYCLE_SPEC_VERSION,
    SPEC_VERSION,
    DecisionSpec,
    JevResponse,
)
from asim_forge.schema_ranking.jev_experiment import from_labelled_case, run_schema_experiment
from asim_forge.semantic_mapping.comparison import compare_approaches


@pytest.fixture
def recorded(tmp_path, monkeypatch):
    case = load_semantic_mapping_cases(Path("examples/evaluation/semantic-mapping-cases.jsonl"))[0]
    item = from_labelled_case(case)

    def cached(request, *_args, **_kwargs):
        answers = {}
        for name, question in request.questions.items():
            if question.type == "noul":
                answers[name] = {"type": "noul", "noul": 0.8}
            else:
                answers[name] = {
                    "type": "choice",
                    "choice": "Authentication",
                    "confidence": 0.95,
                    "probabilities": {
                        key: 0.97 if key == "Authentication" else 0.01 for key in question.criteria
                    },
                }
        return JevResponse.model_validate(
            {
                "model": request.model,
                "answers": answers,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        ).validate_for(request), True

    monkeypatch.setattr(jev_experiment, "cached_response", cached)
    runs = []
    configurations: list[tuple[str, DecisionSpec, bool]] = [
        ("v1", SPEC_VERSION, False),
        ("v3 staged", AUTH_LIFECYCLE_SPEC_VERSION, True),
    ]
    for label, spec, staged in configurations:
        path = tmp_path / label
        run_schema_experiment(
            [item],
            catalogue_revision=case.catalogue_revision,
            output=path,
            cache=tmp_path / "cache",
            mode="replay",
            spec_version=spec,
            staged=staged,
        )
        runs.append((label, path))
    return item, runs


def test_recorded_probes_stages_and_missing_reference_are_presented(recorded):
    item, runs = recorded
    data = dashboard_data([item], runs)
    case = data["cases"][0]
    assert len(data["classifiers"]) == 3
    assert case["reference"]["kind"] == "Case label · synthetic"
    final = case["predictions"]["jev-1"]
    assert final["first_choice"] == final["selected"] == "Authentication"
    assert {probe["id"] for probe in final["probes"]} >= {
        "authentication_relationship_change",
        "communication_lifecycle",
    }
    assert case["predictions"]["source-concept"]["score_kind"] == "Evidence count"
    assert final["score_kind"] == "Probability"


def test_changed_source_evidence_is_rejected(recorded):
    item, runs = recorded
    changed = item.model_copy(
        update={"source": item.source.model_copy(update={"template": "Different"})}
    )
    with pytest.raises(EvaluationError, match="evidence does not match"):
        dashboard_data([changed], runs)


def test_duplicate_missing_and_relabelled_cases_are_rejected(recorded):
    item, runs = recorded
    with pytest.raises(EvaluationError, match="unique"):
        dashboard_data([item, item], runs)
    with pytest.raises(EvaluationError, match="labels differ"):
        dashboard_data([item.model_copy(update={"expected_schema": "AuditEvent"})], runs)
    report_path = runs[0][1] / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["rows"] = []
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(EvaluationError, match="same unique case IDs"):
        dashboard_data([item], runs)


def test_response_and_staged_request_tampering_are_rejected(recorded):
    item, runs = recorded
    report_path = runs[1][1] / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["rows"][0]["probe_stage"]["response"]["answers"]["communication_lifecycle"]["noul"] = 0.1
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(EvaluationError, match="Staged final request"):
        dashboard_data([item], runs)


def test_html_treats_source_and_labels_as_data_and_preserves_existing_exports(recorded, tmp_path):
    item, runs = recorded
    path = tmp_path / "inputs.jsonl"
    path.write_text(item.model_dump_json() + "\n", encoding="utf-8")
    output = tmp_path / "replay.html"
    malicious = '</script><script>alert("injected")</script>'
    data = export_dashboard(
        path, kind="experiment", runs=[(malicious, runs[0][1])], comparisons=[], output=output
    )
    html = output.read_text(encoding="utf-8")
    assert malicious not in html
    assert "\\u003c/script>" in html
    assert "connect-src 'none'" in html
    assert data["classifiers"][1]["label"] == malicious
    with pytest.raises(EvaluationError, match="new dashboard output"):
        export_dashboard(path, kind="experiment", runs=runs, comparisons=[], output=output)


def test_cli_can_export_recorded_cases_without_network(recorded, tmp_path, monkeypatch):
    _, runs = recorded
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    main(
        [
            "evaluation",
            "schema-view",
            "examples/evaluation/semantic-mapping-cases.jsonl",
            "--run",
            f"v1={runs[0][1]}",
            "--output",
            str(tmp_path / "page.html"),
        ]
    )
    assert (tmp_path / "page.html").exists()


def test_all_mapping_classifiers_can_be_imported_without_oracle(recorded, tmp_path):
    item, _ = recorded
    cases = load_semantic_mapping_cases(Path("examples/evaluation/semantic-mapping-cases.jsonl"))
    report = compare_approaches(cases, load_catalog(Path("evaluation/ci-catalog")), resamples=10)
    path = tmp_path / "comparison.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    data = dashboard_data([item], [], [path])
    assert len(data["classifiers"]) == 8
    assert {row["label"] for row in data["classifiers"]} >= {
        "case-retrieval",
        "null-prior",
        "semantic-frame",
    }
    report.oracle = "schema"
    path.write_text(report.model_dump_json(), encoding="utf-8")
    with pytest.raises(EvaluationError, match="no oracle"):
        dashboard_data([item], [], [path])


def test_missing_native_outputs_are_unscored_and_inconsistent_labels_rejected(recorded):
    item, runs = recorded
    for _, directory in runs:
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
        events = [
            {"event_id": f"sample-{i}", "reference_schema": None, "reference_state": "not_selected"}
            for i, _ in enumerate(item.source.representative_events)
        ]
        reference = {
            "catalogue_revision": report["catalogue_revision"],
            "model": report["model"],
            "context": report["context"],
            "capture_sha256": "a" * 64,
            "track": "asim-parser-silver",
            "cases": [
                {
                    "case_id": item.case_id,
                    "request_hash": report["rows"][0]["request_hash"],
                    "reference_schema": None,
                    "reference_state": "not_selected",
                    "events": events,
                }
            ],
        }
        (directory / "reference-agreement.json").write_text(json.dumps(reference), encoding="utf-8")
    data = dashboard_data([item], runs)
    assert data["cases"][0]["reference"]["schema"] is None
    assert data["cases"][0]["reference"]["state"] == "not_selected"
    path = runs[0][1] / "reference-agreement.json"
    reference = json.loads(path.read_text(encoding="utf-8"))
    reference["cases"][0]["reference_schema"] = "Authentication"
    path.write_text(json.dumps(reference), encoding="utf-8")
    with pytest.raises(EvaluationError, match="Inconsistent"):
        dashboard_data([item], runs)
