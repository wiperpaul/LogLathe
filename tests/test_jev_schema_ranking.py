import copy
import io
import json
from email.message import Message
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

from asim_forge.catalog import sync_catalog
from asim_forge.cli import main
from asim_forge.evaluation import load_semantic_mapping_cases
from asim_forge.schema_ranking import jev_client
from asim_forge.schema_ranking.jev import (
    AUTH_LIFECYCLE_SPEC_VERSION,
    DEFAULT_MODEL,
    SCHEMA_DEFINITIONS,
    SPEC_VERSION,
    STRUCTURED_SPEC_VERSION,
    JevNoul,
    JevResponse,
    build_jev_followup_request,
    build_jev_request,
    request_hash,
)
from asim_forge.schema_ranking.jev_client import JevError, cached_response, send_request
from asim_forge.schema_ranking.jev_experiment import (
    SchemaExperimentInput,
    from_labelled_case,
    run_schema_experiment,
)
from asim_forge.semantic_mapping.types import SemanticMappingInput

REVISION = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(jev_client, "build_opener", Mock(side_effect=AssertionError("network")))


@pytest.fixture
def source():
    return SemanticMappingInput.model_validate(
        {
            "cluster_id": "gold-label-in-cluster-id",
            "template": "Accepted publickey for <USER> from <IP> port <PORT>",
            "representative_events": [
                {
                    "source_file": "gold-label-in-path.log",
                    "line_number": 1,
                    "text": "Accepted publickey for dbadmin from 10.20.30.40 port 45678",
                }
            ],
            "source_metadata": {
                "system": "gold-label-in-system",
                "vendor": "OpenBSD",
                "product": "OpenSSH",
            },
            "parameter_slots": [
                {
                    "slot_id": "p1",
                    "label": "gold-label-in-slot",
                    "placeholder": "<IP>",
                    "occurrence": 1,
                    "examples": ["10.20.30.40", "10.20.30.41", "10.20.30.42", "hidden"],
                }
            ],
        }
    )


