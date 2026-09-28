# PS-04C1 计划日期、优先级和项目标签合同

状态：**待批准的书面规格；没有开始业务实现**。本合同与 [PS-04C 设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C2 视图合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)一并审阅。基线 `10afd308a222d3e8c95a149a3fdcd699e6d8bc70`；下面新增规则是熊宝的产品提案，不代表 WorkBuddy 的提交行为或成员权限已验证。

## 不变量与权限

- 每条记录继续使用 020 的 `todo_id`、项目归属、软删除和 `version`。C1 不复制待办、不改变评论身份，不把标签或目录编辑变成任务正文授权。
- 当前成员可读本项目待办及目录；非成员、撤权者、未知项目、跨项目 ID 和已删除待办按现有成员规则统一 404。通过读取目录不能获知其他项目的名称、选项或计数。项目归档本身不被误写成现有的统一撤权：旧 todos/comments/images 继续沿已有成员 ACL，不在本片静默改变；新目录配置写入在归档项目返回 409、reason=project_archived，读取仍按成员资格。完整归档/恢复资源政策留 PS-09 独立合同。
- owner/admin 可管理目录和任意待办字段。member 可创建待办，并仅可修改自己创建或当前指派给自己的待办的标题、正文、状态及新日期/优先级/标签；改处理人和批量仍限 owner/admin。成员创建时只能指派自己或留空。无写权限但仍有读权为 403，不能先写关联再报错。
- 目录项的新增、编辑、排序、停用和恢复是项目配置，仅 owner/admin 可用。前端显示与服务器检查一致；每次写事务重新验证当前角色，不信任 DTO 中的 actor、角色或客户端缓存。
- 所有待办字段写入、标签关联和安全事件在同一事务中完成。响应中的版本、标签集合与本次提交是一致快照；不能提交后通过另一连接补标签，混出一个不对应该版本的响应。

## 计划日期

`start_date`、`due_date` 均为可空 `YYYY-MM-DD` 字符串，表示日历日，不表示 Unix 时间或当地午夜。接受范围为 `1900-01-01` 至 `9999-12-31`，严格验证月份、闰年和实际天数；拒绝时刻、时区后缀、自动归一化的无效日期或其他格式。审计 `created_at/updated_at` 仍为 Unix 秒，不代替计划日期。

服务器按 `default_timezone` 计算 `server_today`，目录 GET 返回今天和时区，日期控件使用这两个值。前端用日历日解析/比较工具，不能通过 `new Date("YYYY-MM-DD")` 再格式化造成跨日。已有 `useServerTimezone` 只用于时刻展示；日期控件若无法加载服务器日期信息，显示可重试的加载失败并阻止选择/提交计划日期，不退回浏览器今天。

| 操作 | 精确规则 |
| --- | --- |
| 创建 | 两日期可省略或为 null；有截止日期时必须不早于服务器今天；有起止时开始不得晚于截止。开始可以在过去 |
| PATCH 只改其他字段 | 保留原日期；已有逾期值不会阻止修改状态/正文等 |
| PATCH 带日期 | 先将请求与当前记录合并再检查起止；新设或改变的非空截止日必须不早于事务内服务器今天；与当前值完全相同的旧截止日可以保留 |
| 清除 | null 表示清除；省略表示保留；清除截止允许，不把 null 当“今天” |
| 跨午夜 | 服务器提交时今天具有最终效力；客户端旧今天导致日期过早时 422，保留草稿并重新获取服务器今天，不自动改成下一天 |

新建弹窗截止日禁用服务器今天之前的日期，开始日允许过去。已逾期待办的详情仍能显示旧值并选择“保持原值”；选择另一个过去截止日被拒绝。起止错误和时间信息加载错误均中英本地化。拖动甘特/日历也使用同一合同，不能绕过 HTTP 规则。

## 项目目录数据

新增独立 `project_todo_catalog_state(project_id, revision, updated_at)`，revision 从 1 开始。它不复用 `experts_revision`，也不与 C2 视图版本共用。现有和新项目均在迁移/创建事务中建立目录状态，并初始化四个有效优先级：紧急/红、高/橙、中/蓝、低/灰，顺序固定。名称是项目数据，后续重命名不被界面语言切换覆盖；系统控件与错误文案中英对齐。“无”由 `priority_id=null` 表示，不建可编辑目录项。

