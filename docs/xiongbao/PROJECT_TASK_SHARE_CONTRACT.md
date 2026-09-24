# 熊宝-Agent 项目任务显式共享合同（PS-05B-1）

状态：2026-09-24 经 GLM 固定 `63658362` 草案只读审查后修订的实施合同；GLM 结论为有两项须在冻结前补足的 P1，本版已补足迁移编号依据与 `all` 单查询分页要求。此片只共享项目任务**卡片摘要**，不共享对话正文、附件、工作区文件、执行流或操作权。它是 PS-05B 的第一片，不能单独核销 WorkBuddy 项目任务或“协同任务”完整旅程。WorkBuddy 的可见行为是对标目标；以下数据库与权限细节是熊宝自己的实现决策，不推断 WorkBuddy 内部结构。

## 用户可见边界

- 默认仍私密。只有任务本人（`threads.user_id`，且仍为项目成员）可以把自己已关联此项目的 Dashboard `:dm` 任务，显式共享给**同一项目的指定成员**，角色固定为 `reader`；项目 owner/admin 身份本身无权分享或读取别人的任务。自己不能作为接收者。
- 共享的是既有安全摘要 `project_id, thread_id, owner_user_id, agent_id, title, source, last_active, created_at`，另外增加 `access=owner|reader` 供界面决定操作。接收者可在“全部任务”和“分享给我的”列表中看到卡片，点开只显示该摘要和明确的“对话内容尚未共享”说明。自己的任务仍沿原有 `/chat/{agent_id}/{thread_id}` 打开。共享卡片绝不跳转到该 `/chat`、Agent 工作区、文件、历史、WebSocket 或 HITL 路由。
- 分享确认框要明确告知“目前只共享任务标题与卡片信息，正文和附件仍私密”；接收者页也要明确只读范围。UI 不能把此片叫作完整的对话共享或可写协同。“全部任务”须说明仅为“我的 + 分享给我的”，不能暗示项目内全员任务可见；既有 `shareReason`、范围禁用提示和项目任务编辑器文案须在中英双语同步改为当前能力。读者卡片根据 `access=reader` 分支渲染为项目内只读详情，不生成 `/chat` 链接。禁用的本地/云端、移交与协同写入入口继续诚实禁用。

## 数据模型与迁移 024

新增成对 `024_project_task_shares.sql` / `.pg.sql`，不得改写已推送的 021/023。理由是 023 已推送到用户仓库 `main`，本地验收数据库也可能已处于 v23；若把分享 DDL 折回已应用的 023，升级器会跳过它，故使用 `AGENTS.md`“本地 DB 已在 N 时切 N+1”的例外。`project_task_shares` 保存 `(project_id, thread_id, grantee_user_id)`、`granted_by_user_id`、`role='reader'`、`granted_at`、`revoked_at`，三元组唯一。024 须先为 `project_task_links` 建立非部分、非表达式的唯一 `(project_id, thread_id)` 索引，之后才能建分享表和同项目复合外键；分享行通过该外键级联到原关联，并通过 `(project_id, grantee_user_id)` 复合外键级联到成员行。`role IN ('reader')`、`granted_at >= 0`、`revoked_at IS NULL OR revoked_at >= granted_at` 为两库一致的 CHECK；时间为 Unix 秒。`granted_by_user_id` 引用用户，删除用户时置空。使用已存在的项目成员复合唯一键。有效授权列表/详情索引可以采用 `WHERE revoked_at IS NULL` 的部分索引，两库保持等价。

撤回更新 `revoked_at`，不删除分享行；再次分享同一接收者可把它重激活并刷新授予者/时间，返回 200。接收者成员资格被删除时该行级联删除；若其日后重新入组，所有者再次分享是新授予，返回 201。删除项目、任务关联或任务本身也级联删除分享行。删除任务所有者成员资格时，021 的 `remove_member` 在同事务删除该所有者的关联，进而级联分享行。项目公共 `project_events` 不记录 `thread_id`、标题或分享操作；PS-09 再设计仅相关人员可读的审计记录。

## HTTP 与授权

沿用 `/api/projects/{project_id}/tasks`，操作者只能来自 `current_user.id`，禁止 `as_user` 和管理员绕过。所有可见性由服务端查询过滤；非成员/未知项目统一 404，未授权/未知/跨项目任务统一 404，不能从总数、搜索或分页推断私密卡片。

