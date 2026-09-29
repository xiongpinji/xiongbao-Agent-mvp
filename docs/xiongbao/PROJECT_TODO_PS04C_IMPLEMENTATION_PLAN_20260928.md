# PS-04C 日期目录与五种计划视图实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 逐项实施；TDD 先红后绿，先规格合规审查、再独立代码质量审查。并行采用用户已批准的按文件分工，所有共享路径串行。任务文本由 Codex 随完整上下文包发送。

**目标：** 在同一组项目待办上持久化日期、可编辑优先级、标签和五类共享视图，提供全项目有界筛选、排序、分组与日期操作。

**架构：** C1 用关系目录、标签链接和独立目录 revision，扩展现有待办 DTO 与版本事务。C2 用独立视图集合 revision/单视图 version、严格配置和 SQL keyset query；五种前端渲染都读取同一待办 ID。先保护旧库升级及 B2 内容，再接真实交互和独立验收。

**技术栈：** Python/FastAPI/Pydantic，SQLite/PostgreSQL，React/TypeScript/Ant Design，pytest/Vitest/Playwright；使用现有依赖。

## 当前交付状态（2026-09-29）

已推送源码 `1e42ba4cd7afe9a099e873b7ee88aac01cd99586`（tree `449b5d059a0a12ed0f950482ce5a6608a44e46d4`）；本次 PS-04C C1/C2 的本地功能验收已通过。Task V 新 PG16 与评论图片浏览器17阶段均实际通过并正常清理。最终六文档的独立规格/质量审查与原生 hook 提交、非强制推送，以仓库外 ROOT 发布闭合记录为准；本地功能通过不代表 WorkBuddy 1:1 或全25项目标完成。

验收工作区当前后端 1310 个输入与 Q4 已通过门禁逐字节一致，采用全 not-live **5456 通过、184 显式跳过**；当前前端 1302 个输入的新门禁为 **249 文件、1906/1906 测试**，格式/lint/TypeScript/build 均实际 exit0。新后端 i18n **71/71**，Q5 登录态五视图旅程 **27/27 阶段**，两个视口共 **10 组严格布局测量**通过。Task V 新 PostgreSQL 备份/归档 **16/16** 已 actual `181b05 exit0`，两池、库连接归零、DROP、PG 正常 STOP 和 Windows Job 闭合另有 ROOT 核验。普通回归的跳过不等于实库通过，单独实库矩阵不与普通测试合计。

GLM 本次未执行，恢复后补审；WorkBuddy 同夹具逐状态像素、键盘及交互 1:1 仍未验；真实用户账号、付费模型、生产部署和 Windows 安装包均不在本次验收。PS-04 的附件/子待办/来源导入、030 UI/文件模式和 045-F 不因本批完成而被核销。全 25 项目标保持 active，项目空间 11 条旅程的“真实旅程 + WorkBuddy 视觉”双验收仍为 0/11。

详见 [C2 验收记录](PROJECT_TODO_PS04C2_ACCEPTANCE_20260929.md) 和 [机器证据](PROJECT_TODO_PS04C2_EVIDENCE_20260929.json)。

## Goal

交付已批准 [C1](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md) 和 [C2](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md) 的全部可检查行为。授权和封存字节见 [批准记录](PROJECT_TODO_PS04C_APPROVAL_20260928.md)。

## Decisions and assumptions

起点 cdf1a1bc；2026-09-28 实测 migration 最大版本 033，本次 C1 分配 034，C2 在 034 接受后再次核对分配 035。不能改旧 020/031/032/033。原三条外部路由仅保留配置；本片原生 Codex 实施是用户显式承接例外，不能伪造为外部 CLI job/GLM 验收。共享内容、成员 ACL、归档例外、NULL 语义、紧凑游标均按已批准合同；附件/子待办/外部来源仍属整体目标的独立依赖。

## Constraints and guardrails

