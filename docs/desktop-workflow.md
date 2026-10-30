# Native desktop autopilot

**Configure once; the agent selects issues and runs the contribution loop automatically.** Users do not supply each issue, relay prompts, execute tests or submit individual PRs.

The Python controller now exposes a durable native-chat dispatch/handoff interface, bundled as the `oss-contributor` Codex skill. The Codex App supplies native chat and heartbeat tools. Python does not call an invented public desktop RPC API. The App and machine must remain running/awake.

## One-time setup

```sh
uv sync --locked --extra dev
uv run oss-contributor init
# Set authorized repositories, actual install/full-test/check commands and budgets once.
uv run oss-contributor doctor --desktop --build-runner
uv run oss-contributor desktop install-skill
```

In Codex App, start the installed skill:

> Use $oss-contributor to start automatic issue discovery, Docker-verified repair and PR follow-up within my configured allowlist. Create a new native chat for every repair or revision.

If the App hasn't discovered the new skill, reload its skill/session context. `desktop setup --config PATH` provides a portable startup prompt and the required tool names. The skill uses the App automation tool to schedule a 10-minute heartbeat in the coordinating chat, reusing an existing matching automation. Discovery remains hourly by default. Installation does not silently start a scheduler or broaden an existing fixed-PR monitor. A full start explicitly binds `desktop scope --autopilot`; a fixed follow-up binds `desktop scope --monitor --task-id ID ...` once. Repository/issue/PR identities are persisted and enforced on recovery, publication, notifications and chat operations, even without a monitor flag.

A default empty allowlist authorizes no publication. The initial policy/configuration step is an operator decision; it does not repeat for every issue. Discovered repositories are candidates, not permission.

## Durable cycle

| Interface | Responsibility |
| --- | --- |
| `desktop tick` | Reconcile verified publications, observe current-head PR evidence, automatically select an eligible issue or revision, reserve budgets and persist one dispatch |
| `desktop attach TICKET THREAD_ID` | Bind the returned native chat to that dispatch; refuse another chat binding |
| `desktop checkpoint TICKET CURSOR` | Persist compact wait progress so a restart resumes the same chat |
| `desktop stopped TICKET --thread-id THREAD_ID` | Record the coordinator's actual native terminal observation before collecting a handoff or settling cancellation |
| `desktop finish TICKET HANDOFF_PATH` | Validate the worker schema/identity, re-read policy/conflicts, independently run clean-container regression/full-suite checks and automatically publish |
| `desktop fail TICKET REASON [--quota]` | Preserve a concrete blocker, settle usage, pause on quota exhaustion; no blind replacement chat |
| `desktop ack IDS...` | Acknowledge durable notices after delivery |
| `desktop tick --monitor-only` | Follow existing PRs without selecting new issues; use persisted `desktop scope --monitor --task-id ID ...` for a fixed, frozen set |

All commands accept `--config PATH`. The skill runs them for the user; they are documented for debugging and integration, not a manual per-issue checklist.

SQLite stores leases, thread IDs, cursors, daily model reservations and notification acknowledgements alongside existing tasks. A dispatch carries a fixed base SHA, reviewed repository commands, regression path, prior Head/patch for revisions and a deadline. Repeated ticks return the same pending/attached job. Worker results cannot choose a different repository, commands, budget or validation report. Handoffs must be regular files at the assigned artifact path; arbitrary paths and links are refused.

Job files live under the configured state's `jobs/TICKET/`. The worker returns a strict `Handoff`: ticket, fixed/description/skip/needs_attention/quota outcome, structured assessment, source changes and diagnosis. For source changes, the coordinator reviews that patch, and the controller re-downloads the fixed base and independently proves original regression failure, fixed regression success and complete configured checks. A claimed pass is not accepted as evidence.

For revisions, classify feedback before editing. Use `description` only for explicit requests about PR-description wording, with empty `changes` and a concise revised `Repair`. The controller reuses the existing verified patch and report, preserves the issue link/template/AI disclosure, and PATCHes only the existing PR body. No commit, fork or new PR is created. Failed CI or mixed CI/review feedback cannot use this path. Broad complaints without a clear target return `needs_attention` instead of triggering cosmetic source changes. Classification is a model judgement reviewed by the coordinator, not a deterministic guarantee of understanding a maintainer's intent.

Description publication persists its intent before writing and can recover a lost PATCH response without creating a source revision. It rechecks scope, policy, revision budget, PR state/head, new feedback and the previous description. An observed human edit blocks the update. GitHub's body-update endpoint has no expected-head/body parameter, so these are preflight checks rather than an atomic compare-and-swap across concurrent GitHub writers. Closed PRs are never reopened by this path.

## GitHub notification listener

`notifications poll --config PATH` performs a bounded observer pass. `notifications watch --config PATH` repeats it in the foreground without invoking models or holding the controller lock while sleeping. On macOS, `notifications service --install --config PATH` installs one LaunchAgent per state directory, running a quiet poll at the configured interval. Use `--uninstall` to remove it; it can be removed even after notification monitoring is disabled. Installation never changes the native heartbeat or starts another coordinator. The existing configured GitHub CLI login is reused.

