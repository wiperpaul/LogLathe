from pathlib import Path

import pytest
from semantic_fixtures import EXAMPLE_CASES, build_catalog

from asim_forge import cli
from asim_forge.commands import evaluation as evaluation_command
from asim_forge.evaluation import SemanticMappingCase, load_semantic_mapping_cases
from asim_forge.models import AsimCatalog
from asim_forge.semantic_mapping.approaches.case_retrieval import CaseRetrievalApproach
from asim_forge.semantic_mapping.approaches.direct_lexical import DirectLexicalApproach
from asim_forge.semantic_mapping.approaches.priors import FieldFrequencyPriorApproach
from asim_forge.semantic_mapping.approaches.semantic_frame import SemanticFrameApproach
from asim_forge.semantic_mapping.comparison import (
    ComparisonError,
    compare_approaches,
    write_comparison_report,
)
from asim_forge.semantic_mapping.contracts import (
    MappingRequest,
    PredictedAsimField,
    RankedFieldCandidate,
    SemanticMappingPrediction,
)
from asim_forge.semantic_mapping.field_ranking import (
    generate_candidates,
    rank_field_candidates,
    score_candidates,
)
from asim_forge.semantic_mapping.metrics import EvaluationError, evaluate_predictions


def _request() -> MappingRequest:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    return MappingRequest(
        case_id=case.case_id,
        catalogue_revision=case.catalogue_revision,
        input=case.input,
    )


def _prediction_payload() -> dict[str, object]:
    request = _request()
    return {
        "case_id": request.case_id,
        "catalogue_revision": request.catalogue_revision,
        "approach": {"name": "example-approach", "version": "1"},
        "disposition": "mapped",
        "ranked_schemas": [
            {"schema_name": "NetworkSession", "score": 1.0},
        ],
        "source_semantics": [
            {
                "source_kind": "slot",
                "locator": "p1",
                "role": "network.source.address",
                "score": 1.0,
            }
        ],
        "asim_fields": [
            {
                "source_kind": "slot",
                "locator": "p1",
                "asim_field": "SrcIpAddr",
                "score": 1.0,
                "ranked_candidates": [
                    {"asim_field": "SrcIpAddr", "score": 1.0},
                    {"asim_field": "SrcMacAddr", "score": 0.5},
                ],
            }
        ],
    }


def _catalog() -> AsimCatalog:
    return build_catalog()


def test_request_contains_no_expected_labels() -> None:
    request = _request()

    assert "expected" not in request.model_dump()
    assert request.input.cluster_id == "cluster-734d3840beb93179"


def test_prediction_keeps_provider_output_outside_gold_case() -> None:
    prediction = SemanticMappingPrediction.model_validate(_prediction_payload())

    assert prediction.approach.name == "example-approach"
    assert prediction.ranked_schemas[0].schema_name == "NetworkSession"
    assert prediction.asim_fields[0].ranked_candidates[0].asim_field == "SrcIpAddr"


def test_selected_field_must_head_its_candidate_ranking() -> None:
    payload = _prediction_payload()
    fields = payload["asim_fields"]
    assert isinstance(fields, list)
    fields[0]["asim_field"] = "DstIpAddr"

    with pytest.raises(ValueError, match="must be the first ranked candidate"):
        SemanticMappingPrediction.model_validate(payload)


def test_selected_field_score_must_match_first_candidate() -> None:
    payload = _prediction_payload()
    fields = payload["asim_fields"]
    assert isinstance(fields, list)
    fields[0]["score"] = 0.75

    with pytest.raises(ValueError, match="score must match the first ranked candidate"):
        SemanticMappingPrediction.model_validate(payload)


