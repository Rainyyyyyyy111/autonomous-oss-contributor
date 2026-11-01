# 固定控制器评测

语料 `controller-eval-v1` 位于包内 resources/controller-eval-v1.json，规范 JSON 摘要为 `1d23fcc07a6869920d866d4f12fbd0ed42c49f9f15d04b2fdc4f089a8f7895a7`。变更语料/标签必须更新版本与摘要，不能悄悄修改期望结果让指标变绿。

14 个固定案例包括三组合成 Python 缺陷的正确/错误补丁、削弱回归、保护文件修改，以及重复调用、无进展、总步数、健康读取、宿主工具和注入式返回结果后的越权轨迹。4 个案例应接受、10 个应拒绝；接受指控制器门槛，不代表模型解决问题或上游接受。

```sh
oss-contributor evaluate --image YOUR_LOCAL_TEST_IMAGE --output report.json
oss-contributor evaluate --image YOUR_LOCAL_TEST_IMAGE --candidates candidates.json --output candidate-report.json
oss-contributor evaluation-compare report.json candidate-report.json
```

命令不读取实际贡献配置、不调用模型或 GitHub。默认 CLI 的候选源码只在独立 DockerSandbox 执行，镜像先解析为不可变本地 ID；使用临时状态，不安装/更改实际服务或默认镜像。找不到镜像会失败，不自动构建、拉取或绕过隔离。CLI 输出文件为本地显式路径。测试中的可信本地执行器仅用于已冻结合成夹具，CLI 无该选项。

候选导入必须覆盖完全相同的案例 ID。patch 案例提供 changes（允许额外 regression_override 仅用于明确测试拒绝行为），trace 案例提供 steps；期望标签、基准、回归 oracle 和保护策略来自固定语料，不能由候选覆盖。输入为大小受限的常规 JSON 文件，工具轨迹只回放数据，不运行 argv 或模型提供的命令。导入来源标为 unverified_import，不能凭其中自称的模型名建立真实归因。

报告保留语料、配置、不可变镜像、候选和实现摘要，以及逐案例退出码和决策。matching_decisions/cases、unsafe_acceptances/expected_reject、valid_fixture_rejections/expected_accept 的分子分母分开保留。比较要求相同语料/配置/镜像/案例/标签，重新核对指标；不自动激活策略或声称统计显著的模型 A/B。

本次实际报告：[2026-11-01 固定评测](workflows/evidence/2026-11-01-controller-eval-v1.json)。真实 Docker 内 14/14 决策符合预期；其中正确补丁实际经历基线绿、原代码回归红、补丁后绿及全套检查。工具越权案例只验证控制器拒绝，不能证明真实模型理解或抵抗注入。这不是随机真实 Issue 成功率、长期 App 验证、实际模型/提示词 A/B 或合并概率。维护者接受、真实模型质量和分布外泛化需要另外的数据与运行授权。

2026-11-02 最终源码复跑：[报告](workflows/evidence/2026-11-02-controller-eval-v1.json)为相同语料/配置/镜像下 14/14 决策符合预期；evaluation-compare 实际对比两日报告，changed_cases 为空。首次复跑的镜像查询曾返回 No such image，未产出报告；随后本地查询与完整评测成功。此项如实保留在[统筹记录](workflows/changes/2026-11-01-006-integrated-audit.md)，不把未完成尝试算作通过，也不改变生产重试策略。
