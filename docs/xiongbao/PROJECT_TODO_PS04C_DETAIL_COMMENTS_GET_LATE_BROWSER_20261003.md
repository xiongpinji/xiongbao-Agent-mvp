# PS04C 项目待办：首次详情评论 GET 迟到浏览器验收

2026-10-03，在固定源码 `1096a6616223d43c42210cd79aef7a07fe5f9e8b` 上运行方法 V2-D10（`run-169caf9c2fad47e9a7182f019a9c987f`）。四个场景、六阶段通过；178 条浏览器 API 请求、356 条传输事件均有完整响应，16 次 A/B 各十二组全行审计及关闭后的只读复核通过。产品源码未改。本说明、最小摘要和 SHA 清单须经独立实际与发布终审 GO 后才可提交。

| 旧 A 首次 comments GET | 当前详情 | 结果 |
| --- | --- | --- |
| 原完整 200 | A→B | 恰一次转发；旧响应在 B 及两份未保存草稿就绪后返回，当前状态保持 |
| 原完整 200 | A→B→新 A | 旧完整评论页一条，新 A 两条；旧响应真正完成后，新评论及完整字段保持 |
| 明确受控 404 | A→B | target 零转发；真实 missing-todo comments404 envelope 仅替换 message canary，B 保持 |
| 明确受控 404 | A→B→新 A | 同类旧404完成后，新 A 两条评论及新草稿保持，不另写 fixture |

只延迟旧实例首次 comments GET：精确 ordinal、limit=20、无 cursor/body。旧 todo GET companion 合法完整完成，当前 B、新 A 的两个 GET 独立完成。两个200通过原 request 的 route.fetch 恰一次转发，无重试、重定向或 method/body/header/URL 覆盖；两个受控404来自独立真实 missing-todo comments 探针，保持 NOT_FOUND/details={} 结构。受控404不证明自然删除或撤权。四 context 使用合成 owner 的真实登录表单与滑块、当前源码 Vite、headless Firefox 155（Playwright 1.63）、隔离 SQLite/schema35，不调用真实账户或付费 provider。

释放前后核对完整 todo16键、评论 DTO7键及 body/author/stamp/images/next_cursor、完整评论 DOM、描述和评论两份草稿、编辑模式、属性/按钮/加载/错误、目录/成员元数据、完整 query 与独立 API reference、42格日期窗口、storage、URL 和原生焦点。每例取消描述草稿并关闭详情，恢复标题按钮焦点。八份 PNG 和八份 ARIA 只保留在私有原件。六类错误均为零，不豁免取消请求。

同值 todo 或草稿本身不足以单独证明成功守卫。第二例直接比较扣住的旧实际评论 page1 与新 A 完整 page2，确认旧响应真正完成后新评论和集合保持；不宣称某个内部 callback 分支单独执行。二段 parent project GET、分页和其它消费者未在本片验收。

十二组为 project_todos、tag_links、todo_events、view_events、catalog_state、priorities、tags、views、view_state、comments、comment_images、comment_image_usage。完整 SELECT */c.*/i.* 覆盖 soft-deleted todo 下评论/图片，项目事件联合覆盖 view 与非 view 事件；不等于全数据库、成员或项目资产覆盖。唯一明确 fixture 是一次真实 POST comments/201。完整新增评论、project.todo_comment_created 事件、规范 UUIDv4、规范化正文指纹、作者、对象和时间已私有核对；允许同秒。仅在证据中移除这两行可恢复既有 A/B 十二组，未回滚数据库。其余四阶段 GET 各零增量，图片 root/_tmp 清单保持。

Root 对已关闭 SQLite 以 mode=ro&immutable=1、query_only 和只读事务核对完整十二组；读取前后 DB/WAL/SHM 原字节保持。四 context、浏览器、owned Windows Job 全树、API/Vite 和两端口关闭，SQLite pool 底层关闭一次。3,632 份受保护源码、八方法、合同及冻结的非控制文件历史原字节保持。

