# PS04C 共享设置：实际 V8 补充验收

状态：**ACTUAL PASS，11/11；固定运行的独立只读实际审查 GO，findings=[]。** 本记录补充本地合成用户、真实 Chrome 与独占 PostgreSQL 的共享设置验收。既有 C1/C2 本地功能验收继续保留；WorkBuddy 同状态原生窗口的视觉与交互 1:1 尚未完成。

固定源码为已发布的 `47bf19d10089f093aaa2c9e3ae9464d262a40d7c`。本轮 driver 记录源码起止均为该 HEAD、clean=true，3119 份来源及六执行方法的 before/after 散列全部相同。该来源已发布到熊宝仓库；本次记录没有修改业务源码。

实际 run 为 `run-9ddef79fbdc04f07aabcdd5b87c23fe6`，journey 为 PASS，driver 为 PASS；实际执行 driver 已正常返回 exit0。旅程时间为 2026-10-01 03:15:10.064–03:16:14.931 UTC。执行前冻结合同的 PREPARING_NOT_RUN 是历史准备状态，本稿依据新 run 补充实际结果，不改写合同或方法审查。

本片使用新建的合成 PG/API 项目、七条待办、两张不分组表格视图；保留其他默认视图。fixture 通过实际 API 准备，不构成 UI 创建验收。owner 在 1280×768、member 在 800×728 的两个独立 Chrome context 中使用真实登录表单、原生 pointer 滑块和登录按钮，均 HTTP200，无 auth token 预载。

定义 A 保留七字段 `title,status,assignee,priority,tags,start_date,due_date`，`schema_version=1`、`group_by=null`。三个 AND 条件依次为 title contains 命中词、status in [todo]、tags any [tagA]；三键排序依次为 priority ASC、due_date ASC、title ASC。三个对照待办分别只违反 title、status、tags 条件，匹配四行使用优先级、日期及标题平局证明排序。

| 定义 | 真实设置差异                                    | 实际四行顺序                    |
| ---- | ----------------------------------------------- | ------------------------------- |
| A    | 三条件及三排序如上                              | Alpha → Bravo → Charlie → Delta |
| B    | 仅第三排序改 title DESC，临时应用               | Bravo → Alpha → Charlie → Delta |
| C    | 从 A 仅第二排序改 due_date DESC，临时应用后保存 | Charlie → Alpha → Bravo → Delta |

| 实际阶段                   | 可核销的动作与结果                                                                                              | 结果 |
| -------------------------- | --------------------------------------------------------------------------------------------------------------- | ---- |
| 1 合成 fixture             | 七待办与三种排除对照、两张表格视图及初始 PG 读回                                                                | PASS |
| 2 owner 登录及初始视图     | 真实表单/滑块登录，主视图原七行 title DESC、query/DOM 一致                                                      | PASS |
| 3 A 临时应用               | 真实控件输入三个 AND 和三排序；完整 override、DTO/顺序精确，Modal 输入保留；非 query 写0、完整 PG 不变          | PASS |
| 4 明确共享保存 A           | 唯一 owner 主视图 PATCH 成功，version1→2；无 override 的四行恢复，事件+1                                        | PASS |
| 5 新 member 登录/刷新      | 800×728 真实登录，读取共享 A；UI 刷新后相同 version、完整 DTO/顺序                                              | PASS |
| 6 member 有效草稿/禁用保存 | 修改有效标题后保存 native disabled；真实 pointer 命中并点击，写0；取消、确认舍弃、页面 reload 恢复 A 和所选视图 | PASS |
| 7 继续编辑 B               | dirty 切换弹出三选择；继续编辑保留主视图、完整 B 输入和四行结果，目标未选中，写0、PG 不变                       | PASS |
| 8 舍弃并切换               | 目标视图真实选中，无 override 七行 title ASC；返回主视图恢复 A 的完整输入/四行，写0、PG 不变                    | PASS |
| 9 保存 C 后切换            | 第二次 owner 主视图 PATCH 成功后才查询目标七行；返回主视图恢复已保存 C，version2→3、事件+1                      | PASS |
| 10 member 获取最新 C       | UI 刷新取得共享 C/version3 和相同四行顺序，写0、PG 不变                                                         | PASS |
| 11 最终写入/查询/PG 审计   | 恰两次 owner PATCH、member/todo/其他领域写0；最终完整 PG 与第二次保存后全等                                     | PASS |

