# PS04C Plan→Detail 确认撤权的源码与真实浏览器验收

日期：2026-10-01。本片撤权缓存修复的源码已发布，实际浏览器与 PostgreSQL 六阶段通过，独立只读验收为 **GO，findings=[]，6/6 PASS**。本批补充验收文档；此前 C1/C2 的本地功能接受记录保留，WorkBuddy 原生界面与交互 1:1 仍待对照。

固定源码为 [47bf19d10089f093aaa2c9e3ae9464d262a40d7c](https://github.com/xiongpinji/xiongbao-Agent-mvp/commit/47bf19d10089f093aaa2c9e3ae9464d262a40d7c)。源码发布收据记录 published_head=47bf、remote=xiongbao/main、clean=true。验收运行与独立复核绑定该源码及六份方法，不将后续文档提交当成重新测试源码。

本片修复处理的是Plan已确认项目失权而Detail仍保存旧项目/成员的问题。Plan只在正常项目GET的实际HTTP403/404后发送带项目和账号的内部确认，Detail清项目与成员缓存，卸载私密配置、tabs/子页和编辑状态；保留active、账号/项目、序列及单次发布守卫。generic查询丢权路径重新确认项目，mutation路径复用已有项目GET。单个对象404后项目GET200、普通PATCH403、500/401/422及network正文中的not-found文字不应误清合法父页。

源码只发布ProjectPlan.tsx、ProjectDetail.tsx和两个对应test文件，未新增backend/API/Assets业务改动。这一确认接线与下述单案浏览器接受结果，不代表整个项目空间的权限与缓存矩阵完成。

本片实际新跑的前端证据与此前后端证据分别归属各自运行。前端测试在b424fe9519ccbe184dd5bd7934e13bec035c83d2加四文件修复候选上执行；36个来源/方法/审查输入均匹配发布binding，binding的3119来源map与最终103项运行一致，四文件raw与47bf一致。不能改写为提交47bf后重新跑了这些前端测试。

| 本片前端验证                  | 实际结果                             | 范围与边界                                                                                      |
| ----------------------------- | ------------------------------------ | ----------------------------------------------------------------------------------------------- |
| 两个组件聚焦回归              | 2文件，103/103 PASS，exit0，62.85s   | ProjectPlan.test.tsx、ProjectDetail.test.tsx                                                    |
| 项目/API相邻回归              | 36文件，704/704 PASS，exit0，269.85s | vitest run src/pages/Projects src/api/modules/project --maxWorkers=2；不是完整dashboard测试套件 |
| types                         | PASS/exit0                           | tsc -b                                                                                          |
| source-format / source-eslint | 各PASS/exit0                         | 只检查四个修改文件                                                                              |
| full-eslint                   | PASS/exit0                           | dashboard全范围；0 errors、67 warnings，保留原警告                                              |
| full-format-windows           | PASS/exit0                           | 完整Prettier --check --end-of-line auto .；已记录的Windows检查，不称默认EOL全格式检查           |
| build                         | PASS/exit0                           | 输出只到QA build目录；大于1600kB的chunk警告保留                                                 |

frontend运行中的related-tests、types、source-format、source-eslint、full-eslint、full-format-windows、build共七步均PASS/exit0，owned Job均active0/closed，源码before/after相同。103项聚焦运行独立保存；103和704不相加，也不把36文件范围称为249文件/1906项全dashboard suite。本片未执行完整make all。

离线TDD历史保留：原两个组件85/85基线不能当新增RED；首次wrapper启动失败时tests与owned child都NOT_RUN。随后真实新增RED为6 FAIL/87 PASS，V1接线93/93 GREEN之后独立源码审查仍发现P1-GET-STATUS-INFERENCE。新边界RED为8 FAIL/2 PASS/42 skipped，覆盖500正文/code、401、network和422的确认入口；严格HTTP门槛后10 PASS/42 skipped，最终两组件103/103和相邻704/704。V1源码NO_GO及其四文件快照、RED和旧GREEN均保留，42 skipped属于-t筛选范围，不写成52项通过。这些组件/API mock结果没有被当作真实403/401浏览器矩阵。

采用的后端证据归属此前固定9d8763fd5bd605402f9a891b9d5f6f5c12ca14b7的owned UTF-8运行：**5329 passed / 319 skipped / 24 warnings，1818.20s，pytest exit0**，实际命令为 `uv run --no-sync pytest -n 2 -m "not live" -q`。子进程PYTHONUTF8=1，ruff、ruff-format、mypy、python-utf8、pytest-offline五步均通过并收尾。本片没有新跑后端，不能将这份已采用结果改绑定为47bf上的fresh backend。

24个后端警告包括20个依赖弃用及4个子进程输出reader线程解码警告；真实外部command输出兼容问题未据此修复，producer并未全部定位，VNC及browser-install弱断言/模拟边界保留。exit0不证明这些平台操作成功，也不称warning-free。此前1200s timeout及19 FAIL/5310 PASS的GBK fixture解码失败原记录均保留，没有被新PASS覆盖。

实际浏览器/PG方法为parent-access-v3，run为run-de1ca0aa9b554a499345f4ae7e6a2f94；浏览器Chrome154.0.8037.58，单独1440×1000 member context。固定source/method门禁在起止检查clean47bf和3119raw，并由独立审查逐项核对当前raw及六方法、METHODS_V3、合同、method-review和EXECUTION_BINDING_V3。

| 真实阶段                 | 实际动作与证据                                                                                                                      | 结果 |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 1 合成fixture            | 正常API建1项目、owner/member邀请码加入、1条owner-created无assignee待办、简单默认共享列表；不是UI创建验收                            | PASS |
| 2 member登录             | 真实表单输入、原生鼠标slider、登录按钮；HTTP200/auth-me user3一致，无JWT预载                                                        | PASS |
| 3 撤权前私密父页与Plan   | query200的limit50完整单条DTO/view ID/version/catalog吻合；项目名、view、待办、指令、成员与展开description实际可见及命中，初始PG不变 | PASS |
| 4 owner撤权诱因          | 正常DELETE指定member204恰一次；是明确QA诱因，不是成员删除UI证据                                                                     | PASS |
| 5 member查询拒绝及父清空 | 状态筛选all→todo触发实际query404，随后两GET404及最终父终态；所有body text/HTML/input和私密区域计数0                                 | PASS |
| 6 最终只读状态/传输      | PG与撤权后全等、browser领域写0、module200、无page/network/observer/external errors                                                  | PASS |

实际请求精确链（UTC）：query#7请求02:40:32.819、404响应.850；hook项目GET#8请求.855、404响应.884，CDP .853来源useProjectPlanQuery.handleError；Plan项目GET#9请求.888、404响应.918，CDP .887来源ProjectPlanContent.lostCallback.current。confirmation request_id=9，stage实际根GET数精确为2；query完整body限定原view/version/catalog、limit50及唯一status override。没有Node补发GET、route stub或内部回调替代UI拒绝链。CDP与Playwright保留各自ID，来源匹配依据同项目顺序/时间及真实栈，不伪造跨协议ID映射。

V3在第二GET确认后等待父独有“返回项目列表”按钮和旧私密breadcrumb消失，随后captured_at=2026-10-01T02:40:32.960Z的浏览器同步DOM读取中，name/description/instructions/todo title/view name在完整body text、body HTML、input/textarea values全0；成员区、指令区、info panel、Plan renderer和tabs全0。最后截图确为父无权访问页，保留全局qa_member账号栏不属于项目私密名单。这只证明最终Plan确认与父React提交后的收敛，**不声明第一GET/首次404瞬间已经清空**，不计算精确callback延迟。

本fixture的PG合法差异只有members和membership_events：指定member2→1，owner原行不变，恰1条project.member_removed，actor owner2、object member3、payload user3/role member吻合。项目完整记录含updated_at、待办字段/version、todo事件、tag_links、catalog/priorities/tags、counts、views与collection state及database identity全等，拒绝后的PG也与撤权后深相等。无assignee避免正常取消指派的合法待办更新干扰本案；不能外推所有撤成员场景都只有这两类变化。

六张PNG均逐一目检和核验签名、1440×1000、bytes及SHA。初始project/instructions/roster三图相同SHA是三次内容不变的命名采证，不宣称三个不同布局；展开info图清楚显示description。它们与真实body HTML/input计数互补，截图不替代隐藏DOM残留检查。

父浏览器方法V1独立NO_GO是两个P2定位问题（breadcrumb前缀和readonly input点击表面），实际NOT_RUN。V2方法GO之后的真实run-8644c384c7d545dd99d65181077e862b前四阶段PASS、phase5 FAIL、phase6 NOT_RUN：首DOM仍有name text2/html2、description/instructions各1及父成员/info/tabs，稍后DOM与截图才清空。V2仍是**ACTUAL FAIL/NO_GO**，首采样无时间戳，不能精确声称采样瞬间第二GET一定pending。V3只修等待与采证时间，业务源码仍47bf；原V2 result、failureHTML/PNG和诊断不改写。

受控收尾与业务接受分开核验：browser exit0且1个context/browser关闭；API正常stop/exit0、schema35、唯一池close1/underlying_closed，sessions0后drop独占DB；PG18.6正常stop0/status3/port_closed=true。4个Owned Job均active0/closed，API/web/PG端口全闭，prefix/user/database/data_directory一致。Vite在runner对自身Job的受控finish阶段记录exit2，owned_process的TerminateJobObject终止码为2；**不是所有child exit0，也不把Job terminated计数0写成Vite自然退出**。

以下本地证明文件的SHA-256在候选制作时只读核对。原日志、请求、截图及连接内容留在本地；本稿提供摘要/散列与路径，不复制JWT、密码或原始连接配置。实际review JSON含完整输入散列清单，run-result含3119源before/aftermap。

| 本地证明                                                                                                                                                                                                                     | SHA-256                                                            |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [起始源码事实](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/publication-preparation/PARENT_SOURCE_FACTS_V1.md)                                            | `5057ba2ae9960f01f4311377cbf1e52c89962e765cffea96af252b8af0e5faa9` |
| [源码V2独立GO](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/parent-access-review-v2/review.json)                                                    | `d81d672d301fbf45bd896bcb2e7d6f986284dfa63ec9cb8fbec50571f76cb729` |
| [源码发布binding](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/ROOT_PARENT_SOURCE_BINDING_V3.json)                                                  | `dff48ae0f8c266659ff010eaa5e6db37c4fc7cb15eaecc9a2faaec23c06b5cf3` |
| [源码发布receipt](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/ROOT_PARENT_SOURCE_RELEASE_RECEIPT_V3.json)                                          | `567ac64498b005e70e18cd8387d8b035e199cb636d2f2a6fd8621e36481dd2e0` |
| [103聚焦result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/parent-access-quality/green-348d3c4384e24349a9465bcbf1bc9dea/result.json)              | `4bc554d3d10869f8d507e5131af3a8966218191719140e1d110db39882cdea87` |
| [103聚焦log](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/parent-access-quality/green-348d3c4384e24349a9465bcbf1bc9dea/two-component-tests.log)     | `8fbec7ab875056795fc3c99794307ddb2852442d80ab1b273cd05bb53d70341f` |
| [704/七步frontend result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/parent-access-quality/frontend-0d2d13185d004b21b19ab5643f2bbc2a/result.json) | `807780acf2ad01a2a1922d360c8f350251488946e10e9422c2e19e3368679530` |
| [704相关回归log](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-native-interactions-20261001/parent-access-quality/frontend-0d2d13185d004b21b19ab5643f2bbc2a/related-tests.log)    | `13a5c042d0462ed26200c692498aa1ef3e9044c3df05b77ad63762279e9dd151` |
| [已采用BE独立诊断](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-required-viewports-20261001/review-backend-failures.json)                                                        | `85cbee03f00b2171d9d7d7e0e03e0f7a06af6b6726bb56afd57a345323637266` |
| [已采用BE5329 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-required-viewports-20261001/quality/backend-utf8-full/result.json)                                            | `fb67a1083ac0abf4071877764b04b23e089c7f0084e9b2f3c873b41fa7324617` |
| [已采用BE5329 log](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-required-viewports-20261001/quality/backend-utf8-full/pytest-offline.log)                                        | `d6bec762358291ba10d45c125532e7cea5c91959a73c350ff54e26abee06e7e5` |
| [父方法V1 NO_GO](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/review-method-v1.json)                                                                      | `d0b737693d4e944c64e4a20b92ae86823edfe394504facf317a5b8d9d8177fd6` |
| [父V2 ACTUAL FAIL原result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-8644c384c7d545dd99d65181077e862b/journey/result.json)                    | `13410a5e66cb0c33cde093533e184831a90db737ba1478a0796b1b4fc3e342f2` |
| [父V2失败独立诊断](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/review-actual-parent-v2-failure.json)                                                     | `22bb2ce0dd4432e5404b8dc622525a66a1e9e1bb6670f9aa4dd1c862c967b197` |
| [V3 METHODS](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/METHODS_V3.json)                                                                                | `6c65838c86d233da592cfa2c2b61b4a36112a955d2da67056a2e28b997e1ebda` |
| [V3合同](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/versions/v3/CONTRACT.md)                                                                            | `148b5bf910f51eee1f53f5c3e3fc6db084a1355dd9a562a1868209fcd61e50e2` |
| [V3方法独立GO](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/review-method-v3.json)                                                                        | `117b8806c91d68a02bc2a1e0c909451618598152c1425fc60fa6d3e690f3aa6d` |
| [V3执行binding](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/EXECUTION_BINDING_V3.json)                                                                   | `5be1c2f8e1c74ae31c24a65cbdbe1e5f6ce5f8af2b1d0ceeba0730e8dabcf4a6` |
| [V3实际独立GO JSON](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/review-actual-parent-v3.json)                                                            | `aaec69dfab8f822cdd3276c8abf7cc35932de5c56d5c3299d089025968d5a8fe` |
| [V3实际独立GO MD](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/review-actual-parent-v3.md)                                                                | `e91fb4e0078c503ba37716aa14f5b2ef846bdbc4ef0aaf603359a3c0cf3dfbec` |
| [V3 journey result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/result.json)                           | `45f28b97a3247f941d97d8443c4a15af85d69b2b005b5c7a975450d2c9aa0898` |
| [V3 driver result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/run-result.json)                                | `a0d780727aa81925ea4858484cce6c4b2c8574482d1f86d83c1e9f5cbac8a42a` |
| [V3 API正常停机](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/server-result.json)                               | `4ee1dcf2ef5691e1f9ab8d0b300732df7d9b6bcae36a569af47df15a3c963261` |
| [V3 PG正常停机](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/pg/cluster-result.json)                            | `86ac9f1dc788c7024926c6569ada07b6e93849ec8221c4a7ec65dafbd4bd8edd` |

| 1440×1000 PNG                                                                                                                                                                                                                                                              | bytes | SHA-256                                                            |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----: | ------------------------------------------------------------------ |
| [member-after-denied-query-project-confirmation.png](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/member-after-denied-query-project-confirmation.png) | 30639 | `a807cba3730202039a3373e7ee5a36d3a1fa40e90a72639d370ae9774354c7ce` |
| [member-initial-instructions.png](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/member-initial-instructions.png)                                       | 86370 | `85f840f67badaa2dd90a3584b9c3523929841fa39d4aa593ba31bce247090f81` |
| [member-initial-parent-info.png](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/member-initial-parent-info.png)                                         | 90031 | `89e4aa9a704db27e37c3739ecaf0a0c79ebd8746e57733a4927ada51f4fc403b` |
| [member-initial-project-and-plan.png](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/member-initial-project-and-plan.png)                               | 86370 | `85f840f67badaa2dd90a3584b9c3523929841fa39d4aa593ba31bce247090f81` |
| [member-initial-roster.png](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/member-initial-roster.png)                                                   | 86370 | `85f840f67badaa2dd90a3584b9c3523929841fa39d4aa593ba31bce247090f81` |
| [member-real-login-slider.png](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-parent-access-20261001/runs/run-de1ca0aa9b554a499345f4ae7e6a2f94/journey/member-real-login-slider.png)                                             | 26009 | `7cb5811f89a728642d9f2ef7c892337592f18ec19cd6c071145c627cf0fac7c0` |

未核销范围保持：未修复版实际browser RED、toolbar刷新、完整403/单对象404/账号项目切换与迟到响应/不合法payload实际矩阵、Assets其它路径、服务重启、其它视口、native与共享控件另批、GLM、WorkBuddy原生窗口/视觉或交互1:1、真实账号/付费provider、安装包/生产验收、C2全部合同项、PS-04与整体25项。原三路由及恢复后补审记录继续保留；本稿不纳入任何并行shared运行结果。

仓库发布[脱敏摘要](evidence/ps04c-parent-shared-20261001/summary.json)与[证据散列清单](evidence/ps04c-parent-shared-20261001/evidence_manifest.json)。原始日志、请求、PNG 和连接内容只保留在本地。本批文档发布不修改业务源码、不重跑测试或构建；共享设置的新增证据见[独立验收记录](PROJECT_TODO_PS04C_SHARED_SETTINGS_20261001.md)。
