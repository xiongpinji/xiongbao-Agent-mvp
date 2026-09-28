# PS-04C2 共享计划视图与全项目查询合同

状态：**待批准的书面规格；没有开始业务实现**。本合同与 [总体设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1 字段合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)一并审阅。实施依赖 C1 的真实字段/目录和相应验收。WorkBuddy 空项目观察只确认入口；个人/共享归属、保存、数据排序与甘特/日历操作尚未实测，以下为熊宝明确提案。

## 权限、身份与视图数据

计划视图是同一套项目待办的显示/查询配置，不保存第二套待办，也不增加任务正文、文件或外部凭据访问权。所有当前成员可读本项目有效共享视图；owner/admin 可新建、改名、改类型/配置、调整顺序、停用/恢复和设置项目默认视图。member 只能临时调整当前会话配置，保存按钮说明所需管理权限，不能借临时配置 API 改共享数据或放宽待办写权。归档项目的新视图配置写入返回 409、reason=project_archived；新增读取/query 和原 todos/comments/images 按已有成员 ACL，完整归档资源策略另属 PS-09，不能把本片当全入口归档锁定。

| 表 | 合同 |
| --- | --- |
| `project_todo_view_state` | 项目 1:1 `project_id`、集合 `revision`、`default_view_id`、`updated_at`；默认必须指向本项目有效视图。初始化事务允许暂空，提交后不得空 |
| `project_todo_views` | 不可猜测文本 `view_id`、`project_id`、`name/name_key`、`view_type`、严格验证的 `definition_json`、独立 `version`、非负 position、可空 archived_at、created_at/updated_at；项目/ID 复合唯一，有效名称项目内唯一 |
| 待办查询派生键 | 为跨 SQLite/PG 一致的标题查询/排序保存 `title_search_key`（NFKC + casefold），迁移回填并在任何改标题路径同事务维护；不作为显示标题返回 |
| 处理人查询派生键 | 在 users 持久化内部 `project_plan_display_sort_key`：当前非空 display_name，否则 username，完整 NFKC + casefold；现有名字没有统一长度上限，不假设为短字符串。不修改原名字/凭据；创建、显示名及 username 的任何变更在同事务维护派生键，查询在 SQL 联接该键排序，不下载当前页后排序 |

每项目初始化“表格”和“看板”两项，映射目前两种 UI 的真实数据，所有视图初始 version=1、集合 revision=1、默认表格。当前项目与未来新项目都初始化，不能仅在浏览器写两条虚构视图。名称 trim 后 1–40 Unicode 字符、拒控制字符，name_key 为 NFKC + casefold；有效视图最多 30、总数含停用最多 100。类型严格为 `list/table/board/gantt/calendar`。现有专家 revision、目录 revision 和视图 revision/version 互相独立。

集合 revision 每次真实视图写入加 1，用于列表、顺序和默认配置；单个视图真实变化 version 加 1。修改不同视图只检查各自 expected_version，不因另一个视图更新而互相冲突。调序会增加所有 position 实际改变视图的 version，默认视图切换只改集合 revision。停用保留配置且增加 version，不物理删除；至少保留一个有效视图，停用最后一个为 409。停用当前默认视图时，同事务改用按 position/ID 排序的首个剩余有效视图。

恢复需没有有效同名、未超限，追加到有效顺序尾部；允许管理者编辑停用视图的名称/配置，但它不能执行数据查询。有效视图名称在旧项停用后可重新使用，新旧 ID 不复用。成员上次选择可按当前账号＋项目保存**视图 ID**，读取服务器有效列表后验证；不本地持久化待办、名称、字段定义或筛选正文。项目默认、用户上次选择和临时配置在 UI 明确区分。

## 视图定义：固定 schema 和白名单

DTO 为 `{view_id,project_id,name,type,definition,version,position,archived_at,created_at,updated_at}`。definition 固定 `schema_version=1`，含 `fields`、`group_by`、`filters`、`sort`，以及仅适用类型的 `calendar` 或 `gantt`；拒绝额外键和不适用类型的配置。type 改变时必须同时提交完整的新 definition，不能留另一个类型的旧配置。

