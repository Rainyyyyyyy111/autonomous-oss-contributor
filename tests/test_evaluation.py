from __future__ import annotations

import json
from copy import deepcopy

import pytest
from conftest import FixtureSandbox
from typer.testing import CliRunner

import oss_contributor.evaluation as evaluation_module
from oss_contributor.accounting import digest
from oss_contributor.cli import app
from oss_contributor.evaluation import (
    CORPUS_SHA256,
    compare_reports,
    load_corpus,
    read_evaluation_json,
    run_evaluation,
)


@pytest.fixture(scope="module")
def frozen_report():
    # The local executor is trusted test-only fixture code; CLI always uses Docker.
    return run_evaluation("synthetic-image", sandbox_factory=FixtureSandbox,
                          image_resolver=lambda image: "sha256:" + "a" * 64)


def test_frozen_corpus_controller_decisions_have_explicit_denominators(frozen_report):
    assert frozen_report["corpus_sha256"] == CORPUS_SHA256
    assert frozen_report["metrics"] == {
        "cases": 14, "matching_decisions": 14, "expected_accept": 4, "expected_reject": 10,
        "unsafe_acceptances": 0, "valid_fixture_rejections": 0,
    }
    assert frozen_report["model_calls"] == frozen_report["upstream_operations"] == 0
    cases = {case["id"]: case for case in frozen_report["cases"]}
    assert cases["addition.correct"]["command_exit_codes"] == [0, 1, 0, 0]
    assert cases["addition.protected_file"]["command_exit_codes"] == []
    assert cases["tools.host_denied"]["executed_steps"] == 1
    assert cases["tools.repeated"]["executed_steps"] == 2


def test_corpus_cannot_change_labels_without_a_new_fingerprint(monkeypatch, tmp_path):
    corpus = load_corpus()
    corpus["cases"][0]["expected"] = "rejected"
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    monkeypatch.setattr(evaluation_module, "CORPUS", path)
    with pytest.raises(ValueError, match="frozen corpus"):
        load_corpus()


def test_comparison_requires_same_environment_and_recomputes_metrics(frozen_report):
    same = compare_reports(frozen_report, deepcopy(frozen_report))
    assert same["changed_cases"] == []
    changed = deepcopy(frozen_report)
    changed["image_id"] = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="incomparable"):
        compare_reports(frozen_report, changed)
    changed = deepcopy(frozen_report)
    changed["metrics"]["matching_decisions"] = 999
    with pytest.raises(ValueError, match="metrics"):
        compare_reports(frozen_report, changed)


def test_comparison_rejects_duplicate_or_changed_case_ids(frozen_report):
    changed = deepcopy(frozen_report)
    changed["cases"].append(changed["cases"][0])
    with pytest.raises(ValueError, match="cases"):
        compare_reports(frozen_report, changed)


def test_candidate_import_cannot_change_or_omit_cases_or_expected_labels():
    with pytest.raises(ValueError, match="exact frozen case IDs"):
        run_evaluation("unused", {"invented": {}}, image_resolver=lambda _: pytest.fail("must not start Docker"))
    assert digest(load_corpus()) == CORPUS_SHA256


def test_evaluation_inputs_are_bounded_and_do_not_follow_links(tmp_path):
    path = tmp_path / "too-large.json"
    path.write_bytes(b" " * 5_000_001)
    with pytest.raises(ValueError, match="size limit"):
        read_evaluation_json(path)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        read_evaluation_json(link)


def test_evaluation_cli_does_not_load_live_configuration(frozen_report, monkeypatch, tmp_path):
    from oss_contributor import cli

    monkeypatch.setattr(cli, "load", lambda *args: pytest.fail("evaluation must not load operator config"))
    monkeypatch.setattr(evaluation_module, "run_evaluation", lambda *args: frozen_report)
    path = tmp_path / "report.json"
    result = CliRunner().invoke(app, ["evaluate", "--output", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text())["metrics"]["cases"] == 14
    assert path.stat().st_mode & 0o777 == 0o600
