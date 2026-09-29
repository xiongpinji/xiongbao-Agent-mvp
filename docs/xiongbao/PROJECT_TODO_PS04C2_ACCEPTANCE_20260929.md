# PS-04C C1/C2 本地功能验收（2026-09-29）

当前状态：**PS-04C C1/C2 本地功能验收通过**。源码、当前输入质量、真实数据库和两轮浏览器旅程已有固定证据；本记录的文档审查与提交推送由仓库外 ROOT 发布闭合记录单独证明。

已推送源码 `1e42ba4cd7afe9a099e873b7ee88aac01cd99586`（tree `449b5d059a0a12ed0f950482ce5a6608a44e46d4`）；本次 PS-04C C1/C2 的本地功能验收已通过。Task V 新 PG16 与评论图片浏览器17阶段均实际通过并正常清理。最终六文档的独立规格/质量审查与原生 hook 提交、非强制推送，以仓库外 ROOT 发布闭合记录为准；本地功能通过不代表 WorkBuddy 1:1 或全25项目标完成。

用户已批准三份书面规格，并明确授权 Codex 和按文件分工的实施子代理承接 C1/C2，以不同的只读规格/质量审查人与本地实际门禁验收，GLM 恢复后补审。授权见 [批准记录](PROJECT_TODO_PS04C_APPROVAL_20260928.md)。本批遵循已批准 [C1 合同](PROJECT_TODO_FIELDS_PS04C1_CONTRACT.md)与[C2 合同](PROJECT_TODO_VIEWS_PS04C2_CONTRACT.md)。

## 已实现的行为

| 范围 | 结果与边界 |
| --- | --- |
| C1 日期与目录 | 起止日期按日历日处理，服务器今天与时区参与校验；可管理优先级和标签，软停用保留历史关联；目录 revision、角色权限与待办 version 校验 |
| C2 共享配置 | 五类视图、创建/重命名/排序/default/archive/restore，集合 revision 与独立视图 version；owner/admin 保存共享配置，member 只读共享且可用临时覆盖；403/404/409 有界处理 |
| 全项目查询 | 服务端白名单 SQL 筛选、排序、分组、counts、keyset 游标与每组独立分页；完整 NFKC/casefold 名称键，标签 EXISTS 与 NULL 语义；目录、成员、锚点或定义变动使旧游标失效 |
| 五类实际 UI | 列表、表格、看板、甘特、日历读取同一待办 ID；实际 PATCH、键盘操作、甘特移位/调整与日历日期基准；计划详情继续复用 B2 组件 |
| 隔离与旧数据 | 034/035 升级、受控归档/恢复/失败补偿；项目、账户及同 ID 返回的迟到响应隔离，撤权后旧令牌拒绝；评论图片按成员/上下文授权获取 |

## 当前质量与执行证据

验收工作区当前后端 1310 个输入与 Q4 已通过门禁逐字节一致，采用全 not-live **5456 通过、184 显式跳过**；当前前端 1302 个输入的新门禁为 **249 文件、1906/1906 测试**，格式/lint/TypeScript/build 均实际 exit0。新后端 i18n **71/71**，Q5 登录态五视图旅程 **27/27 阶段**，两个视口共 **10 组严格布局测量**通过。Task V 新 PostgreSQL 备份/归档 **16/16** 已 actual `181b05 exit0`，两池、库连接归零、DROP、PG 正常 STOP 和 Windows Job 闭合另有 ROOT 核验。普通回归的跳过不等于实库通过，单独实库矩阵不与普通测试合计。

| 证据 | 当前结论 |
| --- | --- |
| Q5 代码审查 | 固定 v5 先 SPEC、后不同人 QUALITY，P0/P1/P2 均为 0；与 41 个发布路径及完整当前源绑定 |
| 后端常规门禁 | 已执行 Q4 not-live 5456 passed / 184 explicit skipped；当前 1310 个 backend 输入 raw 完全相同，因此采用结果。Ruff check/format、严格 mypy 已通过；没有为文档重跑业务套件 |
| 前端当前门禁 | 新 249 文件 / 1906 collected=passed，0 failed/skipped/unhandled；Prettier/ESLint/TypeScript/收集/Vitest/build 六命令 actual0，所有 owned Jobs 闭合 |
| i18n | 新后端 71/71；前端双语在当前 FE 与真实英文 UI 阶段覆盖 |
| Q4 专用 PG 查询/并发 | 当前输入绑定的 16 项真实 PG，通过两连接/锁等待/撤权/游标/组分页语义；正常关闭、库与连接归零，不采用已漂移的旧 PG62 为当前结果 |
| Q5 TCP 浏览器 | actual 27 阶段通过；225 待办、三种角色及 admin、临时覆盖/共享写/版本冲突、五视图同 ID 详情、甘特/日历真实日期 PATCH、项目/账户 ABA 与撤权、英文 UI；0 pageerror。1280×768/800×728 共 10 组几何测量通过 |
| Task V 新备份/归档 PG | actual 181b05 exit0；16 collected/JUnit passed，0 failed/errors/skipped；16 独立库、16 两池关闭记录、16 实际 pg_database/pg_stat_activity 零计数，PG18 STOP0/status3 和两 Native Job active0 |
| Task V B2 当前版本浏览器 | 17/17 阶段新 native 浏览器实际通过：PNG/JPEG/WebP 粘贴预览、201有序元数据、owner/member 私有原字节与 blob、错误上下文/outsider404、项目和账号真实导航后的迟到 HTTP、退组旧令牌404与 SPA 私密状态清理；0 pageerror/外网请求。API 正常 STOP、底层池关闭、连接/库归零与 DROP、PG 正常 STOP、5 个 owned Job active0/closed，3端口另由 ROOT 复核关闭。 |