def test_candidate_scores_must_be_descending() -> None:
    payload = _prediction_payload()
    fields = payload["asim_fields"]
    assert isinstance(fields, list)
    fields[0]["score"] = 0.5
    fields[0]["ranked_candidates"][0]["score"] = 0.5
    fields[0]["ranked_candidates"][1]["score"] = 0.75

    with pytest.raises(ValueError, match="field candidate scores must be descending"):
        SemanticMappingPrediction.model_validate(payload)

    payload = _prediction_payload()
    schemas = payload["ranked_schemas"]
    assert isinstance(schemas, list)
    schemas[0]["score"] = 0.5
    schemas.append({"schema_name": "Authentication", "score": 0.75})

    with pytest.raises(ValueError, match="schema candidate scores must be descending"):
        SemanticMappingPrediction.model_validate(payload)


def test_duplicate_source_semantics_are_rejected() -> None:
    payload = _prediction_payload()
    semantics = payload["source_semantics"]
    assert isinstance(semantics, list)
    semantics.append(dict(semantics[0]))

    with pytest.raises(ValueError, match="source semantics must be unique"):
        SemanticMappingPrediction.model_validate(payload)


def test_prediction_locators_are_trimmed_and_compared_case_insensitively() -> None:
    payload = _prediction_payload()
    fields = payload["asim_fields"]
    assert isinstance(fields, list)
    duplicate = dict(fields[0])
    duplicate["locator"] = " P1 "
    fields.append(duplicate)

    with pytest.raises(ValueError, match="field combinations must be unique"):
        SemanticMappingPrediction.model_validate(payload)


def test_prediction_locator_and_role_cannot_be_blank() -> None:
    payload = _prediction_payload()
    semantics = payload["source_semantics"]
    assert isinstance(semantics, list)
    semantics[0]["role"] = "   "

    with pytest.raises(ValueError, match="value cannot be blank"):
        SemanticMappingPrediction.model_validate(payload)


def test_not_applicable_prediction_cannot_contain_targets() -> None:
    payload = _prediction_payload()
    payload["disposition"] = "not_applicable"

    with pytest.raises(ValueError, match="cannot contain ASIM targets"):
        SemanticMappingPrediction.model_validate(payload)


def test_metrics_score_rankings_sets_exactness_and_edits_independently() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    prediction = SemanticMappingPrediction.model_validate(_prediction_payload())

    metrics = evaluate_predictions([case], [prediction])

    assert metrics.schema_top1_accuracy == 1
    assert metrics.schema_mrr == 1
    assert metrics.source_micro_precision == 1
    assert metrics.source_micro_recall == 0.25
    assert metrics.source_micro_f1 == 0.4
    assert metrics.field_micro_precision == 1
    assert metrics.field_micro_recall == 0.25
    assert metrics.field_mrr == 0.25
    assert metrics.field_recall_at_gold == 0.25
    assert metrics.mapping_exact_match == 0
    assert metrics.mean_mapping_edits == 3


@pytest.mark.parametrize(("score", "recall", "mrr"), [(0.8, 0.25, 0.25), (1.0, 0.0, 0.125)])
def test_candidate_metrics_merge_shared_locators_without_depending_on_row_order(score, recall, mrr):
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    prediction = SemanticMappingPrediction.model_validate(_prediction_payload())
    extra = PredictedAsimField(
        source_kind="slot",
        locator="P1",
        asim_field="DstIpAddr",
        score=score,
        ranked_candidates=[
            RankedFieldCandidate(asim_field="DstIpAddr", score=score),
            RankedFieldCandidate(asim_field="SrcIpAddr", score=0.7),
        ],
    )
    prediction.asim_fields.append(extra)

    metrics = evaluate_predictions([case], [prediction])
    prediction.asim_fields.reverse()

    assert evaluate_predictions([case], [prediction]) == metrics
    assert metrics.candidate_recall_at_1 == recall
    assert metrics.candidate_recall_at_3 == metrics.candidate_recall_at_5 == 0.25
    assert metrics.field_mrr == mrr


def test_metrics_require_predictions_for_the_exact_case_set() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    payload = _prediction_payload()
    payload["case_id"] = "different-case"
    prediction = SemanticMappingPrediction.model_validate(payload)

    with pytest.raises(EvaluationError, match="cover exactly"):
        evaluate_predictions([case], [prediction])


