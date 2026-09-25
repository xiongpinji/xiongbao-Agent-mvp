# 029 · 项目专家选择实现计划

> 面向三条已授权 Agent Orchestrator 路由：Codex 总指挥；Claude/Qwen3.8 Max 后端主实现，OpenCode/DeepSeek V4.1 Flash 前端并行实现，Qwen Code/GLM-5.3 只读审查。执行者只在各自独立 worktree 修改白名单文件，不提交或推送；Codex 整合与交付。

**目标：** 项目管理员可保存共享单专家名单；非空名单在服务端限制后续项目私密任务的专家候选，但不授予成员新的 Agent/工具/文件权限。合同见 [029 专家选择合同](PROJECT_EXPERT_SELECTION_029_CONTRACT.md)。

**架构：** 成对 027 迁移持久化有序名单和管理员 PUT 修订号。项目服务与 API 执行成员角色和隐私规则；项目任务既有原子创建事务读取名单并重验 Agent。React 右栏管理弹窗与任务创建选择器只显示服务端授权数据。GLM 对冻结设计有条件 GO，Codex 已把其 P1/P2 写入合同。

**技术栈：** Python 3.12、FastAPI、SQLite/PostgreSQL、React 18、TypeScript、Vitest。先有失败行为测试，再写最小实现。

## 文件与责任

| 责任 | 路径 | 作用 |
| --- | --- | --- |
| 后端 | `src/octop/infra/db/migrations/027_project_experts.sql`、`.pg.sql` | 版本化名单与 context 审计修订号 |
| 后端 | `src/octop/infra/db/repos/projects.py`、`project_tasks.py` | 原子保存与任务创建门禁、锁序 |
| 后端 | `src/octop/infra/projects/service.py`、`tasks.py` | 角色、有效专家与错误语义 |
| 后端 | `src/octop/api/routers/projects.py`、`project_tasks.py`、`src/octop/infra/errors.py`、`src/octop/i18n/{zh,en}.json` | 类型化 HTTP 合同与中英文错误 |
| 后端 | `tests/unit/db/test_project_experts_repo.py`、`tests/unit/db/test_project_task_create_repo.py`、`tests/integration/test_project_experts_api.py`、`tests/integration/test_project_task_create_api.py` | 仓储/事务/多身份失败与成功证据 |
| 前端 | `dashboard/src/api/modules/projectExperts.ts`、`projectTasks.ts` | 类型化列表/保存与新请求修订号 |
| 前端 | `dashboard/src/pages/Projects/ProjectExperts.tsx`、`ProjectDetail.tsx`、`ProjectTasks.tsx`、`dashboard/src/locales/{zh,en}.json` | 双栏管理、错误/晚到状态、候选过滤 |
| 前端 | `dashboard/src/api/modules/projectExperts.test.ts`、`dashboard/src/pages/Projects/ProjectExperts.test.tsx`、`ProjectDetail.test.tsx`、`ProjectTasks.test.tsx` | 真实行为组件/API 回归 |
| Codex | `docs/xiongbao/PROJECT_EXPERT_SELECTION_029_ACCEPTANCE.md`、台账 | 证据分层与差距记录 |

## 任务 1：后端原子配置与新建门禁

- [ ] 在新 `test_project_experts_repo.py` 写失败测试：同一项目成员读取有序名单，外人不得读；member PUT 为 403，owner/admin PUT 成功，重复/超 20/私有/停用/专家团全部拒绝且修订号不变；两连接同修订号竞争只一个成功。
- [ ] 运行 `uv run pytest tests/unit/db/test_project_experts_repo.py -q`，先确认失败来自缺少 027 模型/方法，不是测试环境。实现成对迁移及 `ProjectRepo` 的读/替换；PostgreSQL 锁成员→项目→按 ID 排序的 Agent，SQLite 用现有写事务；相同有序名单不增修订号。
- [ ] 在 `test_project_task_create_repo.py` 与集成 API 测试先写失败测试：空名单兼容 028；非空名单缺修订号/旧修订号得 409 无 thread/link/context；选中共享专家可创建且 context 记录版本；不在名单或取消共享者（含其 owner）拒绝；成员退组/项目归档/Agent 硬删除均不产生半条任务。
- [ ] 修改项目服务、路由与 `ProjectTaskRepo.create_with_context`；新客户端发送修订号，旧客户端仅在空名单时保留旧语义。项目访问检查先于 Agent 细节，已授权成员见到的失效专家统一可恢复错误。GET 对已取消共享/停用专家返回 `name=null`、`description=null`。
- [ ] 定向重跑上述四组测试；全后端 Ruff check/format、mypy、i18n 测试与相邻项目/任务 API 回归。报告本机 PostgreSQL 实库是否真的执行，不把静态 DDL 检查算实库通过。

## 任务 2：前端管理与任务选择

- [ ] 在新 `projectExperts.test.ts` 与组件测试先写失败用例：GET/PUT 请求字段、owner/admin 与 member 分支、添加/移除/排序、取消不写、保存冲突重载真实数据、项目切换丢弃晚到响应、不可用专家不显示私有资料、提交中不可关闭。
- [ ] 实现 ProjectExperts 双栏弹窗：已选数量、卡片、搜索、添加/移除/排序、取消/确定；只使用真实接口结果。管理入口只对 owner/admin 可用，其他成员只读；连接器/技能/定时仍保持不可用标记。
- [ ] 在 ProjectTasks 现有 028 创建测试先写非空名单过滤、空名单兼容、配置修订号提交、409 清确认与刷新、专家失效、晚到响应和取消中状态；更新类型化 create API。成功仅进入私密对话，不自动发送草稿。
- [ ] 运行受影响 Vitest、`npx tsc -b`、目标 ESLint/Prettier 与生产构建；记录 1280×768 和约 800×728 的布局、焦点与错误状态，交 Codex 复核。

## 任务 3：Codex 集成和验收

- [ ] 按白名单审查两个独立 worktree 的完整 diff，仅整合任务文件。对跨层字段名、409 映射、修订号和不可用资料做 RED→GREEN/人工核对。
- [ ] 独立重跑定向/相邻测试、类型/格式/构建；用隔离本地三身份 HTTP 和真实登录 Chrome 走 owner 配置、member 创建、outsider 404、旧修订 409、撤共享失效、旧任务保留。PostgreSQL、真实模型、安装包和 WorkBuddy 逐状态视觉未实测时分开记录。
- [ ] 固定代码提交交 GLM 只读审查。Codex 修复具体 P0/P1，复测后只暂存白名单文件并提交；核对用户远端 `main` 仍是本分支基线，快进推送并再次比较 SHA。更新 25 项与 11 条项目旅程台账，不凭此片核销整条 PS-07。