def response_payload(*, nouls=False, choice="Authentication"):
    labels = [*SCHEMA_DEFINITIONS, "Unsupported"]
    answers = {
        "schema": {
            "type": "choice",
            "choice": choice,
            "probabilities": {label: 0.97 if label == choice else 0.01 for label in labels},
            "confidence": 0.95,
        }
    }
    if nouls:
        answers.update(
            {f"primary_{name}": {"type": "noul", "noul": 0.8} for name in SCHEMA_DEFINITIONS}
        )
    return {
        "model": DEFAULT_MODEL,
        "answers": answers,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def test_source_projection_excludes_identifiers_and_labels_and_bounds_examples(source):
    request = build_jev_request(source, nouls=True)
    wire = request.model_dump_json()
    assert "gold-label" not in wire
    assert "hidden" not in wire
    parameters = request.state["parameters"]
    assert isinstance(parameters, list) and isinstance(parameters[0], dict)
    assert parameters[0]["physical_type"] == "ipv4"
    assert len(request.questions) == 4
    assert "OpenSSH" not in json.dumps(request.model_dump()["questions"])
    assert build_jev_request(source, context="template").state == {"template": source.template}


def test_cache_identity_includes_context_questions_model_and_catalogue(source):
    requests = [
        build_jev_request(source),
        build_jev_request(source, context="template"),
        build_jev_request(source, nouls=True),
        build_jev_request(source, model="jev-1.12.0"),
    ]
    hashes = {request_hash(request, REVISION) for request in requests}
    hashes.add(request_hash(requests[0], "a" * 40))
    assert len(hashes) == 5
    other_id = source.model_copy(update={"cluster_id": "different-id"})
    assert request_hash(build_jev_request(other_id), REVISION) == request_hash(
        requests[0], REVISION
    )
    with pytest.raises(ValueError):
        build_jev_request(source, model="jev-latest")


def test_structured_questions_preserve_projection_and_use_matching_probe_definition(source):
    old = build_jev_request(source)
    new = build_jev_request(source, spec_version=STRUCTURED_SPEC_VERSION, nouls=True)
    assert new.state == old.state
    assert "gold-label" not in new.model_dump_json()
    definition = new.questions["schema"].criteria["AuditEvent"]
    assert isinstance(definition, dict)
    assert set(definition) == {"covers", "not_for", "missing_context", "examples"}
    probe = new.questions["primary_AuditEvent"].instructions
    assert isinstance(probe, dict) and probe["definition"] == definition
    assert "Meraki" not in json.dumps(new.model_dump()["questions"])
    assert request_hash(old, REVISION) != request_hash(
        new, REVISION, spec_version=STRUCTURED_SPEC_VERSION
    )
    # Specification provenance separates even an identical wire body.
    assert request_hash(old, REVISION) != request_hash(
        old, REVISION, spec_version=STRUCTURED_SPEC_VERSION
    )


def test_v1_frozen_example_hash_is_unchanged():
    case = load_semantic_mapping_cases(Path("examples/evaluation/semantic-mapping-cases.jsonl"))[0]
    assert request_hash(build_jev_request(case.input), case.catalogue_revision) == (
        "0c83d27fb649fdc71c3e005cc9b59e044d4ecf3272324d0977b58263082cbb20"
    )


@pytest.mark.parametrize("spec_version", [STRUCTURED_SPEC_VERSION, AUTH_LIFECYCLE_SPEC_VERSION])
def test_opt_in_experiment_cache_and_cli_provenance(tmp_path, source, monkeypatch, spec_version):
    sender = Mock(return_value=JevResponse.model_validate(response_payload()))
    monkeypatch.setattr(jev_client, "send_request", sender)
    inputs = [SchemaExperimentInput(case_id="probe", source=source)]
    cache = tmp_path / "cache"
    live = run_schema_experiment(
        inputs,
        catalogue_revision=REVISION,
        cache=cache,
        spec_version=spec_version,
        output=tmp_path / "live",
        mode="live",
        api_key="test-key",
    )
    sender.reset_mock(side_effect=True)
    sender.side_effect = AssertionError("network")
    replay = run_schema_experiment(
        inputs,
        catalogue_revision=REVISION,
        cache=cache,
        spec_version=spec_version,
        output=tmp_path / "replay",
        mode="replay",
    )
    assert live["spec_version"] == replay["spec_version"] == spec_version
    assert replay["cache_hits"] == 1
    assert live["rows"][0]["jev"] == replay["rows"][0]["jev"]
    sender.assert_not_called()
    with pytest.raises(JevError, match="No cached"):
        run_schema_experiment(
            inputs,
            catalogue_revision=REVISION,
            cache=cache,
            output=tmp_path / "wrong-spec",
            mode="replay",
            spec_version=SPEC_VERSION,
        )
    output = tmp_path / "cli"
    main(
        [
            "evaluation",
            "schema-rank",
            "examples/evaluation/semantic-mapping-cases.jsonl",
            "--catalog",
            "evaluation/ci-catalog",
            "--output",
            str(output),
            "--decision-spec",
            spec_version,
        ]
    )
    assert json.loads((output / "report.json").read_text())["spec_version"] == spec_version


def test_auth_lifecycle_isolates_boundaries_and_preserves_source_projection(source):
    old = build_jev_request(source, nouls=True)
    new = build_jev_request(source, spec_version=AUTH_LIFECYCLE_SPEC_VERSION, nouls=True)
    assert new.state == old.state
    assert "gold-label" not in new.model_dump_json()
    assert new.questions["schema"].instructions == old.questions["schema"].instructions
    changed = {
        name
        for name, definition in old.questions["schema"].criteria.items()
        if new.questions["schema"].criteria[name] != definition
    }
    assert changed == {"Authentication", "NetworkSession"}
    assert new.questions["primary_AuditEvent"] == old.questions["primary_AuditEvent"]
    assert "Meraki" not in json.dumps(new.model_dump()["questions"])
    assert len(new.questions) == 6
    assert len(build_jev_request(source, spec_version=AUTH_LIFECYCLE_SPEC_VERSION).questions) == 1
    assert request_hash(old, REVISION) != request_hash(
        new, REVISION, spec_version=AUTH_LIFECYCLE_SPEC_VERSION
    )


def test_auth_diagnostic_disagreements_survive_replay_without_overriding_choice(
    tmp_path, source, monkeypatch
):
    payload = response_payload(nouls=True, choice="NetworkSession")
    payload["answers"]["authentication_relationship_change"] = {"type": "noul", "noul": 0.99}
    payload["answers"]["communication_lifecycle"] = {"type": "noul", "noul": 0.01}
    response = JevResponse.model_validate(payload)
    request = build_jev_request(source, spec_version=AUTH_LIFECYCLE_SPEC_VERSION, nouls=True)
    response.validate_for(request)
    missing = response.model_copy(update={"answers": response.answers.copy()})
    del missing.answers["communication_lifecycle"]
    with pytest.raises(ValueError, match="question IDs"):
        missing.validate_for(request)
    monkeypatch.setattr(jev_client, "send_request", Mock(return_value=response))
    inputs = [SchemaExperimentInput(case_id="conflicting-probes", source=source)]
    live = run_schema_experiment(
        inputs,
        output=tmp_path / "live",
        mode="live",
        api_key="test-key",
        catalogue_revision=REVISION,
        cache=tmp_path / "cache",
        spec_version=AUTH_LIFECYCLE_SPEC_VERSION,
        nouls=True,
    )
    sender = Mock(side_effect=AssertionError("network"))
    monkeypatch.setattr(jev_client, "send_request", sender)
    replay = run_schema_experiment(
        inputs,
        output=tmp_path / "replay",
        mode="replay",
        catalogue_revision=REVISION,
        cache=tmp_path / "cache",
        spec_version=AUTH_LIFECYCLE_SPEC_VERSION,
        nouls=True,
    )
    assert replay["cache_hits"] == 1
    result = replay["rows"][0]["jev"]
    assert result == live["rows"][0]["jev"]
    assert result["selected_schema"] == "NetworkSession"
    assert result["response"]["answers"]["authentication_relationship_change"]["noul"] == 0.99
    sender.assert_not_called()


def test_auth_lifecycle_diagnostics_keep_ambiguity_and_labels_out_of_requests():
    cases = load_semantic_mapping_cases(Path("examples/evaluation/auth-lifecycle-cases.jsonl"))
    inputs = [from_labelled_case(case) for case in cases]
    assert len(inputs) == 8
    assert sum(item.expected_schema is None for item in inputs) == 1
    assert all(item.label_source == "synthetic" for item in inputs)
    for item in inputs:
        request = build_jev_request(item.source, spec_version=AUTH_LIFECYCLE_SPEC_VERSION)
        assert "expected_schema" not in request.model_dump_json()
        assert item.case_id not in request.model_dump_json()


def lifecycle_response(*, authentication=0.99, communication=0.01):
    payload = response_payload(nouls=True, choice="NetworkSession")
    payload["answers"]["authentication_relationship_change"] = {
        "type": "noul",
        "noul": authentication,
    }
    payload["answers"]["communication_lifecycle"] = {"type": "noul", "noul": communication}
    return JevResponse.model_validate(payload)


def test_followup_projection_uses_only_semantic_probes_and_hashes_their_values(source):
    request = build_jev_request(source, spec_version=AUTH_LIFECYCLE_SPEC_VERSION, nouls=True)
    response = lifecycle_response()
    followup = build_jev_followup_request(request, response)
    assert set(followup.questions) == {"schema"}
    assert followup.questions["schema"].criteria == request.questions["schema"].criteria
    observations = followup.state["semantic_observations"]
    assert isinstance(observations, dict) and set(observations) == {
        "authentication_relationship_change",
        "communication_lifecycle",
    }
    assert {
        key: value for key, value in followup.state.items() if key != "semantic_observations"
    } == request.state
    assert "gold-label" not in followup.model_dump_json()
    assert "primary_" not in followup.model_dump_json()
    assert "confidence" not in json.dumps(observations)
    changed_choice = JevResponse.model_validate(response_payload(nouls=True, choice="AuditEvent"))
    changed_choice.answers.update({name: response.answers[name] for name in observations})
    assert build_jev_followup_request(request, changed_choice) == followup
    other = build_jev_followup_request(request, lifecycle_response(authentication=0.2))
    assert request_hash(
        followup, REVISION, spec_version=AUTH_LIFECYCLE_SPEC_VERSION
    ) != request_hash(other, REVISION, spec_version=AUTH_LIFECYCLE_SPEC_VERSION)
    with pytest.raises(ValueError, match="lifecycle probes"):
        build_jev_followup_request(
            build_jev_request(source), JevResponse.model_validate(response_payload())
        )


def test_staged_experiment_replays_both_requests_and_retains_first_choice(
    tmp_path, source, monkeypatch
):
    first, final = lifecycle_response(), JevResponse.model_validate(response_payload())
    sender = Mock(side_effect=[first, final])
    monkeypatch.setattr(jev_client, "send_request", sender)
    inputs = [SchemaExperimentInput(case_id="staged", source=source)]
    live = run_schema_experiment(
        inputs,
        catalogue_revision=REVISION,
        output=tmp_path / "live",
        cache=tmp_path / "cache",
        mode="live",
        api_key="test-key",
        spec_version=AUTH_LIFECYCLE_SPEC_VERSION,
        staged=True,
    )
    assert sender.call_count == 2
    assert live["nouls"] is True
    row = live["rows"][0]
    assert row["jev"]["selected_schema"] == "Authentication"
    assert row["probe_stage"]["response"]["answers"]["schema"]["choice"] == "NetworkSession"
    final_wire = json.loads((tmp_path / "live/second-stage-requests.jsonl").read_text())
    assert final_wire["request_hash"] == row["final_request_hash"]
    assert final_wire["body"] == sender.call_args_list[1].args[0].model_dump(mode="json")
    sender.reset_mock(side_effect=True)
    sender.side_effect = AssertionError("network")
    replay = run_schema_experiment(
        inputs,
        catalogue_revision=REVISION,
        output=tmp_path / "replay",
        cache=tmp_path / "cache",
        mode="replay",
        spec_version=AUTH_LIFECYCLE_SPEC_VERSION,
        staged=True,
    )
    assert replay["cache_hits"] == 1
    assert replay["rows"][0]["probe_stage"]["cache_hit"] is True
    assert replay["rows"][0]["final_cache_hit"] is True
    assert replay["rows"][0]["jev"] == row["jev"]
    sender.assert_not_called()


def test_staged_failure_preserves_probe_result_and_resumes_from_cache(
    tmp_path, source, monkeypatch
):
    sender = Mock(side_effect=[lifecycle_response(), JevError("Jev request failed with HTTP 529")])
    monkeypatch.setattr(jev_client, "send_request", sender)
    inputs = [SchemaExperimentInput(case_id="partial", source=source)]
    with pytest.raises(JevError, match="partial report"):
        run_schema_experiment(
            inputs,
            catalogue_revision=REVISION,
            output=tmp_path / "partial",
            cache=tmp_path / "cache",
            mode="live",
            api_key="test-key",
            spec_version=AUTH_LIFECYCLE_SPEC_VERSION,
            staged=True,
        )
    report = json.loads((tmp_path / "partial/report.json").read_text())
    assert report["completed"] == 0
    assert report["rows"][0]["status"] == "error"
    assert (
        report["rows"][0]["probe_stage"]["response"]["answers"]["schema"]["choice"]
        == "NetworkSession"
    )
    assert report["rows"][0]["jev"] is None
    sender.reset_mock(side_effect=True)
    sender.return_value = JevResponse.model_validate(response_payload())
    resumed = run_schema_experiment(
        inputs,
        catalogue_revision=REVISION,
        output=tmp_path / "resumed",
        cache=tmp_path / "cache",
        mode="live",
        api_key="test-key",
        spec_version=AUTH_LIFECYCLE_SPEC_VERSION,
        staged=True,
    )
    assert sender.call_count == 1 and resumed["completed"] == 1
    assert resumed["rows"][0]["probe_stage"]["cache_hit"] is True


def test_staged_prepare_does_not_invent_dependent_answers_and_requires_v3(tmp_path, source):
    inputs = [SchemaExperimentInput(case_id="prepare", source=source)]
    with pytest.raises(ValueError, match="requires --decision-spec"):
        run_schema_experiment(
            inputs,
            catalogue_revision=REVISION,
            output=tmp_path / "invalid",
            cache=tmp_path / "cache",
            staged=True,
        )
    main(
        [
            "evaluation",
            "schema-rank",
            "examples/evaluation/auth-lifecycle-cases.jsonl",
            "--catalog",
            "evaluation/ci-catalog",
            "--output",
            str(tmp_path / "prepare"),
            "--decision-spec",
            AUTH_LIFECYCLE_SPEC_VERSION,
            "--staged",
        ]
    )
    report = json.loads((tmp_path / "prepare/report.json").read_text())
    assert report["nouls"] is True and report["completed"] == 0
    assert report["second_stage_status"] == "requires_probe_answers"
    assert not (tmp_path / "prepare/second-stage-requests.jsonl").exists()


@pytest.mark.parametrize(
    "defect",
    [
        "model",
        "missing_question",
        "extra_choice",
        "mass",
        "argmax",
        "nan",
        "infinity",
        "negative",
        "wrong_type",
    ],
)
def test_rejects_malformed_responses(source, defect):
    payload = response_payload()
    answer = payload["answers"]["schema"]
    if defect == "model":
        payload["model"] = "jev-0.0.0"
    elif defect == "missing_question":
        payload["answers"] = {}
    elif defect == "extra_choice":
        answer["probabilities"]["Invented"] = 0
    elif defect == "argmax":
        answer["choice"] = "AuditEvent"
    elif defect == "wrong_type":
        payload["answers"]["schema"] = {"type": "noul", "noul": 0.9}
    else:
        answer["probabilities"]["Authentication"] = {
            "mass": 0.2,
            "nan": float("nan"),
            "infinity": float("inf"),
            "negative": -0.1,
        }[defect]
    with pytest.raises(ValueError):
        JevResponse.model_validate(payload).validate_for(build_jev_request(source))


def test_transport_wire_and_independent_nouls(source, monkeypatch):
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(response_payload(nouls=True)).encode())
    monkeypatch.setattr(jev_client, "build_opener", lambda *_: opener)
    request = build_jev_request(source, nouls=True)
    response = send_request(request, "test-key")
    wire = opener.open.call_args.args[0]
    assert json.loads(wire.data) == request.model_dump(mode="json")
    assert wire.get_header("Authorization") == "Bearer test-key"
    assert opener.open.call_args.kwargs["timeout"] == 45
    probe = response.answers["primary_NetworkSession"]
    assert isinstance(probe, JevNoul) and probe.noul == 0.8
    assert response.schema_choice.choice == "Authentication"
    assert (
        jev_client._NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere")
        is None
    )