| 配置 | 精确规则 |
| --- | --- |
| `fields` | 有序且不重复；title 必须第一项且不可隐藏；其余白名单为 status/assignee/priority/tags/start_date/due_date/created_at/updated_at/source；各类型均以相应列或卡片属性真实显示。隐藏只改显示，不改变 ACL |
| `group_by` | null、status、assignee、priority、tag、source。list/table/board 支持；board 不接受 null，默认 status；gantt/calendar 必须 null，避免对日历/时间轴虚构分组 |
| `filters` | 最多 12 个条件，全部 AND；每个条件是下面的严格字段/操作符 union，不接收任意表达式、SQL 或函数 |
| `sort` | 最多 3 个不同字段 `{field,direction}`；field 为 title/status/assignee/priority/start_date/due_date/created_at/updated_at，direction 为 asc/desc。空数组默认 updated_at desc。服务器固定 todo_id asc 作为最后键，用户不能去掉 |
| `calendar` | 仅 calendar 必填 `{date_basis:"due_date"|"start_date", mode:"month"|"week"}`，默认截止日期/月 |
| `gantt` | 仅 gantt 必填 `{zoom:"day"|"week"|"month"}`，默认 week；窗口由查询请求传，不隐式保存在定义里 |

默认表格 fields 为 title/status/assignee/priority/tags/start_date/due_date，group_by=null；默认看板 fields 为 title/status/assignee/priority/tags，group_by=status；默认均无筛选，updated_at desc。新建列表沿用表格字段，新建甘特/日历用 title/status/assignee/priority，其他配置按上述默认生成。创建时服务器接受完整显式定义并再次验证，不依赖客户端常量作为唯一验证。

“附件”“显示子待办”“导入数据源”是观察到但尚无数据合同的后继能力。设置菜单明确标示待完成原因，不让其进入本片可保存 fields 或返回无依据的附件计数。`source` 暂只显示实际手动创建的待办，固定 kind=manual、文案本地化；客户端不能在创建/PATCH 伪造 source，来源筛选也不接受未实现的外部种类。PS-10 接入后以独立迁移/合同扩充，不把手动标签当导入。

## 组合筛选和排序含义

| field | op 与数据 | 查询含义 |
| --- | --- | --- |
| title | contains/not_contains，`value` 为 1–200 字符 | 对 NFKC/casefold 标题派生键做字面子串查询；转义 `%`、`_`、反斜杠，不当通配符 |
| status | in/not_in，`values` 为 1–3 个不同合法状态 | IN 或 NOT IN；todo/in_progress/done |
| assignee | in/not_in，values 为 1–50 个不同当前成员 ID 或 null | null 表示未指派；在同一读取快照验证成员归属 |
| priority | in/not_in，values 为 1–32 个不同本项目优先级 ID 或 null | null 表示无；允许本项目停用项用于历史筛选 |
| tags | any/all/none_of，values 为 1–20 个不同本项目标签 ID；或 is_empty/not_empty，无 value(s) | any 至少一个，all 每个都存在，none_of 一个都不存在；允许停用项，使用 EXISTS/去重候选，不因关联数重复返回待办 |
| start_date/due_date | on/before/after，value 为合法日历日；between，values 为有序的两个日期；is_empty/not_empty 无值 | 日期相等/严格早于/严格晚于；between 两端包含；空值不满足 on/before/after/between |
| due_date | overdue，value 为布尔值 | true：有截止、截止早于服务器今天且状态不是 done；false：其余，包括空截止/已完成；今天含义来自 C1 |
| source | in/not_in，values 当前只能含 manual | 只针对已实现的手动来源；不能把不存在的来源当查询成功空态 |

assignee/priority 的 `not_in` 是完整 `in` 谓词的补集：in:[A] 仅 A，in:[A,null] 为 A 或空；not_in:[A] 包含 B 与空，not_in:[A,null] 只包含非空且非 A。必须显式构造 IS NULL/IS NOT NULL 组合，不能把 NULL 直接塞进 SQL IN/NOT IN。A/B/null 三种记录在 SQLite 与 PG 的结果逐项对齐。

每个条件只能提供适用的 value 或 values；拒绝缺值、重复值、错误类型、不合法 ID/操作符和多余键。NFKC/casefold 规范标题可改变兼容 GET 的标题搜索一致性，旧 GET 的字面子串、转义及其他筛选语义必须保留并分别回归；不将空 q 变成错误。

