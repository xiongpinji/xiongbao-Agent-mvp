# PS04C 项目待办：列表编辑结果跨项目迟到浏览器验收

2026-10-02，固定源码 `10e4665a8103fa71158cc388b06732dc4c0a616e`。在合成 owner 的真实表单/滑块登录、当前源码 Vite 开发环境、headless Chrome 和隔离 SQLite/schema35 中，四例均 PASS。155 条浏览器 API 请求保留完整实际 HTTP 终态，16 次完整行差分审计和关闭后 SQLite 全行复核通过。此次只新增验收文档，产品源码未改。

本片承接已批准的 C1/C2 规格及 Codex、按文件分工子代理授权。八方法及 3,632 份受保护源码先固定，独立规格和方法质量 GO 后实际运行；提交还须通过绑定同一原件及三份候选的独立终审。静态 GO、Root 核对或本片四例均不代表完整 C1/C2、项目空间或 WorkBuddy 1:1 已验收。

## 四个实际病例

| 旧编辑结果 | 目标切换 | 实际处理与接受条件 |
| --- | --- | --- |
| 200 | A→B | A 既有待办真实 PATCH 恰转发一次，完整 DTO 扣住至 B 的新编辑草稿就绪后交付；新草稿与 UI 保持 |
| 200 | A→B→新 A | 同样真实单次更新，新 A 从当前 query 合法读到更新后的目标及版本，再建立新草稿；旧成功不能关闭、覆盖或刷新新编辑窗 |
| 明确受控 409 | A→B | target 不转发，采用基线前真实 version conflict envelope，零 Plan 写入；新草稿、提示及锁定状态保持 |
| 明确受控 409 | A→B→新 A | 同上，返回新 A 的当前读回及独立草稿保持 |

每例从实际列表行的原生“编辑待办”按钮打开 modal，修改标题、plain 描述和截止日期，点击实际“保存”。PATCH 精确五键：expected_version/title/description/description_format/due_date；未修改的开始日期、处理人、优先级、标签及 expected_catalog_revision 全部 omitted。真实成功两例各转发一次；受控 409 两例均零转发，没有第二次保存或新建 POST。

409 样本由 fixture 阶段对同一合法目标提交错误的正整数 expected_version，实际 API 返回 409、code=INVITE_INVALID、details 恰为 reason=version_conflict 与 todo_id；探针前后两项目九组完整行相同。受控 target 只替换 message 为明确 canary，保留 typed code/details，不称为自然并发冲突、真实撤权或同 scope 的错误提示验收。