@pytest.mark.parametrize("status,calls", [(401, 1), (422, 1), (429, 3), (529, 3), (302, 1)])
def test_transport_bounded_retries_and_scrubbed_errors(source, monkeypatch, status, calls):
    opener = Mock()
    opener.open.side_effect = lambda *_a, **_k: (_ for _ in ()).throw(
        HTTPError(
            "https://example.invalid", status, "secret-key", Message(), io.BytesIO(b"secret-event")
        )
    )
    monkeypatch.setattr(jev_client, "build_opener", lambda *_: opener)
    monkeypatch.setattr(jev_client.time, "sleep", Mock())
    with pytest.raises(JevError, match=f"HTTP {status}") as error:
        send_request(build_jev_request(source), "secret-key")
    assert "secret" not in str(error.value)
    assert opener.open.call_count == calls


def test_transport_retries_overload_then_succeeds(source, monkeypatch):
    opener = Mock()
    opener.open.side_effect = [
        HTTPError("", 429, "", Message(), None),
        io.BytesIO(json.dumps(response_payload()).encode()),
    ]
    monkeypatch.setattr(jev_client, "build_opener", lambda *_: opener)
    monkeypatch.setattr(jev_client.time, "sleep", Mock())
    assert (
        send_request(build_jev_request(source), "test-key").schema_choice.choice == "Authentication"
    )


