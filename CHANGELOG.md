# Changelog

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
