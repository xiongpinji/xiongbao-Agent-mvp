# PS-04C 当前源码浏览器补验（2026-09-30）

固定源码 HEAD：`925171e7ff0b3e55435cee1034b2f16ae067a5c9`。本批没有修改业务代码；在同一源码上使用全新 SQLite、真实 HTTP 和 Chrome 完成 **8/8 阶段**，取得 6 张截图，独立只读复核给出 **GO，无 P0/P1/P2**。这是 PS-04C 的局部本地功能证据，不代表 WorkBuddy 1:1 或 25 项总目标完成。

## 执行范围

工具与原始记录保留在仓库外：`C:/Users/canqu/Documents/Codex/2026-09-23/qctop-work-buddy-agent-logo-ui-3/work/qa-ps04c-current-20260930`。通过该目录的 `run.py` 执行；成功目录为 `runs/run-00e9fafd24024443baae1919e0c6bf7f`，不重用前三次失败的数据库、账号或浏览器上下文。

API 与 Vite 仅监听回环；服务使用新建的 `.octop` 目录和 SQLite，禁止 PostgreSQL 回退和服务外连。四个账号均为本次合成数据，其中 `qa_admin` 只负责初始化，三个浏览器上下文分别为项目 owner、将被移除的 member 和保留成员资格的 peer。登录通过真实本地 API 获得 JWT，再预载到浏览器；**未验登录页滑块流程**。测试 harness 使用既有 fake 防止模型执行；项目领域 HTTP 响应未重写，浏览器外部请求被阻止。

## 实际通过项

| 阶段 | 结果与可验证边界 |
| --- | --- |
| 合成夹具 | 231 条真实 API 创建的待办、两个列表视图和一个蓝色优先级。每个成员使用独立的一次性邀请入组。 |
| API 跨页游标 | 每页 50 条，共 5 页、231 个不同 ID。全角 `ＡＢＣ` 位于第 226 索引；标题 NFKC、状态和处理人组合筛选返回该项。 |
| 浏览器搜索与筛选 | 真实按钮用 Enter 切换视图；搜索框 Tab 到状态控件。标题、状态、处理人同时发往服务器；`%`、`_`、反斜线均按字面筛选，每次匹配 2 条，包含远页目标。 |
| 浏览器两项批量成功 | owner 通过两项选择和“应用批量修改”按钮把状态改为 done；两项版本均从 1 升为 2，关联审计事件从 2 增到 4。还验证状态弹窗的 Enter 打开、初始焦点和 Escape 归焦。 |
| 浏览器批量冲突 | UI 保留两项旧版本，另一 API 请求先更新其中一项；随后真实批量按钮收到 409 / `version_conflict`。拒绝前后的两项数据库记录完全相同，关联事件均为 3 条；没有部分写入。 |
| 成员移除与筛选恢复 | member 无批量 UI，合法 bulk 请求返回 403 且记录/事件不变。owner 在成员列表确认移除后，旧 member 项目与 query 请求均为 404，整页重载清除项目内容。仍有权限的 peer 在 UI 临时移除失效处理人条件，恢复总数 231；共享视图完整 definition 和版本 1 保持不变。 |
| 截止日期月份与 Escape | 新建弹窗以服务器日期 `2026-09-30` 为基准，当月 prev 禁用；下一月返回边界后焦点转 next。Escape 关闭日历、归焦日期入口，外层编辑弹窗仍显示。没有保存日期。 |
| 优先级逐项编辑 | owner 通过优先级行的编辑按钮进入目录表单，名称和 blue 颜色预填正确；返回并关闭选择层后，优先级与其他草稿字段、服务器目录均不变，也没有浏览器写请求。没有执行目录保存。 |

`journey/result.json` 中 `page_errors`、`external_requests` 均为空，三个 browser context 与 Chrome 正常关闭。`server-result.json` 为 `STOPPED` 且 `stop_complete=true`；API/Web/browser 三个 owned Windows Job 的 active 均为 0，两个回环端口关闭。运行结束再次核对源码 HEAD、干净状态、14 个源码文件和 5 个工具脚本的 SHA256，绑定未漂移。

## 原始证据与复核

以下路径相对于上述仓库外工具目录；6 张截图的散列和 5 个脚本的散列均写入独立审查报告。

| 记录 | SHA256 |
| --- | --- |
| `runs/run-00e9fafd24024443baae1919e0c6bf7f/run-result.json` | `b85baf02199f70559542d4596a15dbabb84549f502eb42cc2724e679b1c57c41` |
| `runs/run-00e9fafd24024443baae1919e0c6bf7f/journey/result.json` | `9ce30814b313cfe306ebbe19d72c789fbb19826c58c6220f627f9f8f9f6c1137` |
| `runs/run-00e9fafd24024443baae1919e0c6bf7f/server-result.json` | `138a81f173307209d269f5ab0a00cb7e5fedcc6099172f64b3833ac0e667a547` |
| `ACCEPTANCE_REVIEW_V4.md` | `6d1532ebb8ff6690c9624a48a7a60703e92c923336a1e30c3306d6595a545659` |

独立代理 `/root/ps04c_current_harness_review` 先做运行工具审查，再对第四次实际报告、数据库快照、绑定和截图清单复核。审查没有再启动一次旅程；Codex 随后以独立的只读校验命令重新核对 8 阶段、6 截图、来源/工具散列和清理状态，退出码为 0。

## 失败与剩余门禁

前三次尝试完整保留，不计入通过结果：`run-ff8a64117e4245f6a17af8b858958bb4` 因 Python 不导出 `CREATE_SUSPENDED` 在创建子进程前失败；`run-9cdbce12365f4e8fa1ab82e09fc62f71` 在夹具复用一次性邀请时收到 409；`run-051cdbadc50044caa81bb54e5273e16b` 在浏览器阶段使用错误组件前缀而超时。修订只涉及仓库外工具。首轮没有启动子进程，后两轮均正常停机、owned Job active=0、端口关闭；V3 的 NO-GO 记录也保留。第四次使用真实控件语义角色和 `octop` Popconfirm 前缀后通过。

本次使用 SQLite，**没有重跑 PostgreSQL 或完整测试套件**。GLM 当前因额度未执行；本批为用户已批准 PS-04C 原生承接下的独立只读替代审查。未操作 WorkBuddy、未使用真实账号或付费模型；同夹具视觉/交互 1:1、WorkBuddy 保存语义、真实登录页、目录/日期保存结果及完整项目空间旅程仍不得由本记录核销。之前的全套、PG 与分片门禁仍以各自固定快照记录为准。
