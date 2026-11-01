# Changelog

## Unreleased

- Record local dependency-complete commit batches and actual Git locators for Workflow v0.3–v0.6, verify exported staged snapshots, and distinguish current checks from prior full-suite/Docker evidence. No runtime contract or package release change. See [CHG-20261102-001](docs/workflows/changes/2026-11-02-001-local-commits.md).

- Keep each budget reservation's local day fixed through usage insertion/update, model accounting, native settlement/refunds and publication metadata, including midnight transitions; refresh native available time after environment preparation. This repairs the existing daily-limit contract without changing limits or runtime scope. See [the November 2 audit](docs/workflows/changes/2026-11-01-006-integrated-audit.md).

- Add Workflow v0.6 memory lifecycle: pinned expiry, supersession, scoped invalidation/deletion/audit, no-content tombstones, active-reference protection and safe derived-report invalidation. Add a frozen synthetic controller corpus with Docker-only candidate evaluation, explicit denominators and paired report checks. Consolidate cross-module validation and document remaining native-platform/model-evidence limits. See [CHG-20261101-004](docs/workflows/changes/2026-11-01-004-memory-governance.md), [CHG-20261101-005](docs/workflows/changes/2026-11-01-005-frozen-evaluation.md) and [CHG-20261101-006](docs/workflows/changes/2026-11-01-006-integrated-audit.md).

- Add Workflow v0.5 finalization time within the existing attempt deadline, cumulative observed-token accounting with unknown/partial coverage, pinned operator-supplied price estimates and per-call prompt/schema/model provenance. Preserve native stop confirmation and reject unverifiable configured usage limits. See [CHG-20261101-003](docs/workflows/changes/2026-11-01-003-finalization-accounting-provenance.md).

- Apply Workflow v0.4 error classification and bounded safe-read retries: pin retry policies, cap attempts/time/shared retry allowance, count monitored retries against tool guards, preserve cancellation and prohibit blind replay of mutations, commands, installs, verification or model sessions. Pause on authentication blockers and escalate known permanent publication failures while retaining verified artifacts. See [CHG-20261101-002](docs/workflows/changes/2026-11-01-002-classified-bounded-retries.md).

- Apply Workflow v0.3 tool guards: cap total attempted calls, repeated canonical requests per workspace, and consecutive calls without novel observed evidence; persist native WorkerTools activity and cancellation without releasing reservations before confirmed stop. Standalone limits abort model sessions and span model stages. Preserve legacy-job compatibility, independent verification and authorization; native host-tool bypass remains a documented limitation. See [CHG-20261101-001](docs/workflows/changes/2026-11-01-001-tool-activity-guards.md).

- Apply Workflow v0.2 controls: bind independent validation to patch/configuration inputs; revalidate legacy or stale source evidence; bound publication recovery with persistent attempts, a time window and capped backoff; persist native cancellation across pause/resume; preserve terminal states around publication. See [CHG-20261031-002](docs/workflows/changes/2026-10-31-002-workflow-controls.md). The full phase-queue design remains a documented proposal.

- Establish Workflow v0.1 as a separate, archived process baseline with diagrams, stage/evidence mapping, default rules, metric definitions, source hashes and a Git locator. Add per-batch change records, an index, a template and contributor/agent documentation conventions.

- Add bug-first discovery with an unlabelled fallback, explainable candidate ranking, configurable issue triage cooldown and assignment deferral. Both native and standalone scheduling retain full eligibility checks and unchanged budgets.
- Record remote PR outcomes independently of review text, preserving GitHub timestamps. Use only a capped prior-merge ordering bonus; do not infer rejection from closure or claim calibrated merge probabilities.
- Add a scope-aware offline HTML dashboard with escaped remote text, no external assets or write controls, current-head CI distinctions, queue reasons, feedback, budgets and job state.
- Add local runner preparation and cached, immutable-image isolation probes before native reservations. Independent validation continues using fresh target environments; no dependency/workspace snapshots are reused.

