# PS-04C 当前 PostgreSQL 浏览器增量补验 · 2026-09-30

在固定干净来源 `00897ccb44ec41db2ebad566cf1b10f8fcbe3f3d` 上，第四版测试方法的新建独立 run 完成 **10/10 阶段 PASS**。当前业务源码没有改动；本批补齐近期日历焦点、优先级行内编辑和冲突比较修复的 PostgreSQL 浏览器证据，不重复核销旧 C1/C2 SQL 矩阵。

## 授权与方法

本批沿用[PS-04C 已批准规格及 Codex 承接](PROJECT_TODO_PS04C_APPROVAL_20260928.md)。原三条 Agent Orchestrator 路由保留，原额度/超时失败 jobs 没有伪完成；当前使用已批准的原生 Codex 实施与独立只读复核门禁，GLM 补审仍待后续。PG 工具子代理在落盘前被停止，由 ROOT 完成 QA 工具，业务源码始终不变。

测试只创建本地合成 owner/member 等账号、全新 WSL PostgreSQL 18 临时集群、独立数据库和私有 Chrome。当前 API 实际绑定 PostgresPool、schema 35，SQLite fallback 拒绝；独立连接验证 DB/user/port/data_directory，使用 REPEATABLE READ READ ONLY 快照。3119 个来源文件和 6 个可执行方法在运行前后逐字节冻结一致。

owner/member 通过真实 `/login` 页面输入合成凭证、拖动本地滑块并完成登录，浏览器不预载 JWT。API 用于准备合成数据、视图和制造明确并发更新；这些操作不宣称为 UI 创建。模型 harness 为 fake，proactive 调度关闭；无关 PyPI 更新探测在 QA 进程内返回 None，产品更新功能未验收。API 外连保护与浏览器来源限制仍生效。

## 当前运行结果

十阶段包括一个 API 数据准备阶段和九个实际页面/数据阶段；两身份真实登录另有证据，不额外增加阶段数。

- owner 字段创建、保存和重载；成员创建/编辑自己的待办，成员目录修改 403/FORBIDDEN 且数据库不变。
- owner 优先级行内编辑保存后，父草稿与既有待办 ID、创建者、版本及事件保持；后续新建继续使用原优先级 ID。
- 三类 409：旧待办版本、新建时目录版本、目录编辑版本冲突。拒绝后无写入、草稿保留；明确刷新比较和确认后重提交。
- 截止日期月界禁止回到过去月份；返回月界时焦点有效，Escape 只关闭日历并恢复触发器焦点，编辑器仍开着且数据库不变。
- owner/member 分别切换表格、列表、看板、甘特和日历，共十组真实渲染/选中检查；同一已保存待办的查询/题名/日期与 API/PG 记录一致，两身份共享 definition 一致。日历显式切到截止日所在月份；渲染不修改待办、事件或共享配置。

最终 PostgreSQL 中有 4 个待办、4 条标签关联、7 个待办事件；目录 R7、7 个持久视图（其中 5 个本批核对）、视图集合 R6。共有 21 张截图；登记 50 条浏览器项目 POST/PATCH/DELETE，其中 11 条非 query 请求，计数不含 API 夹具、并发更新、登录或 GET，也不等于全部请求或全部成功写入。

最终 page error、network failure、浏览器外连、API 被拒外连均为 0；14 条 ProjectDetail 模块响应均 200。独立原始证据复核 **GO / APPROVE，无 P0/P1/P2**，方法门禁与实际运行复核分开进行。

## 生命周期与失败保留

成功 run 的 API/PG 正常退出 0；一个 PG pool 恰好关闭一次且底层 closed，删除测试数据库前会话数 0，数据库删除确认。PG stop=0、status=3、端口关闭；四个 Windows Job 均 closed/active=0，API/web/PG 三端口关闭。另经审核的 Linux disposer 逐个核对四轮的作用域、identity、UID、解析后的唯一 `/tmp` 目录、PG_VERSION 18、无 postmaster.pid、fresh status3 与端口关闭，先归档日志，再删除四个自建停止集群目录。

前三轮均保持 FAIL/INCONCLUSIVE，不能改写成接受证据：V1 在浏览器前发生 QA 行适配误判，初始化 pool 未关导致 DB drop 拒绝，但集群正常停止；V2 前九阶段通过后因 QA 持有目录 R4 投影、当前 R7 而失败，并记录 29 次被拦截 PyPI 更新连接（pool/DB 清理完成、API exit2）；V3 前九阶段通过后误取看板 limit1 数量探测响应，并保留两条动态模块加载 page error，API/DB/PG 完整正常清理。第三轮模块错误的根因未被确立；第四版等待页面网络稳定、记录所有失败请求和模块状态，没有删除错误断言，实际新 run 错误为 0。

## 证据与边界

脱敏结果见 [summary](evidence/ps04c-current-pg-browser-20260930/summary.json)，方法、独立审查、四轮原始结果及 21 张成功截图的哈希见 [manifest](evidence/ps04c-current-pg-browser-20260930/evidence_manifest.json)。原始数据、请求体、凭证、具体项目/待办/视图标识和图片留在本地 QA，未发布。文档候选审查与推送的固定字节另绑定外部 ROOT release receipt，避免 manifest 自引用。

本批不重跑全量质量：`87cec425..00897ccb` 的 src/dashboard/tests/依赖文件差异为空，既有 5329 通过/319 跳过与相邻前端 649 通过仍是[此前绑定的质量证据](PROJECT_TODO_PS04C_SAVE_CONFLICT_20260930.md)，不能称为本批重新运行。默认全前端格式的存量 CRLF FAIL、Windows auto PASS 和 make 不可用的历史限制保留。本批发布候选仅检查新文档/JSON 格式、精确 diff 与证据哈希。

此补验不代表 WorkBuddy 真实保存语义或逐状态视觉 1:1，不代表真实账号、生产数据库、真实 provider/付费模型、内部通知、完整 PS-04/PS-05、25 项总体、打包或部署验收。030 UI、045-F 和未批准后续任务保持原门禁。总目标继续 active。