| 路由 | 行为 |
| --- | --- |
| `GET /tasks?scope=own|shared|all&q=&limit=&offset=` | 默认 `own` 保持 021 行为；`shared` 只返回当前成员的有效 reader 授权；`all` 是两者去重后的并集，必须使用单一服务端 SQL 查询（或有等价单一排序/分页的 SQL UNION），在合并后的结果上仅做一次稳定 `ORDER BY … LIMIT ? OFFSET ?`，按 `limit + 1` 得出 `has_more`，不得分别对 own/shared 分页后在应用层拼接。标题搜索字面匹配，页大小至多 100，不返回跨权限计数。无效 `scope` 返回 422。 |
| `GET /tasks/{thread_id}` | 仅本人或同项目有效 reader 授权可取安全摘要；两者以外统一 404。 |
| `GET /tasks/{thread_id}/shares` | 仅任务本人列出当前仍有效的接收者 ID、授予时间和固定 reader 角色；不返回接收者的个人资料或凭据。 |
| `POST /tasks/{thread_id}/shares`，`{user_id}` | 仅任务本人向当前项目成员授予 reader。首次 201，已有效则 200 且无额外写入；撤回后重新授予 200，角色与操作者由服务端确定。非成员接收者、自身或任务不匹配均统一 404。 |
| `DELETE /tasks/{thread_id}/shares/{user_id}` | 仅任务本人撤回，已有授权 204；重复撤回仍 204，不产生额外写入。任务不存在/非本人统一 404。 |

分享列表的响应固定为 `{"items":[{"user_id":7,"role":"reader","granted_at":<Unix秒>}]}`，仅含有效授权；POST 的 201/200 响应是单个同形状授权对象，DELETE 204 无响应体。POST 请求体只有接收者 `user_id`；授予者、角色、时间由服务端写入，不接受客户端指定。此 DTO 已通过 Agent Orchestrator 问答同步给前端实施线，Codex 在集成时核对后端候选。

写操作在一笔事务中先锁项目成员行，再核实任务链接和 `threads.user_id`、Dashboard `:dm` 绑定，最后插入/更新分享行。PostgreSQL 对相关成员行使用 `FOR SHARE`，与移除成员的 `DELETE` 互斥；按用户 ID 固定锁序避免相反方向分享造成死锁。SQLite 写事务使用既有 `BEGIN IMMEDIATE`。复合外键与唯一约束兜住并发撤权、解绑及重复授予。归档项目可继续读取与撤回授权（隐私退出始终可用），但新授予/再次授予返回 403，且在写事务中核对 `archived`。读取使用当前数据库提交后的成员与有效分享判定；撤回/移除成员/解绑提交后的新请求必须立即 404，不借助前端缓存继续显示旧卡片。客户端收到 404 时清除旧详情和对应列表行，并重新取服务端列表。固定 `/tasks/links` 保持在同方法的 `/{thread_id}` 动态路由之前。

保持现有 `/api/agents/{agent_id}/threads/*`、历史/导出、工作区/媒体/下载、WebSocket/SSE、HITL、上传、搜索和后台执行的 owner/agent 检查**完全不变**。此片不新增项目级正文、文件或下载入口，也不把项目成员当成 Agent 共享用户。阅读共享卡片不改变任务所有者未读状态，不触发历史回填或模型调用。

## 测试和交付门槛

先写失败测试，再实现双数据库迁移、SQL repo、service、薄 router、前端 API/页面和中英词条。后端覆盖新库与 023→024 升级、复合外键、默认私密、owner/admin/member/outsider、同项目/跨项目、授予/重复/撤回/再授予、移除接收者/所有者、归档授予禁用但撤回可用、解绑、字面搜索、`all` 跨 own/shared 边界的单查询分页、SQLite 交错写、PG SQL 与实库（可用时）及公共事件不泄漏。前端覆盖三个范围切换、所有者确认/撤回、读者无 `/chat` 深链、项目切换与撤权后旧数据清除、加载/空/错误态及中英词条。Codex 对隔离 owner/member/outsider 浏览器关键路径独立验收，GLM 对固定代码快照只读审查；通过后由 Codex 按白名单提交并推送。

后继 PS-05B-2 才设计**任务级**正文/附件/下载读面，统一投影与历史归档两条后端读取路径，消除旧 URL 与撤权窗口；PS-05B-3 再处理 WebSocket/SSE 订阅撤权、搜索/导出与其它全入口 ACL。PS-05C 才开放协同写入、移交和项目内创建。只有这些入口分别通过测试与真实界面验收后，才可核销 PS-05B 或 WorkBuddy 对应旅程。