| 表 | 必需数据与约束 |
| --- | --- |
| `project_todo_priorities` | `priority_id` 不可猜测文本 ID、`project_id`、`name`、规范化 `name_key`、枚举 `color`、非负整数 `position`、可空 `archived_at`、创建/更新时间；`(project_id, priority_id)` 唯一，项目外键；有效项名称在项目内唯一 |
| `project_todo_tags` | `tag_id` 不可猜测文本 ID、同样的项目/名称键/颜色/停用与审计字段；`(project_id, tag_id)` 唯一，有效项名称在项目内唯一；按 `name_key, tag_id` 排序 |
| `project_todos` 新字段 | 可空 `start_date`、`due_date`、`priority_id`；优先级必须为空或由 `(project_id, priority_id)` 外键指向本项目目录；数据库约束日期字符串格式、范围和 start≤due，服务严格检查实际日历合法性；复合外键不能使用会同时清空 project_id 的 ON DELETE SET NULL |
| `project_todo_tag_links` | `project_id, todo_id, tag_id` 组合；同一待办/标签唯一；以复合外键同时保证待办与标签属于这个项目，不能只依赖服务层先查 |

公开 ID 使用项目现有不可猜测文本 ID 原语；整数自增主键不进入 API。目录名 trim 后 1–40 Unicode 字符，拒绝控制字符；`name_key` 采用 NFKC + casefold，目录间不共享名字唯一域。颜色仅允许 `red/orange/yellow/green/blue/purple/gray`，由固定样式映射，不接受任意 CSS。有效优先级最多 32、含停用最多 128；有效标签最多 100、含停用最多 500；每条待办最多 20 个不同标签。限制在锁住目录状态后检查，不通过并发请求绕过。

新优先级追加到有效项尾部；完整排序提交后有效项 position 重排为连续整数。停用项不参与有效顺序，恢复时追加到尾部。标签按规范化名称及 ID 稳定排序，不提供无持久化接口的拖动排序。

## 停用和恢复

停用采用软归档，不物理删除、不批量清关联。现有待办仍展示原优先级/标签并标注“已停用”；读取、历史引用和查询该项仍可用。普通选项列表只列有效项，并额外列出当前待办已经引用的停用项，标识为只可保留/移除；不能向其他待办新增该关联。

- 优先级仍等于当前优先级时可以保留；改为 null 或另一有效项可以。不能把停用优先级首次设给待办。
- 标签更新为完整集合；当前已关联的停用标签可以留在集合中或移除，新增的每一标签必须有效。移除后再次加入停用项为 422。
- 目录重命名/变色/排序/停用/恢复仅增加目录 revision，不隐式递增所有引用待办的 version。后续新增关联须携带新目录 revision；旧待办的其他字段写入仍可保留已有引用。
- owner/admin 恢复时同时检查有效数量和有效名称冲突，冲突 409，保留停用项。有效名称可在旧项停用后被新项使用，但二者 ID 永不复用，历史显示带停用标记。
- 本片不提供永久清除目录项；含停用数量触顶后返回明确 409，不自动删除旧引用。

## API：目录

路径前缀为 `/api/projects/{project_id}/plan`；使用现有认证和错误封套。纯请求结构错误可先返回 422；需要读取项目/目录才能判断的存在性、权限或引用错误，必须先通过项目 ACL，不能从非法 ID 暴露其他项目内容。`expected_revision` 是必填正整数，不接受布尔值。读取返回所有有效/停用项，数量受上述总额限制。