@pytest.mark.parametrize("body", [b"not json", b"x" * 1_000_001], ids=["invalid_json", "oversized"])
def test_transport_invalid_body_is_safe(source, monkeypatch, body):
    opener = Mock()
    opener.open.return_value = io.BytesIO(body)
    monkeypatch.setattr(jev_client, "build_opener", lambda *_: opener)
    with pytest.raises(JevError):
        send_request(build_jev_request(source), "test-key")


@pytest.mark.parametrize("failure", [URLError("secret"), IncompleteRead(b"secret")])
def test_timeout_does_not_retry_or_expose_exception(source, monkeypatch, failure):
    opener = Mock()
    opener.open.side_effect = failure
    monkeypatch.setattr(jev_client, "build_opener", lambda *_: opener)
    with pytest.raises(JevError, match="connection error or timeout"):
        send_request(build_jev_request(source), "test-key")
    assert opener.open.call_count == 1


def test_cache_replay_never_calls_provider_and_corrupt_cache_fails_closed(
    source, tmp_path, monkeypatch
):
    request = build_jev_request(source)
    with pytest.raises(JevError, match="No cached"):
        cached_response(request, REVISION, tmp_path)
    sender = Mock(return_value=JevResponse.model_validate(response_payload()))
    monkeypatch.setattr(jev_client, "send_request", sender)
    response, hit = cached_response(request, REVISION, tmp_path, live=True, api_key="test-key")
    assert not hit
    assert cached_response(request, REVISION, tmp_path) == (response, True)
    assert sender.call_count == 1
    entry = tmp_path / f"{request_hash(request, REVISION)}.json"
    assert "test-key" not in entry.read_text()
    entry.write_text("{}", encoding="utf-8")
    with pytest.raises(JevError, match="Invalid Jev cache"):
        cached_response(request, REVISION, tmp_path, live=True, api_key="test-key")
    assert sender.call_count == 1


