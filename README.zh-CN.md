# Autonomous OSS Contributor

[English](README.md) · [桌面自动调度](docs/desktop-workflow.md) · [架构](docs/architecture.md) · [验证范围](docs/validation.md)

**配置一次，让 Agent 自动发现、筛选、修复、验证并提交开源贡献，持续跟进 CI 和维护者反馈。** 正常流程不用你挑 Issue、转发提示词、运行测试或点提交。

`0.3.0a1` 是实验性 Alpha，主路径为 **Codex 桌面技能＋原生 heartbeat＋持久化 Python 控制器**。独立 CLI 的 App Server 模型调用作为备用路径保留，当前开发账号的 `model_not_found` 不再阻挡桌面路径。维护者是否接受、合并，仍由维护者决定。

## 一次配置，持续运行

需要 macOS / Linux、Python 3.12+、uv、GitHub CLI、运行中的 Docker，以及具备原生聊天/自动化工具的 Codex App 会话。复用已有 GitHub 和桌面 Codex 登录；桌面路径不要求独立 Codex CLI 的文件认证缓存。

```sh
git clone https://github.com/Rainyyyyyyy111/autonomous-oss-contributor.git
cd autonomous-oss-contributor
uv sync --locked --extra dev
uv run oss-contributor init
```

默认配置在 `~/.config/oss-contributor/config.toml`。首次设置允许自动贡献的仓库、真实安装/完整测试/检查命令和预算。白名单默认为空，发现热门项目不会自动扩大授权。

```toml
[[repositories]]
name = "your-account/your-python-test-repo"
image = "oss-contributor-runner:0.1"
install = [["python", "-m", "pip", "install", "-e", ".[test]"]]
tests = [["python", "-m", "pytest", "-q"]]
checks = [] # 项目有 lint/类型检查时填写真实命令
contribution_policy_reviewed = true
cla_completed = false
```

先用自己控制的测试仓库验证，再扩展一次性授权的仓库范围。准备镜像并安装技能：

```sh
uv run oss-contributor doctor --desktop --build-runner
uv run oss-contributor desktop install-skill
```

然后在 Codex App 中说：

> 使用 $oss-contributor，按我的配置启动全自动开源贡献：自动选 Issue、修复、验证、提交 PR 并跟进反馈。每次修复或修订都创建一个新的原生 Codex 聊天。

技能负责创建或复用定时 heartbeat，之后自动运行。无需逐个 Issue 授权、手动搬运结果。`desktop setup --config PATH` 可查看首次启动信息；新技能需要 App 会话重新发现时，重新加载技能上下文。

## 它会自动完成什么

1. 每小时发现公开、活跃的 Python 项目，并独立扫描已授权仓库；候选不等于发布权限。
2. 筛选小型 Bug，检查复现、贡献政策、CLA、指派、认领和竞争 PR；证据不充分就跳过。
3. 持久化任务与预算，为每次修复派发一个新的原生聊天；重启后接回原聊天，不盲目重复创建。
4. 在 Docker 中建立通过的原始基线，证明新增回归在原代码因目标断言失败，再修复。
5. 控制器重新下载固定基准，在干净容器中独立运行原代码回归失败、修复后回归通过、完整测试与配置检查。
6. 发布前重新检查上游、冲突和远端 Head，然后自动创建/更新 Fork、分支和普通 PR。
7. 跟进当前 Head 的可读 CI 失败及未处理维护者反馈；每次修订使用新聊天并重新过完整门槛。等待维护者批准工作流不会触发无效推送。

仅在创建/更新 PR、终态或需要处理的阻塞时通知。正常等待保持安静。通知与已处理事件持久化；通知为至少一次投递，极端情况下在“已显示、未确认”的崩溃间隙可能重复。

## 默认边界

- 自动选择 Issue，范围限于一次配置的白名单；不自动扩权。
- 每天最多尝试 3 个 Issue、新建 1 个 PR；每仓库最多一个开放 PR，状态实例全局最多三个。
- 默认串行；每任务 30 分钟，每天模型预算 120 分钟；按新加坡日期计算。
- 每 PR 最多两轮修订，保留原始回归测试和先前 Head，拒绝覆盖外部提交。
- 最多 5 文件、200 增删行（含回归测试）；不能修改已有测试、依赖、CI、配置或凭据。
- 不自动评论、回复 Review、合并、批准 CI，也不在受阻后切换认证。

```sh
uv run oss-contributor status
uv run oss-contributor pause
uv run oss-contributor resume
```

本地 SQLite 是恢复依据，JSONL 为日志镜像。模型预算派发前预留，结束时幂等结算。超时或故障后保留执行位，协调聊天确认原聊天停止后才结算；仅执行恢复命令无法绕过停止确认。通过验证后的发布中断可以核对远端后继续。配置、数据库、原始日志和认证缓存不应提交到 GitHub。

