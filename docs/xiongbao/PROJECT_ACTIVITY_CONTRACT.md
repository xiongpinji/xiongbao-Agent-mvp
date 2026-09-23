# 熊宝-Agent 项目动态与留言合同（PS-03A）

状态：2026-09-24 设计合同，尚未实施。依赖已推送的 018–021；下一迁移编号为 022，必须提供 SQLite 和 PostgreSQL 配对脚本。本片把现有项目安全事件与新建的纯文本留言展示为项目动态，支持“与我相关 / 成员动态”及稳定分页；不把任务私聊、邀请密钥、项目指令或连接器凭据发布到时间线。WorkBuddy 5.5.6 的动态页签和筛选已在本机只读观察，留言能力另见官方更新日志；以下接口与权限是熊宝自己的设计。

## 产品边界与事件白名单

- `project_events` 是项目域已有的事务事件表。**只允许** `project.created`、`project.updated`、`project.member_joined`、`project.member_role_changed`、`project.member_removed`、`project.todo_created`、`project.todo_updated`、`project.todo_deleted` 和本片新增的 `project.message_created` 进入成员动态。白名单在服务端 SQL 查询阶段执行，不能先分页再在前端过滤；未知/新增事件默认不可见。
- 不公开 `project.invite_created`、`project.invite_revoked`、`project.join_requested`、`project.join_approved`、`project.join_rejected`：其邀请/申请对象和请求状态属于管理流程，直到有独立的接收者 ACL 和消息中心。021 私密任务关联/解绑本就不写 `project_events`；未来若新增任务事件，默认仍不可见，须单独审查任务级 ACL。
- 安全动态响应只含事件类型、时间、操作者的安全显示名、许可展示的对象类型和 ID，以及留言正文；绝不直出 `payload_json`、`session_key`、任务 ID/标题、邀请 ID/令牌、项目指令、附件路径或凭据。项目/待办/成员事件由前端按事件类型翻译成文案，不把任意事件载荷解释为 HTML。待办 ID 只对当前成员可见；私密任务 ID 无任何动态入口。
- “成员动态”列上述白名单的本项目事件；“与我相关”列当前用户亲自操作的事件、目标是该用户的成员变动，以及待办事件中当前或此前处理人是该用户的事件。留言仅作者本人进入“与我相关”；本片不实现 @ 提及。**过滤必须在数据库分页之前完成**，不能让别人的动态占掉本人的页数。
- 已移除的成员不能再读项目动态、留言或旧深链；仍在项目的成员可看到历史成员和已删除待办的安全事件。角色 owner/admin/member 都能发布纯文本留言；项目成员资格不授予任务私聊正文访问权。

## 022 留言数据

新表 `project_messages(message_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, author_user_id INTEGER NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL)`：项目 FK `ON DELETE CASCADE`，作者 FK `ON DELETE SET NULL`，`CHECK(length(body) BETWEEN 1 AND 4000)`，索引 `(project_id, created_at, message_id)`。服务端去除首尾空白后再验证 1–4000 字符；正文按纯文本存储和渲染。作者账号删除后历史留言保留，显示为“已删除用户”。本片只创建和读取，不开放编辑/删除、图片、附件、富文本或 @ 提及；相关能力在 PS-04B/PS-06A 有独立存储与撤权设计后再扩展。

一次留言写入在**同一事务**完成：先验证并锁定 `project_members` 当前用户行（PostgreSQL `FOR SHARE`，SQLite 写事务串行），再插入 `project_messages` 与一条 `project.message_created` 事件，事件 `object_id=message_id`、`payload_json='{}'`。成员移除与留言创建按“成员行 → 留言/事件行”同序列化；被移除后新请求统一 404。失败回滚时不得留下半条留言或无正文事件。

## HTTP 与游标合同

| 路由 | 请求与响应 | 权限/失败 |
| --- | --- | --- |
| `GET /api/projects/{project_id}/activity?scope=members|related&limit=20&cursor=` | `items`、`next_cursor`；时间倒序，用 `(created_at,id)` 双键稳定排序；`limit` 1–50；游标表示上一页最后一条双键，查询用严格小于并按白名单和 scope 过滤后取 `limit+1`。 | 仅当前成员；非成员/未知项目统一 404。非法 scope/limit/cursor 为 422。不返回未授权项目的总数。 |
| `POST /api/projects/{project_id}/messages` | `{body}`；返回新动态安全摘要，201。 | 仅当前成员；空白/超长/附加 actor 或任意额外字段 422；非成员/未知项目统一 404。正文纯文本，服务端决定作者和事件类型。 |

游标格式为服务端编码的 `created_at:id`，仅用于排序，**不是授权凭证**；每次翻页都重新校验成员。解析失败返回 422。并发插入新事件不应造成已看过的旧事件在下一页重复或跳页；相同秒内按 `id DESC` 打破平局。SQLite 与 PostgreSQL 使用各自的 JSON 读取表达式做“与我相关”待办处理人过滤；只检查白名单事件已知的 `assignee_user_id`、`from_assignee_user_id`、`to_assignee_user_id` 安全数字字段，不对未知 JSON 路径做猜测。数据库端对 `actor_user_id`、成员事件 `object_id` 和待办处理人字段做 scope 条件；留言仅作者匹配。结果映射再次做类型白名单，防未来新增事件误映射。

具体游标为 `base64url("<created_at>:<id>")`，不带填充，解析后两个值都须是正整数，游标最长 64 字符；编码不用于保密，客户端只当不透明字符串原样回传。每条 `items` 的固定字段为 `event_id`（整数）、`event_type`、`actor_user_id`（可空）、`actor_name`（可空）、`object_kind`（`project|member|todo|message`）、`object_id`（仅项目/待办/留言可有，成员目标 ID 不公开）、`message_body`（仅留言可有）、`created_at`。不返回原始载荷或其他动态字段。对成员目标只返回通用动作文案和操作者；当前成员列表仍由既有独立 API 展示。

## UI 与验收

项目详情“动态”页签接真实接口：默认“与我相关”，可切换“成员动态”；显示时间、操作者、安全动作和纯文本留言，支持加载更多、刷新、空态、失败重试和发表中禁重。切换项目/筛选后旧项目或旧筛选的动态绝不能闪现；晚到的响应不得覆盖新项目。未经 API 支持的评论图片/富文本入口不出现。视觉用熊宝红黑金品牌和项目页既有布局，最终 WorkBuddy 1:1 仍需逐状态截图与实机交互对照。

先写失败测试，再实现迁移、repo/service/router 与 Dashboard API/组件。至少用 owner、member、outsider 验证：两成员各发一条留言并刷新可见；“与我相关”按操作者/目标/历史处理人正确过滤；服务端分页跨同秒与新事件插入稳定；非成员列表/发表均 404；移除成员后旧令牌 404；留言写入失败原子回滚；021 本人私密任务 ID/标题不在任何动态响应；邀请令牌/项目指令和原始 JSON 不出现在响应；React 对 `<script>` 字符串只按文本渲染。PG 迁移、锁与游标查询必须用真实实例验证才称 PG 通过；静态 SQL 和模拟连接仅算补充。Codex 独立运行定向/回归测试、隔离浏览器三身份旅程、GLM 只读审查后才推送。

本片完成不代表 PS-03 全部对齐：消息中心、@ 提及、图片/富文本、精细通知、历史审计修订与 WorkBuddy 视觉逐状态仍在后继批次。
