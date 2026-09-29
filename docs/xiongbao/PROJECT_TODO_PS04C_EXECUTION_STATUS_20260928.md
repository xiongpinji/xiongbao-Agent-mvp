# PS-04C 原生执行台账（2026-09-28）

## 当前状态（2026-09-29）

已推送源码 `1e42ba4cd7afe9a099e873b7ee88aac01cd99586`（tree `449b5d059a0a12ed0f950482ce5a6608a44e46d4`）；本次 PS-04C C1/C2 的本地功能验收已通过。Task V 新 PG16 与评论图片浏览器17阶段均实际通过并正常清理。最终六文档的独立规格/质量审查与原生 hook 提交、非强制推送，以仓库外 ROOT 发布闭合记录为准；本地功能通过不代表 WorkBuddy 1:1 或全25项目标完成。

验收工作区当前后端 1310 个输入与 Q4 已通过门禁逐字节一致，采用全 not-live **5456 通过、184 显式跳过**；当前前端 1302 个输入的新门禁为 **249 文件、1906/1906 测试**，格式/lint/TypeScript/build 均实际 exit0。新后端 i18n **71/71**，Q5 登录态五视图旅程 **27/27 阶段**，两个视口共 **10 组严格布局测量**通过。Task V 新 PostgreSQL 备份/归档 **16/16** 已 actual `181b05 exit0`，两池、库连接归零、DROP、PG 正常 STOP 和 Windows Job 闭合另有 ROOT 核验。普通回归的跳过不等于实库通过，单独实库矩阵不与普通测试合计。

| 项 | 当前功能与门禁 |
| --- | --- |
| M1/M2/M3/R1 | C1 日期、自定义优先级/标签、034 升级、目录与身份隔离已验并发布 b613b90c；原 C1 冻结记录保留 |
| Q1/Q2 | 严格定义、035、完整 NFKC 排序键与受控恢复基础已发布；历史 PG62 属于当时输入，不作当前 C2 新实库结果 |
| Q3 | 共享视图 CRUD、排序/default/archive/restore、集合 revision/视图 version 与角色权限已实施并发布 180e4b91；独立规格→不同人质量门禁通过 |
| Q4 | 全项目 SQL filters/groups/counts/keyset query 已发布 c4b8409e；当前专用 PG16 查询/并发结果按 source1310 输入采用，完整后端门禁 source绑定有效 |
| Q5 | 列表/表格/看板/甘特/日历、共享管理与临时覆盖、真实编辑和日期操作已发布 1e42ba4c；27 阶段登录态浏览器与 10 组严格几何通过，固定 v5 SPEC→不同人 QUALITY 均 open0 |
| V | 16 项新 PG 归档/失败恢复与17阶段新 B2 评论图片实际通过并闭合；六文档 SPEC/不同人 QUALITY 与 exact6 native-hook 正常推送由外部 ROOT 发布闭合记录核验；GLM/WorkBuddy 视觉独立待补 |

GLM 本次未执行，恢复后补审；WorkBuddy 同夹具逐状态像素、键盘及交互 1:1 仍未验；真实用户账号、付费模型、生产部署和 Windows 安装包均不在本次验收。PS-04 的附件/子待办/来源导入、030 UI/文件模式和 045-F 不因本批完成而被核销。全 25 项目标保持 active，项目空间 11 条旅程的“真实旅程 + WorkBuddy 视觉”双验收仍为 0/11。

最新结论以 [C2 验收](PROJECT_TODO_PS04C2_ACCEPTANCE_20260929.md) 与 [机器证据](PROJECT_TODO_PS04C2_EVIDENCE_20260929.json) 为准。外部 durable plan 的 pending 仍是外部 CLI 台账，不能冒充原生代理实时状态。用户已明确授权本片由 Codex/按文件分工子代理承接和独立只读门禁；原三路由、GLM 补审和其他片边界保持原记录。

## Q1/Q2 发布前历史记录（原文保留；以下不是当前待交付状态）

状态：C1发布于 b613b90c；C2的Q1与Q2源码基础均已分别完成SPEC→不同人QUALITY审查并由root接受。Q2最终第四版真实PG62项全部通过并严格清理，完整后端attempt3为5197通过/87跳过，Ruff check/format与mypy通过，1283输入起止一致。前端当前1266输入与既存PASS完全一致，可采用236文件/1686测试及静态/build结果。Q3在独立工作区实施，首个视图API已实际404 RED，活动接线新旧73项development测试通过；Q4/Q5仍等待Q3接受，五视图UI和整体C2未交付。本文件封存Q1/Q2源码检查点的发布前状态，实际提交与非强制推送以Git记录和远程SHA核验为准。