- 执行者不是独占仓库，不回退或覆盖别人的修改；只写所有权路径，额外文件先给协调者定位。
- 禁止 git add .、reset、clean、破坏性删除、依赖/凭据修改、部署、提交/推送及嵌套代理；Codex 审计白名单后提交和非强制推送 fork main。
- 不触碰原 checkout 的 WorkspaceDrawer.privateTask.test.tsx，不开启 PROJECT_TASK_FILES_MODE_ENABLED。
- 薄 HTTP、SQL 仓储、领域验证保持向内依赖；公开文本 ID；中文/英文镜像；日期按服务器 default_timezone 日历日。
- 首个失败测试必须因缺少需求行为而失败，语法/导入环境失败不冒充 RED。每项保存命令、退出码和样例；独立复跑不采信工作者报告。
- 本地合成数据和隔离 home/DB，不使用用户真实项目、登录资料或付费模型。失败草稿不自动覆盖或自动重试。
- 文档提交的 hook 跳过记录只适用文档；业务代码提交之前必须完成真实质量门禁，不能拿历史 make/all 或旧 B2 结果替代。

## 文件与责任

| 负责人/阶段 | 写入路径与职责 |
| --- | --- |
| M1 迁移实施代理 | src/octop/infra/db/migrations/034_project_todo_fields.sql、同名 .pg.sql；新 infra/db/project_plan_seed.py；migrate.py 的034分支；tests/unit/db/test_project_todo_fields_migration.py；旧 test_project_todo_comments.py 唯一水位适配 |
| M2 C1 后端实施代理 | repos/project_todos.py、projects.py、project_activity.py；新 repos/project_todo_catalog.py、project_plan_locks.py；infra/projects/todos.py、activity.py、新 todo_catalog.py；api/routers/project_todos.py、project_activity.py、新 project_todo_catalog.py；services.py、api/app.py、后端 zh/en 安全事件文案；C1 单元/HTTP测试及旧 DTO key 断言 |
| M3 C1 前端实施代理 | api/modules/projectTodos.ts、新 projectTodoCatalog.ts；ProjectPlan.tsx/test/module.less；ProjectTodoDetail.tsx/test/module.less；ProjectDetail.tsx/test 仅账户加载键；新 TodoFields.tsx/test、TodoCatalogManager.tsx/test、planDates.ts/test；dashboard zh/en；仅因必填 DTO 扩展需适配的项目测试夹具 |
| Q1 Codex | 新 infra/projects/plan_definition.py 与 tests/unit/projects/test_plan_definition.py；严格定义解析、游标结构、筛选值纯验证，不接数据库写路径 |
| R1 Codex | dashboard/src/api/request.ts 与 request.unauthorized.test.ts/request.setup.test.ts；捕获请求身份，旧会话续期或401不能改新账户token；只用合成token |
| Q2 迁移代理/Codex串行 | paired035、migrate.py、project_plan_seed.py；users.py、invites.py、backup/snapshot.py 最终名字派生键；旧备份/恢复测试；必须等 M1 释放共享文件 |
| Q3 C2 后端代理 | 新 repos/project_todo_views.py、infra/projects/todo_views.py、api/routers/project_todo_views.py；完成视图 CRUD/集合事务，服务注册/项目种子/活动投影共享文件等 M2 释放 |
| Q4 Codex/查询代理 | 新 repos/project_plan_query.py、infra/projects/plan_query.py、API query 接线；真实 SQL counts/filters/seek；tests/unit/db/test_project_plan_query.py、tests/integration/test_project_plan_query_api.py |
| Q5 C2 前端代理 | 新 projectPlanViews.ts、plan/ProjectPlanViews.tsx、PlanViewSettings.tsx、PlanTable.tsx、PlanBoard.tsx、PlanGantt.tsx、PlanCalendar.tsx 及同名 tests/less；旧 ProjectPlan 接线/语言包等 M3 释放 |
| 验收 Codex + 只读代理 | 独立执行聚焦/完整质量、SQLite/真实PG、备份、TCP登录浏览器和静态截图；只读代理审查固定 tree/文件SHA及完整diff，Codex掌握 Git |

M1/M2/M3 可并行：M2 使用 M1 的 seed_todo_catalog(conn, project_id, ts) 接口，M1 不写 projects.py；M2 不写 migrate.py/034/seed，M3 仅 dashboard。C1整合验收前只允许C2测试/文档准备，不开始C2业务实现。依赖表如下，不绕过未接受基础。