“保存后切换”的成功 PATCH 响应序号为28，目标 query 请求序号为29，证明保存成功先于目标查询。本片证明成功路径；竞争失败时保草稿及不切换仍列后续。

全部17条受观察项目请求为15条 POST query 与2条 owner PATCH。15条 query 均已捕获 HTTP200、limit50、next_cursor=null，view ID/version/catalog revision 与请求相符；取消/abort、network failures、page errors、外连均0。本 fixture 是 table/group_by=null，metadata 从完整页接受；分组独立 limit1 stats 为 **NOT_CLAIMED_UNGROUPED_TABLES**。

13 个必要操作或刷新后的查询页由冻结旅程在内存逐项比较完整 DTO，另两条初始默认视图查询保存已捕获摘要。全部 15 条查询的 metadata、ID、精确顺序、总数与 cursor 已由独立审查重算核对。observer 只序列化响应摘要，未保存整个 HTTP 原始响应体，不能把摘要称为完整响应归档。

两次 PATCH 均由 owner 的 UI 发起并返回200：定义 A/C、expected_version1/2、成功 version2/3；共享集合 revision3→4→5，安全 view update 事件恰+2。事件逐项核对 actor/object/version/collection_revision/fields=[definition]。临时应用、member 操作、继续编辑和舍弃切换阶段均无额外领域写入。

最终只读 PG 快照含 database_identity、完整待办 rows、tag_links、todo events、catalog_state、priorities、tags、project_counts、views、view_state、view_events；它与第二次保存后快照全等。待办、关联、todo 事件、目录、优先级/标签、计数及其他视图均与初始状态相同；仅主视图共享定义/版本、集合状态及两条安全更新事件发生预期变化。

收尾独立于业务断言：两个 context 和 browser 已关闭，browser/API/PG child exit0，四个 Owned Job active0/closed，API/web/PG 三端口闭合。API 池恰关闭一次、underlying_closed=true，session0 后 drop 本轮独占数据库；PG 正常 stop0/status3/port_closed=true。Vite 在 owned 受控结束中为 **exit2**，不得写成全部 child exit0 或自然退出。

源码质量证据沿用 [父访问修复验收](PROJECT_TODO_PS04C_PARENT_ACCESS_20261001.md)：103/103 聚焦回归、36文件704/704相邻回归，以及 related-tests/types/source-format/source-eslint/full-eslint/full-format-windows/build 七步 PASS。这些不是全 dashboard suite，也不是为本 shared 旅程新复跑；此前后端5329 passed/319 skipped/24 warnings归属原固定源码运行，本片没有新跑 FE/BE。

旧记录逐轮保留；实际 FAIL 不因本轮 PASS 被改写。以下链接只指向本地原记录。

| 历史版本                                                                                                                                                                                        | 原结果与失败边界                                                                                                            |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| [V1 原 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-424983e1f8964217a10fa2ffce857475/journey/result.json) | ACTUAL FAIL：fixture HTTP422，浏览器验收 NOT_RUN；原响应体未序列化，不能补造422正文                                         |
| [V2 原 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-ca4b9e33b2b8483e8f77dd96c42c7f31/journey/result.json) | ACTUAL FAIL：前两阶段 PASS，readonly input 鼠标目标被标题 span 拦截                                                         |
| [V3 原 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-53523c7460aa43249929d9d5e1faeb28/journey/result.json) | ACTUAL FAIL：前两阶段 PASS，状态预计清空却仍为 todo；Tab 会触发再选择是已安装控件源码支持的解释，未采集逐键 DOM/event trace |
| [V4 方法审查](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/review-method-v4.json)                                          | 静态 NO_GO，runtime NOT_RUN；不称业务 FAIL                                                                                  |
| [V5 原 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-38d562e42e734eaeac50a3b08931553d/journey/result.json) | ACTUAL FAIL：前两阶段 PASS，页级/Modal 未保存标记定位歧义                                                                   |
| [V6 原 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-a961a55701a1461f8798feb8048b152b/journey/result.json) | ACTUAL FAIL：前四阶段 PASS，实际“刷 新”按钮名称未匹配；只有第一次共享保存完成                                               |
| [V7 原 result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-3c89d7f95a884214acf8fa7d1c83d190/journey/result.json) | ACTUAL FAIL：十阶段 PASS/两 PATCH 后，最终审计误要求 limit1；失败后的 db_final 未采集，不能称整轮成功                       |

