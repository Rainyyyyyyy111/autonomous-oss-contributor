# Workflow 变更索引

本表一行对应一个改动批次。详细原因、行为变化、验证与回滚方案存放在对应记录中；软件发行记录继续见根目录 CHANGELOG。

| ID | 日期（新加坡） | 流程版本 | 类型 | 变化 | 详细记录 |
| --- | --- | --- | --- | --- | --- |
| CHG-20261101-004 | 2026-11-01 | v0.5 → v0.6 | feature / policy / fix | 记忆 TTL、版本覆盖、范围内失效/删除、墓碑与审计 | [记忆治理](changes/2026-11-01-004-memory-governance.md) |
| CHG-20261101-003 | 2026-11-01 | v0.4 → v0.5 | feature / policy | 任务内收尾预留、实际用量与价格来源、提示词/模型调用溯源 | [预算和核算](changes/2026-11-01-003-finalization-accounting-provenance.md) |
| CHG-20261101-002 | 2026-11-01 | v0.3 → v0.4 | feature / fix / policy | 可信错误分类、安全读取有界重试、固定持久额度与永久发布错误升级 | [错误分类与重试](changes/2026-11-01-002-classified-bounded-retries.md) |
| CHG-20261101-001 | 2026-11-01 | v0.2 → v0.3 | feature / policy | 工具调用重复、无进展与总步骤检测；原生活动持久化及保护触发后的取消/交接门槛 | [工具执行保护](changes/2026-11-01-001-tool-activity-guards.md) |
| CHG-20261031-001 | 2026-10-31 | 建立 v0.1 | baseline | 冻结当前自动贡献流程、聊天恢复、质量门槛与学习行为；建立后续留档规则 | [首次基线](changes/2026-10-31-001-baseline.md) |
| CHG-20261031-002 | 2026-10-31 | v0.1 → v0.2 | feature / fix / policy | 验证绑定、有界发布恢复、持久取消与停止竞态保护；记录目标差距 | [控制机制](changes/2026-10-31-002-workflow-controls.md) |