C1 原实施基线为 `3b657f24fc5e5c1441f3cc5127201ad2def976d0`，发布树为 `59701d365f5fcf7690dbefda0e550f0b148ccbb6`。仓库外 QA 的 `ship/c1-integrated-review-final/review-result.json` 已为 `C1_INTEGRATION_PASS`（SHA256 `68964df2750ae9899a5ec4ddd6f41c6512ce0f68465dd867510944bb1b0449d4`）；`ship/c1-publish-result.json` 为 `C1_ACCEPTED_COMMITTED_AND_PUSHED`（SHA256 `ec8a0ca80be6067296ec07943691956757d16355bad0cba52c6fe8a84d1d0e40`），实际 HEAD 与 `xiongbao/main` 已核对为 b613b90c。原 [C1冻结记录](PROJECT_TODO_PS04C1_ACCEPTANCE_20260928.md) 和 [机器证据](PROJECT_TODO_PS04C1_EVIDENCE_20260928.json) 保留发布前的历史快照，不能据其“待发生”文字否认随后实际发布，也不能把旧 C1 全套结果当作新 C2 质量证据。

外部 durable plan：`plan-20260928-045530-5cecba`，起点 cdf1a1bc，risk high、migration、max_parallel 3。该 runner 会把实施计划的全部复选步骤解析为 74 项，其 pending 是外部 CLI ledger 状态，不冒充原生代理实时状态。没有向原两条实现路由发 PS-04C 任务，也没有 GLM 结果。

原池精确保留 claude-bailian/qwen3.8-max 与 opencode-bailian/bailian-token-plan-personal/deepseek-v4.1-flash；review 路由 qwen-code-review/glm-5.3 只读且待恢复后补审。原生 Codex/子代理执行是 [明确书面授权](PROJECT_TODO_PS04C_APPROVAL_20260928.md) 的例外，模型继承当前 Codex 配置，不伪装成 runner job，不自动扩大其他计划。

| 项 | 所有权 | 当前状态与验收 |
| --- | --- | --- |
| M1 | /root/ps04c_m1_migration；034、迁移helper、seed、迁移测试 | SPEC/QUALITY通过；033原子升级与真实 post-watermark KeyboardInterrupt回滚、未来水位保护有独立执行证据 |
| M2 | /root/ps04c_m2_backend；C1后端repo/service/router、项目/活动共享接线 | SPEC/QUALITY v2通过；目录九写路径、字段/角色/并发、批量标签查询与SQLite一致快照通过 |
| M3 | /root/ps04c_m3_frontend；C1前端字段、目录、原页面与身份键 | SPEC→QUALITY v6通过；fresh14场景/4890日期检查，真实浏览器21阶段/25严格几何测量通过 |
| R1 | Codex独占request.ts/token副作用测试 | SPEC/QUALITY通过；五传输身份隔离与旧会话迟到续期/401，使用合成token |
| 备份补偿 | Codex及独立审查；system_archive/受控故障测试 | v2 SPEC/QUALITY通过；真实DB/PNG补偿，失败preimages独立保留；旧FAIL证据保留 |
| 前端基线 | Codex及独立审查；8项限定format/fixture | AST/CSS与原断言证明不变；全FE7最终通过，不把fixture mock当真实PDF验收 |
| Q1 | Codex独占严格定义与compactcursor纯验证 | v1隐私P2及原失败保留；新增9例RED→GREEN，项目纯单位231通过。独立SPEC v2与不同人QUALITY v2均通过，root已接受固定两文件；纯结构不是DB/HTTP/五视图UI验收 |
| Q2 | 38个源/测试文件及Q1两依赖；root串行修复与独立复核 | 第四版PG6为62/62、17阻塞/4普通writer已提交回填交错，zero/drop且集群STOPPED；SPEC4/不同人QUALITY4均PASS/open0，root组件接受；完整BE3通过，前端1266当前字节已核验。原QUALITY FAIL及PG4 RED、旧BE失败完整保留 |
| Q3 | M3独占新repo/service/router与五个测试；root独占7个共享源与3活动测试 | Q2组件接受后仅独立worktree放行；真实认证/项目/catalog控制成功后的新GET404 RED已留证，活动新旧73项dev测试通过。完整生命周期、真实PG和SPEC→QUALITY尚未验收 |
| Q4 | SQL query | RR首读前、override优先、完整成员锁序与撤权冲突准备已固定；业务等待Q3接受，实际SQL/counts/cursor尚未交付 |
| Q5 | 五类型共享视图前端 | 只读task packet/source map就绪；实际联调等待Q4及共享页面释放 |
| V | 独立质量/真实PG/TCP浏览器/视觉/Git | C1全量/PG/browser、最终73整合、精确stage与remote SHA已通过；C2整套、GLM补审与WorkBuddy视觉仍未验 |

