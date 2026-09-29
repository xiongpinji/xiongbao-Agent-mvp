# PS-01 / PS-02 真实 TCP 与 PostgreSQL 局部验收

2026-09-29，在 `1948200af633f17dcc03d19bba07af61cdba4d26` 固定快照上，既有项目创建、成员、邀请、角色、改名、撤权和重启持久化的 **35 项浏览器/HTTP 检查通过**。数据库直接回读通过；独立只读复核对固定证据一致性给出 **PASS**，对完整交付给出 **PARTIAL**。本批补齐了先前“SQLite 浏览器与 PostgreSQL 进程内 HTTP 分开验证”的证据缺口。PS-01/PS-02 整条正式旅程、WorkBuddy 1:1 及全 25 项目标仍未完成。

## 范围与运行身份

- 基线 HEAD：`1948200af633f17dcc03d19bba07af61cdba4d26`。业务源码仍为已发布的 `1e42ba4cd7afe9a099e873b7ee88aac01cd99586`；中间提交仅增加验收/设计文档。本批未修改业务源码，也未重跑全量业务测试或构建。
- 服务从当前 canonical checkout 导入；运行前后 **3118 个受记录文件逐字节一致**。此次使用正常 FastAPI/uvicorn TCP 服务与 Vite 前端，API `127.0.0.1:50439`、前端 `127.0.0.1:53517`；未使用 ASGITransport 替代浏览器网络。
- Windows Python **3.13.13**；WSL 中独立 PostgreSQL **18** 临时集群，端口 `52495`，项目数据库实际 schema version **35**。前端请求、服务端写入和最终直接 SQL 回读使用同一 owned 数据库。
- 四个合成用户分别登录正常页面并实际拖动滑块；浏览器未预置登录 JWT，也未绕过验证码。后续另开一个窄屏上下文，共关闭 5 个 owned 浏览器上下文。补充权限检查使用已登录合成用户的旧令牌，经真实 TCP 请求。
- 仅操作新建的 QA 项目、账号、数据库和受控临时 home。账号密码、邀请令牌、JWT、授权头及浏览器秘密配置不进入本验收文档。

## 已验证的功能结果

35 项 `PASS` 另加 1 项 `COMPLETED_ACTION`。后者只是“请求重启”的动作，已由重启后持久化检查覆盖，不增加测试通过数。逐项原始记录保存在 `attempt-4/browser/browser-result.json`。

| 功能 | 本批实际结果 |
| --- | --- |
| 登录与创建 | 四个合成用户正常 UI 登录返回 200；“项目交付”模板预填说明和指令，名称按现有批准设计留空由用户输入；UI 创建返回 201、owner、成员数 1。只覆盖该模板。 |
| 列表、搜索与改名 | 外人搜索列表返回 200 且没有该项目，详情 404 且页面不显示项目名称；owner 改名返回 200，成员刷新和搜索能看到新名称；无匹配搜索及清空后恢复项目通过。 |
| 免审批邀请 | UI 生成返回 201；成员正常 UI 接受返回 200/`joined`；用过的链接重放返回 409/`INVITE_USED`，页面清除 URL 中的令牌。 |
| 审批邀请 | 申请返回 200/`pending_approval` 并显示等待状态，审批前详情 404；另一个完整浏览器审批路径保存了 owner 点击返回的 200/`approved`，接收者能打开项目；重复审批的补充 TCP 检查返回 409/`INVITE_INVALID`。 |
| 角色权限 | 成员提升 admin 和降回 member 均返回 200；刷新后邀请按钮随权限出现/消失；降权前保存的令牌再生成邀请返回 403；member 自升权限的补充 TCP 检查返回 403。 |
| 移除与撤权 | owner 正常确认移除返回 204；同一旧成员令牌读取详情和成员列表均为 404，项目列表 200 且项目缺席；成员刷新后显示无权且隐藏名称。已有画面不会自动跨会话清空。 |
| 持久化与邀请撤销 | 正常重启后 owner 仍读取改名结果，被移除成员的页面与原令牌仍被拒绝；UI 撤销未使用邀请返回 200/`revoked`，再接受返回 410/`INVITE_REVOKED` 并清除 URL 中令牌。 |
| 取消操作与秘密字段 | 取消移除没有产生 DELETE；取消编辑没有改变名称；邀请列表补充 TCP 检查返回 200 且不含令牌或哈希。 |

最后直接 SQL 回读（`attempt-4/database-readback.json`）确认项目新名称持久化、owner ID 2 与已获准成员 ID 4/5 保留、被移除成员 ID 3 不在成员表，两项申请均 `approved` 且 `resolved_by=2`。此回读独立于上表 35 项计数。

## 界面观察与保留问题

固定证据含 **13 张 PNG**，桌面视口 **1280×768**，移动视口 **390×844**。初始与滚动后的几何记录均未观察到横向溢出；正常滚动后成员列表和邀请控件能完整显示。初始桌面成员卡部分超出视口，移动端成员卡在首屏下方，**首屏完整呈现未通过**。项目名称在可见面包屑显示；1px 可访问性 `h1` 不是可见标题的测量对象。

最终截图是 `09-final-desktop-initial.png`、`10-final-mobile-initial.png`、`11-final-desktop-members-scrolled.png`、`12-final-mobile-members-scrolled.png`。截图只证明熊宝当前状态；没有同夹具、同视口的 WorkBuddy 逐状态对照。

