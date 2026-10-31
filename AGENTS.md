# Project working conventions

The product remains autonomous after one-time operator configuration. Preserve the configured allowlist, runtime scope, budgets, isolation and independent validation contracts. See `docs/workflows/README.md` for the current workflow baseline and change history.

For every coherent product change batch:

1. Read the current workflow baseline and relevant implementation before editing.
2. Add a record under `docs/workflows/changes/` using `docs/workflows/templates/change-record.md`. Record why, before/after behavior, affected workflow nodes, actual verification, limitations and rollback. Update `docs/workflows/CHANGELOG.md` and the root `CHANGELOG.md`.
3. If orchestration, state transitions, defaults for selection, authority, budgets, quality gates or revision triggers change, add the next workflow version and its differences. Otherwise reference the existing workflow version. Keep historical baselines; append dated errata for inaccuracies.
4. Keep workflow versions separate from package/release versions. Record real commits/tags when created; do not invent identifiers or claim unrun checks. Prior verification must be attributed as prior verification.
5. Never include private config, databases, tokens, raw chats or target repository archives in public change records. Use sanitized evidence references.

These are development documentation conventions, not a per-Issue manual approval flow. Documentation work does not authorize changing live monitoring scope, dispatching repairs or publishing upstream PRs.
