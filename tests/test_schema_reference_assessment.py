import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from asim_forge.catalog import load_catalog
from asim_forge.cli import main
from asim_forge.reference.contracts import QueryOutput
from asim_forge.reference.evidence import ReferenceReviewEvidence, ReferenceReviewExample
from asim_forge.reference.workflow import prepare
from asim_forge.schema_ranking import jev_client
from asim_forge.schema_ranking.jev import JevResponse, build_jev_request
from asim_forge.schema_ranking.reference_assessment import compare_schema_reference
from asim_forge.semantic_annotation.artifacts import load_semantic_annotation_queue
from asim_forge.semantic_annotation.queue import prepare_semantic_annotation_queue


def _example(event_id, labels):
    return ReferenceReviewExample(
        event_id=event_id,
        source_values={},
        output=QueryOutput(
            columns={"EventSchema": "string"}, rows=[{"EventSchema": label} for label in labels]
        ),
    )


def _evidence(examples):
    return {
        "case-one": ReferenceReviewEvidence(
            capture_sha256="a" * 64,
            schema_name="DeclaredSchemaMustNotBecomeALabel",
            schema_version="0.1.0",
            source_columns={},
            examples=examples,
            issues=[],
        )
    }


def _report(*, selected="Authentication", probabilities=None):
    return {
        "catalogue_revision": "b" * 40,
        "model": "jev-1.13.0",
        "context": "template",
        "nouls": False,
        "rows": [
            {
                "case_id": "case-one",
                "request_hash": "c" * 64,
                "baseline": {
                    "selected_schema": None,
                    "ranked_schemas": [
                        {"schema_name": "Authentication", "score": 0},
                        {"schema_name": "NetworkSession", "score": 0},
                    ],
                },
                "jev": {
                    "selected_schema": selected,
                    "response": {
                        "answers": {
                            "schema": {
                                "probabilities": probabilities
                                or {"Authentication": 0.9, "NetworkSession": 0.1},
                            }
                        }
                    },
                },
            }
        ],
    }


def test_scores_emitted_schema_not_fixture_declaration_and_deduplicates_output_rows():
    result = compare_schema_reference(
        _report(),
        _evidence(
            [
                _example("e1", ["Authentication", "Authentication"]),
                _example("e2", ["Authentication"]),
            ]
        ),
    )
    assert result["approaches"]["jev"]["templates"]["agreed"] == 1
    assert result["approaches"]["jev"]["events"]["agreed"] == 2
    assert result["reference_event_schema_counts"] == {"Authentication": 2}
    assert result["cases"][0]["events"][0]["output_row_count"] == 2
    assert result["single_schema_reference"]


def test_empty_output_does_not_label_unsupported():
    result = compare_schema_reference(
        _report(selected="Unsupported"), _evidence([_example("e1", [])])
    )
    assert result["template_states"] == {"not_selected": 1}
    assert result["approaches"]["jev"]["templates"]["eligible"] == 0
    assert result["approaches"]["jev"]["events"]["top1_agreement"] is None


@pytest.mark.parametrize("labels", [[None], [""], ["Authentication", "NetworkSession"]])
def test_ambiguous_or_missing_emitted_schema_is_unscorable(labels):
    result = compare_schema_reference(_report(), _evidence([_example("e1", labels)]))
    assert result["approaches"]["jev"]["templates"]["eligible"] == 0
    assert result["approaches"]["jev"]["events"]["eligible"] == 0


def test_mixed_template_labels_are_not_reduced_to_majority():
    result = compare_schema_reference(
        _report(),
        _evidence(
            [
                _example("e1", ["Authentication"]),
                _example("e2", ["NetworkSession"]),
            ]
        ),
    )
    assert result["template_states"] == {"mixed_schemas": 1}
    assert result["approaches"]["jev"]["templates"]["eligible"] == 0
    assert result["approaches"]["jev"]["events"]["top1_agreement"] == 0.5
    assert not result["single_schema_reference"]


def test_partial_selection_counts_only_emitted_events_and_excludes_template():
    result = compare_schema_reference(
        _report(),
        _evidence(
            [
                _example("e1", ["Authentication"]),
                _example("e2", []),
            ]
        ),
    )
    assert result["template_states"] == {"incomplete_or_ambiguous_reference": 1}
    assert result["approaches"]["jev"]["events"]["eligible"] == 1