- Add durable versioned feedback memory, conservative cause hypotheses and preventive checklists for later native/standalone repairs. Archive historical maintainer reviews and late closed-PR feedback without authorizing revisions; expose read-only backfill and offline reports with evidence and local processing outcomes.
- Add scoped GitHub notification ingestion with conditional polling, server interval/rate-limit handling, atomic feed/inbox persistence, immediate state triage, priority review queue and periodic PR sweeps. The observer never invokes models or publishes.
- Add foreground notification monitoring, optional macOS LaunchAgent installation/removal and separately deduplicated local alerts; expose listener health in local desktop status.
- Generate concise PR explanations and validation summaries while retaining command transcripts in local evidence and disclosing Codex assistance.
- Separate description-only feedback from code revisions in native and standalone workflows. Explicit wording requests can update an existing verified PR body without source changes or another commit; ambiguous feedback can escalate for attention.
- Persist description-update intent for recovery, guard observed external edits/head changes/closure and new feedback, and reject CI feedback or file changes in description-only handoffs. Source revisions retain independent validation.

## 0.3.0a1 — 2026-10-30

Automatic operation with enforced runtime scope and confirmed native-chat lifecycle.

- Persist fixed-PR monitor identities or allowlisted autopilot scope. Scope applies to chat tickets, observation, publication recovery, notifications and standalone publication entry points; missing a CLI flag cannot expand a fixed monitor.
- Retain cancelling chats and their model reservations/serial slot until the coordinator records actual native termination. Add `desktop stopped`, late creation reconciliation and conservative 0.2 failed-lease migration; pause/resume cannot bypass confirmation.
- Add read-only `desktop status` for local scope, active jobs, budget/reset time and blockers, with no model/network activity.
- Safely upgrade vendor-managed skills with `install-skill --upgrade`, preserving configuration/extra files and refusing local modifications or linked paths. Interrupted resource upgrades can resume.
- Scope-aware startup prompts, explicit autopilot activation, scoped completion signals and idempotent notification acknowledgement.

Native termination acknowledgement is supplied by the coordinating App session, not independently attested by Python. Long-running unattended App operation and a real remote-CI revision round remain validation goals.

## 0.2.0a1 — 2026-10-30

Desktop-first autonomous contribution: configure once, automatically select issues, create a fresh native repair chat, independently verify and publish, and follow feedback.

- Bundled/installable `oss-contributor` Codex skill and one-time native heartbeat startup instruction; standalone App Server is optional.
- `desktop tick/attach/checkpoint/finish/fail/ack/setup/install-skill` controller interfaces, automatic allowlisted selection and monitor-only operation.
- SQLite native job leases, atomic attempt/model reservations, thread/cursor recovery, expired-job pause, strict assigned-path JSON handoffs and once-only budget settlement.
- Independent clean-container handoff validation and publication recovery without CLI model calls; immutable original regressions and previous-Head checks for revisions.
- Durable notification outbox, acknowledgement handling, quiet pending/approval waits, bounded fresh-chat revisions and no blind duplicate dispatch.
- New controller regression coverage and real Docker native-handoff integration coverage with simulated GitHub publication.

Limits remain explicit: native chats retain host tools; full unattended App longevity and a real remote-CI revision round are not established by fixture tests.

## 0.1.0a1 — 2026-10-30

First experimental public release.

- Typer/Pydantic CLI, empty default allowlist, GitHub discovery and conservative issue screening.
- SQLite task/event/quota persistence, JSONL mirror and interruption-aware publication.
- Codex CLI 0.140.0 App Server adapter with restricted dynamic tools and protocol compatibility checks.
- Non-root Docker runner, file bridge, PyPI-only installation proxy and offline repair/testing.
- Original-baseline and regression red/green verification in an independent clean container; patch limits and protected files.
- GitHub fork/branch/PR publication and current-head CI/maintainer feedback observation, bounded revisions and no automatic merging/comments.
- Mock/local fixture tests, opt-in Docker/live-model tests and package smoke checks.
- Documented native desktop chat workflow and three public submitted PR case studies.

Known limitations: live standalone model access remains blocked in the development account; desktop handoff/scheduling is a documented App-assisted workflow rather than a CLI integration; no independent security audit. See `docs/validation.md`.
