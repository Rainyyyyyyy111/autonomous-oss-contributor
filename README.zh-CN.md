# Autonomous OSS Contributor

[English](README.md) · [架构](docs/architecture.md) · [桌面聊天流程](docs/desktop-workflow.md) · [验证范围](docs/validation.md)

**实验性 Alpha（0.1.0a1）**。可以作为开源研究工具和个人工作流使用，尚不适合作为生产级无人值守服务。三次真实 PR 由原生桌面新聊天修复、协调聊天审查并独立复验后提交。独立 CLI 的模型调用在开发账号中仍返回 `model_not_found`；桌面聊天调度尚未封装成通用 Python 接口。成功的协议握手不能证明模型可用。详见 [验证范围与 Beta 前置条件](docs/validation.md)。

个人使用的 Python CLI。自动发现 GitHub 项目，在你配置的白名单内筛选可复现的小型 Python Bug，通过 Codex 修复，在独立 Docker 容器中验证，然后创建 PR 并跟进 CI / Review。

**默认白名单为空。** `discover` 只读取 GitHub 并保存候选；只有明确配置的仓库才能执行修复、创建 Fork、分支和 PR。维护者决定是否接受或合并。

## 安装和启动

需要 macOS / Linux、Python 3.12+、GitHub CLI、Docker，以及 **Codex CLI 0.140.0**。Docker Desktop 必须运行。

```sh
git clone https://github.com/Rainyyyyyyy111/autonomous-oss-contributor.git
cd autonomous-oss-contributor
uv sync --locked --extra dev
uv run oss-contributor init
uv run oss-contributor doctor --build-runner --probe-codex
uv run oss-contributor discover
uv run oss-contributor status
```

如果还没有登录，分别执行 `gh auth login` 和 `codex login`。首版使用宿主机现有认证，不要求 API Key。Codex 需要现有的文件认证缓存；只存在 OS Keychain 登录而没有缓存文件时，`doctor --probe-codex` 会明确报错。

默认配置：`~/.config/oss-contributor/config.toml`。每个命令都可使用 `--config /absolute/path/config.toml`。

先在你控制的测试仓库中验证发布，再添加第三方项目。对每个仓库阅读贡献政策、确认允许相关贡献，并配置实际可用的安装、测试和检查命令：

```toml
[[repositories]]
name = "your-account/your-python-test-repo"
image = "oss-contributor-runner:0.1"
install = [["python", "-m", "pip", "install", "-e", ".[test]"]]
tests = [["python", "-m", "pytest", "-q"]]
checks = []
contribution_policy_reviewed = true
cla_completed = false
```

镜像基于 Python 3.12，预装 pytest。按目标项目添加依赖，例如 `install = [["python", "-m", "pip", "install", "-e", ".", "pytest", "ruff"]]`。安装只能访问 `pypi.org` 和 `files.pythonhosted.org`；需要 apt、私有索引、Git 依赖、外部服务或其他 Python 版本时，先由你构建兼容镜像。自定义镜像应基于本项目 runner，保留文件桥接脚本、非 root 用户和 `/opt/venv`。

可以设置根级 `model = "your-available-model"`；未设置时，只读取现有 `~/.codex/config.toml` 的 `model` 字符串，再回退到 App Server 默认模型。不会加载用户的插件、MCP、规则或仓库配置。CLI 版本的内置模型列表可能落后于账号可用模型；出现 `model_not_found` 时显式配置账号可用模型。

```sh
uv run oss-contributor run --once
uv run oss-contributor run --daemon
uv run oss-contributor inspect 1
uv run oss-contributor pause
uv run oss-contributor resume
```

`--once` 完成当前一轮可执行工作；`--daemon` 前台轮询，可以放入你自己的进程管理器。Mac 休眠时不会继续工作。Ctrl-C 停止进程，重启会核对持久化状态；本项目不会自行注册开机服务。

## 运行与质量规则

- 每小时发现最多 50 个公开、非归档、非 Fork 的 Python 项目，默认至少 500 Stars、最近 30 天推送。每个白名单仓库扫描最多 10 个带 `bug` 标签的开放 Issue；白名单仓库独立于搜索结果参与扫描。
- 跳过已指派、有认领评论、有关联开放 PR、证据截断、明确限制自动化贡献、CLA 未满足的 Issue。贡献政策需要一次本地确认。冲突判断基于公开信号，不能证明世界上没有人在处理。
- 安装和既有测试先通过，再生成一个新回归测试。该测试必须在原代码上以断言失败，在修复后通过。控制器在新容器中重新执行全过程；回归不得有收集错误或跳过，完整套件和配置检查均要求实际退出码为零。完整套件的跳过情况尚未针对所有测试框架统一解析，需要配置完整命令并审查结果。
- 默认最多修改 5 个文件、200 行，包含新增测试。只允许 Python 源码和一个专用回归测试；不允许修改已有测试、测试配置、依赖、CI、发布配置或凭据。更广的改动留给人工处理。
- 发布前再次检查 Issue 冲突和上游基准。上游无关文件变化时重新验证；涉及补丁文件的变化则停止，避免覆盖新代码。
- 只创建普通 PR、更新自己的分支和 PR 说明。不会发表评论、回复 Review、请求合并、批准 CI 或自动合并。