def test_tied_rank_reports_bounds_and_abstention_is_not_top1_agreement():
    result = compare_schema_reference(
        _report(
            selected=None,
            probabilities={
                "Authentication": 0.5,
                "NetworkSession": 0.5,
            },
        ),
        _evidence([_example("e1", ["Authentication"])]),
    )
    for name in ("jev", "baseline"):
        score = result["approaches"][name]["templates"]
        assert score["top1_agreement"] == 0
        assert score["mean_reciprocal_rank_lower"] == 0.5
        assert score["mean_reciprocal_rank_upper"] == 1


def test_reference_schema_outside_choices_is_a_miss_not_unsupported():
    result = compare_schema_reference(_report(), _evidence([_example("e1", ["FileEvent"])]))
    score = result["approaches"]["jev"]["templates"]
    assert score["eligible"] == 1
    assert score["top1_agreement"] == score["mean_reciprocal_rank_upper"] == 0


def test_duplicate_events_and_misaligned_cases_are_rejected():
    with pytest.raises(ValueError, match="multiple examples"):
        compare_schema_reference(
            _report(),
            _evidence(
                [
                    _example("e1", ["Authentication"]),
                    _example("e1", ["Authentication"]),
                ]
            ),
        )
    with pytest.raises(ValueError, match="case IDs"):
        compare_schema_reference(_report(), {})


def test_cli_replays_against_verified_native_capture_without_sending_reference_answers(
    tmp_path, monkeypatch
):
    fixture = Path("evaluation/reference/openssh")
    catalog_path = Path("evaluation/ci-catalog")
    catalog = load_catalog(catalog_path)
    bundle = tmp_path / "bundle"
    prepare(fixture, bundle)
    first = json.loads((bundle / "build/clusters.jsonl").read_text().splitlines()[0])
    reviews = tmp_path / "reviews.jsonl"
    reviews.write_text(
        json.dumps({"cluster_id": first["cluster_id"], "status": "approved", "reviewer": "test"})
        + "\n"
    )
    queue = tmp_path / "queue"
    prepare_semantic_annotation_queue(
        bundle / "build",
        reviews,
        queue,
        catalog,
        group_id="openbsd-openssh",
        group_strategy="source-family",
    )
    _, tasks = load_semantic_annotation_queue(queue)
    request = build_jev_request(tasks[0].input)
    response = JevResponse.model_validate(
        {
            "model": request.model,
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "answers": {
                "schema": {
                    "type": "choice",
                    "choice": "Authentication",
                    "confidence": 1,
                    "probabilities": {
                        "Authentication": 1,
                        "AuditEvent": 0,
                        "NetworkSession": 0,
                        "Unsupported": 0,
                    },
                }
            },
        }
    )
    cache = tmp_path / "cache"
    monkeypatch.setattr(jev_client, "send_request", Mock(return_value=response))
    jev_client.cached_response(request, catalog.manifest.resolved_revision, cache, live=True)
    sender = Mock(side_effect=AssertionError("Unexpected provider call"))
    monkeypatch.setattr(jev_client, "send_request", sender)
    output = tmp_path / "output"
    args = [
        "evaluation",
        "schema-rank",
        str(queue),
        "--input-kind",
        "queue",
        "--catalog",
        str(catalog_path),
        "--output",
        str(output),
        "--cache",
        str(cache),
        "--replay",
        "--reference-fixture",
        str(fixture),
        "--reference-bundle",
        str(bundle),
        "--reference-capture",
        str(fixture / "reference-output.json"),
    ]
    main(args)
    sender.assert_not_called()
    agreement = json.loads((output / "reference-agreement.json").read_text())
    assert agreement["approaches"]["jev"]["templates"]["agreed"] == 1
    assert json.loads((output / "requests.jsonl").read_text())["body"] == request.model_dump()
    # Corrupted source identity must fail before any provider/cache request or new output.
    (bundle / "input/source.log").write_text("changed")
    args[args.index("--output") + 1] = str(tmp_path / "must-not-exist")
    with pytest.raises(SystemExit):
        main(args)
    assert not (tmp_path / "must-not-exist").exists()
    sender.assert_not_called()