C1历史后端BE4全 not-live 回归为4854通过/45跳过，mypy551文件与Ruff check/format通过；前端FE7为236文件/1686测试，ESLint/Prettier auto/tsc/隔离build通过。PG3真实20用例/20独立库清理、浏览器13真实PG/TCP三种角色及管理员矩阵完成21阶段，API正常停机、所有owned进程/端口/集群关闭。命令结果与68源raw起止一致，旧失败、未完成尝试和QA夹具错误保留。

每次独立审查绑定 actual tree/文件 SHA，规格与质量分开且有顺序；证据区分fresh与采用历史结果。本台账不将C1实施片改写成C1/C2全交付，不能关闭完整PS-04或0/11全项目旅程双验收。

C2证据位于仓库外 `qa-ps04c-20260928/c2/`。Q1原SPEC_FAIL结果 `32ca31b211ab8ce303fd27dbc695a9f55f20f0a8f26d02b2e8f35deda5ae2d77` 保留；SPEC v2=`36f53c407560d07aeef43ee54cee7731a150b039fdd942fc325ba3551618fd8e`、QUALITY v2=`ae38cce906be37ef3f67e8fed01557fed20181ee266989b3a37a32ad61304336`，root组件接受=`f3ecb828e863aba10dd43860bf2ea842af88794c17356f83f7a01eec057254ce`。Q1两源仍待本轮Git发布，不能称整体C2已交付。

Q2原历史夹具638条Assert AST保留，新增4条当前升级/派生键检查。真实PG attempt1 gate=`0deb25cb633c1535a77101533e43fc37a1ca5ec18d1c184c44b41e01f916c846`，database_cleanup为false；租约已STOPPED且端口关闭，集群结果=`94833ccf2657ffc370fb3627231c10866c95f439922c8c638d9c9691e3769c34`。独立Q2 SPEC v1=`741f083a29607d7d78395eb99bc762d95fedcbaa3a57958d26c4f479563cdcb5` 的INCOMPLETE_PG_GATE_FAILED原结果保留。另六项SQLite归档原检查在长临时路径失败；同26源保持原字节、只换短Temp目录后同一23项全部通过，控制结果=`580b4ccadd9e406193c9f731205d30fe047bb1da8a23f9310b06a091c793c8a1`。

Q2第二版29文件冻结清单=`352a418a070785d864f21f68cf4e2b65368903f01d233325cad401e823186211`。PG attempt3 gate=`32d2f9765092524863f47e9cb6887d3c37130f44ddf32f58fc91be4f0af19eda` 为PASSED：58 collected/JUnit58、失败/错误/跳过0、源起止匹配、58独立库连接归零并真实DROP；cluster=`df41a1566f7fc878dd3e9de3bcf3c071aa9643c80401ebb07d3f85ad710c7935` 为STOPPED、status3及端口关闭。attempt2是Windows路径适配启动前错误，未建集群，单独保留。独立SPEC v2=`ed98f79ad6c6fb25c29eefda8ec964840e8817b165e7ab4ca4c4fb033190465e` 为SPEC_PASS：33 fresh通过/1既存Windows跳过、11组实际SQLite行为通过；不同人QUALITY v2=`c62ef7d1becfc72254eeebd5b26af94d855296636c364e2fd608819273d86d19` 为QUALITY_FAIL，开放P0=0/P1=0/P2=1（Q2-QUALITY-01）。实际迁移与仓储的SQLite调度模型复演显示新source配旧key，明确未冒充双连接PG证明；第二版PG58没有覆盖这条交错。将保留旧候选结果，新增真实PG回填/正常writer交错，修复后重封候选并重新SPEC→QUALITY。恢复证据限定已初始化public schema33–35，--no-owner重建对象沿恢复角色所有权；不泛化混合owner/空库/任意旧库，也不扩张到原用户/JWT/chat后处理的自动补偿。

