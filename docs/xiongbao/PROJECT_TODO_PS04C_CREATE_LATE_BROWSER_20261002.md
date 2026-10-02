# PS04C 项目待办：新建结果跨项目迟到浏览器验收

2026-10-02，固定源码 `b0608d868e953f6289a3918f44b8051afa8f5386`。本片在合成 owner 真实表单/滑块登录、当前源码 Vite 开发环境和 headless Chrome 中完成四例，全部 PASS。154 条浏览器 API 请求的真实终态、16 次完整行增量审计，以及关闭后 SQLite 全行复核均通过。此次提交只新增验收文档，产品源码未变。

本片承接用户已批准的 C1/C2 和 Codex、按文件分工子代理授权；独立规格与方法质量 GO 后实际运行，固定候选与原件还必须通过独立终审方可提交。不代表完整 C1/C2、项目空间或 WorkBuddy 1:1 已验收。

## 方法版本与先前失败

此前 V1 实跑 `run-22543cf6d4b84b4db6fd4b3123f52136` 为 FAIL，第一例检查新建弹窗时停止，25条浏览器 API、零 todo POST、完整 Plan 与基线相同，owned 资源关闭。静态 V1 GO、失败日志/截图/ARIA和八方法原字节均保留。源码固定 octop 前缀，V1 ant-only 状态/loading/toast读取与之不符；具体失败操作数没有被保存，不能把推断当作已观测 null 或产品缺陷。

本次接受方法为 V2：精确 octop 状态/loading、octop 与 ant fallback toast 观察、具体断言及安全标量/正向忙态见证；四例、body、due min、一次真实写入、完整 terminal和十六次审计条件保持。独立 V2 静态审查及实际运行原件重新绑定。公开 manifest 保留前次 FAIL 和当前 V2 字节摘要；没有改写历史失败。

## 四个实际病例

| 旧新建结果 | 目标切换 | 实际处理与验收 |
| --- | --- | --- |
| 201 | A→B | 真正向本地 API 单次创建，完整201 DTO扣住至 B 新草稿就绪后交付；B 草稿与 UI 保持 |
| 201 | A→B→新 A | 同样实际单次创建；新 A 当前 query 已合法读取新行，随后旧201不能关闭或覆盖新草稿 |
| 明确受控409 | A→B | target未转发；回放基线前真实错误样本的 typed envelope，零 Plan 写入，新草稿/提示/锁定状态保持 |
| 明确受控409 | A→B→新 A | 同上；当前完整 query 与新草稿保持 |

旧 POST 每例精确一个，201 两例各转发一次，受控409两例均零转发。真实错误样本是在 fixture 阶段对错误目录 revision 发 POST，实际返回409、code=INVITE_INVALID、reason=catalog_revision_conflict；前后两项目九组完整行完全相同。受控 target 仅给 message 加明确 canary，保留真实 code/reason。不能将两条受控409称为自然并发冲突或真实权限撤销。

201 使用原 request 的 route.fetch，显式零重试、零重定向、15秒上游期限，未覆盖 URL、method、body 或 Authorization。[Playwright 官方 Route.fetch 文档](https://playwright.dev/docs/api/class-route#route-fetch) 区分上游获取与下游交付。本片分别保留上游 DTO、真实提交全行差分，以及旧浏览器 request 的 response/body、原 response.finished(null) 和实际 requestfinished；四条旧请求均完整交付且迟于新 scope 草稿，不以 headers 或上游结果替代浏览器完成。

## 草稿、UI 与真实数据

每例独立1440×1000 context，只 preload 中文语言，认证走真实表单和原生滑块。auth/me 与三条登录初始化 GET 完整实际200；只保存 token/Authorization 连续性布尔，未序列化认证正文、header 值或密码。hold 后采用既有 SPA history、项目链接和计划 tab，逐步核 performance.timeOrigin 相同。

新 modal 的标题、描述、状态、处理人、日期、priority/tags、calendar server_today/timezone、日期 min、账号与项目对应的有效 view ID、create/取消 enabled、无 loading/alert/conflict lock，以及完整 current query/reference 在旧结果前后保持。DOM MutationObserver 未观察到旧成功、受控错误或其它额外 toast；旧 callback 未发起目标项目动作。新草稿最终真实取消而不保存。

ABA 201 的新 A 可以显示已经提交的真实新行。oracle 使用新 A 完整当前 query 与独立 API reference 来判断合法读回，只禁止旧 callback 污染新 editor；不错误要求真实写入后 A 全部等于写前基线。

SQLite/schema35 每例在 committed、before-release、after-release、final 四阶段审计完整 A/B 九组数组：todos、tag links、所有项目事件（非视图及视图两组联合）、catalog state、priorities、tags、views、view state。每个201仅允许一条逐列匹配请求/完整 DTO 的新 todo 和一条完整 created event，零 tags；删除这两条已核行后 A 九组完全恢复前值，B 九组完全相同。受控409全部九组零增量。逐例 before 等于前一接受 after，累计移除仅两条已核 todo/event 后 A 恢复 fixture，B 一直相同；最终 A3/B1 的计数仅辅助，没有替代完整数组审计。

Root 还运行23个纯内存 QA oracle 正反样本，覆盖合法增量与重复行/事件、目录事件、旧行及 B 改动、links、DTO缺字段/类型等拒绝情况。它们验证审计方法，不计为23项浏览器或业务回归。

## 原件、清理与接受边界

固定原件为 `run-e7b37233a8724bf5b1c4e4088c1f38ec`，六阶段、四 core cases PASS。全部 page/observer/route/external/illegal-write/未知网络错误为零，目标 POST 无取消。四个 context、browser、owned Windows Job 全树、API/Vite 子进程、两个 localhost 端口关闭；SQLite底层 pool关闭一次。3,632 份来源源码及八方法运行前后 raw SHA 相同。

Root 原件核对仅生成待独立终审 receipt。发布门禁另要求同 run 的独立 actual+候选终审 GO、三个文件固定 SHA 与精确暂存/提交；方法 GO 或 Root 核对不自动等于独立接受。完整私有 DTO/DB/日志/截图和认证值不上传，公开仅最小汇总与原件/方法摘要。

本片没有执行账号新建迟到病例、其它列表编辑/详情/日期/comparison/404二段复核消费者、完整39代表矩阵、PostgreSQL、全套前后端回归、生产 bundle、GLM、旧源码 browser RED、原生 WorkBuddy 视觉、真实账号、付费 provider、部署或安装包。整体25项与 WorkBuddy 1:1保持未完成，继续补各自证据。

相关材料：[批准设计](PROJECT_TODO_FIELDS_VIEWS_PS04C_DESIGN.md)、[C1合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)、[C2合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)、[前一生命周期十例](PROJECT_TODO_PS04C_LIFECYCLE_BROWSER_20261002.md)、[本片摘要](evidence/ps04c-create-late-browser-20261002/summary.json)、[原件与方法 SHA](evidence/ps04c-create-late-browser-20261002/evidence_manifest.json)。
