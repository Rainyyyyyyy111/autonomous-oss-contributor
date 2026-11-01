# Validation and release scope

## Local controller audit — 2026-11-02

Final local verification for Workflow v0.3–v0.6 completed with **401 passed, 9 skipped** in the default suite, plus **8 passed** in the explicitly enabled real Docker suite. Ruff, mypy (27 source files), wheel/sdist build, fresh-wheel installation/resource checks, bundled-skill validation and local document/default-policy checks passed. The frozen synthetic controller evaluation matched **14/14** expected decisions; this measures controller gates, not model effectiveness or upstream acceptance.

The Docker round initially reported one image-lookup failure (7 other cases passed); the complete rerun passed all eight. The initial evaluation image lookup also failed before producing a report; its subsequent complete run passed. Root cause of the temporary lookup failure was not established. No live configuration/state, installed skill, service, automation, real model turn or GitHub operation was used for this audit. See the [full 15-item audit and limitations](workflows/changes/2026-11-01-006-integrated-audit.md) and [final synthetic evidence](workflows/evidence/2026-11-02-controller-eval-v1.json). Historical validation below remains attributed to its own date.

## What the Alpha demonstrates

- Simulated GitHub/Codex and a trusted local bug fixture exercise issue conflicts, policy/CLA/allowlist checks, quotas, pause, wrong patches, baseline failures, timeouts, regression protection, publication interruptions/recovery and duplicate feedback.
- Protocol tests reject unknown host operations/native capabilities and enforce stage-specific dynamic tools. Archive tests reject traversal, links, duplicates and oversize entries.
- Opt-in real Docker tests exercise independent red/green/full-suite verification, non-root isolation, absent credentials/socket, symlink rejection, offline execution, PyPI installation and forbidden-host/direct-egress rejection.
- Distribution validation builds a wheel and sdist, installs the wheel into a fresh environment, checks the CLI and confirms that the Docker runtime files are included.
- Native-controller tests cover automatic issue selection without a CLI model call, persisted chat leases and wait cursors, atomic budget reservation/settlement, identity/authorization/path checks, quality rejection, interruption recovery, terminal feedback, revision limits and the notification outbox. A Docker handoff test uses actual clean-container verification and simulated GitHub publication.

The default suite excludes live account execution unless explicitly enabled. CI requires no model/GitHub credentials and never publishes upstream contributions. Docker checks run on default-branch pushes and manual dispatch. A green CI run validates these scopes, not arbitrary upstream compatibility or model access.

## Initial real contributions — 2026-10-30

All used separate native desktop repair chats plus source review and independent clean-container verification by the coordinating chat. No claim of acceptance/merge is made. Links show live status; the entries below record submission-time facts.