默认串行：每天尝试 3 个 Issue、新建 1 个 PR；每次修复 / 修订最多 30 分钟，模型活动累计每天最多 120 分钟。按 `Asia/Singapore` 日期计数。每仓库最多一个开放 PR，全局最多三个；每个 PR 最多自动修订两轮。

账号额度耗尽会暂停新模型任务；使用 `resume` 恢复不会重置额度。`pause` 会阻止新的模型调用和发布，已经开始的有界工具调用可能完成。GitHub 限流记录服务端退避时间，不占用长时间阻塞等待。

## CI、Review 和恢复

每 10 分钟检查开放 PR。只处理当前 Head SHA 的 CI 和维护者反馈；仅在可以读取对应失败日志时处理 CI 失败。等待维护者授权、日志不可见、分支被其他人修改、修订超限或需求超范围时，记录 `needs_attention` 或等待原因。

同一 Review / CI 事件不会重复处理；维护者的停止请求会终止修改，关闭和合并状态单独记录。失败的未验证任务不会自动重复尝试。对需要关注的任务使用 `inspect` 查看原因和报告，后续由你处理。

发布前先保存已验证补丁、基准、额度预留和发布意图。使用确定性分支、补丁摘要及 GitHub Tree 校验，恢复已推送但尚未建 PR、已建 PR 但尚未保存、修订已推送但尚未确认的中断，不强推、不重复创建 PR。

状态默认位于 `~/.local/share/oss-contributor/`，可通过根级 `state_dir` 修改：

- `state.sqlite3`：任务、候选、证据、报告、发布状态、额度、已处理事件；是恢复依据。
- `events.jsonl`：便于检查的事件镜像。
- `worker.lock`：限制一个工作进程；`pause` / `status` 可从其他终端使用。

数据库和日志包含上游代码、Issue 和 CI 信息；工具不会记录认证缓存。删除数据库会丢失额度和已处理事件，不能当作重试方式。

## 执行边界

Codex App Server 留在宿主机，使用临时独立配置和现有登录。线程和每轮调用都显式设置 `environments: []`。仅开放 `repo_list`、`repo_read`、`repo_search`；修复阶段增加 `repo_apply` 和 `sandbox_exec`。未知服务请求、宿主机审批或意外原生执行能力立即中止。

目标仓库不落到 Codex 的宿主机工作目录；代码作为经过边界检查的数据传入 Docker Volume。非 root 容器无宿主机绑定、Docker Socket 或账号凭据，根文件系统只读，默认 2 CPU、4 GiB 内存、128 个进程。安装通过受限 PyPI CONNECT 代理；其他阶段移除所有 Docker 网络连接。文件工具使用目录描述符和 `O_NOFOLLOW`，拒绝路径穿越、符号链接及特殊文件。

Docker 提供进程和文件隔离，不是针对内核漏洞的独立虚拟机安全保证。回归和测试证据降低错误风险，不能形式化证明任意项目代码正确。首版会跳过特殊文件、超大文件 / 仓库、不兼容运行环境及复杂项目；不会降低门槛来增加 PR 数量。

文件桥接支持单文件最多 4 MiB，仓库展开总量仍限制为 40 MB；允许常见的大型测试资源，同时拒绝越界路径、链接和超限归档。修改桥接代码后需重新构建 runner 镜像。