跨会话移除或角色修改后，已渲染画面需要下一次请求、导航或刷新才能更新。服务端用旧令牌的下一次请求已立即拒绝；**自动推送清空旧画面未验收**。WebSocket 连通观察不等于通知业务、自动撤权 UI 或 PS-11 消息中心验收。

## QA 中断、诊断与恢复

原失败全部保留，最终结果采用独立的正常收口，不覆盖历史失败状态：

| 历史事件 | 保留结果与本批处理 |
| --- | --- |
| 首次 API 夹具对兼容行切片 | `row[:3]` 在 `_CompatRow` 失败；只修 QA 为逐项读取并复现原异常，未改业务仓储。 |
| 第二次启动 home 守卫 | 服务读到非 owned 的默认 home 后由 QA 守卫中止；此后只在 owned 子进程设置 USERPROFILE/HOME。 |
| 第三次 API 绑定 | Windows 端口 53516 拒绝绑定；保留失败，另选验证可绑定的 50439。 |
| 浏览器控制器超时 | 第一条审批其实已落库，但原点击响应未保存，父页刷新卸载弹窗后等待关闭超时；保留 14 项中间记录与空待审列表观察，另走第二条完整审批路径保存真实响应。 |
| 原 PG 租约到期 | 原集群租约仍记录 `FAILED`、实际 exit 1；其 owned 清理已 stop 0、status 3、端口关闭。独立 recovery 租约验证原 owned 数据目录和身份后恢复同一数据库，最终正常 STOP 0。 |
| 首次直接回读 SQL | QA 误用 API 资源名 `projects`；保留 `FAILED_QA_QUERY`，依据当前仓储表 `project_spaces` 修正只读 QA 查询。 |
| 历史 WebSocket 错误 | 保留 31 次 `WebSocket closed without opened.`。诊断确认 QA 前端端口 53517 与默认 HMR 客户端 5173 不一致；仅在启动子进程设置 VITE_DEV_PORT/VITE_HMR_CLIENT_PORT，未改源码。固定观察 **77184 ms** 中，5 条 HMR 连接收到 `connected`，新增 pageerror **0**。 |
| 清空搜索检查的 QA 条件 | UI 清空时请求省略 `q`，原 QA 等待 `q==''` 不成立；保留诊断，用正常刷新捕获 200 且项目恢复的证据。 |

## 固定证据与独立复核

证据目录：`C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-project-members-20260929`（仓库外保留）。Root 在 `ROOT_SCOPED_ACCEPTANCE_EVIDENCE.json` 冻结 **39 个输入**的 SHA256 与字节数；独立 reviewer `/root/ps01_ps02_tcp_evidence_review` 重新计算全部 39 项，缺失、哈希和字节数不一致均为 0。v1 对尚未结束的运行给出 PARTIAL，历史保留；v2 针对最终固定证据给出局部一致性 PASS、完整验收 PARTIAL。

| 固定文件 | SHA256 |
| --- | --- |
| `ROOT_SCOPED_ACCEPTANCE_EVIDENCE.json` | `cf9f88489edce4507a290ff9251f83c60069eb0178f6f28e34a7624211ef1e8c` |
| `readonly-evidence-review-v2/report.md` | `1462af3d288cfb5e99383c60a0707f97a831644c59537db593e0dd71a99bb3d8` |
| `readonly-evidence-review-v2/result.json` | `5ad650932f8183b28e7b23e50d1fd767bb62c4837bb2c9c7198ce12ddac59327` |

独立复核未启动服务、操作浏览器、调用模型或读取 excluded 秘密文件；这是固定证据审查。GLM 原路由本批 **NOT_RUN**，不把 native reviewer 记作 GLM 结论。

## owned 运行资源清理

- API 最终 `STOPPED`：4 次 start、4 次 stop；4 个 PG pool 均只 close 一次且底层 closed，最终库连接 0、owned 数据库 DROP、源码前后不变。实际 terminal `c92501`，exit **0**。
- 浏览器 5 个上下文关闭，所有页关闭且 browser disconnected。
- 原 PG terminal `1e2e80`，exit **1** 的失败保留；recovery PG terminal `861062`，exit **0**，status_after **3**、端口关闭。
- 最终 Vite terminal `038aa0`，exit **1** 来自 owned TTY Ctrl-C，属于明确停服而不是测试通过。Windows 三个 owned loopback 端口 **50439/53517/52495** 均关闭。PG 数据目录为追溯保留，不作为正在运行的服务。

## 后续验收边界

PS-01 仍保留全部模板/覆盖草稿完整旅程、真实历史大库升级、PG 并发、WorkBuddy 逐状态视觉和键盘对照；PS-02 仍保留 SSO 回跳、并发撤权、完整窄屏交互、通知及 WorkBuddy 对照。正常合成登录、实库 TCP 和局部窄屏观察不等于真实账号或生产验收。

Native Electron、用户安装包、付费模型和生产部署本批未验。PS-04D 的书面规格已单独发布且等待批准；030 UI 和 045-F 的实现承接授权仍未得到答复。本批不扩大这些任务的授权范围。[差距台账](PROJECT_SPACE_GAP_AUDIT.md)继续保留 11 条项目空间旅程的“完整真实旅程 + WorkBuddy 视觉”双验收 **0/11**，全 25 项目标保持 active。