V1/V2/V3/V5/V6/V7 均无最终 db_final；各阶段已有快照不替代缺失的最终全量对账。原 failure HTML/PNG、诊断和结果留在原路径。本轮 V8 只纠正不分组表格查询预期，未修改业务源码或增加 UI 动作。

V8 方法独立审查与固定实际运行的独立审查均为 GO；实际审查接受 11/11、15 条已捕获查询、两次 owner 保存、允许变化范围内的完整 PG 对账、15 张 PNG 及 owned 收尾。方法审查与实际审查的原始报告分别保留，下面各文件已只读核对 SHA-256。

| 本地证明文件                                                                                                                                                                                         | SHA-256                                                            |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| [V8 实际独立 GO JSON](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/review-actual-shared-v8.json)                                | `640cdc79a3087f22852b9917831cea0a67a78c5c80b82376903ee763bee0c798` |
| [V8 实际独立 GO MD](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/review-actual-shared-v8.md)                                    | `6ad993f854760132b43ea298d89a3baca234674a1bf5d5897e76c54ffdfb1eca` |
| [V8 journey result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-9ddef79fbdc04f07aabcdd5b87c23fe6/journey/result.json) | `3f21ff39b7ca653e3829267b32f9743f7de76f268bf89169a765e00974b2b32f` |
| [V8 driver result](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-9ddef79fbdc04f07aabcdd5b87c23fe6/run-result.json)      | `2e41089756b6d0bff168a34bb17067d6a0e1292db88bce47af0f665647c1112d` |
| [V8 METHODS](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/METHODS_V8.json)                                                      | `25c8d26c59f1e353fcb5fe9c07d3c3e054477ac39dbee081ff7d67b88e52f802` |
| [V8 冻结合同](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/versions/v8/CONTRACT.md)                                             | `267f69fd189ccf7821939f0ad6c4365d801a14338a4fab11a395246b36f50ee7` |
| [V8 执行 binding](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/EXECUTION_BINDING_V8.json)                                       | `1e0abda0382105dd34ca5271ebe897d970b35c244aab7d425c3874bf32af8c97` |
| [V8 方法 GO JSON](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/review-method-v8.json)                                           | `3922ba17bbda6e32500893006e1fff6837cd3d3c5e991dc85dec00c3e58c802e` |
| [V8 方法 GO MD](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/review-method-v8.md)                                               | `5e5d1e8e122cb9f6dfb1b28db115293c02b334d79f56a974f04ab94fa4249183` |
| [V8 API 收尾](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-9ddef79fbdc04f07aabcdd5b87c23fe6/server-result.json)        | `fcd0ece97005e5c1999fe9e29a4f4dc6ecfa14f053661de8a5ac14a578d3c39c` |
| [V8 PG 收尾](C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-shared-controls-20261001/runs/run-9ddef79fbdc04f07aabcdd5b87c23fe6/pg/cluster-result.json)     | `9cabdcf3f4e4f4c0dc846efb2cb28fa1099e02aaaa2dffd0d701c4bfa197b093` |