def test_prepare_is_offline_and_gold_never_enters_outbound_body(source, tmp_path):
    item = SchemaExperimentInput(
        case_id="gold-id",
        source=source,
        expected_schema="Authentication",
        label_source="human_review",
    )
    report = run_schema_experiment(
        [item], catalogue_revision=REVISION, output=tmp_path / "prepare", cache=tmp_path / "cache"
    )
    assert report["metrics"]["jev"]["accuracy_on_completed"] is None
    assert report["review_policy"] == "advisory_only"
    body = json.loads((tmp_path / "prepare/requests.jsonl").read_text())["body"]
    assert "expected_schema" not in body
    assert "gold-id" not in json.dumps(body)
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize(
    "choice,tied,expected_disposition",
    [
        ("Authentication", False, "suggested"),
        ("Unsupported", False, "unsupported"),
        ("Authentication", True, "tied_top"),
    ],
)
def test_experiment_distinguishes_ties_unsupported_and_suggestions(
    source, tmp_path, monkeypatch, choice, tied, expected_disposition
):
    payload = response_payload(choice=choice)
    if tied:
        payload["answers"]["schema"]["probabilities"] = {
            "Authentication": 0.49,
            "NetworkSession": 0.49,
            "AuditEvent": 0.01,
            "Unsupported": 0.01,
        }
    monkeypatch.setattr(
        jev_client, "send_request", Mock(return_value=JevResponse.model_validate(payload))
    )
    items = [SchemaExperimentInput(case_id="test", source=source, expected_schema=choice)]
    report = run_schema_experiment(
        items,
        catalogue_revision=REVISION,
        output=tmp_path / "live",
        cache=tmp_path / "cache",
        mode="live",
        api_key="test-key",
    )
    assert report["rows"][0]["jev"]["disposition"] == expected_disposition
    assert report["metrics"]["jev"]["correct"] == (0 if tied else 1)
    replay = run_schema_experiment(
        items,
        catalogue_revision=REVISION,
        output=tmp_path / "replay",
        cache=tmp_path / "cache",
        mode="replay",
    )
    assert replay["cache_hits"] == 1


