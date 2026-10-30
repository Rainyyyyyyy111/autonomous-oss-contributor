# Validation and release scope

## What the Alpha demonstrates

- Simulated GitHub/Codex and a trusted local bug fixture exercise issue conflicts, policy/CLA/allowlist checks, quotas, pause, wrong patches, baseline failures, timeouts, regression protection, publication interruptions/recovery and duplicate feedback.
- Protocol tests reject unknown host operations/native capabilities and enforce stage-specific dynamic tools. Archive tests reject traversal, links, duplicates and oversize entries.
- Opt-in real Docker tests exercise independent red/green/full-suite verification, non-root isolation, absent credentials/socket, symlink rejection, offline execution, PyPI installation and forbidden-host/direct-egress rejection.
- Distribution validation builds a wheel and sdist, installs the wheel into a fresh environment, checks the CLI and confirms that the Docker runtime files are included.

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

## Known blockers before Beta

1. Complete a reproducible live standalone CLI run with a model accessible to the operator. In the development account, protocol handshake worked but model turns returned `model_not_found`; no authentication fallback was attempted.
2. Package a stable, validated desktop handoff/import interface instead of session-specific coordinating scripts. Native App tools are not called by the Python daemon.
3. Validate revision cycles against a controlled repository with readable failing current-head CI and actionable review. Existing coverage of those paths is simulated; the submitted upstream PRs had no actionable review yet.
4. Improve contribution-policy/ownership evidence beyond conservative text heuristics and document more supported project environments.
5. Obtain independent review of the security boundary, generated-test relevance and adversarial harness limitations.

Use this Alpha for exploration with repositories you control and deliberate, low-volume contributions. It is not a production unattended contributor or a guarantee of valid/accepted PRs.
