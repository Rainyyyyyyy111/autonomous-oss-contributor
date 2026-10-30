# Security policy

## Supported release

Only the latest Alpha prerelease is supported on a best-effort basis. This project is experimental and has not undergone an independent security audit. Do not run it on a host containing credentials or data you cannot afford to expose.

Report a vulnerability privately through [GitHub private vulnerability reporting](https://github.com/Rainyyyyyyy111/autonomous-oss-contributor/security/advisories/new). Include a minimal synthetic reproducer, affected version, expected boundary and observed behavior. Do not include live credentials or attack a third-party project. Ordinary non-sensitive bugs can use public issues.

## Trust boundaries

- **Trusted:** the operator's local configuration, runner images, host GitHub/Codex CLIs, controller and Docker daemon. Local configuration authorizes publishing. Do not accept configuration or custom images from an untrusted issue or model response.
- **Untrusted:** repository archives, dependency installation, project code/tests, issue descriptions, review text, CI logs and generated patches. They must not choose host tools, credentials, repositories or publication scope.
- **Host credentials:** `gh` performs GitHub operations on the host. The standalone Codex adapter references file-backed Codex login on the host and may persist ordinary OAuth refreshes. Neither auth store is mounted in target containers.
- **Target execution:** unprivileged, resource-limited Docker containers; no host binds or Docker socket, read-only root, temporary volumes, offline repair/tests. Installation has limited PyPI access through a separate proxy.
- **File bridge:** reject traversal, links and special files; restrict file and archive sizes. Files are not extracted from untrusted archives onto the host.
- **Publishing:** allowlist, local pause, persistent intent, known remote Head, no force push, independent validation and fresh conflict/base checks. A model's self-assessment is not test evidence.

## Known limitations

Docker shares a kernel with its execution host or Docker VM. These controls do not protect against Docker/kernel escapes or a compromised daemon. Custom images can undermine the boundary. PyPI allowlisting reduces network scope but does not make dependencies trustworthy or prevent every form of exfiltration to an allowed endpoint. Volume disk usage has no independent storage quota; bounded input, process/memory/time limits do not guarantee protection from disk exhaustion.

Generated tests and upstream test harnesses execute arbitrary code inside the sandbox. AST checks, exit codes, JUnit reports and clean-container repetition reject common mistakes but cannot prove that hostile code has not fabricated its own results. Test quality and root-cause relevance still need review. Public ownership/policy signals and text heuristics can be incomplete or misclassified.

`environments: []` and restricted tools are experimental Codex protocol behavior, version-pinned and checked at runtime. They are not a formal sandbox proof. Native desktop chats have their own host tool permissions: the documented desktop workflow relies on instructions and a coordinating reviewer and is **not equivalent** to the restricted standalone adapter.

SQLite/event logs may contain upstream code, issue text, feedback and command output. Keep state directories private. Log output is bounded, but there is no comprehensive secret redactor; do not run targets containing private secrets or publish raw logs. `pause` blocks new calls/publication but may not interrupt a bounded operation already in progress.

This project does not promise autonomous correctness, upstream acceptance, perfect prompt-injection resistance, or credential safety against a compromised trusted component.
