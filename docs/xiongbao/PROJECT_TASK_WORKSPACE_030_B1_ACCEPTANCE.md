# 030A B1 · 项目任务文件运行时持久化验收

代码提交：`b39121d5`，已推送至用户仓库 `xiongbao/main`。执行候选来自 Agent Orchestrator 任务 `claude-bailian-20260925-101341-503025`；Codex 对改动逐项核对、修复后独立运行下列检查。该验收只覆盖 B1 数据库层，不表示 030A 文件任务已经可用。

## 已实现与核对

- SQLite/PostgreSQL 配套 028 迁移增加数据库可信的 `agents.runtime_kind`、任务上下文 `mode/source_expert_id/runtime_agent_id` 和运行时唯一索引。SQLite 028 由同一事务内的可重放迁移辅助函数执行；已有行默认为 `standard/chat`。
- 专用 `AgentRepo.create_project_task_runtime_with_quota` 在单个写事务里计数和插入，固定每用户 8 个、全实例 64 个。SQLite 使用 `BEGIN IMMEDIATE`，PostgreSQL 使用固定事务级 advisory lock；普通创建与自由 `config_json` 无法设置内部标记。空 owner 被拒绝。
- 旧 027 迁移恢复测试改为比较实际最高迁移版本；其 027 表和列断言仍保留。

## 独立验证

| 环境 | 结果 |
| --- | --- |
| Windows 主工作区 | 相关 6 个数据库测试文件：68 passed、3 skipped；所改 Python 文件 Ruff check/format 通过；暂未运行完整 `make all`。 |
| WSL Linux，提交后的独立工作区 | 同一组：68 passed、3 skipped；Ruff check/format 通过，所改源模块严格 mypy 通过。 |
| PostgreSQL | 首次 B1 验收时 3 项因缺少专用数据库而跳过；2026-09-27 已完成下述 PostgreSQL 18.6 补验。 |

`git diff --cached --check` 在提交前通过。Windows 本机缺少 `make`，提交时用 `SKIP_PRECOMMIT=1` 跳过仓库钩子；上表列出的检查由 Codex 独立执行，不能替代完整 CI。

## 2026-09-27 真实 PostgreSQL 补验

在 WSL 新建专用 PostgreSQL 18.6 测试库，测试只重置该库的 `public` schema。首次执行 B1 的 3 项实库测试得到 2 通过、1 失败：测试夹具先插入了引用不存在 Agent 的线程，触发 `threads_agent_id_fkey`，尚未进入任务上下文约束断言。OpenCode/DeepSeek 只在测试文件中补建同一用户的 Agent；Codex 检查 6 行差异、独立复测并推送修复提交 `1d717862e4bd2431462233b756bc89c26659dc5b` 至用户仓库。GLM-5.3 对该固定候选只读审查 **GO、无 P0/P1**；GLM 本身无法运行 Git 或测试，固定差异与运行证据由 Codex 核对。

补验结果：B1 真实 PG **3/3**，相邻配额 **12/12**，通用 PG 控制面集成 **6/6**；数据库单元回归 **602 通过、3 项未带 PG 环境变量而跳过**；完整非 live 后端回归 **4495 通过、17 跳过、0 失败**。Ruff 检查/格式、严格 mypy（541 个源文件）及 dashboard 生产构建通过。WSL 提交钩子的 CRLF shebang 无法执行；首次无缓存 `testmon` 串行试跑在 101 项通过后主动停止，**不能记为门禁通过**。代码提交前由上述独立全量检查验证，仍不替代远端 CI 或安装包验收。

这些结果只证明 B1 的迁移、约束与配额在该专用 PG 环境运行；030A 后续文件操作的 Windows 安全候选尚未验收，`mode="files"` 创建开关仍关闭。项目级全链路、WorkBuddy 真实视觉/交互和用户安装路径仍待验收。

## 尚未完成

B1 本身不启动内部 Agent、不开放 `mode="files"`、不提供 UI/API；后续 B2/B3/B4 与 F1–F3 有独立受控候选和[集成验收](PROJECT_TASK_WORKSPACE_030_F3_INTEGRATED_ACCEPTANCE.md)，但 030A 的 Windows 路径安全激活门禁仍未通过。WorkBuddy 的完整本地/云端项目空间与视觉 1:1 仍未验收。
