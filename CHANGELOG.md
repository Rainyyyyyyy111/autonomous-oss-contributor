# Changelog

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
