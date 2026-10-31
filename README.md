# Autonomous OSS Contributor

[中文说明](README.zh-CN.md) · [Workflow v0.1 & change archive](docs/workflows/README.md) · [Architecture](docs/architecture.md) · [Desktop workflow](docs/desktop-workflow.md) · [Contributing](CONTRIBUTING.md)

Configure once. The agent discovers Python projects, selects suitable bugs, starts a fresh Codex chat for each repair, independently verifies patches in Docker, submits PRs, and follows CI/review within your allowlist and budgets. Users do not pick each issue or relay the workflow.

**Experimental Alpha — `0.3.0a1`.** Native Codex App chat coordination is the primary path: an installable skill + heartbeat drive a durable Python controller with job leases, confirmed termination, persisted execution scope, restart recovery, strict handoffs and automatic verification/publication. Three real upstream PRs previously used desktop repair chats with independent coordinating review. The packaged scheduler has fixture/adapter and Docker coverage; unattended longevity and a real remote-CI revision round remain unverified. See [validation scope](docs/validation.md).

The optional standalone App Server backend remains available; live model turns returned `model_not_found` in the development account. That backend is not required by the desktop autopilot.

The goal is a useful, verifiable PR. Acceptance and merging remain the upstream maintainer's decision.

## Feedback learning memory

Automatic selection now ranks bounded, bug-first issue search evidence, filling remaining slots from issues without the bug label (`discovery.include_unlabelled = false` disables this fallback). Reproducer blocks, expected/actual behavior, errors and design scope produce inspectable ordering signals. New issues receive 24 hours of triage time by default (`min_issue_age_hours`); assigned search snapshots wait for discovery refresh. Full policy/ownership checks still run before work and publication. Legacy tasks without creation metadata retain the existing eligibility path.

Observed PR states and remote creation/closure/merge timestamps are stored even without feedback. Prior observed merges give a small capped repository ordering bonus; closure alone is never a negative training label. Scores are heuristics, not merge probabilities.

```sh
# Offline scope-aware HTML snapshot: tasks, queue reasons, budgets, chats and evidence
uv run oss-contributor dashboard --config PATH
# Prepare configured local images and probe isolation with fresh empty containers
uv run oss-contributor environments prepare --config PATH
```

The dashboard is `state_dir/dashboard.html`; rerun to refresh, with no web service or external assets. Before native dispatch, the controller checks the configured image and probes isolation; successful metadata is reused for at most an hour and invalidated when the image ID changes. Missing bundled runners can be built with `--build-runner`. No target workspaces, credentials or repaired environment snapshots are reused. Target dependencies still install fresh for independent validation; dependency caching remains future work.

Existing PR monitoring automatically archives maintainer feedback and readable current-head CI failures without additional model calls. SQLite retains versioned evidence, the repair/files/validation state at detection, conservative cause hypotheses, and preventive checks. Inspectable mirrors live at `state_dir/learning/report.md` and `feedback.json`. Later native repair jobs and standalone model calls receive relevant checks from the controller's built-in lesson catalog.

```sh
# Read-only backfill of known PRs in the current allowlist and persisted scope, including closed PRs
uv run oss-contributor learning refresh --config PATH
# Offline statistics and export
uv run oss-contributor learning report --config PATH
```

Old-head reviews are historical evidence, never actionable current-head feedback. The notification listener's periodic sweep also collects late feedback on closed PRs without reopening tasks. Closure alone is not negative feedback; ambiguous quality complaints do not establish a code defect. Raw remote prose never becomes policy. Communication and CI checks transfer across repositories; specific code and stop concerns remain repository-specific. Addressed counts mean locally processed, not maintainer acceptance or causal proof of improvement. This mechanism does not rewrite controller code, skills, authorization or verification gates.

## Workflow

```mermaid
flowchart LR
    A[Discover Python projects] --> B[Allowlist and issue evidence]
    B --> C[Passing original baseline]
    C --> D[Regression fails on original source]
    D --> E[Codex repairs source in Docker]
    E --> F[Independent clean-container verification]
    F --> G[Recheck conflicts and upstream SHA]
    G --> H[Fork, branch, PR]
    H --> I[Observe current-head CI and maintainer review]
    I --> J[Bounded revision and verification]
    J --> G
```