| Upstream issue / PR | Upstream baseline SHA | Submitted Head SHA |
| --- | --- | --- |
| [SCPIpy #46](https://github.com/0z0sk0/scpipy/issues/46) / [#52](https://github.com/0z0sk0/scpipy/pull/52) | `df33bcfbb1da2586adbadd4d9bb0dae4ee2b704e` | `c29113d4732b30a042b4e10feb7be89a1f5f3125` |
| [ConfigObj #94](https://github.com/DiffSK/configobj/issues/94) / [#291](https://github.com/DiffSK/configobj/pull/291) | `b7707c94c0317b8f89b704a01525a68eb6c72521` | `2d2589be1a27dd3462494d9967bb56d3751d785b` |
| [python-jsonpath-rw #53](https://github.com/kennknowles/python-jsonpath-rw/issues/53) / [#104](https://github.com/kennknowles/python-jsonpath-rw/pull/104) | `6f5647bb3ad2395c20f0191fef07a1df51c9fed8` | `59213074cc54894692cdd6b41e3a888318f48de8` |

| Contribution | Baseline | Original regression | Fixed regression | Fixed full suite | Patch |
| --- | --- | --- | --- | --- | --- |
| Empty SCPI parameters | 390 pass | 10 assertion failures, 7 pass | 17 pass | 407 pass | 2 files, +52/-3 |
| Non-finite ConfigObj values | 79 pass + two doctest entry points | 5 assertion failures, 11 pass | 16 pass | 95 pass + both doctest entry points | 2 files, +83/-2 |
| Literal JSONPath field names | 41 pass | 12 assertion failures, 8 pass | 20 pass | 61 pass under coverage | 2 files, +62/-1 |

SCPIpy's first Tests/Build docs runs concluded `action_required`, awaiting maintainer authorization. No workflow was approved by the agent. The other two PRs initially had no remote check runs or Actions runs; absence of CI is not a pass. ConfigObj/JSONPath are mature projects with limited maintenance; tests alone do not establish acceptance probability.

Rejected candidates included already-fixed or claimed issues, duplicate ready fixes, unsupported environments and baselines with existing check failures. These three selected examples are not a benchmark denominator or a promise of success on a random issue. The two additional PRs were explicitly requested by the operator; the default daily PR limit remains one.

## Notification follow-up — 2026-10-30 (Asia/Singapore)

The authenticated GitHub notifications API returned three notifications at this inspection. Their live PRs, reviews and associated issues were read; ConfigObj's complete PR timeline, inline review comments and current-head check/Actions endpoints were also checked.

| Notification | Observed outcome | Time on 2026-10-30 (UTC+08:00) |
| --- | --- | --- |
| [ConfigObj PR #291](https://github.com/DiffSK/configobj/pull/291), for [issue #94](https://github.com/DiffSK/configobj/issues/94) | Changes requested; closed without merging. Original issue remains open. | Review 17:36:35; PR `closedAt` 17:54:38 |
| [python-jsonpath-rw PR #104](https://github.com/kennknowles/python-jsonpath-rw/pull/104), for [issue #53](https://github.com/kennknowles/python-jsonpath-rw/issues/53) | Approved and merged; original issue closed. | Merged 15:09:20 |
| [python-jsonpath-rw PR #105](https://github.com/kennknowles/python-jsonpath-rw/pull/105), for [issue #67](https://github.com/kennknowles/python-jsonpath-rw/issues/67) | Approved and merged; original issue closed. | Merged 17:49:06 |

### ConfigObj #291: maintainer feedback and closure

At 17:36:35, collaborator `jelmer` submitted a [changes-requested review](https://github.com/DiffSK/configobj/pull/291#pullrequestreview-5710468798):

> Please edit AI slop out of the PR before sending our for review

The PR timeline identifies `jelmer` as the actor who closed it at 17:54:39 (one second after the PR object's `closedAt`). `mergedAt` is null, and the submitted head remains `2d2589be1a27dd3462494d9967bb56d3751d785b`. There are no issue-conversation comments or inline review comments on this PR explaining particular offending passages or code lines. The review objects to the submission's quality as perceived by the maintainer; it does not provide a specific technical defect, an explicit blanket ban on AI assistance, or a separate explanation for closure. Closure followed that review, but its precise rationale beyond the review is not documented.

The original bug is the inability to read back `nan` written with `unrepr=True`, because `ast.literal_eval` rejects that name. Issue #94 remains open, so this contribution did not resolve it upstream. Previous independent local evidence remains 79 baseline tests, 16 passing fixed regression cases and 95 passing fixed full-suite tests plus two doctest entry points, on Python 3.12 only. This inspection did not rerun those tests. The current head has zero remote check runs and zero Actions runs; there is no observed CI failure explaining this closure, and no remote CI pass can be claimed.

Potential review-friction sources, inferred from the submitted PR rather than specified by the reviewer, include the long command transcript with insufficient distinction between expected original-code failures and fixed-code results, boilerplate assurances, and the automation-oriented test filename `test_oss_regression_94.py`. These are hypotheses for improving future submissions, not confirmed explanations of the maintainer's objection. Future PRs should summarize the concrete bug, change and meaningful test results briefly, keep full command logs in local evidence, and follow the target repository's test conventions while preserving any required AI disclosure. Passing tests alone do not establish reviewer readiness or acceptance.

The local controller's event mirror already records task 2 as `closed` with reason `maintainer decision recorded`. An intervening revision attempt recorded `PR stopped or Head changed while repair was running`; GitHub still shows the original submitted head. Treat this contribution as closed without merge, not as a successful fix or a pending review. Any future resubmission should first establish whether the maintainer welcomes it and address concrete feedback. This inspection made no upstream edits, comments, pushes or reopening requests, left notification read status unchanged, and did not modify controller state.

## Review-handling repair — 2026-10-30

Investigation found two concrete controller problems: `Engine.body` exposed the full verification command transcript with boilerplate, and both revision paths required new source changes even for feedback solely about the PR description. These are program defects supported by source inspection; they do not establish which material the ConfigObj reviewer meant by their broad complaint.

GitHub's [review guidance](https://docs.github.com/en/pull-requests/concepts/helping-others-review-your-changes) recommends focused PRs with descriptions explaining the problem, approach and result. The [Update a pull request API](https://docs.github.com/en/rest/pulls/pulls#update-a-pull-request) accepts a `body` independently of commits. The repair uses those capabilities: a concise generated explanation with an evidence-backed validation summary, and a separate existing-PR description update. Command transcripts remain local and AI assistance remains disclosed.

Native handoffs now accept `description` with no file changes; the standalone path classifies feedback with a read-only model call before source repair. Both reuse the previous verified patch/report, retain revision limits and policy/scope checks, refuse CI feedback in description-only revisions, and persist update intent for retry. Existing code changes still require independent regression/full-suite validation. Ambiguous quality complaints are directed to `needs_attention`, with no assumption that rewriting prose or altering code will satisfy the reviewer. Classification still depends on model/coordinator judgement.

Regression coverage uses trusted local fixtures and simulated GitHub writes for successful/no-op description updates, lost-response recovery, immutable patch/report/head, CI/mixed/new feedback, human description edits, PR closure/stop and ambiguous escalation. GitHub adapter tests verify a body-only PATCH and observed-conflict guards. These checks do not constitute a new live upstream revision or prove atomic exclusion of concurrent GitHub edits; the API exposes no expected-head/body parameter for this operation.

Local verification on Python 3.12.2: `uv run pytest -q` completed with **169 passed, 5 skipped** (optional integration tests); Ruff, mypy, skill validation, wheel/sdist builds and a fresh-wheel installation/CLI/resource smoke check passed. Failed revision attempts now retain the previous accepted validation report and store attempt evidence separately; standalone lost-response recovery is covered. The managed installed skill was upgraded and verified against the source hashes while preserving its existing interpreter/config paths. The configured interpreter imports this editable checkout. No upstream PR was changed to test the repair.

## GitHub notification listener — 2026-10-30

Added a scoped observer with default 60-second polling, GitHub `Last-Modified`/`304` support, server poll-interval and rate-limit handling, bounded pagination, version deduplication, atomic cursor/inbox persistence and periodic PR-state sweeps. Notifications trigger current-state verification rather than direct action based on their reason/title. The observer does not call models or publish; review work is queued for the native coordinator and broad ambiguous quality feedback can be escalated. Native revision acceptance/publication now reject intervening actionable feedback, and an active native revision requests confirmed cancellation before reassessment. Acknowledgements remain ignorable.

The optional macOS LaunchAgent was installed against the operator's existing config, with no scope or heartbeat expansion. A live account poll successfully returned `not_modified`; matched notifications were recorded as already merged. Repeated LaunchAgent runs were observed, including a successful exit after handling controller-lock contention as a normal deferred attempt. Health reported no notification API error and a subsequent successful poll within the configured interval. This is evidence of local installation/account integration, not long-term uptime or a claim that every GitHub event generates a notification. No remote write was used as a test fixture. Local alert submission is separately deduplicated; actual display depends on macOS notification settings.

Validation of the integrated changes: the full suite completed with **207 passed, 5 skipped** (optional integration tests), including all **6 service tests**. Ruff, mypy, bundled-skill validation, wheel/sdist builds and a fresh-wheel installation/CLI/resource smoke check passed. Coverage includes notification scope/identity matching, ignored external URLs, duplicate and read notifications, server intervals/304 validators, pagination rejection, cursor recovery after observation failure, pause/backoff, fallback sweeps, terminal/CI/ambiguous triage, queue priority, late-feedback publication/cancellation guards, alert deduplication and managed service install/removal/rollback.

The machine must be awake for the listener. It may defer for a held controller lock or GitHub throttling; complex model judgement still waits for the native coordinator's heartbeat (default ten minutes) and budgets. See the [listener workflow](desktop-workflow.md#github-notification-listener) and the [GitHub notification API guidance](https://docs.github.com/en/rest/activity/notifications).

## Known blockers before Beta

1. Exercise the packaged desktop scheduler over a long-running native App cycle, including a coordinator restart; the three earlier real contributions used session-specific coordination. Fixture coverage does not prove App uptime or native chat creation/cancellation behavior under every failure.
2. The optional standalone adapter still needs a reproducible live model run. In the development account, handshake worked but model turns returned `model_not_found`; this does not block the native desktop path. No authentication fallback was attempted.
3. Validate revision cycles against a controlled repository with readable failing current-head CI and actionable review. Existing coverage of those paths is simulated; ConfigObj #291 subsequently received broad quality feedback and was closed, which does not establish a completed review-to-revision cycle.
4. Improve contribution-policy/ownership evidence beyond conservative text heuristics and document more supported project environments.
5. Obtain independent review of the security boundary, generated-test relevance and adversarial harness limitations.

Use this Alpha for exploration with repositories you control and deliberate, low-volume contributions. It is not a production unattended contributor or a guarantee of valid/accepted PRs.

## 0.3 recovery coverage

Regression cases cover frozen monitor identities, foreign task/publication/chat/notification exclusion, explicit scope changes, terminal acknowledgement before handoff collection, cancellation across pause/restart, legacy lease accounting, late creation reconciliation, read-only health, and managed skill upgrade/configuration/link/customization/interruption behavior. Fixture termination acknowledgements simulate App observations; they do not independently prove native process termination or unattended longevity.