`GET /catalog` 返回 `{project_id, revision, server_today, server_timezone, priorities, tags}`。优先级项固定为 `{priority_id,name,color,position,archived_at,created_at,updated_at}`；标签项为 `{tag_id,name,color,archived_at,created_at,updated_at}`，时间为 Unix 秒、未停用为 null。有效优先级按 position/ID 排列，停用项放在其后稳定排列；标签有效/停用分段后按名称键/ID 排列。

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /priorities` | `{expected_revision,name,color}` | 201 `{revision,item}`，item 为新优先级 |
| `PATCH /priorities/{priority_id}` | `{expected_revision,name?,color?}`，至少一项实际变化 | 200 `{revision,item}` |
| `PUT /priorities/order` | `{expected_revision,priority_ids}`，必须恰好覆盖全部有效优先级且不重复 | 200 `{revision,priorities}`，为更新后的完整有效顺序 |
| `POST /priorities/{priority_id}/archive` | `{expected_revision}` | 200 `{revision,item}` |
| `POST /priorities/{priority_id}/restore` | `{expected_revision}` | 200 `{revision,item}` |
| `POST /tags` | `{expected_revision,name,color}` | 201 `{revision,item}`，item 为新标签 |
| `PATCH /tags/{tag_id}` | `{expected_revision,name?,color?}`，至少一项实际变化 | 200 `{revision,item}` |
| `POST /tags/{tag_id}/archive` | `{expected_revision}` | 200 `{revision,item}` |
| `POST /tags/{tag_id}/restore` | `{expected_revision}` | 200 `{revision,item}` |

不存在/跨项目目录项为 404；仍有项目读权但无目录管理权的写请求为 403；格式、额外字段、重复 ID、无变化编辑、已停用项再停用或有效项再恢复为 422；目录 revision 过期、有效名称冲突、数量上限和归档项目写拒绝为有明确原因的 409。事务失败不改目录 revision、不写事件。成功写入 revision 恰加 1，客户端以响应 revision 更新缓存，再重新读取完整目录；并发保存导致的 409 保留名称/颜色/顺序草稿，提供“刷新后比较”，不自动覆盖。

停用项允许改名/变色，但仍需 revision；排序请求不能含停用或跨项目 ID。REST 路由中静态 `/priorities/order` 要在动态 ID 路径前明确接线。

## API：待办兼容与写入

现有 `/api/projects/{project_id}/todos` 路径继续使用。所有详情、创建、PATCH、列表和批量的待办 DTO 增加必填 `start_date:null|string`、`due_date:null|string`、`priority_id:null|string`、`tag_ids:string[]`、`catalog_revision:int`；tag_ids 去重后按 ID 排列，目录 revision 是响应读取快照的当前修订，不存成待办创建时的永久版本。旧记录全部日期/优先级为空、标签为空；现有 `description_format`、version、状态和审计字段保持有效。

| 写入 | 新参数及兼容 |
| --- | --- |
| 创建 POST | 原有字段保持；新增可选 `status`（默认 todo）、`start_date`、`due_date`、`priority_id`、`tag_ids`、`expected_catalog_revision`。省略新字段分别取初始状态、null、null、null、空数组；选了非空优先级或标签时必须提供当前目录 revision |
| 单条 PATCH | 原有 `expected_version` 必填；新增可选日期、优先级和 tag_ids。省略不修改；日期/优先级 null 清除；标签空数组清除全部，null 为 422。只要传 priority_id 或 tag_ids，就必须带 expected_catalog_revision，即使清除 |
| 原有 bulk | 本片不扩展批量输入字段；仍仅状态/处理人，仍 owner/admin、最多 50、原子版本检查；响应必须含新增 DTO 字段和一致目录 revision |

选择/替换目录项时目录 revision 不一致为 409；目录项无效/新增停用关联为 422，不能作为未知项目 404 清空当前合法页面。待办 expected_version 过期为 409；即使请求字段值与当前相同也先遵守现有版本规则，不把旧请求悄悄认作成功。单条无实际变化沿用现有 400、`reason=no_change`；旧 PATCH 仅带合法当前 expected_version 也保持 400/no_change、版本不变。新增 expected_catalog_revision 在 PATCH 中必须与 priority_id 或 tag_ids 配对，孤立元数据为 422；创建时若提供目录 revision 则仍按当前修订校验。原 bulk 仍允许重复值并按其已批准语义为各项增版本/事件，不能无意统一为单条 noop。所有额外 actor/source/project_id/目录名称字段拒绝，客户端不能伪造目录项或来源。

单次创建/更新在同一事务构造 DTO 和标签集合，提交成功后返回，不采用提交后的无锁 `self.get()` 作为该写入的版本证明。单条/列表/批量读取也保证目录 revision 与所返回关联一致；前端发现本地目录 revision 不同应刷新目录后再显示名称，不能按数组位置猜名称。退组仍只清理处理人并增版本/事件，保留日期和目录引用；账户物理删除按现有外键语义另行处理，不能把退组当删除待办。

## 锁序、事件和迁移恢复

沿用现有成员锁/写时重查，新增路径遵守 **涉及的成员行按 user_id 排序 → 项目有效性行 → 目录状态 → 目录项按类别/ID 排序 → 待办行按 todo_id 排序 → 标签关联/事件**。目录写仅到目录状态/目录项，不锁所有引用待办。PostgreSQL 使用真实行锁，SQLite 在写事务里串行化；同一事务在锁后重查成员、归档、版本和有效关联。项目有效性行与现有项目归档、专家配置路径的锁序须在实施计划/独立审查中一致，不因新路径增加反向锁。

现有退组路径用多行 UPDATE 清处理人，不能假设其已有与 bulk 相同的已证明 todo_id 锁序；须对退组、bulk、新字段更新与目录停用的真实双 PostgreSQL 连接交错验证无死锁和原子性。既有名称为 single_winner 的顺序 PATCH 测试、fake PG cursor 都不能算这项证据。新增字段成功更新沿用安全 `project.todo_updated`，只扩大“变更字段名”白名单，不写日期、目录名字、描述或原始请求；原有状态/处理人的安全载荷继续生效。

新增白名单 `project.todo_catalog_updated`：仅 `{catalog_revision, catalog_kind, option_id?, action, fields}`；kind 为 priority/tag，action 为 created/updated/ordered/archived/restored，fields 仅 name/color/order/archived；不含名称、颜色值、待办正文、查询、项目指令或任何凭据。事件与目录写同事务，“与我相关”只按当前安全事件规则定位操作者。动态仓储必须显式投影新安全字段，不能下发 payload_json。

SQLite/PostgreSQL 迁移成对创建状态/目录/关联、补待办字段和复合约束、回填旧项目四级目录；编号在实际实施基线查询后分配，不预占当前 033 的后一个编号。明确增加父键 `(project_id,todo_id)` 唯一，供标签链接复合 FK 使用。SQLite 在旧 project_todos 加复合 FK 需要受控父表重建，不能仅 ADD COLUMN；重建须保留正式表的 FK 目标、公开 ID、version、description_format、原日期/引用，以及 031 评论→033 图片、图片用量和文件字节。禁止重命名/删旧父表时触发子表级联丢失。

SQLite 要对 DDL、回填与 schema waterline 实现可恢复的原子重放/存在性校验，不能仅依赖通用 executescript 后更新水位；PG 在事务中执行。升级和故障重试门禁必须检查 foreign_key_check、所有子表 FK 仍指向正式父表、评论/图片 ID/计数/用量/字节及原待办 ID/版本保持，失败可回到原库或原子恢复；新库外键测试不代替旧库重建验收。受控备份自动包含 DB 新表，但本批必须验证旧备份恢复后升级、目录/关联 roundtrip 与错误恢复，不能引用 PS-04B 评论备份证据代替。

## UI 与私密状态

- 新建、列表编辑与双栏详情使用共享日期/优先级/标签控件；新建还接状态选择，默认“待开始”（todo），后台状态值不改。优先级支持搜索、无、有效项及管理入口；标签支持搜索、多选与清空；管理入口对 member 不可提交并解释权限，字段选择仍可按其待办 ACL 使用。
- owner/admin 可从字段弹层进入目录管理并返回原草稿；新增目录成功后可选中新项。停止/恢复项、改名、颜色、优先级顺序均调用真实接口，不用乐观假数据假装保存。
- 日期/目录字段保存携带待办 version 和适用目录 revision；成功以该提交 DTO 更新两种视图和详情。列表编辑与详情统一 409 保留草稿及明确刷新比较，不沿用旧列表遇冲突关闭弹窗的行为。
- GET/保存 403 只提示当前写拒绝并保留仍能读的内容；项目/待办 404 清空该项目私密字段、评论、图片 Blob、目录草稿和弹层。目录项输入错误不会被误认为退组。
- 切项目、切账户、关闭详情、切待办时撤销关联请求或通过项目+账号+待办 key/请求序号丢弃晚到成功和错误。旧目录名称、选择草稿或保存结果不得进入新项目。可保存的本地视图 ID 不包含待办/目录内容。
- 1280×768、800×728 有可滚动日期/目录弹层，键盘可搜索、选择、清除、保存、取消；Escape 和关闭将焦点还给原控件。双栏详情正文与受控图片的既有行为不回退。

## 验收清单

1. SQLite 新库/旧库父表重建、部分迁移失败/重试、复合外键/父键与 foreign_key_check、旧备份恢复；旧 020/032 数据、031 评论/033 图片/用量/文件字节、公开 ID/version、纯文本/Markdown 和原批量 DTO 不变。
2. 真实 PostgreSQL 同等迁移/约束/备份；两个真实连接的目录同名竞争、revision 单赢家、停用与新增关联、退组/归档与待办保存、bulk 交错；失败关联/事件/版本全不变。
3. owner/admin/member/outsider：读目录、目录 CRUD、创建者/处理人字段 ACL、跨项目 ID、已删和撤权；在写锁后撤权的结果单独记录。
4. 日期闰年/无效日、过去开始、过去新截止、旧逾期不变、清除、合并后的起止、UTC/Asia/Shanghai/DST 时区跨午夜；客户端与服务器今天不同但不自动覆写。
5. 默认四级、自定义名称颜色、NFKC/casefold 同名、数量上限并发、完整排序、标签 20 上限和去重、停用显示/保留/移除/恢复冲突；不能新增停用关联。
6. 新建→表格/看板→详情→刷新相同 ID/版本/日期/引用；真实 409 保留草稿和人工比较；目录改名不改待办 version；403/404、项目切换的迟到请求、窄屏键盘及中英。
7. 原评论、图片上传/撤权和旧文本保持，新增动态字段不含目录名字/正文。质量、真实浏览器和 WorkBuddy 同视窗对照分开留证，所有未验证项仍在差距台账。