- Discovery is global; **publication is allowlist-only**. The default allowlist is empty.
- Reject claimed issues, known competing fixes, blocking contribution policies, unmet CLAs, unclear reproducers, and failing baselines. Public signals are incomplete; one-time policy review is required per repository.
- Require the same new regression test to fail with an assertion on original source and pass after the patch. Independently repeat it and the configured full tests/checks in a fresh container.
- Default patch limit: **5 files / 200 added + deleted lines**, including the regression test. Existing tests, CI, dependency and credential files are protected.
- Persist task state, publication intent and remote Head. Reconcile interruptions without force-pushing or creating duplicate PRs.
- Follow only current-head CI and maintainer feedback. Waiting for workflow approval does not trigger a repair. No automatic comments, review replies, CI approval or merging.
- Optional GitHub notification listener: conditional polling (default 60 seconds), scoped immediate triage, durable priority queue and macOS alerts. See [notification monitoring](docs/desktop-workflow.md#github-notification-listener).

These checks reduce mistakes; they are not a proof that generated code is correct or that an adversarial repository cannot forge its own test results. Read [SECURITY.md](SECURITY.md).

## Install and inspect

Prerequisites: macOS or Linux, Python **3.12+**, [uv](https://docs.astral.sh/uv/), authenticated [GitHub CLI](https://cli.github.com/), running Docker, and Codex App with native chat/heartbeat tools for the primary desktop path. **Codex CLI `0.140.0`** is needed only for the optional standalone adapter. The default runner uses Python 3.12. Never send credentials into target containers.

```sh
git clone https://github.com/Rainyyyyyyy111/autonomous-oss-contributor.git
cd autonomous-oss-contributor
uv sync --locked --extra dev
uv run oss-contributor --help
uv run oss-contributor init
uv run oss-contributor doctor --desktop --build-runner
uv run oss-contributor desktop install-skill
uv run oss-contributor status
```

Log in to GitHub once with `gh auth login` and use the existing Codex App login. The optional standalone adapter uses `codex login` and requires a file-backed Codex auth cache; a Keychain-only login is insufficient. It references that cache on the host. Protocol fields are experimental and the CLI version must match the configured pin.

Default configuration: `~/.config/oss-contributor/config.toml`. Override it per command with `--config /path/to/config.toml`. See [config.example.toml](config.example.toml).

`discover` reads GitHub and stores candidates; it never publishes:

```sh
uv run oss-contributor discover
```

## Authorize one test repository

Start with a repository you control. Read its contribution policy and configure its actual installation, test and lint/type commands. An entry authorizes publication to that upstream; discovered repositories are never added automatically.

```toml
[[repositories]]
name = "your-account/your-python-test-repo"
image = "oss-contributor-runner:0.1"
install = [["python", "-m", "pip", "install", "-e", ".[test]"]]
tests = [["python", "-m", "pytest", "-q"]]
checks = [] # Add the project's real lint/type commands when applicable.
contribution_policy_reviewed = true
cla_completed = false
```

Install dependencies through PyPI only. Projects requiring apt, private indexes, Git dependencies, external services or another Python version need a reviewed custom image or must be skipped. Custom images must preserve the trusted bridge, non-root user and `/opt/venv` conventions. Images and local configuration are trusted operator inputs.

## Start automatic contribution

After that one-time setup, ask the Codex App:

> Use $oss-contributor to start automatic issue discovery, Docker-verified repair and PR follow-up within my configured allowlist. Create a new native chat for each repair or revision.

The skill schedules the native heartbeat, automatically selects issues, creates/resumes repair chats and feeds their structured results to independent verification/publication. Normal cycles need no user interaction. Only account/environment/scope blockers need attention. The default empty allowlist publishes nothing.

```sh
uv run oss-contributor status
uv run oss-contributor pause
uv run oss-contributor resume
```

[Desktop autopilot and recovery](docs/desktop-workflow.md) documents the packaged `desktop tick/attach/checkpoint/finish` interfaces. Native tools come from the App session; this is not a standalone public desktop API. The App and machine must stay running/awake.

For the optional standalone adapter, first verify accessible model execution and a complete run on your own repository, then use `run --once` or `run --daemon`. A root-level `model = "your-available-model"` selects an available model. `doctor --probe-codex` checks protocol only, not model access. No authentication fallback is attempted.

## Runtime scope and upgrades

The primary autopilot automatically chooses issues inside the repository allowlist. `desktop scope --autopilot` activates that runtime scope when the operator asks to start it. For follow-up limited to existing PRs, `desktop scope --monitor --task-id 1 --task-id 2` freezes their repository/issue/PR identities. A fixed monitor cannot discover or publish another task even if a heartbeat omits `--monitor-only`; new database rows do not extend its scope. Scope changes require all native chats to be stopped and settled.

```sh
uv run oss-contributor desktop status # Local health, budgets and blockers; no remote/model calls.
uv run oss-contributor desktop install-skill --upgrade
```

A managed skill upgrade preserves its configured path and extra operator files, refuses customized vendor resources or links, and can resume after interruption. Native failures/timeouts keep the serial slot until App wait confirms termination and the coordinator records `desktop stopped`. Clearing pause cannot release an unconfirmed chat. These coordinator commands run automatically through the skill; users do not repeat them per issue.

## Limits and recovery

| Default | Value |
| --- | --- |
| Discovery | Hourly, Python, ≥500 stars, pushed in the last 30 days, ≤50 repositories |
| Issue scans | Up to 10 open `bug`-labelled issues per allowlisted repository |
| Work | Serial; 3 issue attempts / day; 1 new PR / day |
| Open PRs | 1 per repository; 3 globally |
| Model budget | 30 minutes per task; 120 minutes / day |
| Revisions | At most 2 per PR, each requiring full verification |
| Observation | Every 10 minutes while the worker runs |
| Accounting | `Asia/Singapore` date, configurable |

State lives in `~/.local/share/oss-contributor/`: SQLite is authoritative; JSONL is an inspectable event mirror. Keep it across restarts. Quotas and processed events do not reset on `resume`. `pause` prevents new model calls and publication; an already-running bounded call may finish. Desktop scheduling uses an App heartbeat; standalone CLI polling uses a foreground process. Neither survives Mac sleep or a stopped App/process. Native job leases and notification delivery survive coordinator restarts.

## Execution boundary

Target code runs as UID 10001 in Docker, with a read-only root filesystem, dropped capabilities, no host bind mounts, no credentials or Docker socket, and default limits of 2 CPUs / 4 GiB / 128 processes. Source is transferred through validated archive bytes and a file bridge. Traversal, links, special files and oversized inputs are rejected (4 MiB per file, 40 MB expanded input archive).

Dependency installation uses a separate CONNECT proxy restricted to `pypi.org` and `files.pythonhosted.org`; repair and testing have no Docker network connections. PyPI dependencies still execute code. Docker is not an independent VM security guarantee.

The standalone Codex adapter stays on the host with `environments: []`, isolated configuration and a restricted tool bridge. Native desktop chats have their own host tools and are governed by workflow instructions; **they do not provide the same enforced tool boundary**. See [architecture](docs/architecture.md) and [security limitations](SECURITY.md).

## Evidence from the first three contributions

These are submitted PRs, **not a claim of maintainer acceptance or a general success rate**. Each used a separate desktop repair chat plus independent parent validation. No upstream source, private local state or auth files are bundled here.

| Issue → PR | Original regression | Fixed full suite |
| --- | --- | --- |
| [SCPIpy #46 → #52](https://github.com/0z0sk0/scpipy/pull/52): reject empty parameters | 10 assertion failures / 7 controls pass | 407 pass |
| [ConfigObj #94 → #291](https://github.com/DiffSK/configobj/pull/291): read non-finite floats in `unrepr` mode | 5 assertion failures / 11 controls pass | 95 pass + both doctest entry points |
| [python-jsonpath-rw #53 → #104](https://github.com/kennknowles/python-jsonpath-rw/pull/104): preserve literal field names | 12 assertion failures / 8 controls pass | 61 pass |

[Validation details and limits](docs/validation.md) record the tested commits and initial CI observations. All three were submitted on 2026-10-30; later status should be read from GitHub.

## Develop

```sh
uv sync --locked --extra dev
uv run ruff check src tests
uv run mypy src/oss_contributor
uv run pytest -q
uv build
uv run python scripts/smoke_wheel.py
# Real container isolation, network boundary and red/green verification:
uv run oss-contributor init --config /tmp/oss-contributor-dev.toml
uv run oss-contributor doctor --desktop --config /tmp/oss-contributor-dev.toml --build-runner
OSS_TEST_DOCKER=1 uv run pytest tests/test_docker.py -q
```

Runner building only: `docker build -t oss-contributor-runner:0.1 src/oss_contributor/runtime` (does not require GitHub or Codex login). An optional live Codex test consumes account usage and uses fake GitHub writes; it is not part of CI. See [CONTRIBUTING.md](CONTRIBUTING.md).

MIT licensed. Not affiliated with or endorsed by OpenAI or GitHub.
