# 熊宝-Agent 项目任务对话文本只读合同（PS-05B-2A / 025）

状态：2026-09-24 经 GLM 对固定草案 `993d6ee2` 的只读审查后修订的实施合同。GLM 条件 GO、无 P0；本版修复其两项 P1：版本化历史不能误报空正文、流错误不能混入普通 assistant 文本。此片是 WorkBuddy 项目“任务分享”的一个受限增量，不代表分享面板、附件、搜索、实时协作或项目空间 1:1 验收。WorkBuddy 的可见交互是对标目标；以下表结构与权限是熊宝自己的设计。

## 用户行为与授权边界

- 024 的卡片分享继续默认**只显示标题等安全摘要**。升级到 025 后，所有既有卡片授权的正文权限都必须为 false；代码、迁移和 UI 不得根据 `role='reader'` 自动授权正文。
- 任务本人可以对已有有效卡片授权的**同一项目指定成员**，再单独确认授予“只读对话文本”。确认文案说明：接收者可查看本片支持且已完成投影的历史与后续文本；文本本身可能包含用户粘贴的路径、链接或敏感内容；版本化历史存储暂不提供正文，界面会明确显示待支持/未同步，不能承诺所有记录都可见。本人可分别撤销正文权限或整个卡片授权；撤销卡片须在同一事务内删除正文授权。卡片重新授予后，正文仍须再次确认。
- 接收者只能在项目任务只读详情查看文本。项目 owner/admin 身份或项目成员资格本身不提供任务正文权限；接收者不能回话、运行工具、恢复 HITL、打开 owner 的 `/chat`、文件/附件/工作区、原始历史、导出或流。正文页面不得生成这些深链。
- 读者看到正文是否开放，由服务端随卡片返回 `can_read_text` 布尔值；卡片-only 时显示明确文案而不请求正文。打开的对话面板在 404、项目切换、成员撤权、卡片撤权或正文撤权后清空已缓存消息；旧请求晚到不得覆盖新状态。界面按纯文本输出，禁止把模型输出当 HTML 渲染或自动加载远程图片/链接。此片没有外链、微信、朋友圈、二维码分享能力，现有 UI 不应暗示已经支持。

## 025 数据模型与事务

- 新增成对 `025_project_task_content_grants.sql` / `.pg.sql`。创建 `project_task_content_grants(project_id, thread_id, grantee_user_id, granted_by_user_id, granted_at)`；前三列构成主键并作为复合外键引用 024 的 `project_task_shares(project_id,thread_id,grantee_user_id)`，`ON DELETE CASCADE`。`granted_at` 为非负 Unix 秒；授予者引用 users，用户删除可置 null。现有 024 行不做回填，不改写历史迁移。成员/项目/任务解绑会经 024 级联删除新行。
- 正文授予、撤回、卡片撤回都先验证 actor 的当前项目成员资格及任务本人身份；正文授予还验证接收者当前同项目成员资格、有效卡片授权、未归档项目及精确 Dashboard `:dm` 绑定。PostgreSQL 按 user ID 顺序锁 actor/接收者成员行，随后锁卡片分享行再增删正文行；SQLite 依既有 `BEGIN IMMEDIATE` 串行化。卡片撤回必须修改 `revoked_at` 并删除正文行于同一事务；即便正文授予与卡片撤回交错，也不得留下再次分享后自动激活的旧正文权。
- 首次正文授予返回 201，已有效则 200 且不重写时间；撤回返回 204，重复撤回 204。撤回在归档项目也可做；归档禁止授予/再次授予（403）。正文授予的目标无效、跨项目、本人/非成员、任务非本人或卡片未授予均用统一任务 404；项目不可见也用既有项目 404。只记录相关人可见的授权元数据，不写含任务 ID/标题的公共 `project_events`。

## 项目绑定 HTTP 合同

操作者始终取 `current_user.id`，不接受 `as_user`。保留现有 `/api/agents/{agent_id}/threads/*`、`/chat`、WebSocket/SSE、HITL、文件、下载、导出、搜索的 owner 检查，不通过任何旧路由间接扩权。

| 路由 | 权限及输出 |
| --- | --- |
| `GET /api/projects/{project_id}/tasks` 与 `GET .../tasks/{thread_id}` | 在既有安全摘要后添加 `can_read_text`：本人为 true，读者仅在卡片有效且有 025 正文授权时为 true。默认 024 读者为 false。列表仍按原单 SQL 稳定分页和 `own/shared/all` 范围过滤。 |
| `GET .../tasks/{thread_id}/shares` | 本人的有效卡片授权列表追加 `can_read_text`；只有本人可读；024 既有字段保持。 |
| `POST .../tasks/{thread_id}/shares/{user_id}/text` | 任务本人显式授予该有效卡片接收者正文；201/200 返回 `{user_id, granted_at}`。请求体为空，不得由客户端指定授予者、角色、时间或访问范围。 |
| `DELETE .../tasks/{thread_id}/shares/{user_id}/text` | 任务本人撤销正文；204，重复 204；不撤销卡片。 |
| `GET .../tasks/{thread_id}/messages?limit=&before_seq=` | 任务本人或**同时持有有效卡片和正文授权的当前项目成员**可读取。非授权统一 404；不读取原始历史/执行物。`limit` 为 1–100，默认 50；`before_seq` 为正整数，可选，返回 `seq` 递减的旧消息页及 `next_before_seq` 和 `has_more`。 |