验收工作区 `ps04c-view-service` 与发布工作区 `ps04c-fields-views` 的完整输入分别固定；既存非本次拥有文件的后端11处、前端21处CRLF/LF差异已单独证明无损等价，41个新前端发布文件 raw完全一致。实际发布 native guard 再次验证两工作区和整树，不能把“等价”误写成所有输入原始字节都相同。

后端普通回归、单独 PG 与浏览器阶段是不同证据，不相加成一个“总测试数”。日期边界1900/9999、目录错误重试、重复标签、批量50等单元覆盖没有逐项重做 WorkBuddy 同夹具实机旅程，不能称每一界面状态已达到 1:1。

## 提交与历史失败

Q5 发布为 exact41 native component；原 native commit 因 Git 原生 author date/exec-path 环境适配失败，原日志/退出码/物理 index 均保留。在固定白名单和独立方法审查后，从同一 staged index 继续原生 hook commit、提交后整树核验及正常非强制 push，actual 3b0149 exit0。HEAD 与 remote main 均为 1e42ba4c；永久 .githooks、Makefile 与 Git 配置未改，没有使用 skip/no-verify/reset/clean/amend/force。

Q5 浏览器原 attempt13 的 journey 与 cleanup 通过，但原 outer receipt 字段适配错误导致 exit1；原 FAIL 保留，新 attempt14 全链路 actual0。Task V PG 原 attempt3 的16项 runtime PASS保留，但后来发现 POSIX内部符号链接/..绑定语义缺口，不采用它为修正方法后的最终结果；新的方法 v5和PG attempt4已完成。该方法修补只在仓库外 QA，未改产品字节。

Task V B2 原 attempt1 因 Windows 保留端口连接超时，attempt2 虽真实连上正确数据库，但地址返回 `/32` 被裸 IP 检查误拒绝；两次均未启动 API/浏览器，原 FAIL 与正常关闭记录保留。仅仓库外方法修补并独立复审后，attempt3 的旧合成项目名超过当前接口15字符限制返回422；attempt4 的旧夹具误以为新项目已有列表，而真实新项目默认表格/看板，GET 200 返回零列表。夹具按当前合同以真实 POST 201 创建独立列表后，attempt5 通过13阶段，但账号阶段在人工网络延迟下未结束，触发480秒预算；排查发现该阶段的 Blob 探针缺少独立超时，原日志不足以定位具体 await；原 journey RUNNING、native FAIL、强制关闭 browser Job 和 API 正常池关闭/DROP 未证实均保留。ROOT另验6个PID消失、3端口关闭、PG正常停止，停止后的临时集群目录保留未删除。方法加入有限探针、细粒度标记，并在真实迟到 HTTP 完成后恢复网络。独立控制随后证实，fetch 已返回成功响应后的正文读取错误不能当作地址撤销；修正后只有 fetch 本身的非超时拒绝才通过，可读与两类超时仍失败。重新独立复审后，attempt6 的细记录定位到通用 response.finished 等不到已收到204后发出 ABORTED 的退出请求；页面已真实进入 /login，原私有GET在导航之后完成，但13阶段PASS/原journey RUNNING/native超时FAIL和browser强制关闭均保留。该次API正常STOP、池关闭、连接归零、DROP及PG停止已经独立验证。只读诊断和本地Playwright请求事件实现证实等待原语不适用：新专用退出观测绑定真实204、真实/login且token已清空、有界请求终止，仅允许FINISHED或精确204/net::ERR_ABORTED并保留原ABORTED；其他请求完成条件与原私有GET迟到顺序不变。再独立复审后，新 attempt7 才作为当前17阶段结果。

Windows 未运行不存在的 make；本次使用已批准的平台等价实际命令和有界临时 native hook。最终六文件文档采用先规格审查、后不同人质量审查，再经文档门禁的原生 hook 提交与正常推送；审查结果和文档提交 SHA 由仓库外发布闭合记录固定，避免文档自引用。

## 保留的验收边界

GLM 本次未执行，恢复后补审；WorkBuddy 同夹具逐状态像素、键盘及交互 1:1 仍未验；真实用户账号、付费模型、生产部署和 Windows 安装包均不在本次验收。PS-04 的附件/子待办/来源导入、030 UI/文件模式和 045-F 不因本批完成而被核销。全 25 项目标保持 active，项目空间 11 条旅程的“真实旅程 + WorkBuddy 视觉”双验收仍为 0/11。

数据与登录均为新临时合成账号、专用非5432 PostgreSQL、隔离 Chromium profile，使用实际项目 API/前端；未连接用户真实 WorkBuddy 项目、账号或付费供应商。截图与严格几何反映熊宝候选行为；评论图片使用真实浏览器 ClipboardEvent/DataTransfer 和真实文件/接口链路验证，系统级 Windows 剪贴板及快捷键未执行。本次没有更新 WorkBuddy 逐状态实机对照记录。

机器结果和原始 QA 引用见 [证据清单](PROJECT_TODO_PS04C2_EVIDENCE_20260929.json)。