| 后继 | 必须已接受的前置 |
| --- | --- |
| C1 整合验收 | M1、M2、M3、R1规格/质量及真实DB/浏览器 |
| Q1、Q2 业务实现 | C1已整合验收；Q2另需M1迁移文件释放 |
| Q3 视图配置 | Q1、Q2 |
| Q4 SQL查询 | Q3 |
| Q5 实际联调和验收 | Q4、M3、R1；共用页面文件释放 |

## C1 与 Q1/Q2 发布前历史冻结门禁（2026-09-28，保留原记录）

M1/M2/M3/R1与补偿恢复、C1全量/真实PG/TCP浏览器和最终73文件整合审查已完成，发布 b613b90c；详见原C1冻结记录与仓库外实际发布结果。C2的Q1两源及Q2最终38件各自SPEC→不同人QUALITY已PASS/open0并由root组件接受。第四版PG6为62项零失败错误跳过，62库严格zero/drop及STOPPED；原并发P2四例真实RED、6项回归与9旧模块313项、2010原断言保留。当前完整BE3为5197通过/87跳过，Ruff/format/mypy均0，1283输入raw起止一致，owned进程闭合；FE236文件1686测试与静态/build PASS的1266当前输入也已只读核验。所有旧FAIL/格式失败保留。Q3已在独立worktree按新8/共享10路径并行实施，首个新GET404 RED与73项活动dev GREEN有证据；完整Q3、Q4、Q5及整体C2/V未完成，GLM/WorkBuddy视觉仍待验。此处是Q1/Q2源码检查点的发布前封存状态，实际Git/远程结果单独核验。

## 测试环境与精确命令

Windows PowerShell 在专用 checkout 执行：

```powershell
$env:UV_PROJECT_ENVIRONMENT = 'C:\Users\canqu\Documents\Codex\2026-09-23\qctop-work-buddy-agent-logo-ui-3\work\030-windows-path-qa\.venv'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
uv run --no-sync python -c "import octop; print(octop.__file__)"
uv run --no-sync pytest tests/unit/db/test_project_todo_fields_migration.py -q
```

已实测 Python 3.13.13 导入新 checkout 源码；--no-sync 不改变共用运行库。新 dashboard/node_modules 是指向旧已装依赖的目录 junction。前端在 dashboard 执行 npm test -- 相对 src 路径，不安装新包。pytest 仍通过 uv；PG验收单独创建合成库和真实两连接，不依赖 bare pytest/SqlitePool.path。

## 任务 M1：安全迁移与目录种子

- [x] 写 tests/unit/db/test_project_todo_fields_migration.py：构造033旧库、待办/评论/图片/usage，再 run_migrations，断言新日期空、公开ID/version/description_format与子行完全保持；PRAGMA foreign_key_check为空，FK目标仍是正式父表。故障在复制后与水位更新前注入，失败旧库完整，重放一次完成。
- [x] 运行 uv run --no-sync pytest tests/unit/db/test_project_todo_fields_migration.py -q，保留缺少新列/表的 RED。
- [x] 实现 paired034 和专用原子分支。SQLite 同一连接 BEGIN 前关 FK，建临时新父表、复制所有列、替换正式父表，检查 FK/子行/约束和种子，水位同事务；finally恢复 FK。不能先 RENAME 旧父表让子 FK 指向临时表。PG事务 ALTER、复合唯一/FK/约束、回填种子。seed_todo_catalog 只用传入 conn，不开第二连接。
- [x] 复跑新用例与 uv run --no-sync pytest tests/unit/db/test_project_todos.py tests/unit/db/test_project_todo_comments.py -q，旧水位断言用动态 _max_discovered_version 保留含义。
- [x] 独立规格→质量审查；Codex核验 migration pair、DDL兼容、失败回滚及raw schema并提交 M1 接受证据，未接受不启动Q2。

种子入口固定：

```python
from typing import Any
from octop.infra.utils.ulid import new_ulid

def seed_todo_catalog(conn: Any, project_id: str, ts: int) -> None:
    existing = conn.execute(
        "SELECT revision FROM project_todo_catalog_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if existing is not None:
        return
    conn.execute(
        "INSERT INTO project_todo_catalog_state(project_id, revision, updated_at) VALUES (?, 1, ?)",
        (project_id, ts),
    )
    for position, (name, color) in enumerate(
        [("紧急", "red"), ("高", "orange"), ("中", "blue"), ("低", "gray")]
    ):
        conn.execute(
            "INSERT INTO project_todo_priorities("
            "priority_id, project_id, name, name_key, color, position, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_ulid(), project_id, name, name, color, position, ts, ts),
        )
```