def test_metrics_require_the_case_catalogue_revision() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    payload = _prediction_payload()
    payload["catalogue_revision"] = "f" * 40
    prediction = SemanticMappingPrediction.model_validate(payload)

    with pytest.raises(EvaluationError, match="uses catalogue revision"):
        evaluate_predictions([case], [prediction])


def test_metrics_require_one_approach_version() -> None:
    first_case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    second_case = first_case.model_copy(update={"case_id": "second-case"})
    first_prediction = SemanticMappingPrediction.model_validate(_prediction_payload())
    second_payload = _prediction_payload()
    second_payload["case_id"] = second_case.case_id
    second_payload["approach"] = {"name": "example-approach", "version": "2"}
    second_prediction = SemanticMappingPrediction.model_validate(second_payload)

    with pytest.raises(EvaluationError, match="one approach name and version"):
        evaluate_predictions(
            [first_case, second_case],
            [first_prediction, second_prediction],
        )


def test_correct_empty_labels_receive_full_case_level_f1() -> None:
    case_payload = load_semantic_mapping_cases(EXAMPLE_CASES)[0].model_dump(mode="json")
    expected = case_payload["expected"]
    assert isinstance(expected, dict)
    expected.update(
        disposition="not_applicable",
        schema_name=None,
        source_semantics=[],
        asim_fields=[],
    )
    case = SemanticMappingCase.model_validate(case_payload)

    prediction_payload = _prediction_payload()
    prediction_payload.update(
        disposition="not_applicable",
        ranked_schemas=[],
        source_semantics=[],
        asim_fields=[],
    )
    prediction = SemanticMappingPrediction.model_validate(prediction_payload)

    metrics = evaluate_predictions([case], [prediction])

    assert metrics.source_macro_f1 == 1
    assert metrics.field_macro_f1 == 1


def test_direct_lexical_approach_maps_slots_without_source_frame() -> None:
    prediction = DirectLexicalApproach().predict(_request(), _catalog())

    assert prediction.disposition == "mapped"
    assert prediction.ranked_schemas[0].schema_name == "NetworkSession"
    assert prediction.source_semantics == []
    assert {(field.locator, field.asim_field) for field in prediction.asim_fields} == {
        ("p1", "SrcIpAddr"),
        ("p2", "DstIpAddr"),
        ("p3", "DstPortNumber"),
    }


def test_semantic_frame_approach_maps_slots_and_static_meaning() -> None:
    prediction = SemanticFrameApproach().predict(_request(), _catalog())

    assert {(semantic.locator, semantic.role) for semantic in prediction.source_semantics} == {
        ("p1", "network.source.address"),
        ("p2", "network.destination.address"),
        ("p3", "network.destination.port"),
        ("connection allowed", "network.connection.allowed"),
    }
    event_result = next(
        field for field in prediction.asim_fields if field.asim_field == "EventResult"
    )
    assert event_result.source_kind == "template_constant"
    assert event_result.constant_value == "Success"


def test_all_approaches_share_structured_identifier_normalization() -> None:
    reference = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    target_input = reference.input.model_copy(
        update={
            "template": (
                "CEF:0|Demo|Gateway|1|connectionAllowed|connectionAllowed|1|"
                "src=<VAR:IPV4> dst=<VAR:IPV4> dpt=<VAR:NUMBER>"
            )
        }
    )
    request = MappingRequest(
        case_id="demo.network.allowed.cef",
        catalogue_revision=reference.catalogue_revision,
        input=target_input,
    )

    direct = DirectLexicalApproach().predict(request, _catalog())
    frame = SemanticFrameApproach().predict(request, _catalog())
    retrieval = CaseRetrievalApproach([reference]).predict(request, _catalog())

    expected_fields = {"SrcIpAddr", "DstIpAddr", "DstPortNumber"}
    assert {field.asim_field for field in direct.asim_fields} == expected_fields
    assert {field.asim_field for field in frame.asim_fields} >= expected_fields
    assert {field.asim_field for field in retrieval.asim_fields} >= expected_fields
    assert direct.approach.version == "3"
    assert frame.approach.version == "3"
    assert retrieval.approach.version == "2"


