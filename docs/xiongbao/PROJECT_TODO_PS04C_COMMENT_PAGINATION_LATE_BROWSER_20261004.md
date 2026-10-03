# PS04C 项目待办：评论分页迟到响应验收

2026-10-04，在基线 `187c70a30c4f6ab0edba240157fd22daf07774f7` 上完成评论分页消费者的独立验收。新增八项组件回归；Firefox 155 / Playwright 1.63、当前 Vite 源码和隔离 SQLite/schema35 的四例 actual 全部通过，运行 `run-7a00e7270f0f4e23b07dad5cb9078494`。业务源码未修改。本片补齐 `loadMore` 的真实 cursor 证据，不把前片首次 comments GET 验收重复计入。

| 旧 A 第二页 | 当前详情 | 实际结果 |
| --- | --- | --- |
| 原完整 200 | A→B | 恰一次原请求转发；旧响应完整结束后 B 的评论、两草稿、游标、错误/loading 和焦点保持 |
| 原完整 200 | A→B→新 A | 旧第二页与新首屏内容不同；旧响应完成后新 A 保持，随后真实分页达到完整21条、无重复、next_cursor=null |
| 明确受控 404 | A→B | target 零转发；保持真实 missing-todo comments404 envelope，仅替换 message canary；B 保持 |
| 明确受控 404 | A→B→新 A | 旧404完成后新 A 保持，当前真实第二页仍可完成并达到21条 |

夹具用真实 TCP 接口建立 A 的21条、B 的1条评论。先核 page1=20、真实 next_cursor 和 page2=1，全部四 context 用真实登录表单、滑块、侧栏和项目卡进入；没有注入 token、React 状态或页面存储。登录与项目列表响应以实际 requestfinished、完整 body 和 response.finished=null 为完成栅栏。正式基线建立后，仅允许严格匹配完整 DTO 的只读 plan/query POST，其他写请求阻断。两个200使用原 request 的 route.fetch 恰一次转发，零重试、零重定向、无 method/body/header/URL 覆盖；受控404不证明自然删除或撤权。

184条浏览器 API 请求均完整结束；六类页面、观察器、路由、外部连接、非法写入和网络错误为零。释放旧响应前后比较完整评论 DTO/DOM、服务时区时间、游标与加载更多按钮、描述和评论两份未保存草稿、错误/loading、当前详情及原生焦点。每例取消描述草稿、关闭详情并恢复真实“查看待办”按钮焦点。四份 PNG 与四份 ARIA 只保留在私有原件。

每例五个审计点（before、old-page2-held、before-release、after-release、final）逐字节比较 A/B 各十四组全行状态，共20个审计点。十四组为 project_todos、tag_links、todo_events、view_events、catalog_state、priorities、tags、views、view_state、comments、comment_images、comment_image_usage、project_record、members。SELECT 全行覆盖 soft-deleted 待办的评论/图片及项目事件；图片目录和字节清单保持。不等于全数据库或项目资产覆盖。

关闭后 SQLite 以 mode=ro&immutable=1、query_only、只读事务再核十四组与基线一致，读取前后 DB/WAL/SHM 原字节不变。四 context、browser、API/Vite、owned Windows Job 全树和两端口关闭，SQLite pool 底层关闭一次。3,633个受保护输入、方法、工具和五份合同在 actual 前后保持原字节。独立方法审查和独立 actual 审查均 GO；当前使用已授权原生只读审查，未调用 GLM 或外部实现路由。

新增组件测试另证正常翻页的排序/去重/游标、当前失败重试，以及旧成功/404在 A→B、ABA、同项目账号切换时对当前评论、两草稿和正在分页 loading/finally 的隔离，8/8通过。账号切换只属于组件证据，本片浏览器没有实测账号切换。组件早期定位失败的第一轮完整原日志未保留，仅有摘要；不作为业务 RED。第二轮原件、合法 B/t2 DTO 修正及第三轮当前8项通过日志均保留。

| 本轮质量项 | 当前结果 |
| --- | --- |
| `npm test -- --maxWorkers=4` | 251文件、1977测试通过，绑定当前新增测试 SHA |
| `uv run --offline --no-sync pytest -n 4 -m 'not live'`，短ASCII临时目录 | 5329通过、319显式跳过、25警告、exit0；非 PostgreSQL 实库验收 |
| Ruff check / format check、strict mypy | 通过；mypy 558文件 |
| ESLint、`npm run build -- --outDir <私有QA目录>` | exit0；ESLint有67警告；build含TypeScript检查，不修改内置生产SPA |
| 新增测试 Prettier；全前端 `--end-of-line auto` 检查 | 通过 |
| 默认 `npm run format:check` | 失败：1073文件行尾格式问题；原日志保留，不全库格式化 |
| `make all` | Windows PATH 缺 make，未执行成功；不称默认 ship bar 通过 |

原长Unicode临时路径全后端结果为2失败、5327通过、319跳过；同两项短ASCII临时目录定向通过，随后全量重跑通过。它们说明执行环境差异，不将路径长度认定为唯一原因。保留 browser-install subprocess reader-thread UnicodeDecodeError 警告，不混入本片分页源码修改。

浏览器 V1 在初始化读取不存在的 /auth/me 观察项时报错，零正式案例；V2 登录和十四组正式基线完成，第一例因标题按钮遗漏“查看待办：”ARIA前缀超时，尚未持有旧第二页。两批均保持 FAIL/独立NO-GO及完整原件，资源回收有证据。V3仅修必要等待和两处按钮定位，不改业务源码、四例或成功标准；不把方法失败冒充产品 RED。

交付严格限定新增测试、本说明、最小摘要和 SHA 清单四文件。完整 DTO、日志、数据库、图片、截图、认证 body/header 不公开。默认 make/hook 与前端格式门禁尚未满足，提交/推送须遵守当前 AGENTS.md，不能把当前候选称已发布。唯一源码工作区保持 D:/AI编程库/项目库/进行中的项目/xiongbao-Agent，未新建仓库副本。

本片不证明完整 PS04C、项目空间或 WorkBuddy 1:1；未实测 PostgreSQL、自然撤权、原生 WorkBuddy 同窗视觉、真实账户/provider、生产部署、Windows安装包、完整25项或其他消费者。030文件模式/030UI、045-F、046、PS04D未因本片开放。

相关材料：[批准设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)、[C2合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)、[首次评论GET前片](PROJECT_TODO_PS04C_DETAIL_COMMENTS_GET_LATE_BROWSER_20261003.md)、[本片摘要](evidence/ps04c-comment-pagination-late-browser-20261004/summary.json)、[证据SHA](evidence/ps04c-comment-pagination-late-browser-20261004/evidence_manifest.json)。