上面四个固定中文名称的规范键等于原名。迁移已存在state时另外验证目录结构及约束；不以重新种子覆盖已有自定义项。文本公开ID由现有 new_ulid 分配。

## 任务 M2a：目录 API 与配置事务

- [x] 新测试 tests/integration/test_project_todo_catalog_api.py 的首例调用真实路由，断言四级、revision1、server_today/server_timezone，member写403/outsider读404；NFKC同名409、排序全覆盖、停用保留、恢复同名冲突、活跃/总量上限、归档写409读有效。
- [x] uv run --no-sync pytest tests/integration/test_project_todo_catalog_api.py -q：404新路由是有效RED。
- [x] 领域 todo_catalog.py 做名字/颜色/数量规则，仓储同事务锁成员→项目→状态→项；创建/编辑/排序/停用/恢复 revision恰+1与白名单事件，失败不增。API按C1明确9条写路径注册静态order优先。服务容器注册新repo；ProjectRepo.create_with_owner 原事务调用seed，读不补种子。
- [x] 同一命令GREEN，再跑 uv run --no-sync pytest tests/unit/db/test_project_repo.py tests/integration/test_projects_api.py -q。
- [x] Codex对完整diff独立复跑，规格审查后质量审查；目录 DTO 不含名字键/rawJSON，安全事件只有允许字段。

首个 HTTP 断言：

```python
async def test_catalog_defaults_and_revision(env):
    ctx = await _base(env)
    response = await ctx["client"].get(
        f'/api/projects/{ctx["pid"]}/plan/catalog', headers=ctx["owner_auth"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert [(p["name"], p["color"]) for p in body["priorities"]] == [
        ("紧急", "red"), ("高", "orange"), ("中", "blue"), ("低", "gray")
    ]
    assert body["tags"] == []
```

_base 来自 tests/integration/test_project_todos_api.py，后续helpers在新测试中明确定义或显式导入，不虚构fixture。

## 任务 M2b：待办字段与并发锁序

- [x] tests/integration/test_project_todo_fields_api.py 先测状态/日期/目录/标签创建及DTO五字段；PATCH null与省略、过去开始/旧逾期/更改过去截止422、闰日、合并起止；停用只能保留/移除；目录版本冲突和待办版本冲突无写/事件。旧 expected_version 单独400/no_change；孤立 expected_catalog_revision422；bulk重复值仍增版本。
- [x] uv run --no-sync pytest tests/integration/test_project_todo_fields_api.py -q，确认字段拒绝或DTO缺失RED。
- [x] 扩展 ProjectTodoRow/TodoView 与仓储读写。字段、tags、目录revision在原事务构造DTO，提交后返回，create/update不再 self.get 混版本；old GET offset保持。服务按事务内服务器今天检查，布尔version拒绝。remove_member 先按todo_id锁目标行再清处理人，归档和项目字段锁序一致；日期/目录引用保留。
- [x] 运行上述GREEN及 uv run --no-sync pytest tests/integration/test_project_todos_api.py tests/integration/test_project_todo_comments_api.py tests/unit/db/test_project_todos.py -q。新增单元覆盖20tags、32/128priority、100/500tag上限与失败关联原子性。
- [x] 真实两PG连接 barrier交错目录停用/新增关联、revision同名赢家、退组/bulk/新字段、归档配置；记录成功或可解释冲突、无死锁、无半关联，独立审查接受后C1后端才算通过。

## 任务 M3a：共享字段与目录控件

- [x] tests先于实现：planDates.test.ts 对1900/9999、闰日、DST无跨日、服务器日期不可用和计算上下界；TodoFields.test.tsx 对过去截止禁用/nullclear/保留停用/20tag/manager入口；TodoCatalogManager.test.tsx 对真实API/409草稿/403/404。
- [x] dashboard内 npm test -- src/pages/Projects/planDates.test.ts src/pages/Projects/TodoFields.test.tsx src/pages/Projects/TodoCatalogManager.test.tsx，断言需求缺失RED。
- [x] 实现 calendar-day 累加/比较而非 new Date(YYYY-MM-DD)；目录API typed wrappers只通过request模块；共享控件接受完整Todo草稿、catalog/roles及提交回调，管理弹层使用真实CRUD/修订。未知服务器日期阻止日期提交并可重试。
- [x] 相同命令GREEN；npx tsc -b，必填DTO夹具补空日期/priority/tag/revision，不把字段改可选躲避错误。
- [x] 规格与质量只读审查，后端未接受前仅组件联调候选，不能宣称真实HTTP交付。