正文响应仅允许 `status=ready|pending`、`items:[{seq,role,text,created_at,truncated}]`、`has_more`、`next_before_seq`。`role=user|assistant`，`truncated` 为布尔值。当既有 `thread_history_projection` 尚未 ready 时返回 `status=pending,items=[]`，不触发回填或模型执行；若投影 ready，使用现成 `thread_messages` 行，不访问 checkpoint blob。若服务存在 `HistoryArchive`（版本化历史模式），本片统一返回 `status=pending,items=[]`，既不读取 archive 也不把 `thread_messages` 的空行/旧片段谎报为完整历史；该模式的真实文本共享归后续独立批次。读取授权与消息行须在一个 DB 事务/一致快照内：PostgreSQL 与写路径同序，先按 user ID 升序锁成员行，再锁卡片分享行，最后锁正文授权行；SQLite 使用显式 `BEGIN IMMEDIATE` 事务保证跨连接一致。获取原始行后立即结束事务，再做 JSON 解析/截断以缩短锁持有。撤权提交后开始的新请求须 404；已经开始且获授权的响应可能完成，UI 收到后续 404 必须清空缓存。分页游标使用行 `seq`，不依赖 offset；序号既有单调唯一约束使追加消息不会重排旧页。`has_more` 由读取 `limit+1` 条**原始**行决定，跳过的行也占游标，不能根据净化后的数量计算。不可向未授权请求暴露 projection 状态或消息数量。

服务端解析白名单仅为外层字典、`data.content`、`data.tool_calls` 和 `data.invalid_tool_calls`（仅检查**非空**，非空即整行跳过；正常 AI wire 也含空列表）、`data.additional_kwargs.octop_stream_error`（仅检查布尔真，真即整行跳过），并核对存储 `role` 和 wire `type` 是匹配的 human/user 或 ai/assistant。不读取或返回任何其它附加字段。只接受纯字符串、列表中的纯字符串项或明确 `type=text` 的文本块；其它块、工具消息、系统消息、工具调用、思考/推理字段、附加元数据、模型参数、路径/附件结构、异常堆栈全部排除，不把原始 `message_json` 返回客户端。空/无效正文行跳过。分页 `next_before_seq` 以本次扫描的最后一个原始 seq 为准，跳过的行也占游标，避免重复或漏扫。文本中的普通字符原样显示，但前端只以 escaped plain text 渲染，绝不解析 Markdown/HTML 或自动展开 URL。每条输出文本至多 32768 UTF-8 字节，在字符边界截断并设置 `truncated=true`；100 条的页上限因此约为 3.2 MiB 文本。原始 `message_json` 超过 256 KiB **UTF-8 字节**的行整体跳过，避免一次读取被大体积多媒体块拖垮；这类遗漏须在界面如实提示“部分非文本或超大消息未显示”，不能称为完整历史。

## 测试与验收

- 先写失败测试：全新及 024→025 升级、旧卡片授权不升级、双库 FK/约束、授予/重复/撤销/重授、卡片撤销→重授后正文仍关闭、成员/所有者移除及解绑、归档、管理员越权、跨项目/外人 404、双连接交错授权撤回。PG 无可用实库时只记录 DDL 检查，不能宣称实库通过。
- 文本投影以真实 `message_to_dict` 形状测试：普通 human/ai（含空 `tool_calls=[]`）、文本块、工具/系统/带非空 tool_calls 或 invalid_tool_calls 的 ai、`octop_stream_error` 标记及错误堆栈、思考字段、图片/音视频/文件块、畸形 JSON、超长文本、分页跨被跳过行、未 ready 状态、存在 HistoryArchive 的 v2 任务仍 pending、撤权并发。另测同项目未分享成员、项目 admin、只有卡片无正文授权的读者 `/messages` 均 404；只撤正文后卡片仍可见且 `can_read_text=false`。断言响应不存在 `message_json`、`additional_kwargs`、`tool_calls`、`usage_metadata`、`session_key`、`artifact`、`path` 等结构字段。
- 前端测试卡片-only、正文确认/撤销、读者分页与只读、项目切换和晚到请求清除、404 清除、文本转义及中英词条。Codex 在隔离 owner/member/outsider 环境验证真实 API/UI，GLM 审查固定代码快照；验证、集成、推送证据分开记录。附件、外链、实时流、协同写入和 WorkBuddy 视觉逐状态未覆盖时，项目空间仍维持未核销。