排序在 SQL 全项目执行，NULL 始终最后，方向只影响非空值；状态按 todo→in_progress→done 的序位；标题用完整派生键；处理人用 users 的完整 project_plan_display_sort_key、user_id 作为其内层稳定键，未指派最后。优先级的有效/停用/无类别顺序固定，方向只改变类别内 position 或名称键，最终以内层 priority_id 稳定收尾。日期按日历日，审计时间按 Unix 秒。SQLite/PG 使用同一规范化键和显式空值/类别序位，不依赖不同数据库的自然 collation/NULL 默认顺序；不能直接截断完整排序键却沿原排序 seek。

目录名修改可改变排序位置，并增加目录 revision，使旧查询指纹失效。处理人排序或分组参与查询时，指纹另纳入当前项目成员 ID/安全显示名规范键的有序摘要；成员名单/显示名改变不冒充目录修订，而使该摘要与旧游标不匹配。新请求重新计算处理人排序。不同请求间并发数据变更可能使行移动，本合同不承诺冻结快照跨页；已成功写入后前端重新加载所有当前页/组并按 ID 去重，不在旧页上静默保留过期统计。

## 分组、计数和多标签

分组 keys 为固定类型＋可空目录/成员 ID，而非用户可重命名的字符串。status 返回三组；assignee 包含当前成员和未指派；priority/tag 包含有效目录项、仍被匹配待办使用的停用项及无优先级/无标签；source 当前仅 manual。每个分组 count 在 SQL 对匹配集合计算，分页和隐藏组不会改变 count。

标签分组允许同一 todo 出现在其多个标签组，并在卡片上保留相同 todo_id；无标签只包含确实没有关联的待办。**项目 total 是 distinct todo 数；标签组 count 之和可能更大。** UI 区分“待办总数”和各组数量，不能相加制造项目总数。按某一标签组展开时，在已完成公共 filters 后加该组条件，分页不再把一个 todo 重复出成多行。

list/table 可选择分组且每组独立分页；board 每列独立分页/加载更多。未加载完整列表的客户端不能自行构造全项目组数、排序或搜索。总数/组数和当前 items 在同一请求读取快照中形成；跨请求数据改变后重载，不能以全量下载所有待办替代有界服务端查询。

## API：视图配置

路径前缀 `/api/projects/{project_id}/plan`，ACL 和错误封套同 C1。所有 version/revision 均为必填正整数且不接受 bool。`GET /views` 返回 `{project_id,revision,default_view_id,items}`，有界地返回所有有效及停用配置，按有效/停用分段及 position/ID 稳定排序；读取不创建虚构条目。`GET /views/{view_id}` 返回该项目 DTO，停用配置仍可读取但不执行查询。

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| `POST /views` | `{expected_revision,expected_catalog_revision,name,type,definition}` | 201 `{revision,default_view_id,item}` |
| `PATCH /views/{view_id}` | `{expected_version,name?,type?,definition?,expected_catalog_revision?}`；至少一项实际变化；提交 definition 时必须带目录 revision | 200 `{revision,default_view_id,item}`；不同 view 的版本互不阻挡 |
| `PUT /views/order` | `{expected_revision,view_ids}`，恰好覆盖全部有效视图，不重复 | 200 `{revision,default_view_id,items}`，完整有效顺序与各自新版本 |
| `PUT /views/default` | `{expected_revision,view_id}`，必须有效且同项目 | 200 `{revision,default_view_id}`；已经是默认且无变化为 422 |
| `POST /views/{view_id}/archive` | `{expected_version}` | 200 `{revision,default_view_id,item}`，若为默认则已选择剩余默认 |
| `POST /views/{view_id}/restore` | `{expected_version}` | 200 `{revision,default_view_id,item}` |

不存在/跨项目为 404，无配置写权为 403，格式/白名单/无实际变化/不合法生命周期转换为 422；过期版本/修订、有效同名、数量上限和最后一个有效视图为可区分原因的 409。需要验证 definition 的目录/成员引用时，在同一事务检查；目录过期为 409。配置失败不改版本/revision/默认、不写事件。

