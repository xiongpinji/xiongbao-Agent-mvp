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
| PostgreSQL | 3 个需要专用真实数据库的测试因没有 `OCTOP_TEST_DATABASE_URL` 而跳过；DDL 和并发行为未获真实 PG 验收。 |

`git diff --cached --check` 在提交前通过。Windows 本机缺少 `make`，提交时用 `SKIP_PRECOMMIT=1` 跳过仓库钩子；上表列出的检查由 Codex 独立执行，不能替代完整 CI。

## 尚未完成

B1 不启动内部 Agent、不开放 `mode="files"`、不提供 UI/API，也未实现安全工具门禁、文件路径、删除补偿或重启恢复。B2/B3/B4、F1 与最终 GLM 只读审查继续按 [030 合同](PROJECT_TASK_WORKSPACE_030_CONTRACT.md)推进。WorkBuddy 的完整本地/云端项目空间与视觉 1:1 仍未验收。