协议接口包含实验字段，因此版本不匹配会停止工作。参考 [Codex App Server 官方文档](https://learn.chatgpt.com/docs/app-server) 和 [0.140.0 接口定义](https://github.com/openai/codex/blob/rust-v0.140.0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs)。

## 开发与验证

```sh
uv run ruff check src tests
uv run mypy src/oss_contributor
uv run pytest -q
OSS_TEST_DOCKER=1 uv run pytest tests/test_docker.py -q
OSS_TEST_LIVE_CODEX=1 uv run pytest tests/test_live_codex.py -q
# 可用模型与当前默认不同时，为这次测试指定模型：
OSS_TEST_LIVE_CODEX=1 OSS_TEST_CODEX_MODEL=your-model uv run pytest tests/test_live_codex.py -q
uv build
```

默认测试通过模拟 GitHub / Codex 和受控本地故障仓库验证状态机、发布幂等、质量门槛与恢复。Docker 测试验证真实容器中的回归、网络隔离、PyPI 安装与路径边界。Live Codex 测试消耗现有账号额度，连接真实 Codex 和 Docker，但 GitHub 发布仍由内存适配器代替，不会产生外部写入。开发中的写操作测试只使用自有测试仓库；第三方贡献按白名单和当前用户授权执行。

`doctor --probe-codex` 验证版本和协议握手，不运行模型，不能证明模型访问权限。当前环境的独立 CLI / App Server 调用对默认模型、用户配置模型和 `gpt-5.3-codex` 返回 `model_not_found`；这条调用路径仍受阻。原生桌面聊天路径已完成下面的真实修复和 PR 发布，不受该 CLI 调用错误影响。

## 原生新聊天路径：已完成三次真实贡献

用户明确授权“每次修复创建新聊天，并任选一个真实 Issue 试跑”后，2026-10-30 完成了 [SCPIpy #46](https://github.com/0z0sk0/scpipy/issues/46) → [PR #52](https://github.com/0z0sk0/scpipy/pull/52)。

1. 协调聊天调用 Codex App 的原生 `create_thread`，创建用户可见的新聊天；不模拟鼠标，不调用独立 CLI 模型。
2. 新聊天筛选 Issue、检查政策和认领情况，在 Docker 内修复，交付 JSON manifest、补丁和实际日志。
3. 协调聊天重新下载固定上游 SHA、审查补丁并在另一个干净容器独立调用 `Engine.validate`。
4. 验证通过后，用现有 GitHub CLI 登录经 `Engine.publish` 创建 Fork、提交分支和 PR；发布意图、额度和远端 Head 持久化。
5. 原生聊天 heartbeat 每 10 分钟跟进已提交的 PR；若有可执行的 CI / Review 修订，再创建一个新聊天承接，每个 PR 最多两轮，不扫描或发布其他 Issue。

本次修复只改解析器和一个新增测试文件，共 +52/-3 行。基线 **390 passed**；新增回归在原代码上 **10 个目标断言失败、7 个兼容用例通过**；修复后新增用例 **17 passed**，完整测试 **407 passed**。新聊天和协调聊天分别在干净容器复验通过。远端 diff 与预期一致。

首次远端观察：Tests 和 Build docs 工作流均为 `action_required`，需要上游维护者批准后才运行。跟进器已覆盖“工作流等待批准、但 Check Run 尚未创建”的状态；不会将它视为测试失败而反复推送。

用户随后明确要求“继续修两个issue”，同日又分别创建了两个原生修复聊天，完成以下贡献。两项补丁均经协调聊天在干净容器独立复验，并核对远端 Head、文件和增删行数。

| Issue → PR | 修复 | 原代码回归 | 修复后完整验证 |
| --- | --- | --- | --- |
| [ConfigObj #94](https://github.com/DiffSK/configobj/issues/94) → [PR #291](https://github.com/DiffSK/configobj/pull/291) | `unrepr` 安全读取 `nan` / `inf`，保留非字面量拒绝规则 | 5 个断言失败、11 个控制用例通过 | 95 passed，两个既有 doctest 入口通过 |
| [python-jsonpath-rw #53](https://github.com/kennknowles/python-jsonpath-rw/issues/53) → [PR #104](https://github.com/kennknowles/python-jsonpath-rw/pull/104) | 字段名字符串化时保留引号及转义，避免点号改变路径语义 | 12 个断言失败、8 个控制用例通过 | 61 passed（含 coverage 执行） |

两项分别为 2 文件 +83/-2 行和 2 文件 +62/-1 行，均未修改已有测试或 CI。首次观察两条新 PR 均无远端 Check Run / Actions run；这不代表远端 CI 通过。三个 PR 由同一 heartbeat 跟进，无变化时保持安静。两个项目维护较少，合并与否仍由上游决定。

本地任务状态和原始证据没有纳入开源仓库；公开可核对的基准、提交 SHA、测试数量见 [验证记录](docs/validation.md)。默认示例白名单为空。当天总计 3 个 PR 使用用户明确授权的一次性额度，未改变默认每日 1 个 PR 的配置。

这条路径依赖 App 聊天提供的线程工具；独立 Python `run --daemon` 仍使用 CLI / App Server，不能自行调用当前聊天的工具。App heartbeat 负责调度原生新聊天。原生聊天仍有自身宿主机工具权限；工作指令要求第三方代码只在 Docker 中执行，并复用受限文件桥接，不能将其描述为与 `environments: []` 相同的能力隔离。

使用 MIT 许可证。请先阅读 [安全边界](SECURITY.md) 和 [贡献指南](CONTRIBUTING.md)。本项目与 OpenAI、GitHub 无隶属或背书关系。
