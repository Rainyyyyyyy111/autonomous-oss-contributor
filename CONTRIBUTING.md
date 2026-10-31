# Contributing

This is an experimental, single-user CLI. Small, reproducible improvements are welcome. For design changes, discuss the intended behavior in an issue before implementing a large patch. There is no CLA; contributions are accepted under the repository's MIT license.

## Local setup

```sh
uv sync --locked --extra dev
uv run ruff check src tests
uv run mypy src/oss_contributor
uv run pytest -q
uv build
uv run python scripts/smoke_wheel.py
```

The default suite uses simulated GitHub/Codex adapters and trusted local fixtures. It needs no GitHub credentials or model account. Fixture execution on the host is restricted to our own test fixtures; production target repositories execute only in Docker.

For container changes, run the real Docker suite too:

```sh
docker build -t oss-contributor-runner:0.1 src/oss_contributor/runtime
OSS_TEST_DOCKER=1 uv run pytest tests/test_docker.py -q
```

An optional live test runs real Codex and Docker but simulates GitHub writes. It consumes the existing account's model usage and can fail when that account lacks model access:

```sh
OSS_TEST_LIVE_CODEX=1 OSS_TEST_CODEX_MODEL=your-available-model \
  uv run pytest tests/test_live_codex.py -q
```

Do not enable the live test in CI or upload credentials to make it pass. Do not weaken a test, remove a required check, broaden an allowlist, or silently switch authentication to work around a failure.

## Pull requests

Use the [workflow archive](docs/workflows/README.md) and [change-record template](docs/workflows/templates/change-record.md) to record each coherent change batch. Link its record ID in the PR and update both change indexes. Workflow/state/authority/budget/quality-gate/default-selection changes get a new workflow baseline; other fixes reference the current version. Preserve historical baselines and distinguish workflow numbering from package releases. Record actual verification and rollback requirements, including missing evidence.

Explain the concrete problem, resulting behavior, and commands actually run. Add meaningful regression coverage for changes to state transitions, publication recovery and isolation boundaries. Documentation-only changes need only the relevant link/example checks.

AI-assisted contributions are welcome when disclosed, reviewed by the submitter, and supported by actual validation. Submitting invented test results, unread generated patches, or repetitive speculative changes is not acceptable. Review and merge decisions remain with the maintainers.

Never use a third-party repository as a publishing test fixture. GitHub write tests must target a repository you control and explicitly authorize. Mock tests cover normal development; external upstream contributions require a separate reviewed allowlist entry and must respect upstream policies.

## Data and security

Do not commit local configuration, state databases, raw session logs, auth caches, private repository archives or tokens. Use synthetic fixtures or explicitly sanitized public evidence. Report vulnerabilities through [SECURITY.md](SECURITY.md).

Be respectful to maintainers and other contributors. A maintainer's request to stop ends automated work. Do not pressure maintainers to merge or approve a workflow.