新建/保存 definition 中的处理人 ID 必须是当前成员，非法提交为 422。已保存的合法条件在成员退组/账户删除后成为失效引用时，配置仍保留；query 使用该定义返回 409、reason=filter_reference_unavailable，并给该成员有权读取的条件索引。member 可显式在临时 override 中移除整个失效条件，或选择其他有效视图继续；owner/admin 可比较后 PATCH 保存修复，不自动改写共享定义。服务器先选择本次有效的 override 或共享定义，再验证引用，合法 override 不能被旧共享条件阻断。停用/恢复视图同样检查此恢复路径，不能让重置一直回到无解的 422。

静态 order/default 路由在动态 view ID 前接线。管理者保存 409 时保留名称及完整设置草稿，刷新后比较；不自动覆盖他人的视图设置。项目成员临时应用配置只产生查询，不调用 PATCH，也不写共享动态。

## API：真实查询与分页

新增 `POST /query`，与视图配置使用同一 `/plan` 前缀。请求为 `{view_id,expected_view_version,expected_catalog_revision,override_definition?,group_key?,window?,bucket?,limit?,cursor?}`；view 必须有效且同项目，两个期望值必填。override_definition 是与该 type 匹配的完整临时定义，不能改变 type、name、ID 或写入存储。所有成员可按自己有权读取的项目查询；输入配置不能扩大 ACL。

limit 默认 50、范围 1–100。返回 `{items,next_cursor,total,matched_total,unscheduled_total,groups,view_id,view_version,catalog_revision,server_today,server_timezone,query_fingerprint}`。items 使用 C1 完整待办 DTO；groups 为 `{key,count}`，key 是 `{kind,id}`，kind 严格对应 group_by，id 为该组合法文本 ID 或 null。名称从已验证目录/成员元数据映射，不在游标里重复放配置正文。

- list/table/board 不接受 window/bucket；total 和 matched_total 为完整 filters 后 unique todo 数，unscheduled_total 为 null。group_key 省略查整个匹配集合，给定时只查该组 items；groups 始终是公共 filters 后的全组计数，不因当前展开组而改变。
- gantt/calendar 必填 `window:{start_date,end_date}`，两端包含、顺序有效且最多 366 天；必填 bucket 为 scheduled 或 unscheduled。total 为 filters 后全项目 distinct todo 数；matched_total 为所选 bucket 在窗口条件下的数；unscheduled_total 为 filters 后缺少该视图日期依据的数。groups 固定为空。
- 日历 scheduled 用 date_basis 选定日期是否落在窗口；unscheduled 为该依据日期为 null，窗口不排除这些记录。甘特 scheduled 用实际/退化起止区间与窗口相交；unscheduled 为两日期皆空。窗口外数据不被当作不存在或被删除。
- 先 SQL ACL/filters/window/group，再排序和 keyset 分页；使用 limit+1 判断 next_cursor；多标签不让一个 todo 占多个 items。不能在浏览器过滤当前页，也不能因窗口限制丢掉无日期待办入口。

游标采用紧凑记录锚点，固定为 base64url 编码的 UTF-8 JSON `{v:1,query_fingerprint,last_todo_id,last_version}`，无 padding；长度最多 2 KiB，拒绝额外键。last_todo_id 使用当前公开 ID 格式，last_version 为数据库可存的正整数；不携带排序字符串、任意字段名、SQL 或正文，编码本身不构成权限边界。指纹覆盖项目、视图版本、目录 revision、临时定义、分组 key、窗口/bucket、适用的成员排序/分组摘要和会影响过滤的 server_today。

每页在同一个读取快照中按当前项目、未删除、完整 filters/window/bucket/group 找回锚点，并验证 last_version，再由服务器当前派生键、目录序位和固定 NULL 规则重建全排序元组进行 seek；重建与 ORDER BY 必须用同一个键原语。锚点删除、版本变更、不再匹配，或视图/目录/成员摘要/指纹变化均为 409、reason=query_changed，手动刷新保留查询草稿；纯游标结构错误为 422。一个合法的极长标题/显示名不会被送进游标或被截断来改变排序。验收包括 200 个 U+FDFA 标题、长显示名、三排序组合、相同长前缀与不同后缀、锚点变化/删除的真实双库分页。

