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
| `selection.py`, `learning.py` | Explainable queue ordering, triage cooldown, versioned feedback evidence and observed PR outcomes; never new authorization or self-certified validation |
| `environments.py`, `dashboard.py` | Local image readiness probes and offline scope-aware operational snapshots; no workspace snapshot reuse or web service |
| `tool_guard.py`, `worker_tools.py`, `errors.py`, `retries.py` | Persistent monitored-call limits, typed failures and bounded safe-read retries; no blind mutation replay |
| `accounting.py` | Pinned metering policy, cumulative observed usage, unknown coverage/prices, exact-input/model provenance and within-deadline finalization reserve |
| `evaluation.py`, `resources/` | Frozen synthetic controller cases, Docker-only candidate validation and environment-matched offline comparison; no production authority |

## State and publication

SQLite is authoritative; JSONL is a readable mirror rather than a replay journal. Each issue has a unique `(repository, issue)` task. Attempts, publication reservations and model activity use the configured local date, defaulting to Singapore. A state-directory lock prevents competing controller processes. Resume does not clear quotas or retry failed work indiscriminately.

Reservation selects that local date once; usage insertion/update, call ledger and refund retain it across midnight. Native dispatch refreshes available time after environment preparation and records the same budget day atomically with its job. No billing-day allocation is inferred. New attempts reserve up to 300 seconds (at most a third of total time) for independent finalization inside the existing deadline; the worker receives the earlier deadline. Cancellation still needs actual native termination before slot/refund settlement.

Memory records pin TTL on first observation, retain version order through supersession and deletion tombstones, and exclude expired/invalidated records from future context. Scoped operator deletion invalidates known exports before committing; it is logical learning-payload deletion, not secure disk erasure or removal from historical chats/evidence. See [Workflow v0.6](workflows/v0.6/README.md). The [fixed evaluation](evaluation.md) measures controller decisions on synthetic inputs, not actual model or upstream success.

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

Workflow v0.4 classifies trusted error types and HTTP status/headers. A single retry owner bounds safe reads by per-call attempts, attempt timeout, elapsed window, jitter and a shared extra-attempt budget. Native policy/allowance is ticket-persisted; standalone tool retries share the attempt guard. Controller GitHub read allowance is adapter-instance scoped. Commands, writes, installation, validation and model sessions are never blindly replayed. Known permanent publication errors retain artifacts and escalate; uncertain external effects keep the existing bounded remote reconciliation. Validation reports retain classification separately from their unchanged proof rules. See [v0.4](workflows/v0.4/README.md) for compatibility and limits.

Workflow v0.3 adds a shared tool-activity guard. Standalone model callbacks are intercepted before execution; a fatal limit escapes the normal tool-error response and terminates the session. One attempt shares its counters across model stages. Native workers use the ticket-bound `WorkerTools` facade, with controller-pinned limits and hash-only activity persisted in the existing job JSON before and after each operation. Repository access comes from the pinned GitHub adapter and DockerSandbox, not worker-supplied configuration. Guard limits request confirmed cancellation, retaining reservations and the serial slot; independent verification remains unchanged. See [v0.3](workflows/v0.3/README.md) for exact counting, progress heuristics, compatibility and bypass limitations.

A persisted monitor freezes task/repository/issue/PR identities. The controller and shared Engine enforce it before dispatch, observation, recovery and publication; scoped notices/acknowledgements do not affect other tasks. Full autopilot is a separate explicit runtime scope and still cannot extend the repository allowlist.

Native cancellation remains an active job and holds the model reservation/serial slot until the coordinating App confirms termination. Handoff collection also requires that acknowledgement. Budget settlement is idempotent, including legacy failed-lease reconciliation. This is an explicit coordinator protocol, not cryptographic attestation of an App process.
