# PS-04D 待办附件与子待办设计草案

状态：**第三版已获用户批准，按技术门禁连续实施**。2026-10-04 用户回复“批准两项按门禁连续推进”，并统一授权 Codex/按文件分工的实施子代理承接、独立只读审查与本地测试替代不可用的 GLM；执行边界见 [全量交付计划](WORKBUDDY_FULL_DELIVERY_20261004.md)。严格附件存储门禁仍未通过，D2 仍依赖 D1 接受，不因授权宣称已交付。初稿基线为 `3cd120a5ef888e15347cc4c613883a30a428f0dc`；第三版以已推送主线 `e3e3ebf0ca71f805febbcb53fea470aa93900ac7` 的现存代码为核对对象，明确投影新鲜度、旧列表子项语义和表格字段 schema。此设计与授权不核销文件模式、外部来源或全产品视觉验收。

## 目标与证据

本批补齐计划事项的私密附件和子待办能力，继续使用现有 todo ID、版本、日期、目录、富文本和评论，不把计划事项转换成 AI 工作会话。

现有实机记录见 [PS-04C WorkBuddy 观察](PROJECT_TODO_PS04C_WORKBUDDY_OBSERVATION.md) 和 [逐页观察](WORKBUDDY_LIVE_UI_AUDIT.md)：新建待办有添加附件入口；表格字段菜单有附件；表格设置有显示子待办。观察没有提交附件或创建子待办，未证明类型限制、删除/恢复、层级深度、联动状态及排序行为。