配置读取和 query 在成员复核的事务内执行，锁序为 **相关成员按 ID → 有效项目 → 目录状态/项 → 视图集合状态/项**，写待办时再按 todo_id 锁行。计数、items 与版本从同一读取快照产生：PG 使用可证明的单语句快照或 repeatable-read 事务，SQLite 使用一致事务快照及项目成员撤权串行化；不把“先服务查成员，再另一个连接查数据”当读竞态已解决。事务冲突返回明确 409 并保留查询设置。查询不锁全项目所有待办，也不保存跨请求的永久快照。

现有 `/todos` GET 的 offset 合同保留，仍返回 `{items,limit,offset,has_more}`，增量 DTO 按 C1；不改老客户端的翻页参数。新的五视图只使用上述有界 query，不把旧 50 条一次读完当完整实现。

## 五种视图与实际操作

| 类型 | 必须实现的真实行为 |
| --- | --- |
| 列表 | 同 ID 的连续待办行/状态、可见属性、分组与独立加载更多；打开同一双栏详情；服务器排序与筛选结果 |
| 表格 | fields 控制真实列及顺序，title 固定；分组、排序指示、筛选条件、分页与批量保持原权限；隐藏字段刷新后按共享定义恢复 |
| 看板 | 实际分组的列、服务器 count、每列分页，卡片属性可见；状态列间拖动调用 PATCH；处理人分组仅 owner/admin 可拖动指派，优先级分组仅可向有效项/无拖动；标签多组不提供含义不明确的拖动改标签，使用字段编辑；停用项列禁止作为新增落点 |
| 甘特 | 真实日期区间、日/周/月缩放和窗口移动；无日期侧栏、仅一个日期时的单日节点；打开详情；条拖动整体平移两非空日期、边缘调整相应日期，使用单条版本化 PATCH 并遵守 C1，不写示例条 |
| 日历 | 月/周切换、前后/今天、服务器今天；月网格周一开始、6×7 日格，查询窗口覆盖所有显示格；周视图七日。按 date_basis 显示真实待办，无该日期侧栏；拖到日格只改所选日期，保留另一项并由 C1 检查；打开同一详情 |

甘特起止：有两日期用实际闭区间；只有开始用该日；只有截止用该日；皆空进入无日期侧栏。整体平移保持原有空值及区间长度；单日期节点若要首次设置另一日期，通过日期编辑浮层明确确认，不能悄悄补值。原有过去截止若被拖成另一个过去截止会被拒绝，退回显示已提交值但保留可比较的日期草稿；不把失败拖动动画当持久化成功。

日期工具按日历日算术计算网格/窗口/位移，不用 UTC 午夜转换。接近 C1 的 1900/9999 年边界时，月/周网格仍保留格位，范围外格位不可操作；查询 window 裁到合法边界，导航和拖动不能产生范围外日期。跨年、闰日、DST 与边界格位须有明确行为测试。

拖动只对该 todo 的有效写角色开启；只读卡片仍可打开详情。每种拖动都有键盘“修改日期/状态/处理人/优先级”的等价入口；成功以返回 DTO 更新后重新查询受影响视图与统计；409 保留拟议更改、显示当前服务器值与刷新比较，不自动重试覆盖。项目撤权/已删 404 清私密状态，目录/日期 422 不误清合法项目。

字段/分组/筛选/排序设置先作为临时查询实际生效，界面标识“未保存调整”；owner/admin 明确“保存到共享视图”后 PATCH。成员可以重置为服务器共享配置，失效处理人条件按上面的专用恢复规则提示。切视图有未保存设置提示：“继续编辑”取消此次切换并停留原视图，“舍弃并切换”清除原临时草稿并载入目标共享配置；管理者还可“保存后切换”，保存失败则停留且保留草稿。临时配置不跨视图缓存/沿用，刷新只恢复已保存定义。type 变更需要全定义并重新查询，不以改一个类型标签代表渲染完成。

视图新建、管理、设置、过滤弹层遵循现有熊宝品牌与 WorkBuddy 可见交互结构，使用当前组件/样式原语；不能把与工程有关的 cursor、revision 或路由名称暴露在日常用户流程中。当前类型、加载/空/错误、保存成功、409、权限与未实现能力均使用中英文案。