## 任务 M3b：原表格/看板和详情接线

- [x] ProjectPlan.test.tsx/ProjectTodoDetail.test.tsx 先写新建选状态+日期标签、详情字段、409草稿不关、账号项目切换的late success/error、403保留404清私密、目录管理返回原草稿，保留全部B2测试。
- [x] npm test -- src/pages/Projects/ProjectPlan.test.tsx src/pages/Projects/ProjectTodoDetail.test.tsx，只选新增测试先记录RED，随后全文件复跑。
- [x] C1字段在TodoEditorModal、表格列/board卡片、双栏属性栏统一显示/编辑；每个mutation capture账号+项目+todo key，detail key加账号；403/422仅写错误，404清私密；409不关闭原编辑器，刷新比较不自动覆盖。返回DTO为提交真值；发现catalog不同刷新目录再映射名称。
- [x] 全文件GREEN、npm test -- src/api/modules/projectTodos.test.ts、npx tsc -b；新增locale镜像检查与1280/800滚动/Escape焦点。
- [x] C1 SQLite+PG真实登录三角色HTTP/浏览器合成旅程，日期/目录写刷新同ID/版本；保存原图字节/B2行为；通过两阶段审查后记录C1接受再对外推送。

## 任务 R1：请求会话身份与账户切换

- [x] 在 request.unauthorized.test.ts/request.setup.test.ts 写 deferred fetch：账户A请求发出，切换合成账户B token，然后A晚到401或renewal不得clear/replace B；同会话续期仍正常，setup路径不改变。
- [x] dashboard内 npm test -- src/api/request.unauthorized.test.ts src/api/request.setup.test.ts，仅新增用例先记录真实全局副作用RED。
- [x] request.ts 在发出时捕获请求token/会话标记，全局更新/清理前与当前标记比较；组件仍负责丢弃旧渲染结果，不用传输guard取代页面key。所有request/requestBlob/requestUpload同规则。
- [x] 全两文件GREEN，复跑auth/setup与ProjectDetail账户加载测试；不打印或读取用户实际token。
- [x] 独立规格→质量审查，此路径不归字段前端工作者，避免同时修改共享传输文件。

## 任务 Q1：严格视图定义和游标纯验证

- [x] 新 tests/unit/projects/test_plan_definition.py：五类型defaults、额外键/错类型/过量filters、NULL in/not_in结构、boolversion、非法日期、重复keys、memberids验证标记、200 FDFA标题；compactcursor只有v/fingerprint/id/version且无padding、2KiB限制。
- [x] uv run --no-sync pytest tests/unit/projects/test_plan_definition.py -q 缺少需求RED；保留原缺键隐私失败及修复9例RED。
- [x] 实现 plan_definition.py 的 strict Pydantic unions 与 defaults/normalization；错误不接受任意SQL/表达式。纯结构与项目引用检查分离，query先选override再校验当前引用。紧凑游标不传标题、完整成员名或SQL元组。
- [x] 同命令GREEN；纯项目单位231通过，限定Ruff/format/mypy通过。
- [x] 独立SPEC v2→不同人QUALITY v2接受Q1；root组件记录 q1-acceptance-v2/acceptance-result.json=f3ecb828e863aba10dd43860bf2ea842af88794c17356f83f7a01eec057254ce。仅纯验证接受，Q3/Q4仍须等Q2。

## 任务 Q2：视图迁移与所有派生键写路径