def test_operational_failure_preserves_partial_report_and_does_not_become_unsupported(
    source, tmp_path, monkeypatch
):
    sender = Mock(side_effect=JevError("HTTP 401"))
    monkeypatch.setattr(jev_client, "send_request", sender)
    items = [
        SchemaExperimentInput(
            case_id=f"case-{index}", source=source, expected_schema="Authentication"
        )
        for index in range(2)
    ]
    with pytest.raises(JevError, match="partial report"):
        run_schema_experiment(
            items,
            catalogue_revision=REVISION,
            output=tmp_path / "live",
            cache=tmp_path / "cache",
            mode="live",
            api_key="test-key",
        )
    report = json.loads((tmp_path / "live/report.json").read_text())
    assert [row["status"] for row in report["rows"]] == ["error", "not_run"]
    assert report["metrics"]["jev"]["completed"] == 0
    assert report["metrics"]["jev"]["labelled"] == 2
    assert report["rows"][0]["jev"] is None
    assert sender.call_count == 1


def test_label_adapter_excludes_unresolved_and_rejects_out_of_scope():
    case = load_semantic_mapping_cases(Path("examples/evaluation/semantic-mapping-cases.jsonl"))[0]
    assert from_labelled_case(case).expected_schema == "NetworkSession"
    payload = case.model_dump()
    payload["expected"] = {"disposition": "unresolved", "unresolved_reasons": ["unknown"]}
    assert from_labelled_case(type(case).model_validate(payload)).expected_schema is None
    payload["expected"] = {"disposition": "not_applicable"}
    assert from_labelled_case(type(case).model_validate(payload)).expected_schema == "Unsupported"
    changed = copy.deepcopy(case)
    changed.expected.schema_name = "FileEvent"
    with pytest.raises(ValueError, match="outside"):
        from_labelled_case(changed)