## 清理、审计、迁移和完成条件

新状态按账号+项目+view key 隔离，请求有序号/取消与私密状态清理。切项目/账户的晚到 query、视图保存、目录读取和日期更新成功/失败都不能改新页面。视图被停用/删除的 404 与项目撤权区分：复核仍有项目读权后加载有效列表供选择，不把旧 view 草稿自动应用到其他项目；项目/待办撤权沿用 B2 评论/图片清理。现有深链仍是项目+todo ID，不把视图配置 URL 当匿名共享链接。

新增 `project.todo_view_updated` 安全事件仅含 view_id、action、version、collection_revision、变更字段名，action 为 created/updated/ordered/archived/restored/default_changed；不含视图名、筛选文本或 definition。独立 SQL/服务白名单投影，不下发 JSON；member 的临时查询/设置不写共享事件。

SQLite/PG 配对迁移建立视图状态/记录、旧项目初始视图、标题及 users 内部显示名派生键；按实际实施基线分配下一未用编号。C1 的恢复水位要求继续适用，不能改历史 020/032/033 或覆盖已有自定义视图；project 创建事务接入种子。必须盘点用户创建/改名及待办改标题所有写路径，确保派生键同事务维护和旧数据回填、内部字段不影响用户公开 DTO/凭据；不能仅在计划页面改名时更新。

必须特别覆盖绕过 UserRepo 的 `infra/db/repos/invites.py::InviteRepo.accept` 新用户写入，以及备份恢复 `backup/snapshot.py::upsert_users_into_pool`。后者在 system_archive 迁移完成后可直接覆盖 username/display_name，必须在该 upsert 事务里以最终恢复值重新计算派生键，而不是依赖之前的迁移回填。派生键不信任归档包的值、不加入 UserSnapshot 公开凭据模型；旧包恢复后查询/游标按恢复后的名字生效。新旧数据库备份需实际往返验证视图定义、默认/顺序/version、目录引用、派生键和原评论图片。没有新文件字节资源，不为纯 DB 表虚构额外归档清单。

必须分别验收：

1. 新/旧/中断迁移和备份恢复；同名、停用/恢复、最后视图与默认切换；owner/admin/member/outsider、归档/撤权及跨项目 ID。
2. 两个真实 PG 连接修改同一视图的单赢家、不同视图均可成功、集合顺序/默认并发、目录停用与保存定义、退组与读取/query；失败无半定义/事件，实际无死锁。
3. 超过 200 条合成待办，三状态、多个处理人、有效/停用优先级、多标签/无标签、空日期和跨月区间；SQLite 与真实 PG 比较相同筛选、全项目 counts、空值、多键排序和跨页 ID。
4. 标题 `%/_/反斜杠`、NFKC/casefold、极长名称与派生键同事务维护、标签 any/all/none_of、nullable in/not_in A/B/null 真值、组合 AND、无效表达式/额外字段、无效/跨查询游标、锚点变化/删除、配置变化和午夜的指纹失效；所有 SQL 使用静态字段/操作符映射。
5. 登录态三身份创建/保存/刷新五类型；临时设置不写共享配置；列显示/顺序、组数/独立分页、排序与过滤实际变化；两个窗口的409不丢草稿；处理人退组/账户删除使保存条件失效时，member 临时移除条件、管理者显式保存修复；切视图未保存三种选择和切项目/账户迟到响应不串数据。
6. 甘特真实起止、单日/无日期、窗口/缩放/日期拖动与键盘入口；日历月/周/今天/跨月格/无日期、日期依据及版本化移动；日期上限/起止/旧逾期和撤权一致。
7. 1280×768、800×728 键盘/Escape/焦点/独立滚动、三种权限、双语；真实 TCP+PG 浏览器与 WorkBuddy 同视窗逐状态对照分开记录；前端组件/模拟数据不能替代真实视图旅程。

C2 交付只核销这里实际通过的字段与五视图能力；完整 PS-04 仍保留待办附件、子待办、数据源及未验证视觉，整体项目空间仍须按 PS-01–11 全旅程独立验收。
