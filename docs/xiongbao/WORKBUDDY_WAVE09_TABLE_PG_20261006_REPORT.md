# 熊宝 Agent：表格一级子待办与 PostgreSQL 验证

2026-10-06。本记录补充已推送的表格一级子待办实现及真实 PostgreSQL 聚焦验证。源码检查点为 `0d4d06d1bff79eb702c848c13014792bd912f919`，位于 `feature/workbuddy-full-parity-20261004`；[PR #2](https://github.com/xiongpinji/xiongbao-Agent-mvp/pull/2) 仍为面向 develop 的草稿，未合并或发布。

## 表格实现与普通质量

表格视图支持严格布尔值 `show_subtodos`，默认关闭。开启后，每个根待办及一级子待办独立参与服务端筛选、分组、排序和分页；查询响应投影同一授权快照中的父标题。旧客户端省略该键时保留已存值，显式布尔值才修改它；开启模式的游标绑定项目层级版本。前端显示子行缩进和父待办入口，根待办的子项统计继续独立计算。完整行为见[表格合同](PROJECT_TODO_TABLE_SUBTODOS_W03.md)。

表格实现已随 `cf4a0710` 推送。随后 `0d4d06d1` 仅修改三份测试文件，修复 Windows 测试访问 WSL PostgreSQL 临时集群时的路径判断，并增加纯路径回归。两次提交均通过原有提交钩子；未跳过钩子。

| 验证层次 | 固定记录 |
| --- | --- |
| 本地完整 `make all PYTEST_JOBS=4` | 5,874 项通过、324 项跳过、25 条警告；Ruff、格式及严格 mypy 通过 |
| 前端默认全量测试 | 262 个文件、2,264 项通过 |
| 前端类型、格式与构建 | 通过；lint 零错误、67 条既有警告 |
| `0d4d06d1` 普通提交钩子 | `make precommit` 30 项通过、160 项跳过，随后 dashboard build 通过 |

完整 `make all` 绑定修改后的源码字节，并非由提交钩子代替。当前钩子实际执行 `make precommit` 和前端构建。本地完整测试包含六份尚未接线、未提交的存储候选文件中的测试；它们未进入本次发布，不能将本地计数等同于远端计数。前端源码在后续三文件测试修复中未变化，前端全量结论保留为独立记录。

## 真实 PostgreSQL 唯一节点

实际运行原始节点：

`tests/integration/test_project_plan_query_pg.py::test_tabletrue_parent_snapshot_hierarchy_cursor_and_constant_budget`

结果为 **1 项通过，0 失败、0 错误、0 跳过**。JUnit 测试时间为 2.989 秒，完整受监督运行退出码为 0，用时 65.224 秒。两组查询均为 15 次 SELECT，父标题快照、层级游标及固定查询预算断言通过。

测试使用本轮新建的 WSL PostgreSQL 18 集群和独立测试身份，Windows Python 通过 TCP 核验数据库身份后执行该节点。3091 项约定的源码、运行依赖及方法输入在运行前后一致。这是约定范围的验证，不代表整个操作系统或全部第三方实现文件均已核验。

清理记录确认：临时测试库通过普通 DROP 删除，剩余客户端连接为零；同一 postmaster 身份正常 smart stop，最终 pidfile 不存在；Windows 与 WSL 测试端口监听为空。22 个 Windows 命令的 Job、子进程及日志句柄已关闭，18 个远端控制均 CLOSED，没有未知关闭状态。

独立只读审查结论为 `GO_SCOPED_ACTUAL_ONE_NODE`，无阻断，仅接受这个原始节点及其自有资源关闭。此前三次实际尝试在集群或测试启动前失败，原日志仍保留；本次没有改写这些失败结果。

| 私有原始证据 | SHA-256 |
| --- | --- |
| `ROOT_OUTER_EXECUTION_RECEIPT_V4.json` | `3d6d475659388db43371aa631f635b60c8fed14d777f6c426d665c4ee231bcdf` |
| `INDEPENDENT_ACTUAL_REVIEW_V4.json` | `553ecce56b60c189178fe2bd73e29a2b58f3bd284900ae2164b80d8d6600ac74` |
| 完整质量 `normal-gates-v19/RESULT.json` | `6a0d2e13a9101edbaef506943ac436a74e686475dbceb9b6e7bef4697bf5539b` |

原件位于本库忽略目录 `_local/qa/full-parity-20261004/`，PG 原件位于其中的 `w03-table-subtodos-owned-pg-runtime-v1/`。日志中的初始 schema watermark 查询错误及数据库正常关闭消息保留，不以删日志方式获得通过。该节点不是完整 PostgreSQL 套件或浏览器旅程验收。

## Hosted 与 Windows 交付状态

以下 Hosted 结论绑定 V2 抓取快照，时间为 UTC `2026-10-05T22:33:33.368075+00:00`。[CI 37378312698](https://github.com/xiongpinji/xiongbao-Agent-mvp/actions/runs/37378312698) 对应功能分支 head `0d4d06d1`；其 Linux 实际检查的是 PR 合成合并提交 `39ae360a7fac8006a7a9fbf347547ea994b7454d`，不是已合并到 develop 的证明。

- Linux Python 3.12 已通过：5,959 项通过、194 项跳过、35 条警告。
- Windows Python 3.12 仍在运行，不能登记为通过。
- Live job 的 33 项全部跳过，6,153 项未选中；真实供应商调用未验证。
- [桌面构建 37378312610](https://github.com/xiongpinji/xiongbao-Agent-mvp/actions/runs/37378312610) 的 Windows amd64/arm64 与 Darwin amd64/arm64 均成功。本轮只读取构建及制品元数据，未下载或安装制品；安装、升级、卸载和原生界面仍待验证。

后续文档提交或源码修改的 Hosted 结果需要分别绑定新运行，不挪用上述 SHA 的通过结果。

随后 V3 抓取确认同一 CI 运行已整体成功。Windows Python 3.12 原始日志为 **5,834 项通过、319 项跳过、21 条警告**，测试用时 5,026.72 秒；Windows 实际检查的同样是合成合并提交 `39ae360a7fac8006a7a9fbf347547ea994b7454d`。V2 的 Windows 运行中快照作为历史状态保留。V3 收据 SHA-256 为 `af7ff03b89dd9d46c5a452a9b6aad054594b1d078f7d5cd49432830c61e046ad`；原始 Windows 日志 SHA-256 为 `dfb5ded751d75027b83932f266822b138d0f6c81d909e284d6f1563f2dece834`。Live 跳过和安装未验收的边界保持不变。

## 仍未完成的边界

私有 SDK 候选的 16 项恢复会话语义测试通过，但严格离线方法仍因两处第三方依赖初始化探针被拦截而 NO-GO。原尝试整体退出码为 1，697 个原生句柄已关闭、Job 为零、输入字节未变；没有将语义通过改写为整体运行通过。该 SDK 候选未发布，046 M0 仍未验收。

待办附件存储、030 文件模式与 046 项目资产正文进入任务仍保持关闭。项目通知仍处于设计阶段。本片没有核销实际多身份浏览器旅程、WorkBuddy 原生同状态交互和视觉、真实 Office/模型/连接器或完整安装交付，也不表示整体 1:1 对齐已经完成。
