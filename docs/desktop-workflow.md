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

Job files live under the configured state's `jobs/TICKET/`. The worker returns a strict `Handoff`: ticket, fixed/skip/needs_attention/quota outcome, structured assessment, source changes and diagnosis. The coordinator reviews that patch, and the controller re-downloads the fixed base and independently proves original regression failure, fixed regression success and complete configured checks. A claimed pass is not accepted as evidence.

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