- [x] C1已整合验收且M1释放迁移文件后，新 tests/unit/db/test_project_todo_views_migration.py、test_project_plan_sort_keys.py：033→034→035，默认两视图、title完整NFKCcasefold；UserRepo create/update、InviteRepo.accept、snapshot.upsert最终恢复名同事务派生键。
- [x] uv run --no-sync pytest tests/unit/db/test_project_todo_views_migration.py tests/unit/db/test_project_plan_sort_keys.py -q RED。
- [x] paired035建state/views/复合约束/新索引，回填全标题/全username或displayname，不截断；种子seed_todo_views同conn；用户派生字段不进入公开DTO/UserSnapshot凭据模型。现有自定义配置不重种，迁移/恢复失败事务回滚。
- [x] 同命令GREEN，并 uv run --no-sync pytest tests/unit/db/test_repo_users.py tests/unit/users/test_invites.py tests/unit/backup/test_snapshot.py -q；新旧受控备份实际roundtrip日期目录关联视图/default/version与评论图片。
- [x] 同一tree两阶段审查；M1和M2释放shared files后接线，归档包派生值不可信，GLM补审状态仍未完成。

## 任务 Q3：共享视图 CRUD 与生命周期

- [x] 新 tests/integration/test_project_todo_views_api.py：默认两项、fivecreate、strictdefinition、不同view独立version、集合order/default、archive最后一项409/default原子切换、restore冲突、member只读403/outsider404/归档409。
- [x] uv run --no-sync pytest tests/integration/test_project_todo_views_api.py -q，记录新route404RED。
- [x] 新 SQL repo/service/thin router 使用Q1 parser；目录引用同事务复核，view集合→item锁；所有成功写增collectionrevision，真实单view变化增version，静态order/default在dynamic前；活动白名单不含名称/filter文本。
- [x] 全新测试GREEN，复跑C1 catalog/fields API；真实PG两个连接证明同view单赢家、不同view双成功、order/default交错与目录revision冲突。
- [x] 规格→质量审查，fixedtree接受后Q4可依赖真实服务读取view，而非前端虚拟菜单。

## 任务 Q4：SQL全项目查询和有界分页

- [x] tests/unit/db/test_project_plan_query.py 和 tests/integration/test_project_plan_query_api.py：>200合成Todo、tags多组unique总数、12 AND filters、A/B/null真值、%/_/backslash字面、完整长标题/长用户名三排序、锚点版本/删除/指纹变化409。
- [x] uv run --no-sync pytest tests/unit/db/test_project_plan_query.py tests/integration/test_project_plan_query_api.py -q 缺少queryRED。
- [x] 用固定列/运算符白名单编译SQL，EXISTS标签，显式NULL类别与完整排序键；计数/metadata/items在成员复核一致读取事务，PG证明repeatable-read或同statement快照；limit+1和同ORDERBY primitive seek。只从当前query内找锚点再重建fulltuple，cursor返回compact字段。
- [x] 复跑SQLite并真实PG相同fixture，逐项对齐IDs/count/ordered pages；savedassignee离组query409索引，合法override先选可恢复；calendar/gantt窗口含无日期bucket/跨窗区间366day。
- [x] 真实双PG读/revoke与title/name/catalog/view更改游标失效验收，审查SQL注入/其他连接reads/分组total；接受Q4才算真实查询能力。

## 任务 Q5a：共享视图管理与真实列表表格看板

- [x] 新 PlanViewSettings/ProjectPlanViews/PlanTable/PlanBoard tests先红：types管理、tempoverride只query、manager显式PATCH、switch未保存三选择、badassigneeconditionmember移除、fields真实顺序/groups独立分页。
- [x] npm test -- src/pages/Projects/plan/ProjectPlanViews.test.tsx src/pages/Projects/plan/PlanViewSettings.test.tsx src/pages/Projects/plan/PlanTable.test.tsx src/pages/Projects/plan/PlanBoard.test.tsx RED。
- [x] typed projectPlanViewsApi只request；ProjectPlan主入口交给新shell，旧详情B2复用；每组独立query/cursor/count，不全量下载或客户端排序。board拖status/assignee/priority调用真实PATCH并按ACL/drop规则，tags组只字段edit；每种drag有键盘动作。
- [x] 相同命令GREEN+ProjectPlan/Detail旧回归+npx tsc -b。localStorage仅账号项目viewId，不存私密目录正文/filters。
- [x] 两阶段审查，所有temp/save/409/late response行为都标示候选直至真实API浏览器通过。

## 任务 Q5b：真实甘特与日历

