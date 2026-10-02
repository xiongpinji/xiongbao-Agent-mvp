# PS04C 项目待办：首次详情 todo GET 迟到浏览器验收

2026-10-02，在固定源码 `afe35c0b666142062b1b1593087148f9c46ca03d` 上单次执行 V4（`run-eb2cbf629016412189fff1456b3a2701`）：四个交互场景、六阶段均通过。192 条浏览器 API 请求与 384 条实际传输事件完整对应；16 次 A/B 各十二组完整数据审计和关闭后的只读复核通过。本次交付验收材料，产品源码未改。

范围承接已批准 PS04C C1/C2 及 Codex、实施子代理和独立原件审查授权。合成 owner 通过真实登录表单与滑块进入同一 SPA，在当前源码 Vite、headless Chrome、隔离 SQLite/schema35 下操作原生标题按钮和双栏待办详情。八方法、3,632 份源码、批准合同和历史证据先固定，独立规格与质量 GO 后运行 V4；Root 原件复核后，仍需独立实际与精确三文件终审才可提交。

| 旧 A 首次 todo GET | 当前详情 | 实际结果 |
| --- | --- | --- |
| 原完整 200 | A→B | 原 request 恰一次转发；B 详情及两个未保存草稿就绪后释放旧 body，当前完整状态保持 |
| 原完整 200 | A→B→新 A | 旧 comments companion 一条；唯一合法真实201 fixture 新增评论后，新 A 完整两条保持，未被旧结果覆盖 |
| 明确受控 404 | A→B | target 零转发；复制真实 missing-todo 探针 envelope，仅改 message canary，当前 B 保持且无错误清空 |
| 明确受控 404 | A→B→新 A | 同类旧404完成后，新 A 累计两条评论和新草稿保持，没有第二次 fixture 评论写入 |

只延迟旧实例的首次 `GET /api/projects/A/todos/todoA`，精确 ordinal、无 query/body。旧 `comments?limit=20` companion 和当前 B、新 A 自己的两个 GET 都合法完整完成；同 URL 的重开请求独立识别。两个200使用原 request 的 route.fetch，零重试、零重定向、15秒期限，未覆盖 method/body/header/URL。两个受控404复制独立真实404探针的 `NOT_FOUND/details={}` 结构，只替换明确 canary message；探针前后完整十二组及图片清单零增量，不宣称自然删除或撤权。

所有旧 target 都在新详情、独立描述草稿和评论草稿就绪后真正交付。原件记录 response headers、完整 JSON/text SHA、原 response.finished===null、实际 requestfinished 及完成次序。192 条 API 全部为完整 http_response；page、observer、route、external、illegal_write 和 network 六类错误均为零，API abort 不获豁免。

释放前后完整 todo16键、comments7键及正文/作者/时间 DOM、两份草稿、描述编辑模式、按钮/加载/错误状态、属性、目录/成员元数据、query与独立 API reference、42格日期窗口、storage、URL与原生焦点均保持。每例取消未保存描述并关闭详情，恢复标题按钮焦点；未保存描述、未由浏览器发表评论、未注入 token/state、未以退出登录替代同一 SPA 切换。八份 PNG 与八份 ARIA 原件保留私有；评论集合与完整 DOM 由结构证据核对，不把单张截图当作完整集合证明。

首次成功链写 DetailState，但不清草稿，较高版本的 newerTodoSnapshot 也可能保留。因此同值 todo 或草稿不能单独证明成功守卫；A→B→新 A 的真实评论从一条到两条提供区别性见证。本片接受旧 HTTP 真正完成后的当前状态保持，不宣称内部某个 callback 分支已单独执行。首次 comments GET、后续 parent project GET 是另外消费者，未在本片核销。

十二组是 project_todos、tag_links、todo_events、view_events、catalog_state、priorities、tags、views、view_state、comments、comment_images、comment_image_usage。完整 SELECT */c.*/i.* 包含 soft-deleted todo 下的评论和图片；view及非view事件联合覆盖项目事件。todo SQL17列含 title_search_key，评论7列、事件7列、图片8列、用量2列。它们不等于全数据库、成员或项目资产覆盖。

唯一明确 fixture 是同合成 owner 的一次真实 POST comments/201。独立核对完整新评论、新 project.todo_comment_created 事件、规范 UUIDv4、UTF-8紧凑 normalized body/images=[] 指纹、真实作者、event.object_id=todoId/actor/payload_json={}/一致时间；同秒合法。只在证据中移除这两条已核新增行，可完整恢复所有既有 A/B 十二组；没有数据库回滚。第二例 committed 在 fixture 独立审计后，其余四阶段对目标 GET 各零增量；后例 before 接前例 accepted after。评论图片 root 与 _tmp 存在性、目录及普通文件原字节各阶段不变，不扩展到 Chrome 或 runner 临时目录。

历史 V1、V2、V3 各自实际 FAIL、零核心病例接受，均保留原件。V1 精确初始断言未恢复；V2 在登录阶段出现 ERR_NETWORK_CHANGED，未证明唯一网络原因；V3 当前 B 描述相等，但 uiReceipt 小写名称正则先拒绝大写 DTO，是已定位 QA 名称缺陷。V4 仅修正五个固定名称和版本绑定，原比较和严格规则不变。长路径准备失败也保留。旧59项纯QA oracle的实际 stub RED与GREEN仅是历史内存方法证据，本轮未重复运行，也不充当产品/browser/database RED。

Root 旧只读解析器 R1 将七个带 URL/JSON/DOM 的固定 localFailureGuard site 混同为仅小写的 uiReceipt 名称，在闭库 SQL 前失败，原文件及失败记录保留。R2 仅以固定七名称和 local_site_fixed=true 区分两类记录，实际比较和网络失败门禁保持；同一浏览器运行没有重跑。R2 读取已关闭的 SQLite，以 mode=ro&immutable=1/query_only/事务核完整十二组与最终原件相同；DB/WAL/SHM 存在性、大小和原字节读取前后不变。

四 context、browser、owned Windows Job 全树、API/Vite及两端口已关闭，SQLite pool 底层关闭一次。源码、方法、合同及历史原字节保持。公开仅本说明、最小摘要和SHA清单，不上传完整 DTO、数据库、日志、截图、密码或认证 header/body。固定三候选需独立实际与发布 GO、精确暂存、普通推送至 xiongbao/main 并核新鲜远端 SHA；永久 Git hook 保持。

本片不证明完整 PS04C、项目空间或 WorkBuddy 1:1。未运行首次 comments/二段 project GET、日期/字段 PATCH、分页/图表/比较/账号切换、完整39模板、PostgreSQL、全套回归、生产 bundle、GLM、旧源码 browser RED、原生 WorkBuddy 同窗视觉、真实账户/provider/生产部署或整体25项验收。原三条 CLI 路由未被改写为本次成功证据。

相关材料：[批准设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)、[C2合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)、[前一列表编辑验收](PROJECT_TODO_PS04C_LIST_EDIT_LATE_BROWSER_20261002.md)、[本片摘要](evidence/ps04c-detail-todo-get-late-browser-20261002/summary.json)、[原件与方法SHA](evidence/ps04c-detail-todo-get-late-browser-20261002/evidence_manifest.json)。