C2当前全前端检查 `c2/q2-ship/frontend-attempt-1/frontend-result.json`=`1798b8923218d8d52848641725314e93852594eea0a3064a74e829d6326076c6` 为PASS：236文件/1686测试、ESLint/Prettier/tsc/build均exit0，1266输入起止一致，owned进程已关闭。这是前端当前基线检查，尚无五视图业务实现。Q2第二版全后端 `c2/q2-ship/backend-attempt-1/backend-result.json`=`559960cd14dc2ac2a039d6dca8575cf2f47bb093ea882cfcec6c27875216d4cd` 为FAILED：Ruff/format/mypy均exit0，但全not-live为5174通过/17失败/83跳过，起止源一致且owned child/process group已结束。17原失败及原报告保留；初步均为最新035库人工倒水位后触发034未知列防丢保护，须逐项独立诊断和真实历史夹具修复，不降低保护或删原断言。并发修复及夹具变更后须重验当前后端，不沿用旧候选结果。提交检查器v1仅PREPARATION_ONLY_PENDING，52/52合成控件通过但实际SPEC字段adapter探针exit1，原失败保留、未启用。

Q2第三版38文件冻结清单=`a0f5365b606ef29f425a6e816b0b5cd8252a4e8501cc0690c538928ccb69c86c`。真实PG attempt4在修复前执行完整canonical迁移，四种普通writer先提交，四例均在最终派生键断言失败；各线程结束、4库严格清理、集群停止。随后只给原回填UPDATE增加原source比较条件，6项单位回归通过。17个旧回归失败经独立诊断归于9个降水位夹具的035派生列残留；每处只移除该已知列，保留2010个原Assert及旧DDL/helper，9模块313项通过，未放宽034未知列防丢保护。真实PG attempt5=`a85861cf10d02f51af8a986971bf3af4469b85d8d2302d24a1f86889f5507304` 为PASSED/62项无失败或跳过；62库连接归零并DROP，17条锁等待及4条真实已提交交错有独立leaf；cluster=`52274e0a4a49136b3768156bb6bf81ade40da2c9d79aaf6efa3802b548633a21` 为STOPPED/stop0/status3/端口关闭。全后端attempt2保留FAILED：Ruff check0、format check1仅migrate.py，未进入mypy/pytest；不能采用旧失败或旧PG字节为格式后版本的当前门禁。

Q2最终第四版 manifest=29517f8033508ecdc848e78c3a28323655201a60d2f514e53ab617da50fa85cd；仅migrate.py排版改变、完整AST等价，其余37件与Q1两件raw不变。PG6 gate=113725d7fedde3bbc76dd0fc43ac6daad36684d19f3dd3817c15c0222aa52b0e，cluster=38993d9fcd82e14495864ee9e88b8fd63266fa80e8641043e706f8c0b3254149；62通过/零失败错误跳过、62库归零并DROP、STOPPED/stop0/status3/端口关闭。SPEC4=e4e7bba8f6d3afc557bc51358b84c236239d411b49b3deedab90d17436f9336b，QUALITY4=d0f9cf7a2bebfefede74e7f47f019eb2774e799b656f3733667a323d551d7340，均open0且不同独立审查人，root组件接受=16741091310b4fadbc000d9f20a5c3012f987f0a40fd902f50407d64d6c416b7。BE3=686af79ad75e97885cf2b1299368fd7d3fc638db27360323a3e6396ef443b9f1，为PASS、4项实际exit0/5197通过87跳过/1283输入起止一致/owned child与process group闭合。普通回归的PG跳过不能代替单独真实PG6；第三版SPEC、旧格式失败和旧PG历史均未覆盖。

原 checkout 保持 cdf1a1bc 与旧未跟踪030私密任务测试；文件模式仍False/NO-GO；030 UI、045-F保持独立。Windows无make，C1已按批准计划完成平台等价门禁和 per-command 精确提交hook，不声称运行旧make/testmon；原hook/Makefile/永久Git配置未改，未用SKIP_PRECOMMIT或--no-verify。C1门禁固定旧基线与73文件，不能原样用于C2。C2最终须重新完成全套当前门禁、独立审查、精确allowlist/index核验和非强制推送。