15张当前 PNG 的文件签名、尺寸、bytes 与 SHA 已逐一核对，尺寸/散列与 journey 清单一致；只列本地媒体索引，不嵌入或复制 PNG。以下核对不作 WorkBuddy 原生窗口、视觉或1:1推论；两张目标七行图相同 SHA 是内容相同的两次采证。

| 本地 PNG 文件名                      | 尺寸     |  bytes | SHA-256                                                            |
| ------------------------------------ | -------- | -----: | ------------------------------------------------------------------ |
| member-800x728-real-login-slider.png | 800×728  |  22075 | `6338a53a09026871af078ca0f9c934e2b2d277ca743b45cd980e4ff4d8b82646` |
| member-latest-saved-C.png            | 800×728  |  77855 | `08e5cede8fe9ef619ee019ade00b656ae21c637b0dc53cadba7442f7b0ff9fe8` |
| member-reload-restored-A.png         | 800×728  |  76023 | `e10ce9f60c97515e53199233f8e5b436f3f5437f3ccb1b105b2e6935c5e7b714` |
| member-shared-A-refreshed.png        | 800×728  |  78076 | `1637408179b0fc352740fef28f619720ecd1e579c055caa2d2ad34692f544aed` |
| member-valid-draft-disabled-save.png | 800×728  |  49550 | `13be4cf6a2452c9e3db51185b63e5d3ac3cd9a3a7fa093723ec677c0ae6ace0e` |
| owner-1280x768-real-login-slider.png | 1280×768 |  23940 | `bf13080319659528740d7dbe86547e3e3404feae1c937c471b1aee92a9353a17` |
| owner-applied-three-sort.png         | 1280×768 |  87489 | `9df83d6140a27868e37a482df012bb758cebc753539118b2f86f4b416fd96c69` |
| owner-continue-retained-B.png        | 1280×768 | 102450 | `efe9225a2c5b44ba7acb9dcde5c1c224fee6dc276eed3e4058a65b85ee187242` |
| owner-dirty-three-choices.png        | 1280×768 | 111951 | `fef37bcf7877ffa6d91fd78cae45784f2c2d47f2aee3388a25aed9abd7386c5b` |
| owner-discard-restored-A.png         | 1280×768 |  99336 | `a7efc137de11901a01bab657412d87b2b6c17abc66b4d30899930a97ed50e805` |
| owner-discard-target-seven.png       | 1280×768 |  99227 | `9c3e11068cd64548708cce3c64afcd5df93d9f95669e9f6de34df6377f53ce0a` |
| owner-initial-seven.png              | 1280×768 |  99600 | `c8a65eb4fbdbbfb8c1bcd504e7eb94af79b38e9f92fe2fb97b53344de39709cb` |
| owner-return-saved-C.png             | 1280×768 |  99307 | `f0f2c92a5cfc1e55a4baa5d09161d09ce8e899d32a0c193fd8a167f06fa1c0ce` |
| owner-save-then-target.png           | 1280×768 |  99227 | `9c3e11068cd64548708cce3c64afcd5df93d9f95669e9f6de34df6377f53ce0a` |
| owner-shared-A-saved.png             | 1280×768 |  99244 | `0c1e839d122268b64db12b51e8620ae7e7433db88b3171ca14c3a5a542c23edf` |

既有 C1/C2 本地功能验收结论继续保留。本片只补充上述共享设置动作的合成 PG/API/Chrome 证据；WorkBuddy 原生窗口视觉与交互 1:1、完整 PS04/整体25项、真实账号、付费 provider、实际业务422、竞争失败、metadata失败，以及 Gantt 等其他组合或分页的补充证据仍未核销。

仓库发布[脱敏摘要](evidence/ps04c-parent-shared-20261001/summary.json)与[证据散列清单](evidence/ps04c-parent-shared-20261001/evidence_manifest.json)。原始日志、请求、PNG 和连接内容只保留在本地。本批文档发布没有修改业务源码、没有重跑测试或构建；父页撤权的新增证据见[独立验收记录](PROJECT_TODO_PS04C_PARENT_ACCESS_20261001.md)。
