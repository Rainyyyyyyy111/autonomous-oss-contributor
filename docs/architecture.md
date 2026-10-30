# Architecture

The Python controller owns authorization, budgets, state transitions, verification and publication. The model supplies a structured assessment, one regression test, and source changes. It cannot authorize new repositories or replace the controller's validation report.

| Module | Responsibility |
| --- | --- |
| `cli.py`, `config.py` | Typer commands, validated TOML, empty default allowlist |
| `store.py` | SQLite tasks/usage/events, worker lock, processed-feedback IDs, JSONL mirror |
| `github.py` | Host `gh` API adapter, public evidence, fork/commit/PR recovery, current-head CI/review |
| `codex.py` | Version-pinned App Server adapter, isolated config, structured output, restricted dynamic tools |
| `desktop.py`, `installation.py`, bundled skill | Primary native-chat autopilot: automatic selection, durable leases/cursors, strict handoffs, independent verification/publication and notification delivery |
| `sandbox.py`, `runtime/` | Docker lifecycle, PyPI proxy, offline execution, bounded file bridge |
| `safety.py` | Paths, archives, contribution evidence heuristics, patch and regression checks |
| `engine.py` | Baseline → assessment → failing regression → repair → clean verification → publication → observation |

## State and publication

SQLite is authoritative; JSONL is a readable mirror rather than a replay journal. Each issue has a unique `(repository, issue)` task. Attempts, publication reservations and model activity use the configured local date, defaulting to Singapore. A state-directory lock prevents competing controller processes. Resume does not clear quotas or retry failed work indiscriminately.

A task stores the upstream SHA, changed-file hashes, patch, actual validation commands and results, publication intent, deterministic branch, PR/Head and pending feedback IDs. Publication re-reads issue conflicts and upstream. Unrelated upstream changes require fresh validation; changed patch files stop publication for diagnosis. GitHub git-data operations compare known commits/trees and do not force-update branches. Review IDs are marked processed only after the corresponding publication succeeds.

Transient rate limits use server-provided retry information. Publication interruption is reconciled against the remote branch/PR. An unexpected external commit must cause attention, not be overwritten. Current implementation is deliberately single-user and serial; distributed coordination is out of scope.

## Model boundary

The standalone adapter starts host Codex with a temporary configuration and no target repository as its host working directory. It sends `environments: []` on thread/turn creation and exposes controller-supplied dynamic tools. Read-only stages get list/read/search; repair additionally gets source apply and container execution. Unexpected native host execution, browser, MCP or collaboration events abort the session. Credentials stay on the host.

The standalone `run` CLI uses this optional adapter. The primary desktop autopilot uses the bundled skill and App-native tools, with the Python `desktop` command group owning durable selection, reservations, handoffs and publication. Python **does not** invoke a public desktop RPC API. This separation and restart protocol are described in [desktop-workflow.md](desktop-workflow.md).

## Quality boundary

1. Install the unchanged upstream and pass configured baseline tests/checks.
2. Add a new regression file. Require pytest exit 1 and JUnit assertion failures on original source, without collection errors or skips in that regression.
3. Preserve that regression while repairing Python source. Protect existing tests and configuration.
4. In a new container, repeat installation/baseline, original regression failure, fixed regression success, and every configured full test/check command. Reject unexpected file mutation.
5. Apply file/line limits and fresh publication evidence. Only the controller's actual report permits publication.

Full-suite commands currently use their actual exit codes; they do not universally parse each framework's skipped-test reports. Operators must configure complete commands and review test relevance. See [SECURITY.md](../SECURITY.md) for the limits of generated tests and untrusted harnesses.

## Runtime authorization and cancellation

A persisted monitor freezes task/repository/issue/PR identities. The controller and shared Engine enforce it before dispatch, observation, recovery and publication; scoped notices/acknowledgements do not affect other tasks. Full autopilot is a separate explicit runtime scope and still cannot extend the repository allowlist.

Native cancellation remains an active job and holds the model reservation/serial slot until the coordinating App confirms termination. Handoff collection also requires that acknowledgement. Budget settlement is idempotent, including legacy failed-lease reconciliation. This is an explicit coordinator protocol, not cryptographic attestation of an App process.
