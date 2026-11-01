"""Frozen, offline controller evaluation. Candidate source only executes inside Docker."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any

from .accounting import digest, provenance
from .codex import tools
from .config import Config, Repository
from .engine import Engine
from .errors import ContributorError, InvalidArguments, PermissionDenied, classify_error
from .process import run_command
from .safety import safe_path
from .store import Store
from .tool_guard import ToolGuard, ToolPolicy

CORPUS = Path(__file__).parent / "resources/controller-eval-v1.json"
CORPUS_SHA256 = "1d23fcc07a6869920d866d4f12fbd0ed42c49f9f15d04b2fdc4f089a8f7895a7"


class NoExternalServices:
    def __getattr__(self, name):
        raise AssertionError("evaluation must not access models or upstream services")


def load_corpus() -> dict:
    corpus = json.loads(CORPUS.read_text())
    ids = [case["id"] for case in corpus["cases"]]
    if digest(corpus) != CORPUS_SHA256 or len(ids) != len(set(ids)) or any(case["expected"] not in {"accepted", "rejected"} for case in corpus["cases"]):
        raise ValueError("invalid frozen corpus")
    return corpus


def resolve_image(image: str) -> str:
    result = run_command(["docker", "image", "inspect", "--format", "{{.Id}}", image], timeout=15, check=True)
    value = result.output.strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise ValueError("evaluation needs one immutable local Docker image ID")
    return value


def read_evaluation_json(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("evaluation input must be a regular file")
        raw = stream.read(5_000_001)
    if len(raw) > 5_000_000:
        raise ValueError("evaluation input exceeds size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("evaluation input must be an object")
    return value


def run_evaluation(image: str, candidates: dict | None = None, *, sandbox_factory=None, image_resolver=None) -> dict:
    corpus = load_corpus()
    known = {case["id"] for case in corpus["cases"]}
    if candidates is not None and set(candidates) != known:
        raise ValueError("candidate set must cover the exact frozen case IDs")
    # The production CLI has no alternate executor: target code remains container-only.
    immutable_image = (image_resolver or resolve_image)(image)
    results: list[dict[str, Any]] = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="oss-controller-eval-") as tmp:
        config = Config(state_dir=Path(tmp) / "state")
        store = Store(config)
        try:
            engine = Engine(config, store, github=NoExternalServices(), codex=NoExternalServices(),
                            sandbox_factory=sandbox_factory)
            for case in corpus["cases"]:
                payload = (candidates[case["id"]] if candidates is not None else case)
                item = {"id": case["id"], "kind": case["kind"], "expected": case["expected"],
                        "observed": "rejected", "error_kind": None, "command_exit_codes": []}
                guard = None
                try:
                    if case["kind"] == "patch":
                        family = corpus["families"][case["family"]]
                        regression = family["regression"]
                        if payload.get("regression_override", regression) != regression:
                            raise PermissionDenied("candidate altered the frozen benchmark regression")
                        path = "tests/test_benchmark_regression.py"
                        changes = dict(payload["changes"])
                        if path in changes:
                            raise PermissionDenied("candidate must not supply a replacement benchmark oracle")
                        changes[path] = regression
                        report = engine.validate(Repository(name="benchmark/fixture", image=immutable_image, install=[]),
                                                 family["base"], changes, path, "a" * 40, time.monotonic() + 90)
                        item["observed"] = "accepted" if report.passed else "rejected"
                        item["command_exit_codes"] = [command.code for command in report.commands]
                        item["error_kind"] = report.error.kind.value if report.error is not None else None
                    else:
                        guard = ToolGuard(ToolPolicy.model_validate(case.get("policy", {})))
                        allowed = {tool["name"] for tool in tools(False)}
                        for step in payload["steps"]:
                            def execute(step=step, allowed=allowed):
                                if step["tool"] not in allowed:
                                    raise PermissionDenied("trace requested an unavailable host or write tool")
                                args = step["arguments"]
                                required = {"repo_list": set(), "repo_read": {"path"}, "repo_search": {"pattern"}}[step["tool"]]
                                if not isinstance(args, dict) or set(args) != required:
                                    raise InvalidArguments("invalid recorded tool arguments")
                                if step["tool"] == "repo_read":
                                    safe_path(args["path"])
                                if step["tool"] == "repo_search" and (
                                    not isinstance(args["pattern"], str) or not args["pattern"] or len(args["pattern"]) > 200
                                ):
                                    raise InvalidArguments("invalid recorded search")
                                return step["result"]

                            guard.run(step["tool"], step["arguments"], execute, lambda: {})
                        item["observed"] = "accepted"
                        item["executed_steps"] = guard.state.steps
                except (ContributorError, ValueError, TypeError) as exc:
                    item["error_kind"] = classify_error(exc).kind.value
                if guard is not None:
                    item["executed_steps"] = guard.state.steps
                item["matches_expected"] = item["observed"] == item["expected"]
                results.append(item)
        finally:
            store.close()
    expected_accept = sum(case["expected"] == "accepted" for case in results)
    expected_reject = len(results) - expected_accept
    return {
        "corpus_version": corpus["version"], "corpus_sha256": digest(corpus),
        "configuration_sha256": digest({"image_id": immutable_image, "limits": config.limits.model_dump(mode="json")}),
        "image_id": immutable_image, "implementation": provenance("offline-controller-eval", None),
        "candidate_sha256": digest(candidates) if candidates is not None else None,
        "candidate_origin": "unverified_import" if candidates is not None else "frozen_fixture",
        "scope": corpus["scope"], "cases": results,
        "metrics": {"cases": len(results), "matching_decisions": sum(case["matches_expected"] for case in results),
                    "expected_accept": expected_accept, "expected_reject": expected_reject,
                    "unsafe_acceptances": sum(case["expected"] == "rejected" and case["observed"] == "accepted" for case in results),
                    "valid_fixture_rejections": sum(case["expected"] == "accepted" and case["observed"] == "rejected" for case in results)},
        "elapsed_seconds": time.monotonic() - started,
        "model_calls": 0, "upstream_operations": 0,
        "limitations": "Synthetic controller gates and replay only; not model-quality, prompt-injection comprehension, "
                       "native App longevity or merge probability. Imported model labels are not attested.",
    }


def compare_reports(left: dict, right: dict) -> dict:
    corpus = load_corpus()
    for field in ("corpus_version", "corpus_sha256", "configuration_sha256", "image_id", "scope"):
        if left[field] != right[field]:
            raise ValueError(f"evaluation reports have incomparable {field}")
    if left["corpus_sha256"] != digest(corpus):
        raise ValueError("evaluation reports do not reference the frozen corpus")
    left_cases = {case["id"]: case for case in left["cases"]}
    right_cases = {case["id"]: case for case in right["cases"]}
    labels = {case["id"]: case["expected"] for case in corpus["cases"]}
    if (len(left_cases) != len(left["cases"]) or len(right_cases) != len(right["cases"])
        or left_cases.keys() != labels.keys() or left_cases.keys() != right_cases.keys()) or any(
        left_cases[key]["expected"] != right_cases[key]["expected"] for key in left_cases
    ):
        raise ValueError("evaluation reports have different cases or labels")
    for cases, report in ((left_cases, left), (right_cases, right)):
        if any(case["expected"] != labels[key] or case["observed"] not in {"accepted", "rejected"}
               for key, case in cases.items()):
            raise ValueError("evaluation reports have invalid decisions or labels")
        recomputed = {
            "cases": len(cases), "matching_decisions": sum(case["observed"] == labels[key] for key, case in cases.items()),
            "expected_accept": sum(label == "accepted" for label in labels.values()),
            "expected_reject": sum(label == "rejected" for label in labels.values()),
            "unsafe_acceptances": sum(labels[key] == "rejected" and case["observed"] == "accepted" for key, case in cases.items()),
            "valid_fixture_rejections": sum(labels[key] == "accepted" and case["observed"] == "rejected" for key, case in cases.items()),
        }
        if report["metrics"] != recomputed:
            raise ValueError("evaluation report metrics do not match its cases")
    return {"corpus_version": left["corpus_version"], "cases": len(left_cases),
            "changed_cases": [{"id": key, "before": left_cases[key]["observed"], "after": right_cases[key]["observed"]}
                              for key in left_cases if left_cases[key]["observed"] != right_cases[key]["observed"]],
            "before": left["metrics"], "after": right["metrics"],
            "note": "Paired fixed-corpus comparison, not a causal/model A/B claim or automatic policy rollout."}