Defaults in `[notifications]` are `enabled=true`, `desktop_alerts=true`, `poll_seconds=60`, `lookback_days=7`, `max_pages=10`. GitHub's [notifications API](https://docs.github.com/en/rest/activity/notifications) requires supported account authentication and the `notifications` or `repo` scope. It supplies `Last-Modified`, conditional `304` responses and `X-Poll-Interval`; the listener observes the larger server interval and shared rate-limit backoff. It reads `all=true`, so reading notifications elsewhere does not hide updates, and never marks threads read. Pagination is bounded; partial fetches do not advance the feed cursor. A two-second overlap and durable version fingerprints prevent duplicate processing. Conditional validators are cleared when the incremental query changes.

Only known task identities in the persisted scope and repository allowlist are matched. Notification titles, reasons and comment URLs are hints rather than proof of a failure: the listener fetches current PR/review/CI state from the existing trusted adapter. It does not follow arbitrary subject/comment URLs. Related issue and check-suite notifications can refresh an existing PR, but cannot create new tasks or widen authorization. Full PR sweeps at `discovery.watch_seconds` (default 600) catch state changes that do not produce an inbox notification.

The listener records closed/merged/stopped/external-head/CI-wait outcomes immediately. Unprocessed review and readable failed-CI events enter a priority queue; the next native tick consumes that queue without waiting for its periodic sweep. A broad AI-slop complaint without an explicit description target is conservatively escalated for attention rather than turned into a cosmetic code revision. Explicit description feedback still requires normal coordinator review. A changed notification is not treated as proof that feedback has been addressed.

New actionable feedback during an active revision requests the normal confirmed-cancellation protocol before reassessment. Handoff acceptance and publication recheck for later feedback, including feedback arriving during independent validation. Simple acknowledgements remain ignorable. The scheduled poll treats controller-lock contention as a quiet deferred attempt and does not bypass the lock.

The feed cursor and pending inbox commit together before PR observation, so a failed observation survives retries and restarts. macOS alerts have their own delivery records and leave the coordinator's notification acknowledgement untouched. They are at-least-once: a crash after showing an alert but before recording delivery can repeat it. System notification permissions/focus settings control visibility; failed local delivery does not discard the durable triage result. `desktop status` exposes poll timestamps, the latest error and queued tasks without network calls.

This is near-real-time observation, not a webhook or an always-running model session. Default detection is approximately one minute plus API/lock delays; machine sleep, throttling and outages increase it. The native heartbeat still defaults to ten minutes, so complex model judgement waits for that coordinator and its budgets. The listener itself can run while the App is closed, but cannot create native chats until the App coordinator runs. Repository-owner webhook deployment would require separate hosting/permissions and is not configured here.

## Recovery and stopping

- Before creating a chat, reconcile the exact deterministic title/ticket against native App history. If creation succeeded before binding, attach that chat. Ambiguous/lost creation results need attention; never retry chat creation blindly.
- One chat is active at a time. An expired or failed lease retains the serial slot in `cancelling` until its old chat is confirmed stopped; pause/resume cannot bypass this. Pending creation must be reconciled, then an existing chat can be bound for cancellation even after expiry. Only confirmed absence permits `desktop stopped TICKET --not-created`. Failed 0.2 leases lacking a native terminal record are conservatively reconciled without a second refund. Interrupted, unverified work cannot publish. Unused reserved model time is refunded once; expiration conservatively consumes the reserved budget.
- Verified publication interruptions reconcile against the remote PR/branch, without another model turn or duplicate PR. The known previous Head is preserved; external commits stop revision.
- Pending/approval-wait CI does not cause repairs. Only current-head readable failed CI or unprocessed maintainer feedback can select a revision, with at most two attempts and the original regression unchanged. Simple acknowledgements are ignored.
- Durable notices cover created/updated PRs, terminal decisions and actionable blockers. Healthy/pending cycles stay quiet. Delivery is at least once: a crash between showing and acknowledging a notice may repeat it.
- Pause, account exhaustion, policy restrictions, ownership conflicts and environment failure cannot trigger authentication fallback or widened authorization. No comments, review replies, merges or CI approval.

Native desktop chats retain host tools. Their Docker-only execution is a workflow instruction, not an enforced removal of host capabilities. See [SECURITY.md](../SECURITY.md). The optional standalone App Server adapter has a separate restricted-tool design and remains independently compatibility-limited.

## Validation status

The first three real upstream contributions used native chats and independent coordinator review before this interface was packaged. Tests now exercise the packaged scheduler, fresh-chat leases, restart/cursor recovery, independent handoffs, quotas, duplicate notifications, publication interruption and revisions with simulated App/GitHub boundaries; Docker tests exercise actual clean-container handoff validation. A long-running native App cycle and an upstream real-CI revision round are still release-validation goals, not claimed by the existing mock tests.

## Local operations

`desktop status` reads local scope, budget/reset time, active leases and blockers without network or model execution, including during a controller lock. `desktop install-skill --upgrade` refreshes vendor-managed resources, preserves the configured path/custom files, rejects changed managed resources or linked paths, and can resume an interrupted upgrade. It does not start/reconfigure a heartbeat. Native termination records come from the coordinating App session; Python does not independently attest a native process's state.