首次 todo GET 前片在源码 `afe35c0b666142062b1b1593087148f9c46ca03d` 上以 V4 验收，之后推送至 `1096a6616223d43c42210cd79aef7a07fe5f9e8b`，本片未重跑它。评论 V1 的实际 FAIL（`run-ec96dc0117334715b99c84024e887fda`）保留：一例通过，第二例 fixture201 已发生但 QA snapshot 的场景字面量不匹配；资源已关闭，不证明产品缺陷。V2-D1 是静态 NO_GO，未启动 actual。D2 的启动实际失败（`run-6c9763e971574cffb257aec28630f754`）发生在 API 状态文件替换，浏览器/Vite 未启动，零核心用例，owned jobs 与端口已关闭；暂存 stop 观察不充当权威 server receipt，未证明产品缺陷或实际占用进程身份。D3 因绑定的外部上传控制临时文件消失而静态 NO_GO，未启动 actual。D4 只排除 `.baiduyun.uploading.cfg` 控制文件，记录原缺失路径与 SHA，不重造原字节，不豁免方法、JSON、日志、数据库或截图；状态文件替换使用有界重试。独立两秒占用探针的旧方法 PermissionError 与新方法完整状态通过属于方法持久化证据，不充当业务或浏览器 RED。原评论 V2 纯内存 RED 的四个合法例失败和修正后59项 GREEN 均保留；D5 新跑同字节纯内存 oracle 的59项通过（4正向、55反向），未再改 snapshot。它们不充当产品/browser/database RED 或全套回归。

D4 实际批次（`run-ebb14e8d425e4fa4859e5eaf4d8f7b8a`）的前三例通过，第四例尚未完成时，方法写 result.json 出现 EBUSY，整批未验收；资源全部关闭，失败原件保留。D5 的证据层改为一次打开的追加帧日志，最后完整状态与最终 result.json 必须逐字节相同，序号/长度/逐帧 SHA/全日志 SHA 严格核对；不复用中间 PASS。哈希采用流式读取，单帧和总日志有限制，写入、关闭、索引或最终结果任一步失败仍阻断。四例消费者及业务断言未改。Chrome 非消费者临时资料原目录保留，另以 ZIP 和逐文件 SHA 归档，避免嵌套历史造成 Windows 长路径；没有删除或重建丢失原件。

D5 实际批次（`run-4884c8267bbd42758778ba3d211a49e2`）因每次 save 重写累计报告，产生5,959帧、8,041,520,908字节，在浏览器收尾时超过300秒上限；虽然四例断言已完成，最终索引与结果均缺失，整批仍为 FAIL，原件和 SHA 保留且资源全部关闭。D6 仅将完整报告落盘时机改为阶段、场景和错误检查点，上限128帧；所有网络、DOM、数据库记录继续完整保存在报告，最终封存强制写入完整终态并严格核对。不延长超时、不删除原件、不放宽业务断言，不把中间 PASS 作为验收。D6 新跑同字节 fixture oracle 59项通过，新增检查点探针仅属于验收方法测试。

D6 实际批次（`run-b5c3faac694e4cd7a4acf5b2405be93c`）第一例通过，第二例初始 A 计划页的 owned Vite script 出现 ERR_NETWORK_CHANGED，随后页面 TypeError，尚无 target 评论或 fixture201；失败记录保留。两个 context 已关闭，browser.close 未确认返回，最终300秒清理超时；所有 owned 子树、端口和数据库 pool 随后由管理器关闭，整批 FAIL，未证明产品缺陷。D7 仅给失败和清理动作增加有限等待；正常 PASS 仍要求浏览器真实关闭，FAIL 才在终态封存尝试后 exit2 交还 owned 进程管理器。不豁免网络错误、不重试场景、不修改成功消费者断言或原运行超时。当前没有外部 CLI executor job，原路由及其尝试上限保持；供应商 usage 未测，不冒充0。

D10 仅为 Firefox 155 / Playwright 1.63 本地四例验收。D7 Chromium 四例消费者断言虽通过，但 SDK browser.close 未完成，整批 FAIL 原件保留；本片不将它改称 Chromium 整批 PASS，也不宣称 WorkBuddy 原生视觉或整体功能 1:1。

公开不上传完整 DTO、数据库、日志、截图、密码、认证 header/body 或图片原件。交付严格限定三文件，独立终审后精确暂存、普通推送并新鲜核对远端 SHA；永久 Git hook 保持。此片不证明完整 PS04C、项目空间或 WorkBuddy 1:1。未运行 PostgreSQL、全套回归、生产 bundle、GLM、旧源码 browser RED、日期/字段 PATCH、分页/图表/比较/账号切换、完整39模板、原生 WorkBuddy 同窗视觉、真实账户/provider、生产部署或整体25项验收。

相关材料：[批准设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)、[C2合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)、[首次todo GET验收](PROJECT_TODO_PS04C_DETAIL_TODO_GET_LATE_BROWSER_20261002.md)、[本片摘要](evidence/ps04c-detail-comments-get-late-browser-20261003/summary.json)、[原件与方法SHA](evidence/ps04c-detail-comments-get-late-browser-20261003/evidence_manifest.json)。
