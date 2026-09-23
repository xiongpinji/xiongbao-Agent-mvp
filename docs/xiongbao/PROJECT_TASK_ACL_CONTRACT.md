# 熊宝-Agent 项目任务归属与权限合同（PS-05，第一片）

状态：2026-09-24 设计合同，尚未实现。下一数据库版本为 021，必须同时提供 SQLite 与 PostgreSQL 迁移。此片仅让成员把**自己已有的 Dashboard 对话任务**归属到一个项目，并在项目里重新找到自己的任务；不开放共享正文或云端协作。WorkBuddy 5.5.6 已观察到项目任务“全部/个人/协同”、默认私密提示和本地/云端入口；以下权限边界是熊宝的产品设计，并非对 WorkBuddy 内部实现的推断。

## 既有事实与不可越过的边界

- `threads.user_id` 是现有任务所有者；`ThreadRegistry` 的 Dashboard 列表、历史、文件、运行流与 Session 绑定按所有者隔离。`project_members` 只授予项目元数据权限，**不授予成员读取其他人的任务标题、正文、附件、产物、执行流、工具调用或本机工作目录**的权限。
- 第 021 片不得放宽现有 `/api/agents/{agent_id}/threads`、历史/导出、文件、WebSocket、后台任务和搜索的 owner 检查。不能给项目成员批量列出所有 `thread_id` 再让前端隐藏；服务端查询自身先过滤到有权任务。
- 项目任务路由的操作者只取 `current_user.id`，不提供 `as_user`/客户端 actor 参数，也不因为实例管理员或项目管理员身份绕过任务所有权。项目管理权与私密任务读取权始终分开。
- Agent 专家团 `teams`、RAG 知识库、Agent 级 `workspace_dir` 均不是人类项目任务。此片不改变执行目录、工具授权、个人 OAuth、模型或项目配置注入，也不把“本地/云端”按钮接成假任务。

## 021 私密关联数据与 API

`project_task_links(project_id, thread_id, owner_user_id, source, created_at)` 以 `thread_id` 唯一，一个任务至多归属一个项目；`project_id`/`thread_id`/`owner_user_id` 分别引用项目、任务和用户，删除关联不删除原任务。`source='manual'` 仅由服务端根据允许的 Dashboard 任务写入，客户端不能指定来源或所有者。保存联结时必须在同一写事务内再次检查：操作者仍是项目成员、`threads.user_id` 等于操作者、`threads.channel_type` 是 Dashboard、任务所属 Agent 对当前用户可访问、该任务未归属其它项目。`project_events` 只记项目/任务 ID 和动作，不记标题、消息、路径或凭据。

| 路由 | 行为 | 授权与失败 |
| --- | --- | --- |
| `POST /api/projects/{project_id}/tasks/links`，`{thread_id}` | 关联自己的既有 Dashboard 任务；返回安全摘要。重复关联同一项目幂等，不重复事件。 | 非成员/未知项目统一 404；他人、未知或非 Dashboard 任务统一 404；自己在其他项目已关联的任务为 409。 |
| `GET /api/projects/{project_id}/tasks?q=&limit=&offset=` | 仅列当前用户拥有且仍在该项目的任务；服务端按标题字面搜索、按活动时间和任务 ID 稳定倒序分页；`has_more`。 | 非成员/未知项目统一 404。成员不能由总数、筛选或分页得知其他人的私密任务。 |
| `GET /api/projects/{project_id}/tasks/{thread_id}` | 返回当前用户自己任务的安全摘要。 | 项目非成员、他人任务、跨项目任务和不存在均 404。 |
| `DELETE /api/projects/{project_id}/tasks/{thread_id}` | 仅移除项目归属，保留原对话、历史、附件和 Dashboard Session。 | 仅关联任务本人可操作；缺失/跨项目/他人均 404。重复删除不写事件。 |

安全摘要仅含 `project_id, thread_id, owner_user_id, agent_id, title, source, last_active, created_at`；不返回 `session_key`、`artifacts`、`pending_plan_path`、消息、工作目录、个人配置或模型凭据。输入标题搜索中的 `%`、`_` 和反斜杠按字面转义，`limit` 最大 100。成员被项目管理员移除时，同一事务删除其 `project_task_links`；他的原任务仍归本人所有，旧项目深链和列表立刻 404。任务本身被删或项目被删时由外键级联清除关联；不得保留可枚举的孤儿。

路由注册时固定的 `/tasks/links` 必须先于动态 `/tasks/{thread_id}`，避免请求被误判为任务 ID。任何关联操作的数据库写入与事件追加须在同一事务中完成；重复请求不产生第二个事件。

## 实施、测试与下一片

先写失败的迁移/仓库/API 测试，再实现 021 双数据库脚本、SQL repo、项目任务 service、独立 router 和最小 DI/app 接线。测试 owner、member、outsider、跨项目、他人任务、重复/并发关联、字面搜索/分页、移除成员、解绑后原历史仍可由任务本人读取；SQLite 新库和 020→021 升级必须过。PostgreSQL 需实库验证才称通过，静态 SQL 对照不算运行验收。

本片落地后，项目“任务”页可以展示**个人任务**及真实来源，不得显示“全部/协同已可用”。后续 PS-05B 才添加显式分享/撤销与任务级 ACL，逐一覆盖历史、搜索、附件、文件下载、导出、WebSocket、后台执行入口；PS-05C 再实现协同写入、队列/移交和项目配置快照。每批须以双用户/撤权者的真实 API 与界面旅程证明，不因任务出现在项目列表就核销完整 PS-05。