def test_cli_offline_prepare_and_key_required_for_live(tmp_path, capsys):
    case = load_semantic_mapping_cases(Path("examples/evaluation/semantic-mapping-cases.jsonl"))[0]
    catalog = tmp_path / "catalog"
    csv = (
        "ColumnName,ColumnType,Class,Schema,LogicalType,ListOfValues,Aliased,DynamicType,ArrayValuesType\n"
        "EventCount,int,Mandatory,Common,,,,,\n"
        + "".join(f"EventType,string,Mandatory,{name},,,,,\n" for name in SCHEMA_DEFINITIONS)
    ).encode()
    sync_catalog(catalog, revision=case.catalogue_revision, fetch_bytes=lambda _: csv)
    args = [
        "evaluation",
        "schema-rank",
        "examples/evaluation/semantic-mapping-cases.jsonl",
        "--catalog",
        str(catalog),
        "--output",
        str(tmp_path / "prepare"),
    ]
    main(args)
    assert "prepare: 1 input(s), 0 completed" in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        main([*args[:-1], str(tmp_path / "live"), "--live"])
    assert error.value.code == 2
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "live").exists()


@pytest.mark.parametrize(
    "options,message",
    [
        (["--partition", "test"], "--partition requires --split"),
        (["--limit", "0"], "--limit must be positive"),
        (["--split", "unused.json"], "requires --case-groups and --promotion-manifest"),
        (["--case-groups", "unused.jsonl"], "require --split"),
    ],
)
def test_cli_rejects_invalid_selection_before_creating_artifacts(
    tmp_path, capsys, options, message
):
    output = tmp_path / "experiment"
    with pytest.raises(SystemExit) as error:
        main(
            [
                "evaluation",
                "schema-rank",
                "examples/evaluation/semantic-mapping-cases.jsonl",
                "--catalog",
                "evaluation/ci-catalog",
                "--output",
                str(output),
                *options,
            ]
        )
    assert error.value.code == 2
    assert message in capsys.readouterr().err
    assert not output.exists()


def test_cache_io_failure_saves_an_operational_error(source, tmp_path, monkeypatch):
    monkeypatch.setattr(jev_client, "send_request", Mock(side_effect=OSError("private-path")))
    with pytest.raises(JevError, match="Could not read or persist"):
        run_schema_experiment(
            [SchemaExperimentInput(case_id="case-test", source=source)],
            catalogue_revision=REVISION,
            output=tmp_path / "live",
            cache=tmp_path / "cache",
            mode="live",
            api_key="test-key",
        )
    report = (tmp_path / "live/report.json").read_text()
    assert "private-path" not in report
    assert json.loads(report)["rows"][0]["status"] == "error"
