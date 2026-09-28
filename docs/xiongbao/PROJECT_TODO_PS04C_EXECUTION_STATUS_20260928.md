# PS-04C 原生执行台账（2026-09-28）

状态：C1 各实施片的规格→质量已通过，68 项业务/测试字节冻结；真实全量后端、前端、PG20 与 TCP 浏览器21阶段已通过。此台账封存于最终73文件整合和Git发布前；最终接受/发布由下列固定报告和精确提交门禁确认。C2业务尚未启动。

源基线为 `3b657f24fc5e5c1441f3cc5127201ad2def976d0`，候选细目见 [C1冻结记录](PROJECT_TODO_PS04C1_ACCEPTANCE_20260928.md) 和 [机器证据](PROJECT_TODO_PS04C1_EVIDENCE_20260928.json)。仓库外 QA 的 `ship/c1-integrated-review-final/review-result.json` 必须为 `C1_INTEGRATION_PASS`，`ship/c1-publish-result.json` 必须核对实际提交与 `xiongbao/main`，协调者才可释放 Q1/Q2。记录这些待发生门禁不等于它们已执行。

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
| Q1 | Codex独占严格定义与compactcursor纯验证 | 已准备测试草稿与只读接口包；未运行，不计RED；等待C1最终整合发布 |
| Q2 | /root/ps04c_m3_frontend承接；035、名字派生键、备份 | v2只读任务包就绪；实际SQL BaseException与旧历史包分支明确；等待C1及共享文件释放 |
| Q3/Q4 | 视图配置与SQL query | 只读准备；等待Q1/Q2分别接受；不同view独立版本与一致query快照不得绕过 |
| Q5 | 五类型共享视图前端 | 只读task packet/source map就绪；实际联调等待Q4及共享页面释放 |
| V | 独立质量/真实PG/TCP浏览器/视觉/Git | C1全量/PG/browser通过，最终73整合/精确stage/remote SHA仍待执行；C2、GLM与WorkBuddy视觉未验 |

后端BE4全 not-live 回归为4854通过/45跳过，mypy551文件与Ruff check/format通过；前端FE7为236文件/1686测试，ESLint/Prettier auto/tsc/隔离build通过。PG3真实20用例/20独立库清理、浏览器13真实PG/TCP三种角色及管理员矩阵完成21阶段，API正常停机、所有owned进程/端口/集群关闭。命令结果与68源raw起止一致，旧失败、未完成尝试和QA夹具错误保留。

每次独立审查绑定 actual tree/文件 SHA，规格与质量分开且有顺序；证据区分fresh与采用历史结果。本台账不将C1实施片改写成C1/C2全交付，不能关闭完整PS-04或0/11全项目旅程双验收。

原 checkout 保持 cdf1a1bc 与旧未跟踪030私密任务测试；文件模式仍False/NO-GO；030 UI、045-F保持独立。Windows无make，本次业务按批准计划执行上述全部平台等价门禁；不声称make all/default LF全树格式或旧testmon hook。原hook/Makefile/永久Git配置不改，不用SKIP_PRECOMMIT或--no-verify；本次per-command外部hook必须验证真实成功/清理、固定68源+5文档、6只读上下文、3批准合同与精确index，然后非强制推送fork main。