[腾讯云项目说明](https://cloud.tencent.com/document/product/1831/138797)确认五种计划视图，并明确计划事项和 AI 任务是两个概念；它没有给出待办附件和子待办的详细事务语义。下面的数据、权限、限额和一级层级规则是熊宝产品提案，不能作为 WorkBuddy 已实测行为引用。实现通过后只核销本合同的功能，1:1 仍需同夹具实机对照。

## 三种方案与推荐

| 方案 | 结果和代价 |
| --- | --- |
| 推荐：待办独立附件 + 保留身份的一级父子关系 | 附件随待办 ACL 可见；子待办仍可用日期、标签和评论；迁移增加独立表，不重建现有父表。分 D1、D2 连续实施，共享文件串行整合 |
| 直接链接项目资产，子待办写进 Markdown 清单 | 资产生命周期和待办附件混在一起；正文清单没有独立指派、版本和查询；无法完成真实计划能力 |
| 完整任意深度树 + 统一所有文件体系 | 同时改变目录、任务引用、回收站及五视图查询，缺少 WorkBuddy 深度观察；需要另行批准更大合同 |

D1 为独立私密附件及表格真实附件列；D2 为同项目一级子待办、详情入口和表格显示开关。附件不自动进入项目资产、评论或模型上下文；外部数据源仍交 PS-10。一级是本批明确交付边界，不能宣称支持任意深度或已经完成 WorkBuddy 1:1。

## D1：归属、权限与生命周期

- 每个附件属于一个 `(project_id,todo_id)`，使用不可猜测 `attachment_id` 和服务生成的 object key。上传文件名只做显示，不用作磁盘路径；不存在公开静态 URL、外部 URL 拉取、跨项目绑定或客户端 object key。
- 当前项目成员可读取活待办的附件；非成员、撤权、跨项目 ID、未知或已删除待办统一 404。已移除附件的直接下载也为 404，不能拿旧链接继续读。
- 上传、移除和恢复沿用待办字段修改权限：owner/admin 或待办创建者/当前处理人。仅作为一般项目成员能评论，并不自动获得该待办的附件写权。服务器在写事务重新查角色和处理人；前端缓存、上传者 ID 和客户端角色不能授权。
- 项目归档后的附件只读，新附件写入返回 409/project_archived；不借本批改变旧待办和评论的已批准归档语义。完整归档/恢复政策仍属 PS-09。
- 移除为可恢复的软移除。活附件列表不再显示它，但已移除列表可查看名称及恢复；恢复重新检查当前待办写权、数量及集合 revision。被删除待办没有附件恢复入口。附件无版本历史，不把同名新文件当作旧附件的新版本。
- 本批不做永久删除或自动清理已移除的有效对象。已移除附件继续占容量；待办软删除也不释放实际仍存储的字节。容量满时提示实际占用，不能假装移除已经释放磁盘。后续永久回收另立合同。

提案限额：每个文件最多 25 MiB；每个待办有效附件最多 20 个，含已移除最多 100 个；单个项目独立附件容量 1 GiB，包含活附件和已移除附件。它不冒充 WorkBuddy 账号容量，也不混用评论图片的 512 MiB 配额或当前资产字节展示。先支持本地文件上传、下载和安全图片缩略图；不执行或解压附件，不加入付费解析、RAG、Office/HTML/SVG 内联预览。

文件名 trim 后 1–120 字符，拒绝路径分隔符、控制字符和纯点名称。下载始终 `Content-Disposition: attachment`、`X-Content-Type-Options: nosniff`、`Cache-Control: private, no-store`。图片预览单独由认证端点提供，只允许完整解码通过的 PNG/JPEG/WebP，最多 8 MiB、4000 万像素；其他文件保留下载入口，不能按客户端 MIME 直接内联 HTML。图片即便不符合预览条件，也不把合法普通附件误写成上传成功的可预览图片。

## D1：数据与并发合同

新增独立 `project_todo_attachment_state(project_id,todo_id,revision,updated_at)`、`project_todo_attachments`、`project_todo_attachment_usage`。附件元数据至少包含项目、待办、attachment ID、上传者、规范化显示名、实际字节数、服务端 SHA256、服务判定的预览类型、object key、创建时间、可空 removed_at、client_request_id 和请求指纹。

使用 `(project_id,todo_id)` 复合 FK，保留现有 todo 主表。附件集合 revision 从 1 开始，上传/移除/恢复成功恰加 1；集合无实际变化不增加。附件操作不改 todo.version 或日期，使用自己的 expected_revision，不将评论或正文并发误报成附件丢失。待办响应增加真实 `attachment_count` 和 `attachment_revision`，读取在同一成员复核和 DB 快照中投影，不在返回以后逐项补查。count 只算活附件；查询、分组、分页不把附件 JOIN 展开成多条待办。

仅有 `todo.version` 和 `catalog_revision` 不能判断上述 count 是否新鲜：附件修改不增正文版本，迟到的旧 GET 可以覆盖新 count。D1 同时新增 `project_todo_display_state(project_id,todo_id,revision,updated_at)`，为既有及新待办初始化 `display_revision=1`。从激活起，**所有待办自身字段或附属投影**的成功变化都对该待办的 display revision 恰加 1：现有创建后的字段 PATCH/批量字段更新、附件集合成功变化，以及 D2 的关系和父统计变化；失败、无变化和幂等重放不加。目录元数据变化仍由独立的 `catalog_revision` 表达，不要求改写全项目待办水位。`todo.version` 继续只管原正文/字段写入冲突，附件和子项计数只增 display revision。所有详情、旧列表、五视图 items、创建/修改响应都带正整数 `display_revision`，并从同一 DB 快照投影版本与 count；不能给缺失版本填假默认值。

前端以 `(display_revision,catalog_revision)` 两个单调水位处理同一个 todo 的迟到响应：两者均不低于已接受值才替换公开 DTO；一高一低为不可比较，暂停覆盖并重新 GET 当前 DTO/目录，不能静默取一个旧 count 或旧目录名。`todo.version` 仍保留在保存草稿与乐观写入中，不再单独决定缓存哪个 DTO。须同时改 `uniquePlanTodos`、`useProjectPlanQuery`、`ProjectPlanViews` 及详情缓存等现存合并点，并用“先收新附件/子统计，后到旧正文 GET”和反向顺序用例证明无回退。D1 没有完成这条客户端门禁前，不开放附件计数列；D2 复用同一机制，不能另造一套仅看 children_revision 的排序。

写锁序沿用成员按 user_id → 项目 → 目录状态/项 → 涉及待办按 todo_id 的既有顺序，再锁附件用量、集合状态和附件 ID。附件用量、数量、角色、todo 活状态、expected_revision 与安全事件在一个事务检查和提交；并发不能越过数量/容量上限。恢复时保留原 attachment ID/object key，不重复计费；缺失或损坏对象不能返回恢复成功。

POST 单文件上传用 `client_request_id` 幂等键，唯一域为项目、待办、actor、请求 ID，与附件元数据同事务持久化并有唯一约束。请求 ID 为客户端生成并规范化的小写 UUIDv4；指纹覆盖真实文件哈希、字节数、规范化显示名和服务判定类型，不包含 expected_revision。服务器在同一事务先复核当前成员、当前待办写权、活待办和项目未归档，再查既有成功记录、核对指纹及附件仍活；同键不同输入 409/idempotency_conflict，已移除/已删除/撤权对象不返回旧成功 DTO。命中相同成功记录后允许原 expected_revision 已过期，返回原 attachment ID 的当前元数据与当前快照 revision/count，明确 replayed=true，不新增对象、用量、revision 或事件。仅未命中成功记录的新写入才比较 expected_revision；重放不能绕过当前权限或生命周期检查。

文件先写专用私密临时区，以随机对象 ID 原子发布，再提交绑定与用量。确定回滚才清理本次拥有的临时/未引用对象；数据库提交结果未知时保留最终对象并返回明确失败，不能先删字节再让已提交元数据成为断链。重试复查幂等结果；孤儿回收仅核对无 DB 引用、租约过期的应用自有临时对象，不扫项目资产、评论对象、工作空间或用户文件。

字节读取的成员、活待办、活附件检查和打开/有界读取使用同一个受控授权租约；不能先用一条连接查权限、释放锁后再打开路径。每个读取最多 25 MiB，文件描述符与 DB 租约在构造响应前关闭，不跨网络响应长期持锁。撤权先提交时新请求 404；已合法完成授权读取的响应不能收回已交付字节，这个顺序界限须在并发测试中如实记录，不能称为远程擦除。

私密存储可复用 asset storage 的流式限额、随机 ID、临时发布和受控备份模式，但独立根目录、配额、元数据和生命周期。POSIX descriptor/no-follow 代码不能当作 Windows junction/reparse 竞态已通过；实施须有平台能力探针和对应实际逃逸用例。无法证明所需私密根隔离的平台必须拒绝附件字节操作并显示能力不可用，不开启 030 文件模式来绕过。此项是 D1 激活门禁，不能以字符串路径前缀或前后两次 resolve 代替。

安全事件新增 `project.todo_attachment_changed`，仅投影 attachment ID、action、collection revision 和安全计数；不含文件名、哈希、正文、object key 或请求内容。事件与修改同事务，动态仓储使用明确白名单。

## D1：接口和界面

所有路径在 `/api/projects/{project_id}/todos/{todo_id}` 下，使用现有认证与错误封套。

| 方法与路径 | 请求 | 成功行为 |
| --- | --- | --- |
| GET `/attachments` | state=active/removed、limit 1–50、cursor | 同快照返回 revision、真实用量/上限、items、next_cursor；稳定按 created_at/ID 分页 |
| POST `/attachments` | multipart：file、expected_revision、client_request_id | 新增 201 `{revision,item,attachment_count,replayed:false}`；幂等重放 200，返回同一个活 attachment ID、当前快照 revision/count 和 replayed=true |
| GET `/attachments/{id}/content` | 无路径/URL参数 | 鉴权后按应用自有对象读取下载；所有读取重新验证活待办、成员和活附件 |
| GET `/attachments/{id}/thumbnail` | 无 | 仅合格图片缩略图；非图片不以空成功冒充预览 |
| POST `/attachments/{id}/remove` | expected_revision | 200 更新集合 revision/count，字节仍计容量 |
| POST `/attachments/{id}/restore` | expected_revision | 200 恢复原 ID；数量、状态、字节或并发冲突不部分提交 |

字段/结构错误 422，过大 413，仍有读权但无写权 403，revision/idempotency/限额/归档/生命周期冲突 409，未知/跨项目/撤权 404。分页游标只有固定结构，不含文件名或路径；集合 revision 改变返回 409/query_changed，保留用户当前操作并手动刷新。

详情正文下使用附件卡区：添加、名称/大小、下载、图片缩略图、移除菜单、已移除附件及恢复入口。上传中的进度和失败草稿可重试；成功以真实新增 201 或幂等重放 200、当前元数据及可授权下载字节确认。合法 200 replay 按同一个附件成功处理，不重复新增、不继续显示失败草稿。新建待办先只保存本地文件草稿：取消不上传；创建待办成功后逐文件提交，部分失败明确显示“待办已创建，部分附件未上传”，保留未完成文件，不复制创建第二个待办，不把两次请求伪装成原子成功。

表格字段菜单开放真正的 `attachments` 字段，按真实 count 显示；无附件为清楚空值，点击进入同一个详情附件区域。C2 的 `PlanDefinition.fields` 当前最多 10 项且公共 `VisibleField` 白名单恰为 10 项；D1 必须使用**按视图类型验证**的表格定义，表格允许原 10 项加 `attachments`，总数最多 11、`title` 仍必在首位且不重复。list/board/gantt/calendar 保留最多 10 项且拒绝 `attachments`；`attachments` 不加入 filter、sort、group_by、来源或其他视图字段菜单。旧表格定义保持原字段与顺序，不自动添列；新表格默认也不强行展示附件，用户可临时显示或按 C2 权限保存。后端严格模型、默认定义、旧库/备份规范化、前端 TS union/字段设置/渲染必须成套修改，不能只把前端按钮解锁。来源仍为 manual，附件不新增来源筛选类型。当前项目/账号/成员资格改变时 abort 请求、清空私密元数据、revoke Blob URL；迟到响应还须 context-generation 校验，不能恢复离开后的私密状态。

## D2：一级父子关系

新建 `project_todo_children(project_id,parent_todo_id,child_todo_id,created_by,created_at)`；以 `(project_id,parent_todo_id,child_todo_id)` 为稳定主键、`UNIQUE(project_id,child_todo_id)` 限制唯一 parent。父和子分别使用 `(project_id,todo_id)` 复合 FK：当前 034 SQLite/PG 迁移已经建立对应 UNIQUE 目标，实施和旧库迁移测试仍须核实实际数据库约束。只支持 root→child 一层；已有子关联的 child 不能成为 parent，即使其父已软删除；自指、环、跨项目和已删除对象都拒绝。用独立关系表避免再次重建 todos/评论/图片父表。

另建 `project_todo_children_state(project_id,todo_id,revision,updated_at)` 和项目级 `project_plan_hierarchy_state(project_id,revision,updated_at)`。前者用于单个父的 **关系集合** `children_revision`，后者用于整个项目查询的关系指纹，均初始 1；不把所有父集合 revision 拼进游标。活关系定义为关联存在且父、子均未软删除；软删除只改 todo，不物理移除关联。保留关联计入每父 500 的总量上限；活统计只计活关系。仅子字段/状态编辑不改关系 revision，完成统计在当前读取快照派生。子状态从未完成变为完成或反向变化时，在同一事务对父 `display_revision` 加 1；子自身任何公开字段变化按 D1 规则增加自己的 display revision。子列表按子 DTO 的 display revision 防迟到，不能用未改变的关系 revision 压过已更新的完成数。

| 成功操作 | 父 children_revision | 项目 hierarchy revision | todo.version |
| --- | --- | --- | --- |
| 新增一个子 | +1 | +1 | 新子沿原创建初值；父不变 |
| 单条删除活子 | +1 | +1 | 子 +1；父不变 |
| 整树删除且有活子 | 整次请求 +1 | 整次请求 +1，不按子数累加 | 父和每个本次删除的活子各 +1 |
| 无活子 root 的单条或显式树删除 | 不变 | 不变 | root +1 |
| 子普通字段/状态编辑 | 不变 | 不变 | 仅子沿原合同 +1 |
| 失败、已成功创建的幂等重放 | 不变 | 不变 | 不增加 |

上表的关系 revision 与正文 version 不代替 D1 的 display revision：新增/删除子、整树删除以及子完成态变化在同一事务更新所有受影响的公开 DTO 水位。批量修改多个同父子项时父 display revision 按**成功事务**加 1，完成统计在同一快照重算；只有指派/标题等不影响父公开计数的子字段变化不增加父水位。关系变化仍增加 children/hierarchy revision；单纯子状态变化不增加它们。实施须覆盖旧 `PATCH /todos/{id}`、`POST /todos/bulk`、旧单条 DELETE 和新 D2 端点，不能只在新路由增加父水位。

子待办保留自己的 todo_id/version、标题/Markdown、评论/图片、附件、状态、处理人、日期、优先级和标签；默认字段为空/待开始，不隐式继承父字段。父完成不批量完成子，子完成不自动完成父，父日期不裁剪子日期。详情显示活子数/完成数只是真实统计，不回写状态。一级提案之外的多层树和自动状态联动保持差距。

创建子待办要求当前能修改父待办的字段，并保留旧创建/指派规则：member 只能指派自己或留空；owner/admin 可指派其他当前成员。父必须仍为 root 且活；创建子记录、关系、幂等结果、状态和事件同事务，失败不留下孤儿 todo。D2 复合写入由一个事务所有者持有同一 conn，使用接受该 conn 的内部创建/删除原语，完整保留既有检查、规范化键、标签和事件；不得在外层事务调用自行开启事务的公开 create/delete。旧 API 签名和普通创建行为保留；失败全部回滚，版本增量沿上表。父集合变化不自动改父正文 version。

新子创建携带同格式的 client_request_id。新增持久创建结果表，唯一域为 `(project_id,parent_todo_id,actor_user_id,client_request_id)`，记录规范化请求指纹和 child_todo_id，并与子创建同事务提交；指纹覆盖规范化 C1 创建字段，不含 expected_children_revision，标签集合排序后计算。当前成员、父字段写权、父活且仍为 root、项目未归档复核通过后，先查幂等结果；命中时核对输入指纹、同一父子关系及活子，再返回同一个子当前 DTO、当前快照集合 revision/count 和 replayed=true。原 expected_children_revision 过期不阻止相同成功重放；父/子已删除、关系失效或撤权不返回旧私密 DTO。未命中成功结果的新写入才检查 expected_children_revision 和当前创建字段/指派规则。重放不重复 todo、事件、revision；不同输入 409/idempotency_conflict。未知结果不得自动换请求 ID 或刷新集合后重建另一子待办。原普通 todo 创建合同不在本批偷偷重写。

子内容的读取/编辑/删除继续按它自己的既有 todo ACL；仅是父创建者并不自动获得别人创建的子待办删除权。父 deletion 必须锁住当前 root 与所有活 children，逐项检查当前 delete 权限及根/子版本，在同一 conn 中成功整树软删除；任何一个子不允许删除则整个请求 403，不偷偷留下可读子节点或只删父。集合 revision 和精确活子版本集过期 409，保留现场重新确认。旧 `DELETE /todos/{id}` 对无活 children 的待办保持原合同，包括只剩软删除子关联的 root；有活 children 时返回 409/children_confirmation_required，由新显式树删除端点完成确认。整树操作只删除本次精确活集合，已软删除子不重复增加版本。软删除保留关联、评论和附件字节及容量，但被删除对象所有读入口不可访问。

旧单条 DELETE 删除一个 child 时仍只检查该 child 的既有 delete 权限，不额外授予或要求父创建者权限；同时按统一顺序锁父/子，原子增加父集合和项目 hierarchy revision。新建子、单条子删除、树删除及旧 root 删除均须接入这个检查，不能只防守新端点而让旧端点生成隐藏孤儿。D2 父子操作在相关成员→项目→目录→按 ID 排序的父/子行后锁集合状态；如需先查询 parent，只能作非授权定位提示，并在锁后重查最终关系。真实交错须覆盖父删除与并发 child 创建/删除。

| 接口 | 合同 |
| --- | --- |
| GET `/todos/{parent}/children` | 当前成员，同快照按 created_at/child ID 稳定分页活一级 children，返回 `children_revision,parent_display_revision,active_count,done_count`；游标绑定这两个 revision，任一变化为 query_changed，不能用相同关系 revision 的旧完成数覆盖新值 |
| POST `/todos/{parent}/children` | expected_children_revision、client_request_id + 当前 C1 创建字段；新增 201、幂等重放 200，返回同一个活子当前 DTO、当前快照集合 revision/count、`parent_display_revision`、replayed；parent/actor/source 由服务器决定 |
| POST `/todos/{parent}/delete-tree` | expected_version、expected_children_revision、完整活子 `{todo_id,expected_version}` 集，最大 100 个；真实确认后原子软删除，失败无变化 |

每个 root 最多 100 活子，含软删除的关系最多 500；不提供静默级联、跨项目移动、将旧待办转父子或多层 API。本批 todo DTO 增加可空 `parent_todo_id`、真实 `children_count/done_children_count/children_revision`，并保留 D1 的 display revision；root 的 parent ID 为 null 且 children revision 为正整数，child 的 parent ID 为真实 root、两个 count 为 0、children revision 为 null。来源不能伪造。关系删除/创建使用明确安全字段事件，不下发父子标题。

旧 `GET /api/projects/{project_id}/todos`（含 q、status、assignee 过滤、offset/limit 分页）在 D2 后明确只返回**活 root**，保持旧平面列表与旧客户端不出现意外重复子行。原响应形状仍为 `{items,limit,offset,has_more}`，不凭空增加 `total`；`has_more` 必须基于 root 过滤后的集合判断。旧 `POST /todos` 永远新建 root，不接受客户端 parent/source 字段。已知 child ID 的 `GET /todos/{id}`、现有 PATCH、单条 DELETE 和显式 ID 的 bulk 更新继续按该 child 自身的成员与字段权限工作，详情 DTO 给出 `parent_todo_id`；bulk 中包含 child 的状态变化须同事务重算受影响父的 done count/display revision。旧列表不隐式接收 `include_children` 开关，想看子项应使用新 `/children` 或表格显式开关。root 的旧 DELETE 有活 child 时仍按上述确认规则拒绝；无活 child 时沿旧行为。此兼容决定是熊宝提案，不把 WorkBuddy 未实测的列表语义写成事实。

## D2：五视图、表格开关和分页

definition 的 `schema_version` 继续为 1：D1 的严格 table 模型在 D2 增加布尔 `show_subtodos`，默认 false；非 table 模型拒绝这个额外键，并继续拒绝 table 专属的 `attachments` 字段。table 的 fields 上限为 11，其他四种仍为 10，二者均要求首项 title/全项唯一。SQLite/PG 配对迁移对活及归档的旧 table JSON 显式补 false，不改变 view_id/version、位置、归档状态、默认视图或其余配置。新建 table 或从其他类型切到 table 的完整定义若省略该键，规范化为 false；对已有 table 做 PATCH 且完整 definition 省略该键时，为兼容旧客户端**保留服务器当前值**，新客户端必须显式发送 true/false 才改变它。PATCH 完全省略 definition 也不改设置；切到非 table 必须移除该键和 attachments 字段。旧备份恢复及旧记录读取缺失该键规范化为 false，导出只返回当前类型的规范结构。

目前路由的 `UpdateViewBody.definition` 是 Pydantic union，服务层 `_definition()` 又在锁定当前视图前规范化；这会抹掉“调用方是否显式传了 show_subtodos”的关键信息。D2 必须让 PATCH 路由把 definition 的**原始字段存在性**传至服务/仓储边界（例如严格 JSON 映射），在同一授权与 `expected_version` 事务中锁定当前 view、确定目标类型，再按上一段规则补入当前 show_subtodos，之后才用相应类型的严格模型校验、检查目录引用并写入。不能先把省略值默认成 false，也不能在事务外读旧值后合并。新建与切换类型继续按完整定义严格校验。要测试旧客户端在另一人启用 true 后，用当前版本 PATCH 其他 table 字段仍保留 true，以及旧版本按原合同 409。

| 查询入口/配置 | D2 后的基础集合 | 既有/新建/临时配置 |
| --- | --- | --- |
| 旧 `GET /todos` | 活 root | 原 offset/limit/has_more；无子开关 |
| `plan/query` 的 table `show_subtodos=false` | 活 root | 迁移后的既有 table、服务端新默认、显式 false 的共享保存及临时 override 均为此值 |
| `plan/query` 的 table `show_subtodos=true` | 活 root + 活 child | 仅显式 true 的共享保存或临时 override；child 按自身过滤/分组/排序，可在父不匹配时单独出现 |
| `plan/query` 的 list/board/gantt/calendar | 活 root | 既有和新建配置、临时 override 一律不接受 `show_subtodos`；本批不在这四种视图渲染或拖动 child |

所有五种视图都在当前 `project_plan_query.py` 的公共 `t.deleted_at IS NULL` 谓词之后、filters/group/window/seek **之前**加入上表对应的 root/child 基础谓词；root 以关系表中不存在该 todo 的 child 关联判定。统计、items、锚点重建使用完全相同的基础谓词和读取快照，不能只在渲染层藏 child。非表格 child 呈现及交互留作 WorkBuddy 同夹具观察后的后继片，D2 不宣称五视图子行 1:1。

table false 的 SQL 层级基础集合为活 root；true 为活 root+child，child 同样参加当前 filters/group/sorts/window。所有统计、items 与锚点重建使用同一读取快照和相同层级基础谓词，保留 C2 的 total、matched_total、unscheduled_total、groups 名称和各自范围：total 为公共 filters 后的 distinct todo 数；groups 为公共 filters 后的全组计数，不随当前 group_key、日期窗口、分页或 seek 缩小；日期视图的 matched_total/unscheduled_total 沿 C2。标签组内按 todo_id 去重，同一个 todo 可分别计入多个标签组，组 count 之和允许超过 total，不能冒充项目总数。items 和锚点在公共范围上追加适用的 bucket/window/group 谓词，seek/LIMIT 仅用于 items，不能 LIMIT 后再追加子待办或用隐藏行凑计数。true 的 table 中 child 展示一级缩进和父链接，但不承诺父子紧邻：父与子按各自筛选/分组/排序独立排列，父不匹配时也可显示匹配的子。这个明确的平铺查询提案尚未经过 WorkBuddy 同夹具验证，不可声称等于其未知的嵌套排序。

指纹覆盖视图类型和 table 的有效 `show_subtodos`；仅 table true 因关系变化能改变结果集合，其指纹还覆盖项目 hierarchy revision，关系变化导致旧锚点明确 `query_changed`，而不是漏行/重行。D1 启用附件投影时将 C2 游标从 `{v:1,last_version}` 升为有界 `{v:2,last_display_revision}`，锚点仍只存 ID/正整数、不存标题；旧 v1 游标在切换后返回 409/query_changed 以要求刷新，不误作缺损查询。锚点在同一快照中按当前公开 display revision 校验；附件、子完成数或其他公开投影变化也不能在锚点不变的假象下继续翻页。跨页仍沿 C2 的现有非冻结快照约束，不声称外部并发时全列表快照一致。父显示名改变只影响展示，不用长标题写进游标。临时表格开关对 member 可用；保存共享定义仍 owner/admin 且版本化保存，刷新、默认视图和导出当前定义沿 C2 的现有角色和冲突合同。

详情有子待办列表、完成统计、添加入口和打开同一子详情的路径；子详情显示返回父待办。父删除确认展示实际活子数、整体删除结果，不能把授权错误转成部分成功。隐藏子行不影响详情的真实统计。桌面/窄屏键盘、焦点返回、独立滚动和中英文文案沿现有组件合同。

## 迁移、备份和验收

初稿编写时最高迁移为 035；实施时重新核对实际集成基线，再串行分配 D1/D2 编号，SQLite/PG 成对。不预占编号，不降水位、不重建现有 todo 父表、不丢评论/图片/标签/五视图。D1 迁移对所有既有 todo（包括软删除项）新增附件状态及 display 状态行，初始 revision=1，不伪造附件；D2 再新增 children 状态，不伪造关系。新建普通及子 todo 在自身创建事务插入对应状态。既有及新项目的独立用量初始 0、hierarchy revision 初始 1，项目创建和迁移分别接线；不改变既有 todo/version。旧 offset 列表保留接口，但 D2 后明确只含 root；C2 视图配置除本合同规范化外保留 ID/version 与共享状态。新增状态、关联、幂等结果和字节路径进入完整备份/恢复合同，测试旧备份恢复后升级及 DB/字节一致性。

先写 RED，再实现最小 GREEN，相关回归和独立只读审查绑定候选 SHA。

- D1：真实上传/下载字节、幂等相同/不同输入、过大/损坏/预览类型、数量/容量竞态、撤权和删除竞态、移除/恢复、未知 commit 保留对象、受控孤儿回收、两平台根/链接逃逸、旧库/备份恢复；附件 count 改变后新旧 DTO 交错不回退；table 可保存第 11 个附件字段且其他视图拒绝。
- D2：same-project FK、一层/自指/环拒绝、成员创建/指派、子自己的 ACL、整树删除零部分提交、精确集合/版本冲突、parent-delete/child-create 双 PG 连接交错；旧 `/todos` 根列表的 items/has_more 和旧 bulk/DELETE 对 child 的明确语义；子状态变化与迟到 GET 不能回退父 done count；五视图按上表基础谓词一致计数、table 开关保存/刷新、旧客户端省略开关时不重置、长文本游标。
- 前端：实际私有 TCP API+数据库，创建部分附件失败、重试不重复、旧 Blob/迟到 HTTP/退组 404；table true 的真实子行/父链接与另外四视图 root-only；display/catalog 双水位不可比较时重取、v1→v2 游标失效重取；两个视口键盘和焦点。
- 记录分开：源/组件通过、真实 PG、真实浏览器、Windows 存储能力、WorkBuddy 同夹具视觉、真实账号、付费供应商及安装包。未运行的不写 PASS；本地旅程不证明 1:1。

## 实施分工与依赖

| 波次 | 所有权 | 前置和并行规则 |
| --- | --- | --- |
| D1-BE | 附件 repo/service/router/storage、成对迁移、专属测试 | 主实现路由；先固定接口、存储能力探针、事务/旧库测试。现有 todos/services/backup/event 聚合接线交 Root 串行整合 |
| D1-FE | 新附件 API/组件/测试/样式、附件字段渲染 | 并行实现路由；以冻结接口写 RED。ProjectTodoDetail、共享 API 类型、双语聚合文件由该波次唯一指定集成者串行修改 |
| D2-BE | children repo/service/router、成对迁移、query/definition 接缝和测试 | D1 接受后的同基线，不能并行改同一 todos/query 文件或占用同一迁移编号 |
| D2-FE | 子待办组件、table true 子行/父链接、其他四视图 root-only 回归及测试 | 可与 D2-BE 按合同并行；现有共享详情/API/双语接线继续一人串行 |
| REVIEW | 固定 SHA 的规格/代码/测试只读审核 | 原 GLM 路由只读；实现路由不可给自己终验。Root 处理真实状态、独立复核、完整相关门禁和正常非强制推送 |

原三条路由精确保留：claude-bailian/qwen3.8-max，opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash，qwen-code-review/glm-5.3。模型额度和旧路线失败是当前已知约束；PS-04D 若需 Codex/按文件 native 子代理实施和替代只读门禁，须对此批获得明确承接授权，不借用 PS-04C 例外。所有执行者不得嵌套代理、提交/推送/部署、改凭据或清理他人工作区。

批准设计后，Root 才编写逐步实施计划和有界 task packets，锁定文件所有权与候选 SHA 后启动。该草案本身不是实施进度或验收证明。