def test_retrieval_transfers_a_labelled_case_without_reading_target_gold() -> None:
    reference = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    target = reference.model_copy(
        deep=True,
        update={"case_id": "demo.network.allowed.variant"},
    )
    request = MappingRequest(
        case_id=target.case_id,
        catalogue_revision=target.catalogue_revision,
        input=target.input,
    )

    prediction = CaseRetrievalApproach([reference]).predict(request, _catalog())
    metrics = evaluate_predictions([target], [prediction])

    assert prediction.disposition == "mapped"
    assert metrics.mapping_exact_match == 1
    assert metrics.source_micro_f1 == 1


def test_retrieval_requires_at_least_one_neighbor() -> None:
    with pytest.raises(ValueError, match="neighbors must be at least one"):
        CaseRetrievalApproach([], neighbors=0)


def test_retrieval_ignores_references_from_another_catalogue_revision() -> None:
    reference = load_semantic_mapping_cases(EXAMPLE_CASES)[0].model_copy(
        update={"catalogue_revision": "f" * 40}
    )

    prediction = CaseRetrievalApproach([reference]).predict(_request(), _catalog())

    assert prediction.disposition == "unresolved"
    assert any("No labelled reference" in warning for warning in prediction.warnings)


def test_comparison_reports_all_registered_approaches() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]

    report = compare_approaches([case], _catalog())

    evaluations = {evaluation.approach.name: evaluation for evaluation in report.approaches}
    direct = evaluations["direct-lexical"].metrics
    frame = evaluations["semantic-frame"].metrics
    retrieval = evaluations["case-retrieval"]
    assert set(evaluations) == {
        "null-prior",
        "majority-schema-prior",
        "field-frequency-prior",
        "direct-lexical",
        "semantic-frame",
        "matcher-ensemble",
        "case-retrieval",
    }
    assert direct.schema_top1_accuracy == 1
    assert direct.field_micro_recall == 0.75
    assert direct.source_micro_f1 == 0
    assert frame.mapping_exact_match == 1
    assert frame.full_exact_match == 1
    assert retrieval.metrics.coverage == 0
    assert any("smoke test" in warning for warning in evaluations["semantic-frame"].warnings)
    assert any("no mapped predictions" in warning for warning in retrieval.warnings)
    assert any("No explicit grouped split" in warning for warning in retrieval.warnings)
    assert not any(
        "No explicit grouped split" in warning for warning in evaluations["semantic-frame"].warnings
    )
    assert any(
        "No labelled reference" in warning
        for prediction in retrieval.predictions
        for warning in prediction.warnings
    )


def test_comparison_rejects_catalogue_revision_drift() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    catalog = _catalog()
    catalog.manifest.resolved_revision = "f" * 40

    with pytest.raises(ComparisonError, match="loaded catalogue revision"):
        compare_approaches([case], catalog)


def test_comparison_requires_at_least_one_approach() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]

    with pytest.raises(ComparisonError, match="At least one semantic mapping approach"):
        compare_approaches([case], _catalog(), [])


@pytest.mark.parametrize(
    ("approaches", "message"),
    [
        (["missing-approach"], "Unknown semantic mapping approaches"),
        (["direct-lexical", "direct-lexical"], "names must be unique"),
    ],
)
def test_comparison_rejects_invalid_approach_selection(
    approaches: list[str],
    message: str,
) -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]

    with pytest.raises(ComparisonError, match=message):
        compare_approaches([case], _catalog(), approaches)