- [x] PlanGantt.test.tsx/PlanCalendar.test.tsx 先红：两日期bar、one日期point、无日期栏、window/zoom/resize/shift保留null；Monday6x7月格/7日周、basis拖动只改一日期、1900/9999边界格禁用。
- [x] npm test -- src/pages/Projects/plan/PlanGantt.test.tsx src/pages/Projects/plan/PlanCalendar.test.tsx，记录缺失渲染/操作RED。
- [x] 仅渲染Q4真实scheduleditems，独立加载unscheduled；date arithmetic复用C1工具；保留失败拖动提议并比较，不把动画当保存；单日期补另一日期必须明确浮层确认。服务器今天加载失败不提供浏览器today。
- [x] 两文件GREEN及C1/C2全部frontend tests+npx tsc -b；keyboardedit/date跨midnight422与409不丢草稿。
- [x] 两阶段审查后进入真实登录浏览器五视图旅程；同todoId跨视图刷新、真实counts、数据移动和窄屏scroll/Escape/focus分别留证。

## 任务 V：整体质量、独立审查、浏览器与Git

2026-09-29：当前功能门禁已通过。以下保留原计划条目；后端常规套件按1310个当前输入 raw一致采用 Q4，其余新 FE/PG/浏览器实际执行。最后的六文档 SPEC→不同人 QUALITY/native-hook 正常推送由仓库外 ROOT 闭合记录确定，不将旧计划命令当作另一次执行。

- [ ] 独立复跑全部C1/C2 focused suites与旧projects/todos/comments/assets/activity/auth/backup/i18n；保留每次fail和修复RED，fresh质量输出不得复用旧B2。
- [ ] 完成make all的等价/可用平台实际命令：ruffcheck+formatcheck全src/tests、mypystrict、uv run --no-sync pytest -m "not live"；前端npm test完整、tsc -b、npm run build、ESLint/Prettier范围检查。make缺失必须明确记录；禁止为了commit抹掉全套红灯。
- [ ] 新专用PG两池交错、SQLite/PGbackup、真实TCP Uvicorn+Vite+私有Playwright profile+owner/member/outsider1280x768及800x728合成数据。看板tagscounts、列表表格保存、甘特日历移动、跨用户project切换/revoke403404与B2附件字节验证；不调用付费模型。
- [ ] 先按合同逐项规格审查，再独立固定SHA/tree质量审查；修复后复核当前字节。视觉Workbench比较单列未验证项，不写1:1完成。
- [ ] Codex仅stage被审核allowlist，校验index树/外来untracked/远程main基线、非强制push；再次核对HEAD==remote/tree与实际文件hash；更新执行台账、交付文档及资源ownedcleanup。原全25目标保持active。

## Checklist

- [x] M1: 034迁移、种子及旧库失败重放通过独立审查。
- [x] M2: C1目录/字段/锁序真实服务和API通过。
- [x] M3: C1字段/目录真实UI、账号隔离及冲突草稿通过。
- [x] Q1: 严格五类型定义与compactcursor验证通过（组件接受，不等于五视图UI或整体C2交付）。
- [x] Q2: 035与所有用户/标题写路径及受控备份通过（组件验收，非整体C2交付）。
- [x] Q3: 真实共享视图配置与生命周期通过。
- [x] Q4: SQL全项目query/filters/groups/counts/seek跨双库通过。
- [x] Q5: 五类型真实UI与日期/键盘操作通过。
- [x] V: 当前输入质量（后端逐字节采用、前端新执行）、实库/两轮浏览器与源码独立审查/推送/资源清理通过；最终六文档审查及推送另受外部 ROOT 发布闭合门禁。

## Validation strategy

每项先RED后GREEN，Codex独立复跑，规格审查先于质量审查；SQL/HTTP/真实PG双连接/登录TCP浏览器/WorkBuddy视觉各有证据。局部passed、default-suite failures、skips、GLM待补和视觉未验不可混写。截图/模拟组件不会单独核销旅程。

## Completion criteria

C1/C2合同逐条有真实实现和相应验收；无未解决可操作P0/P1/P2，质量门禁真实通过，备份与旧B2资源无损，远程main固定SHA已验证。GLM恢复前补审保持“待补”；WorkBuddy未观察保存/权限/视觉部分单列差距。PS-04完整附件/子待办/来源、030文件模式、045-F和整体25目标不被本计划核销。
