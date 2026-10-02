# PS04C 项目待办：跨项目与账号生命周期浏览器验收

2026-10-02，固定源码 `71aa16ad4942be1fecd514a217c6fd1a664ff3ad`。本批在真实登录、当前源码的 headless Chrome/Vite 本地开发环境中完成十例，全部 PASS；500 条浏览器 API 请求均有实际终态，两项目九组完整 Plan 行在各例前后、最终快照及关闭后的只读复核中均保持基线。此次发布只新增验收文档，不修改产品源码。

本批由用户已批准的 PS04C C1/C2 和 Codex 按文件分工承接授权覆盖；以固定八方法独立 GO、原始运行复核及固定候选独立终审 GO 为发布门禁。不代表完整 PS04C、项目空间或 WorkBuddy 1:1 已验收。

## 实际通过的十例

| 路径 | 切换动作 | 例数 | 实际证据 |
| --- | --- | --- | --- |
| 旧目录 GET 200 / 404 | A→B、A→B→新 A | 4 | 精确旧 request 的完整 JSON、原 response.finished 校验、实际 requestfinished；当前草稿、日期、优先级与页面保持 |
| 旧目录 GET 200 / 404 | owner 头像登出→member 表单滑块登录→同一 A | 2 | 新 member auth/me 200；当前 catalog/views/query 完整 DTO、实际请求凭据匹配布尔、草稿、DOM、元数据及账号与项目对应的 view ID 保持 |
| 旧共享 query | A→B、A→B→新 A | 2 | 精确旧 request 真实 ERR_ABORTED 早于方法 release / route.abort；当前 query 完整实际 200，新草稿与数据保持 |
| 旧共享 definition PATCH | A→B、A→B→新 A | 2 | 真实客户端先取消；旧 PATCH 扣住未转发；目标共享定义和新草稿保持 |

六个目录旧响应中 200、404 各三条。200 回放基线前通过真实 API 保存的 A/R2 历史目录，当前 A 是 R3；`server_today=1900-01-01` 明确作为受控日期 canary 覆盖。404 是受控 NOT_FOUND envelope，不冒充真实撤权。四个 query/PATCH 是实际客户端取消验收，没有将已取消请求描述为旧 HTTP 200/409/404 已投递，也没有将扣住的 PATCH 描述为后端写入或并发验收。

每例均使用新浏览器 context，认证只走真实表单、原生滑块和按钮，不预载 token。登录后三条初始化 GET（tools、skills、auth/me）全部实际 200 后才进入项目。hold 后只用现有 DOM/SPA 导航，并验证 performance.timeOrigin 相等；目标就绪以完整 DTO、目录和视图版本、当前待办与 DOM 为依据，排除精确 held ID，没有以全局 networkidle 作为 oracle。

## 登出传输事实与身份验收边界

494 条 API 请求具有真实 requestfinished；另外四条是目标 query/PATCH 真实客户端取消，另两条是两个身份病例中头像菜单触发的 owner logout。后两条实际顺序为 204 headers→net::ERR_ABORTED，没有 requestfinished，终态始终保留 requestfailed，单列 known_no_content_logout_aborts，未计入 query/PATCH 取消或 HTTP 完成。

该分类要求 exact 两个身份病例、事先 armed 的唯一真实头像菜单 POST /api/auth/logout、fetch 非导航、真实本地 API 转发、当前 owner Authorization 内存匹配、无 held/cleanup/方法 abort/fulfill，并发生在 member 登录前。方法允许正常 finished 的 204 或符合上述守卫的真实失败子集 0..2，不强迫浏览器产生取消；本次实际子集是两条。

身份病例只有在清 token、同文档回到登录页、member 真实登录及 auth/me 200、同一 A 当前完整 DTO、实际项目请求凭据匹配、草稿/DOM/元数据/有效选中 view ID，以及完整数据库基线全部成立后才 PASS。此证据验证 C1 账号 scope 隔离，不证明 logout 完整传输，也没有证明 Chromium abort 的原因。产品原有 best-effort logout 语义保持；未改产品或通用豁免 auth/204 错误。[RFC 9110 15.3.5](https://www.rfc-editor.org/rfc/rfc9110.html#name-204-no-content) 仅提供 204 无正文的协议背景，不能据此证明浏览器取消天然无害。

## 完整数据与资源证据

SQLite/schema35 的两个独占合成项目，每例前后、最终及关闭后只读复核均深度比较九组完整数组：project_todos、tag_links、todo_events、view_events、catalog_state、priorities、tags、views、view_state。读取采用 mode=ro、query_only 和同一 BEGIN 事务。基线后 direct API 仅 GET；所有非法写入、外连、pageerror、observer error、route error 或未知网络失败均为零。

十个 context 和浏览器关闭，Windows Job 全树 active=0；API/Vite 子进程与两个端口均关闭，SQLite 底层 pool 实际关闭一次。3,632 份受保护源码及八方法运行前后 raw SHA 完全一致。私有原件包括 30 PNG、30 ARIA、完整 DTO/事件 ledger、行快照、日志、方法归档与审查；公开文件仅保存最小摘要及 SHA，不上传数据库、认证值或完整私有原件。

## 保留的失败原件

V1 `run-ec330f2ff7eb49acb981c6c224c39544` FAIL：刷新按钮实际 ARIA 为“刷 新”，原 literal 选择器在点击前失败。V2 `run-c2078ece787b4901b1720404ec7b0ffa` 和 V3 `run-b67f2a475bd748cca5a0950fb9131756` 均整体 FAIL，分别只在失败 run 内有四个项目切换病例 PASS；不计为接受批次。V3 精确复现 response204 后 requestfailed ERR_ABORTED，定位为观察器提前把 headers 记为终态。V4 修正事件模型并增加上述专用身份后置检查；旧 run、诊断、方法及审查仍按原字节保留，没有改写为 PASS。

本次通过原件为 `run-2f7d22ed1e7445ac8ea73a5a4ac42a22`，12 phases、10 core cases、500 条 API 实际终态。Root 原始证据复核只生成待独立终审 receipt；固定候选仍需独立终审及精确三文件提交门禁，不能拿方法 GO 或 Root receipt 冒充独立运行验收。

## 尚未覆盖

本批没有运行 PostgreSQL、全套业务回归、生产 bundle、GLM、旧源码浏览器 RED、同页账户热切、完整待办 create/detail/date 迟到矩阵、主动取消后的旧 HTTP 投递、真实账号、付费模型、部署或安装包；也没有新增 WorkBuddy 原生 UI 的实机视觉验收。前次测试、源码或本批十例不替代这些证据。完整 25 项和 WorkBuddy 1:1 继续保留未达项。

可追溯材料：[批准的 C1/C2 设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1 合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)、[C2 合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)、[前一目录错误与撤权浏览器批次](PROJECT_TODO_PS04C_CATALOG_BROWSER_20261002.md)、[本批摘要](evidence/ps04c-lifecycle-browser-20261002/summary.json)、[原件与方法 SHA 清单](evidence/ps04c-lifecycle-browser-20261002/evidence_manifest.json)。