def test_candidate_generation_is_reported_separately_from_the_ranking_cut() -> None:
    fields = _catalog().fields_for_schema("NetworkSession")

    ranking = rank_field_candidates(fields, "source ip address", limit=1)

    assert len(ranking.candidates) == 1
    assert ranking.pool_size >= len(ranking.candidates)
    assert ranking.considered_field_count >= ranking.pool_size
    assert ranking.candidates == score_candidates(
        generate_candidates(fields, "source ip address"), limit=1
    )


def test_candidate_generation_discards_fields_without_lexical_evidence() -> None:
    fields = _catalog().fields_for_schema("NetworkSession")

    assert generate_candidates(fields, "zzzz-no-catalogue-concept") == []


def test_score_candidates_records_equal_score_ties() -> None:
    fields = _catalog().fields_for_schema("NetworkSession")

    candidates = score_candidates(generate_candidates(fields, "address"), limit=5)

    for candidate in candidates:
        tied = [other for other in candidates if other.score == candidate.score]
        assert candidate.tied_with == len(tied) - 1


def test_priors_establish_a_floor_without_reading_the_source_event() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]

    report = compare_approaches(
        [case],
        _catalog(),
        ["null-prior", "majority-schema-prior", "field-frequency-prior"],
    )

    evaluations = {evaluation.approach.name: evaluation for evaluation in report.approaches}
    assert set(evaluations) == {"null-prior", "majority-schema-prior", "field-frequency-prior"}
    assert evaluations["null-prior"].metrics.coverage == 0
    assert evaluations["null-prior"].metrics.field_micro_f1 == 0
    # Leave-one-out leaves the single example case with no labelled frequency to copy.
    assert evaluations["field-frequency-prior"].metrics.field_micro_f1 == 0


def test_field_frequency_prior_copies_the_majority_label_without_source_evidence() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    reference = case.model_copy(update={"case_id": "demo.network.allowed.reference"})

    prediction = FieldFrequencyPriorApproach([reference]).predict(_request(), _catalog())

    assert prediction.disposition == "mapped"
    assert prediction.ranked_schemas[0].schema_name == "NetworkSession"
    assigned = {field.asim_field for field in prediction.asim_fields}
    # A prior must ignore the event, so every slot receives the same majority label.
    assert len(assigned) == 1
    assert all(
        "no source evidence used" in " ".join(field.evidence) for field in prediction.asim_fields
    )


def test_comparison_warns_when_no_prior_supplies_a_floor() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]

    report = compare_approaches([case], _catalog(), ["direct-lexical"])

    assert any("no floor" in warning for warning in report.warnings)


def test_schema_oracle_is_reported_as_a_diagnostic_condition() -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]

    report = compare_approaches([case], _catalog(), ["direct-lexical"], oracle="schema")

    assert report.oracle == "schema"
    assert report.approaches[0].metrics.schema_top1_accuracy == 1
    assert any("not approach accuracy" in warning for warning in report.approaches[0].warnings)


def test_writes_reproducible_comparison_report(tmp_path: Path) -> None:
    case = load_semantic_mapping_cases(EXAMPLE_CASES)[0]
    report = compare_approaches([case], _catalog(), ["semantic-frame"])
    output = tmp_path / "comparison.json"

    write_comparison_report(output, report)

    text = output.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert '"field_recall_at_gold": 1.0' in text
    assert '"name": "semantic-frame"' in text


def test_cli_compares_a_selected_approach(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(evaluation_command, "load_catalog", lambda _: _catalog())
    output = tmp_path / "comparison.json"

    cli.main(
        [
            "evaluation",
            "compare",
            str(EXAMPLE_CASES),
            "--catalog",
            "unused-by-test",
            "--approach",
            "semantic-frame",
            "--output",
            str(output),
        ]
    )

    stdout = capsys.readouterr().out
    assert "cand@1" in stdout
    assert "semantic-frame" in stdout
    assert "direct-lexical" not in stdout
    assert "no floor" in stdout
    assert output.is_file()