真实 200 使用原 request 的 route.fetch，显式零重试、零重定向和 15 秒上游期限，不覆盖 URL、method、body 或 Authorization。分别保存上游完整 DTO、真实提交后的全行差分、浏览器 downstream 的完整 JSON、原 response.finished===null 及真实 requestfinished。四条旧 PATCH 都在离开 A 且新草稿建立后完整交付；未将 headers、upstream 成功或取消算作迟到 HTTP。[Playwright 官方 Route.fetch 文档](https://playwright.dev/docs/api/class-route#route-fetch)

## 新草稿与持久化数据

每例采用独立 1440×1000 context，认证只走真实表单与原生滑块，auth/me 核真实 owner；未注入 token 或合成 UI 状态。切换通过既有 SPA history、项目链接和计划 tab，performance.timeOrigin 保持。

旧保存按钮必须是被点击的同一原生 DOM button、octop 前缀、disabled 且具有正向 loading 见证，旧标题、描述、状态、处理人、起止日期、优先级、标签及取消均 disabled。新编辑窗实际状态“待处理”，按钮“保存”与“取消”可用。旧结果释放前后，新草稿所有字段、目标 ID/服务端 base version、目录与日期窗口、当前有效视图、storage、完整 query/独立 API reference 和 UI/DOM 保持；没有额外 toast、Alert、冲突锁、loading 或旧回调发起的项目 API。每个新草稿最终真实取消而不保存。

ABA 的真实 200 可以合法显示已提交的更新，oracle 使用新 A 的真实当前 query/版本建立新编辑窗，区分合法读回与旧回调污染。未错误要求提交成功后目标 todo 等于旧基线。

四例均在 committed、before-release、after-release、final 审计 A/B 九组完整数组：todos、tag links、所有项目事件（非视图与视图两组联合）、catalog state、priorities、tags、views、view state。真实 200 的完整 16 键 DTO 与目标 17 列逐列对应，只允许 title/description/due_date/title_search_key/version/updated_at 六列变化；版本加一，其余列及原 ID/created_at 保持。title_search_key 按 NFKC/casefold；updated_at 是整数秒，可与旧 stamp 相等。

每次成功只新增一条完整七列 project.todo_updated 事件，actor/object/project 对应真实请求，created_at 与 DTO updated_at 相同，payload 恰为排序后的三项 fields 及未改动的前后状态/处理人，没有内容值或 version 等额外键。恢复当前目标的完整旧行并移除仅该条已核事件后，A 九组恢复 before，B 完全相同。409 全部九组零增量。逐例 before 连到前一 after，累计同一目标 version 加二且只新增两个已核事件；最终恢复原目标并移除仅两个事件后，A/B 九组恢复 fixture。数量仅辅助，未替代完整行审计；九组也不声称覆盖全数据库、资产或评论。

## 方法、原件与边界

Root 先执行纯内存 oracle TDD：未实现 stub 拒绝四个合法正例的 expected RED 保留原字节，之后 66 项 GREEN。正反样本涵盖合法同秒 stamp、NFKC/casefold、冲突零写及非法行列/事件/DTO/目录/视图/其它项目变化拒绝。它们验证 QA 审计方法，不是 66 项产品回归或浏览器测试，也不是旧产品 bug 的 RED。

固定方法版本为 V1，原件 `run-c3d11a69d2d5429a8960cd701b23250c`，六阶段、四核心病例 PASS。全部 page/observer/route/external/illegal-write/network 错误为零；本片不豁免任何 API requestfailed 或 query cancellation。四 context、browser、owned Windows Job 全树、API/Vite 进程、两个 localhost 端口关闭，SQLite pool 底层关闭一次；关闭后的 readonly 完整 A/B 数组与最终审计相同，读取前后 DB raw SHA 相同。3,632 份源码、八方法、批准合同、历史与两份 GO 的归档在运行及 Root 核对前后保持原字节。

公开只提交本说明、最小 summary 与 SHA manifest；完整私有 DTO、数据库、日志、PNG/ARIA 与认证正文/header/密码不上 Git。manifest 绑定本地原件、审查、QA 方法和 Root 核对，独立终审另外绑定固定三候选及发布 guard/private hook，之后才允许精确暂存、提交、普通推送和远端 SHA 复核。永久 Git hook 不变；没有宣称执行全量 build/CI。

本片没有执行列表编辑账号切换迟到病例、详情/日期/图表/comparison 等其它消费者、二段 404 项目复核、完整 39 代表模板、PostgreSQL、全套前后端回归、生产 bundle、GLM、旧源码 browser RED、原生 WorkBuddy 视觉对照、真实账号、付费 provider、部署或安装包。整体 25 项与 WorkBuddy 1:1 仍未完成。

相关材料：[批准设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1 合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)、[C2 合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)、[前一新建迟到验收](PROJECT_TODO_PS04C_CREATE_LATE_BROWSER_20261002.md)、[本片摘要](evidence/ps04c-list-edit-late-browser-20261002/summary.json)、[原件与方法 SHA](evidence/ps04c-list-edit-late-browser-20261002/evidence_manifest.json)。

发布候选 V1 的独立终审认可实际运行，但因本说明 C2 合同本地链接不存在而 NO-GO。V1 三候选、发布方法和回执已保留原字节；V2 修正为实际批准的 C2 合同链接并更新发布摘要绑定，沿用同一已通过实际运行，不重跑或改写浏览器原件。V2 仍须独立发布终审 GO 才能提交。
