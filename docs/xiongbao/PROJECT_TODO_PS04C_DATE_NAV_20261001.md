# PS-04C 日期导航补充验收

日期：2026-10-01。固定干净源码 `67e8849048ddb2380aa7e1e5b2401c8e9e054261`，合成 PostgreSQL 与真实 Chrome 1440×1000 的独立实际旅程 **15/15 阶段通过，补齐 10 个日期导航动作**。业务源码未改；此前 C1/C2 本地功能接受继续保留。本批不核销 WorkBuddy 同状态原生视觉、完整 PS-04 或全 25 项。

实际 run 为 `run-da614d0a451f43ee94486a132ff8e32e`。服务器返回 today=`2026-10-01`、timezone=`Asia/Shanghai`；日期预期从该元数据和普通 API 创建、读回的七条完整待办 DTO 独立计算，没有导入产品日期算法。历史夹具使用合法 start-only 日期、due=null，不绕过过去截止日校验。一个合成 owner 通过真实登录表单、原生滑块和登录按钮进入项目计划；默认视图初始化和真实登录审计完成后才采集全表基线。

| 本批核心动作   | 实际闭日期窗口           | 日期格数 | 验证                                                  |
| -------------- | ------------------------ | -------- | ----------------------------------------------------- |
| 甘特日缩放     | 2026-09-17 至 2026-11-12 | 57       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 甘特周缩放     | 2026-09-17 至 2026-11-12 | 57       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 甘特月缩放     | 2026-09-17 至 2026-11-12 | 57       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 甘特上一个窗口 | 2026-07-22 至 2026-09-16 | 57       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 甘特下一个窗口 | 2026-09-17 至 2026-11-12 | 57       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 甘特今天       | 2026-10-01 至 2026-11-26 | 57       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 日历下一周     | 2026-10-05 至 2026-10-11 | 7        | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 日历周切月     | 2026-09-28 至 2026-11-08 | 42       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 日历上一个月   | 2026-08-31 至 2026-10-11 | 42       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |
| 日历今天       | 2026-09-28 至 2026-11-08 | 42       | 两 bucket 实际 200、完整 DTO 与独立顺序一致、全表不变 |

甘特从真实默认 57 日窗口开始，day/week/month 均保留 57 个 aria 日期 header；刻度文字和 CSS 最小列宽 42/22/9px 逐格核对，不将最小宽度描述为实际固定列宽。上/下窗口平移 57 日，今天从 server_today 开始保留 57 日。缩放只产生完整临时 definition，导航不保存共享版本。

日历以 start_date、week 为共享基线；下一周 anchor 加 7，week→month 保留当时 anchor，上一月将 anchor 改为相邻月初，今天恢复 server_today。周为周一起 7 格，月为周一起 42 格并包含跨月日期；日期按钮、星期标题、跨月 CSS 与卡片落格均核对。跨视图使用真实未保存弹窗的“舍弃并切换”，没有清 store 或保存。为了让今天确实改变窗口，脚本仅在必要时以既有 month next 作 setup；本 run 该 setup required=`false`。旧 week previous/today 和 month next 的已有证据仍保留，不重复登记为新缺口。

每个核心动作提前挂 scheduled/unscheduled 两个 limit50 请求，完整比较 view/version/catalog、window 和 override_definition；query body 没有顶层 date_basis。独立日期过滤与 title ASC 排序确定 IDs、全 DTO、total=7、各 bucket matched_total、未排期计数、空 groups 和 null cursor。含初始视图及切换 setup 共 **24 个必需完整 DTO 数据页**实际完成 200。24 页完整 DTO 在执行内存中 deepEqual，公开 pages 只保留 IDs、count、request metadata 与 full_dto_equal；另 1 条完成响应不追加称为完整 DTO 比对证据。最终所有观察到的 query 共 **27**：25 条捕获完成 200，2 条明确取消，0 条未捕获；取消不记作成功响应。浏览器关闭、drain 后复做全请求审计，导航区间非 query 写尝试、外连和网络/页面错误均为 0。

独占 owned 库 **53 张 public 应用基表、43 行**在基线、每步及浏览器关闭后的完整行与 owned identity 均相等。全表原始 JSON 仅从私有子进程 PIPE 进入内存，不落日志或报告；归档的是同一个已比较 raw snapshot 的安全项目投影和逐表 count/SHA。此项不涉及物理 PostgreSQL/WAL/统计或生产库接受。

14 张 PNG 的尺寸与 SHA 经独立实际复核。API 正常停止、池各关闭一次、session0 后 owned database drop；PG 正常 stop exit0/status3，三个回环端口关闭，四个 Windows Job active0 并关闭。Vite 是 owned 终止，退出码 `2`，不能称全部 child exit0。3119 来源文件与六方法在运行前后相等，HEAD/clean 保持；本地 fake 提供模型依赖，没有调用付费供应商或真实账号。

方法 V1 静态 NO_GO、NOT_RUN 原字节保留：登录断言失败可能持久化 JWT，关闭 context 后的 traffic 摘要可能过期，db_final 原来来自未比较的另一次快照。V2 只修这三项门槛并通过独立静态 GO；首个实际 `run-a868aa98244d40c39c9416620900eb1d` 在全表基线读回失败，10 个核心动作未开始，清理完成。静态核对确认 QA reader 误查 projects，真实 schema/repo 使用 project_spaces；旧私有 child 错误码未捕获，不声称实际 42P01。V3 只修 QA 表名、增加安全类别/固定阶段/SQLSTATE 元数据，并绑定旧失败 SHA 和完整清理后才执行一次新 owned run；日期、DTO、零写或清理条件未放宽。成功实际证据经独立只读 GO。运行与审查的原始文件、方法和图片保留本地，仓库只发布[摘要](evidence/ps04c-date-nav-20261001/summary.json)及[哈希清单](evidence/ps04c-date-nav-20261001/evidence_manifest.json)。

本批只追加验收文档，未重跑业务全套或构建；此前源码质量证据保留，不计为新执行。原三条 Agent Orchestrator 路由不变，历史 429、超时和未完成门禁保持历史状态；本批依据已授权 Codex 承接，以独立只读审查接受。WorkBuddy 原生双状态视觉/交互、其他 C2 竞态及错误组合、完整 PS-04/25 项、桌面安装、真实模型/账号和生产接受继续明确保留。