## 运行状态与升级

```sh
uv run oss-contributor desktop status
uv run oss-contributor desktop install-skill --upgrade
```

状态查询完全本地，不访问 GitHub 或调用模型，可查看剩余额度、重置时间、暂停原因和待确认停止的聊天。技能升级保留已有配置路径和额外文件；检测到本地修改或链接路径会拒绝覆盖，中断后可继续。

主模式仍是自动选 Issue。固定 PR 跟进则用 `desktop scope --monitor --task-id 1 --task-id 2` 一次性冻结仓库/Issue/PR 身份，后续即使漏传监控参数也不会扩权。`desktop scope --autopilot` 仅在用户要求启动完整自动贡献时切换，且不能绕过活动聊天。技能会自动执行这些协调命令，不要求用户逐个 Issue 操作。

## GitHub 通知监听

新增轻量通知监听器，默认约每 60 秒检查一次；遵守 GitHub 返回的更长间隔和限流退避。只核验已授权、已跟踪的 PR，以及相关 Issue/CI 通知，不会把 GitHub 通知标为已读。关闭、合并、停止、提交变化和 CI 状态先由程序判断；具体评审进入优先队列，含糊的质量投诉可转为需人工处理。通知可能不完整，因此默认每 10 分钟另行核对 PR 状态。

```sh
uv run oss-contributor notifications poll --config /path/to/config.toml
uv run oss-contributor notifications service --install --config /path/to/config.toml
uv run oss-contributor notifications service --uninstall --config /path/to/config.toml
# 非 macOS 或前台调试
uv run oss-contributor notifications watch --config /path/to/config.toml
```

macOS 后台监听可发送本机系统通知，并独立记录送达结果；不会提前确认 Codex 聊天已送达。监听器不调用模型、不创建修复聊天、不修改远端 PR。复杂评审在现有 Codex 协调心跳运行时处理，默认仍为 10 分钟；“每分钟检测”不代表每分钟完成模型判断。电脑需要保持唤醒，系统通知是否显示取决于 macOS 通知权限及专注模式。`desktop status` 提供最近检查时间、错误和排队任务。

## 隔离与现实限制

第三方代码只在非 root Docker 容器执行：无用户目录/认证/Docker Socket 挂载，只读根，默认 2 CPU / 4 GiB / 128 进程。安装仅通过 PyPI 代理，修复和测试断网。文件桥接拒绝路径穿越、链接、特殊文件及超限输入，单文件 4 MiB、展开归档总量 40 MB。

**桌面聊天本身仍有宿主机工具权限。** 要求它使用 Docker 是工作流约束，不等于移除了宿主机能力。Docker 及测试证据也不能形式化证明任意恶意仓库或补丁安全。详细边界见 [SECURITY.md](SECURITY.md)。

机器和 App 必须保持运行、唤醒。0.3 已提供可安装技能、持久调度、统一交接和恢复测试；长期无人值守运行，以及一次真实远端 CI 失败后的完整修订，仍需实测，不能用模拟测试代替。独立 `run --daemon` 仍调用备用 App Server 路径，不是桌面自动化入口。

## 已有真实贡献记录

以下三个 PR 使用独立桌面修复聊天，再由协调聊天审查并在干净容器复验后提交；它们发生在新调度接口封装之前，不代表 0.2 长期运行的成功率，也不声称已被接受。

| PR | 原代码回归 | 修复后完整测试 |
| --- | --- | --- |
| [SCPIpy #52](https://github.com/0z0sk0/scpipy/pull/52)：拒绝空参数 | 10 个目标断言失败、7 个控制用例通过 | 407 通过 |
| [ConfigObj #291](https://github.com/DiffSK/configobj/pull/291)：读取非有限浮点值 | 5 个目标断言失败、11 个控制用例通过 | 95 通过＋两个 doctest 入口 |
| [python-jsonpath-rw #104](https://github.com/kennknowles/python-jsonpath-rw/pull/104)：保留字段名引号及路径语义 | 12 个目标断言失败、8 个控制用例通过 | 61 通过 |

基准 SHA、提交 Head 和初始 CI 情况见 [验证记录](docs/validation.md)。无远端检查不代表 CI 通过。

## 开发

```sh
uv sync --locked --extra dev
uv run ruff check src tests scripts
uv run mypy src/oss_contributor
uv run pytest -q
OSS_TEST_DOCKER=1 uv run pytest tests/test_docker.py -q
uv build
uv run python scripts/smoke_wheel.py
```

MIT 许可证。欢迎有真实复现与测试证据的贡献；见 [贡献指南](CONTRIBUTING.md)。本项目与 OpenAI、GitHub 无隶属或背书关系。
